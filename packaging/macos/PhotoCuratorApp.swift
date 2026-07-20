import AppKit
import Combine
import QuickLookUI
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var workerStatus = "Запуск локального движка…"
    @Published var albums: [AlbumItem] = []
    @Published var sharedAlbums: [AlbumItem] = []
    @Published var selectedAlbumID: String {
        didSet { UserDefaults.standard.set(selectedAlbumID, forKey: "selectedAlbumID") }
    }
    @Published var density: String {
        didSet { UserDefaults.standard.set(density, forKey: "selectionDensity") }
    }
    @Published var project: ProjectItem?
    @Published var jobs: [JobItem] = []
    @Published var photos: [PhotoItem] = []
    @Published var errorMessage: String?
    @Published var isBusy = false
    @Published var publishPlan: PublishPlan?
    @Published var publishMessage: String?
    @Published var tasteStatus = "Не настроен"
    @Published var tasteExamples = 0
    @Published var tastePair: TastePair?
    @Published var tasteRemaining = 0
    @Published var tasteMessage: String?
    @Published var isTasteBusy = false
    @Published var selectedPhotoID: String?

    let worker = NativeWorkerClient()
    private var pollTask: Task<Void, Never>?
    private var decisionHistory: [DecisionUndo] = []

    init() {
        selectedAlbumID = UserDefaults.standard.string(forKey: "selectedAlbumID") ?? ""
        let savedDensity = UserDefaults.standard.string(forKey: "selectionDensity") ?? "balanced"
        density = ["compact", "balanced", "broad"].contains(savedDensity)
            ? savedDensity : "balanced"
    }

    var progress: Double {
        guard !jobs.isEmpty else { return 0 }
        return jobs.map(\.progress).reduce(0, +) / Double(jobs.count)
    }

    var progressMessage: String {
        if let active = jobs.last(where: { $0.status == "running" }) {
            return active.message.isEmpty ? stageName(active.stage) : active.message
        }
        if project?.state == "ready" { return "Анализ завершён — подборка готова к проверке" }
        return "Подготовка анализа"
    }

    func start() {
        Task { await bootstrap() }
    }

    func shutdown() {
        pollTask?.cancel()
        worker.stop()
    }

    func restartWorker() {
        worker.stop()
        workerStatus = "Перезапуск…"
        Task { await bootstrap() }
    }

    func createAndAnalyze() {
        guard !selectedAlbumID.isEmpty else {
            errorMessage = "Сначала выберите альбом"
            return
        }
        isBusy = true
        errorMessage = nil
        photos = []
        selectedPhotoID = nil
        decisionHistory = []
        tastePair = nil
        tasteMessage = nil
        publishPlan = nil
        Task {
            do {
                let created = try await call("create_project", [
                    "album_id": selectedAlbumID,
                    "selection_density": density,
                ])
                guard let value = created as? [String: Any], let project = ProjectItem(value) else {
                    throw NativeWorkerClientError.invalidResponse
                }
                self.project = project
                _ = try await call("start_analysis", ["project_id": project.id])
                isBusy = false
                startPolling(projectID: project.id)
            } catch {
                isBusy = false
                errorMessage = error.localizedDescription
            }
        }
    }

    func setDecision(photoID: String, disposition: String?, recordUndo: Bool = true) {
        guard let project else { return }
        let previousManual = photos.first(where: { $0.id == photoID })?.manualDisposition
        Task {
            do {
                var params: [String: Any] = ["project_id": project.id, "asset_uuid": photoID]
                params["disposition"] = disposition ?? NSNull()
                let result = try await call("decision", params)
                if let value = result as? [String: Any],
                   let updated = PhotoItem(value),
                   let index = photos.firstIndex(where: { $0.id == photoID })
                {
                    photos[index] = updated
                    if recordUndo && previousManual != disposition {
                        decisionHistory.append(DecisionUndo(photoID: photoID, previous: previousManual))
                    }
                }
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func movePhotoSelection(_ offset: Int) {
        guard !photos.isEmpty else { return }
        let current = selectedPhotoID.flatMap { id in photos.firstIndex(where: { $0.id == id }) } ?? 0
        selectedPhotoID = photos[min(max(0, current + offset), photos.count - 1)].id
    }

    func decideSelected(_ disposition: String) {
        guard let selectedPhotoID else { return }
        setDecision(photoID: selectedPhotoID, disposition: disposition)
    }

    func undoLastDecision() {
        guard let change = decisionHistory.popLast() else { return }
        selectedPhotoID = change.photoID
        setDecision(photoID: change.photoID, disposition: change.previous, recordUndo: false)
    }

    func previewSelected() {
        guard let selectedPhotoID,
              let path = photos.first(where: { $0.id == selectedPhotoID })?.imagePath
        else { return }
        QuickLookController.shared.show(path: path)
    }

    func chooseTaste(preferredID: String) {
        guard let project, let pair = tastePair else { return }
        isTasteBusy = true
        tasteMessage = nil
        Task {
            defer { isTasteBusy = false }
            do {
                let result = try await call("taste_preference", [
                    "project_id": project.id,
                    "left_uuid": pair.left.id,
                    "right_uuid": pair.right.id,
                    "preferred_uuid": preferredID,
                ])
                if let value = result as? [String: Any],
                   let profile = value["profile"] as? [String: Any]
                {
                    applyTasteProfile(profile)
                }
                if tasteExamples >= 3 {
                    let trained = try await call("taste_train")
                    if let profile = trained as? [String: Any] { applyTasteProfile(profile) }
                    tastePair = nil
                    tasteMessage = "Вкус обновлён. Пересчитываем Swipe Score…"
                    _ = try await call("start_analysis", [
                        "project_id": project.id,
                        "from_stage": "decisions",
                    ])
                    try await Task.sleep(nanoseconds: 250_000_000)
                    startPolling(projectID: project.id)
                } else {
                    await loadTastePair(projectID: project.id)
                    tasteMessage = "Выбор сохранён. Ещё \(max(0, 3 - tasteExamples))."
                }
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func preparePublish() {
        guard let project else { return }
        isBusy = true
        Task {
            defer { isBusy = false }
            do {
                let result = try await call("publish_dry_run", [
                    "project_id": project.id,
                    "kind": "best",
                ])
                guard let value = result as? [String: Any], let plan = PublishPlan(value) else {
                    throw NativeWorkerClientError.invalidResponse
                }
                publishPlan = plan
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func applyPublish() {
        guard let publishPlan else { return }
        isBusy = true
        Task {
            defer { isBusy = false }
            do {
                let result = try await call("publish_apply", [
                    "publish_id": publishPlan.id,
                    "confirmed": true,
                ])
                let value = result as? [String: Any]
                publishMessage = value?["status"] as? String == "applied"
                    ? "Готово: одобренная подборка сохранена в Photos."
                    : "Публикация завершена."
                self.publishPlan = nil
            } catch { errorMessage = error.localizedDescription }
        }
    }

    private func bootstrap() async {
        do {
            let status = try await call("status") as? [String: Any]
            guard status?["status"] as? String == "ready" else {
                throw NativeWorkerClientError.invalidResponse
            }
            workerStatus = "Локальный движок готов"
            let rawAlbums = try await callWithRetry("albums", attempts: 3)
            let rawTaste = try await call("taste_profile")
            let rawProjects = try await call("projects")
            if let groups = rawAlbums as? [String: Any] {
                albums = (groups["regular"] as? [[String: Any]] ?? []).compactMap(AlbumItem.init)
                sharedAlbums = (groups["shared"] as? [[String: Any]] ?? []).compactMap(AlbumItem.init)
                let availableIDs = Set((albums + sharedAlbums).map(\.id))
                if !availableIDs.contains(selectedAlbumID) {
                    selectedAlbumID = albums.first?.id ?? sharedAlbums.first?.id ?? ""
                }
            }
            if let taste = rawTaste as? [String: Any] { applyTasteProfile(taste) }
            let projects = (rawProjects as? [[String: Any]] ?? []).compactMap(ProjectItem.init)
            if let restored = projects.first(where: { $0.state == "ready" || $0.state == "running" }) {
                project = restored
                if (albums + sharedAlbums).contains(where: { $0.id == restored.albumID }) {
                    selectedAlbumID = restored.albumID
                }
                await restoreProject(restored)
            }
        } catch {
            workerStatus = "Ошибка: \(error.localizedDescription)"
            errorMessage = error.localizedDescription
        }
    }

    private func startPolling(projectID: String) {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled {
                do {
                    let result = try await call("project", ["project_id": projectID])
                    guard let value = result as? [String: Any],
                          let rawProject = value["project"] as? [String: Any],
                          let updated = ProjectItem(rawProject)
                    else { throw NativeWorkerClientError.invalidResponse }
                    project = updated
                    jobs = (value["jobs"] as? [[String: Any]] ?? []).compactMap(JobItem.init)
                    if updated.state == "ready" {
                        await loadPhotos(projectID: projectID)
                        return
                    }
                    if updated.state == "error" {
                        errorMessage = "Анализ завершился с ошибкой. Детали сохранены в локальном журнале."
                        return
                    }
                    try await Task.sleep(nanoseconds: 1_000_000_000)
                } catch is CancellationError {
                    return
                } catch {
                    errorMessage = error.localizedDescription
                    return
                }
            }
        }
    }

    private func loadPhotos(projectID: String) async {
        do {
            let result = try await call("assets", ["project_id": projectID])
            let value = result as? [String: Any]
            photos = (value?["items"] as? [[String: Any]] ?? []).compactMap(PhotoItem.init)
            if !photos.contains(where: { $0.id == selectedPhotoID }) {
                selectedPhotoID = photos.first?.id
            }
            await loadTastePair(projectID: projectID)
        } catch { errorMessage = error.localizedDescription }
    }

    private func restoreProject(_ restored: ProjectItem) async {
        do {
            let result = try await call("project", ["project_id": restored.id])
            guard let value = result as? [String: Any],
                  let rawProject = value["project"] as? [String: Any],
                  let updated = ProjectItem(rawProject)
            else { throw NativeWorkerClientError.invalidResponse }
            project = updated
            jobs = (value["jobs"] as? [[String: Any]] ?? []).compactMap(JobItem.init)
            if updated.state == "running" {
                startPolling(projectID: updated.id)
            } else if updated.state == "ready" {
                await loadPhotos(projectID: updated.id)
            }
        } catch { errorMessage = error.localizedDescription }
    }

    private func loadTastePair(projectID: String) async {
        do {
            let result = try await call("taste_pair", ["project_id": projectID])
            guard let value = result as? [String: Any] else {
                throw NativeWorkerClientError.invalidResponse
            }
            tasteRemaining = value["remaining"] as? Int ?? 0
            tastePair = (value["pair"] as? [String: Any]).flatMap(TastePair.init)
            if tasteMessage?.contains("Пересчитываем") == true {
                tasteMessage = "Swipe Score обновлён с учётом вашего вкуса."
            }
        } catch { errorMessage = error.localizedDescription }
    }

    private func applyTasteProfile(_ value: [String: Any]) {
        tasteExamples = value["preference_count"] as? Int ?? tasteExamples
        tasteStatus = value["status"] as? String ?? "Не настроен"
    }

    private func call(_ method: String, _ params: [String: Any] = [:]) async throws -> Any {
        let worker = worker
        return try await Task.detached(priority: .userInitiated) {
            try worker.request(method: method, params: params)
        }.value
    }

    private func callWithRetry(_ method: String, attempts: Int) async throws -> Any {
        var lastError: Error = NativeWorkerClientError.invalidResponse
        for attempt in 1...attempts {
            do { return try await call(method) } catch {
                lastError = error
                if attempt < attempts { try await Task.sleep(nanoseconds: 500_000_000) }
            }
        }
        throw lastError
    }

    private func stageName(_ stage: String) -> String {
        [
            "inventory": "Читаем альбом",
            "previews": "Готовим изображения",
            "metrics": "Проверяем качество",
            "duplicates": "Сравниваем серии",
            "vision": "Apple Vision оценивает кадры",
            "decisions": "Формируем подборку",
        ][stage] ?? "Анализируем"
    }
}

private struct DecisionUndo {
    let photoID: String
    let previous: String?
}

final class QuickLookController: NSObject, QLPreviewPanelDataSource {
    static let shared = QuickLookController()
    private var previewURL: NSURL?

    func show(path: String) {
        previewURL = URL(fileURLWithPath: path) as NSURL
        guard let panel = QLPreviewPanel.shared() else { return }
        panel.dataSource = self
        panel.currentPreviewItemIndex = 0
        panel.reloadData()
        panel.makeKeyAndOrderFront(nil)
    }

    func numberOfPreviewItems(in panel: QLPreviewPanel!) -> Int {
        previewURL == nil ? 0 : 1
    }

    func previewPanel(_ panel: QLPreviewPanel!, previewItemAt index: Int) -> QLPreviewItem! {
        previewURL
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        model?.shutdown()
        return .terminateNow
    }
}

@main
struct PhotoCuratorApplication: App {
    @StateObject private var model: AppModel
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        let model = AppModel()
        _model = StateObject(wrappedValue: model)
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 920, minHeight: 680)
                .onAppear {
                    appDelegate.model = model
                    model.start()
                }
        }
        .defaultSize(width: 1180, height: 820)
        .commands {
            CommandMenu("Проверка фото") {
                Button("Предыдущее фото") { model.movePhotoSelection(-1) }
                    .keyboardShortcut(.leftArrow, modifiers: [])
                    .disabled(model.photos.isEmpty)
                Button("Следующее фото") { model.movePhotoSelection(1) }
                    .keyboardShortcut(.rightArrow, modifiers: [])
                    .disabled(model.photos.isEmpty)
                Button("Быстрый просмотр") { model.previewSelected() }
                    .keyboardShortcut(.space, modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Divider()
                Button("Оставить") { model.decideSelected("keep") }
                    .keyboardShortcut("1", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Button("На проверку") { model.decideSelected("review") }
                    .keyboardShortcut("2", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Button("Не брать") { model.decideSelected("reject") }
                    .keyboardShortcut("3", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Divider()
                Button("Отменить решение") { model.undoLastDecision() }
                    .keyboardShortcut("z", modifiers: .command)
            }
        }

        Settings {
            SettingsView()
                .environmentObject(model)
                .frame(width: 480, height: 230)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 18) {
                Label("Photo Curator", systemImage: "camera.aperture")
                    .font(.title2.bold())
                StepLabel(number: 1, title: "Выбрать альбом", active: model.project == nil)
                StepLabel(number: 2, title: "Настроить вкус", active: model.project == nil)
                StepLabel(number: 3, title: "Анализ", active: model.project?.state == "running")
                StepLabel(number: 4, title: "Проверить и сохранить", active: !model.photos.isEmpty)
                Spacer()
                Label(
                    model.workerStatus,
                    systemImage: model.workerStatus == "Локальный движок готов"
                        ? "checkmark.circle" : "arrow.triangle.2.circlepath"
                )
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(24)
            .navigationSplitViewColumnWidth(min: 220, ideal: 250)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    header
                    sourceSection
                    tasteSection
                    analysisSection
                    if !model.photos.isEmpty { reviewSection }
                }
                .padding(34)
                .frame(maxWidth: 1280, alignment: .leading)
            }
            .background(Color(nsColor: .windowBackgroundColor))
            .alert("Photo Curator", isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { model.errorMessage = nil }
            } message: {
                Text(model.errorMessage ?? "")
            }
            .confirmationDialog(
                "Сохранить подборку в Photos?",
                isPresented: Binding(
                    get: { model.publishPlan != nil },
                    set: { if !$0 { model.publishPlan = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Создать альбом и импортировать \(model.publishPlan?.itemCount ?? 0) фото") {
                    model.applyPublish()
                }
                Button("Отмена", role: .cancel) { model.publishPlan = nil }
            } message: {
                Text("Исходный альбом не изменится. Будет создан только новый Best‑альбом.")
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ваши лучшие фотографии")
                .font(.system(size: 36, weight: .bold, design: .rounded))
            Text("Swipe Score находит кадры, которые хочется оставить, а вы принимаете финальное решение.")
                .font(.title3)
                .foregroundStyle(.secondary)
        }
    }

    private var sourceSection: some View {
        StepCard(number: 1, title: "Выберите альбом", symbol: "photo.on.rectangle.angled") {
            if model.albums.isEmpty {
                if model.workerStatus.hasPrefix("Ошибка:") {
                    Button("Повторить чтение Apple Photos") { model.restartWorker() }
                } else {
                    ProgressView("Читаем Apple Photos…")
                }
            } else {
                Picker("Альбом", selection: $model.selectedAlbumID) {
                    Section("Мои альбомы") {
                        ForEach(model.albums) { album in
                            Text("\(album.name) · \(album.photoCount) фото").tag(album.id)
                        }
                    }
                    if !model.sharedAlbums.isEmpty {
                        Section("Общие альбомы") {
                            ForEach(model.sharedAlbums) { album in
                                Text("\(album.name) · \(album.photoCount) фото").tag(album.id)
                            }
                        }
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                Picker("Объём подборки", selection: $model.density) {
                    Text("Компактная").tag("compact")
                    Text("Сбалансированная").tag("balanced")
                    Text("Широкая").tag("broad")
                }
                .pickerStyle(.segmented)
            }
            if !model.sharedAlbums.isEmpty {
                Label("Для общего альбома PhotoKit подготовит локальные review‑копии; источник не изменится.", systemImage: "person.2")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var tasteSection: some View {
        StepCard(number: 2, title: "Персональный вкус — опционально", symbol: "heart.text.square") {
            Text(model.tasteExamples > 0
                 ? "Профиль: \(model.tasteStatus), сравнений: \(model.tasteExamples)."
                 : "Можно начать без настройки. После анализа выберите лучший из пары кадров.")
                .foregroundStyle(.secondary)
            if let pair = model.tastePair {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Какой кадр вы бы оставили?").font(.headline)
                    HStack(spacing: 14) {
                        TasteChoiceCard(photo: pair.left, disabled: model.isTasteBusy) {
                            model.chooseTaste(preferredID: pair.left.id)
                        }
                        TasteChoiceCard(photo: pair.right, disabled: model.isTasteBusy) {
                            model.chooseTaste(preferredID: pair.right.id)
                        }
                    }
                    Text("Три выбора дают первую настройку. Фотографии и исходный альбом не изменяются.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if model.isTasteBusy { ProgressView("Запоминаем выбор…") }
            if let message = model.tasteMessage {
                Label(message, systemImage: "heart.fill").foregroundStyle(.pink)
            }
        }
    }

    private var analysisSection: some View {
        StepCard(number: 3, title: "Проанализируйте", symbol: "sparkles") {
            if model.project?.state == "running" || !model.jobs.isEmpty {
                ProgressView(value: model.progress) {
                    Text(model.progressMessage)
                } currentValueLabel: {
                    Text("\(Int(model.progress * 100))%")
                }
                .progressViewStyle(.linear)
                DisclosureGroup("Детали этапов") {
                    ForEach(model.jobs) { job in
                        HStack {
                            Image(systemName: job.status == "running" ? "arrow.triangle.2.circlepath" : "checkmark.circle")
                            Text(job.stage)
                            Spacer()
                            Text("\(job.processed) / \(job.total)")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Button {
                model.createAndAnalyze()
            } label: {
                Label(model.project == nil ? "Начать анализ" : "Создать новый анализ", systemImage: "play.fill")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(model.isBusy || model.selectedAlbumID.isEmpty || model.project?.state == "running")
        }
    }

    private var reviewSection: some View {
        StepCard(number: 4, title: "Проверьте и сохраните", symbol: "checkmark.rectangle.stack") {
            Text("Фото отсортированы по Swipe Score. Исправьте только спорные решения; причины спрятаны в ⓘ.")
                .foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 16)], spacing: 16) {
                ForEach(model.photos) { photo in
                    PhotoCard(
                        photo: photo,
                        selected: model.selectedPhotoID == photo.id,
                        select: { model.selectedPhotoID = photo.id },
                        preview: {
                            model.selectedPhotoID = photo.id
                            model.previewSelected()
                        }
                    ) { disposition in
                        model.setDecision(photoID: photo.id, disposition: disposition)
                    }
                }
            }
            Button {
                model.preparePublish()
            } label: {
                Label("Подготовить Best‑альбом", systemImage: "photo.badge.plus")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
            .controlSize(.large)
            .disabled(model.isBusy)
            if let message = model.publishMessage {
                Label(message, systemImage: "checkmark.seal.fill").foregroundStyle(.green)
            }
        }
    }
}

struct StepCard<Content: View>: View {
    let number: Int
    let title: String
    let symbol: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Text("\(number)")
                    .font(.headline)
                    .frame(width: 32, height: 32)
                    .background(.tint, in: Circle())
                    .foregroundStyle(.white)
                Label(title, systemImage: symbol).font(.title2.bold())
            }
            content
        }
        .padding(22)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(.separator.opacity(0.5)))
    }
}

struct StepLabel: View {
    let number: Int
    let title: String
    let active: Bool

    var body: some View {
        HStack(spacing: 10) {
            Text("\(number)").font(.caption.bold()).frame(width: 24, height: 24)
                .background(active ? Color.accentColor : Color.secondary.opacity(0.15), in: Circle())
                .foregroundStyle(active ? .white : .secondary)
            Text(title).fontWeight(active ? .semibold : .regular)
        }
    }
}

struct PhotoCard: View {
    let photo: PhotoItem
    let selected: Bool
    let select: () -> Void
    let preview: () -> Void
    let decide: (String?) -> Void
    @State private var showDetails = false

    private var scoreText: String {
        photo.swipeScore.map(String.init) ?? "—"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ZStack(alignment: .topTrailing) {
                Group {
                    if let path = photo.imagePath, let image = NSImage(contentsOfFile: path) {
                        Image(nsImage: image).resizable().scaledToFill()
                    } else {
                        Rectangle().fill(.quaternary).overlay(Image(systemName: "photo"))
                    }
                }
                .frame(height: 180)
                .clipped()
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .onTapGesture(count: 2, perform: preview)
                .onTapGesture(perform: select)
                Text(scoreText)
                    .font(.headline.monospacedDigit())
                    .padding(8)
                    .background(.ultraThickMaterial, in: Capsule())
                    .padding(8)
            }
            HStack {
                Text(photo.filename).lineLimit(1).font(.subheadline.weight(.medium))
                Spacer()
                Button { showDetails.toggle() } label: { Image(systemName: "info.circle") }
                    .buttonStyle(.plain)
                    .popover(isPresented: $showDetails) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Почему этот результат").font(.headline)
                            Text("Generic: \(photo.genericScore ?? 0, specifier: "%.1f")")
                            Text("Ваш вкус: \(photo.personalDelta ?? 0, specifier: "%+.1f")")
                            Text("Уверенность: \((photo.confidence ?? 0) * 100, specifier: "%.0f")%")
                            ForEach(photo.reasons, id: \.self) { Text("• \($0)") }
                        }.padding().frame(width: 260)
                    }
            }
            Picker("Решение", selection: Binding(
                get: { photo.disposition ?? "review" },
                set: { decide($0) }
            )) {
                Text("Оставить").tag("keep")
                Text("Проверить").tag("review")
                Text("Не брать").tag("reject")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
        }
        .padding(12)
        .background(.background, in: RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(selected ? Color.accentColor : Color(nsColor: .separatorColor).opacity(0.4),
                        lineWidth: selected ? 3 : 1)
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(photo.filename), Swipe Score \(photo.swipeScore ?? 0)")
        .accessibilityValue(dispositionTitle(photo.disposition))
        .accessibilityHint("Клавиши 1, 2 и 3 меняют решение; пробел открывает быстрый просмотр")
    }
}

struct TasteChoiceCard: View {
    let photo: PhotoItem
    let disabled: Bool
    let choose: () -> Void

    var body: some View {
        Button(action: choose) {
            VStack(alignment: .leading, spacing: 8) {
                Group {
                    if let path = photo.imagePath, let image = NSImage(contentsOfFile: path) {
                        Image(nsImage: image).resizable().scaledToFill()
                    } else {
                        Rectangle().fill(.quaternary).overlay(Image(systemName: "photo"))
                    }
                }
                .frame(maxWidth: .infinity).frame(height: 190)
                .clipped().clipShape(RoundedRectangle(cornerRadius: 12))
                HStack {
                    Text(photo.filename).lineLimit(1)
                    Spacer()
                    Text(photo.swipeScore.map(String.init) ?? "—")
                        .font(.headline.monospacedDigit())
                }
                Text("Выбрать этот кадр").font(.caption.weight(.semibold))
            }
            .padding(10)
            .contentShape(RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(.tint, lineWidth: 1))
        .disabled(disabled)
        .accessibilityLabel("Выбрать \(photo.filename) для настройки вкуса")
    }
}

private func dispositionTitle(_ disposition: String?) -> String {
    ["keep": "Оставить", "review": "На проверку", "reject": "Не брать"][disposition ?? ""]
        ?? "Без решения"
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Локальный движок") {
                LabeledContent("Статус", value: model.workerStatus)
                Button("Перезапустить движок") { model.restartWorker() }
            }
            Section("Персональный вкус") {
                LabeledContent("Профиль", value: model.tasteStatus)
                LabeledContent("Сравнений", value: "\(model.tasteExamples)")
            }
            Text("Все вычисления и предпочтения остаются на этом Mac.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(20)
    }
}
