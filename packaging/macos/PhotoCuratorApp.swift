import AppKit
import Combine
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var workerStatus = "Запуск локального движка…"
    @Published var albums: [AlbumItem] = []
    @Published var sharedAlbums: [AlbumItem] = []
    @Published var selectedAlbumID = ""
    @Published var density = "balanced"
    @Published var project: ProjectItem?
    @Published var jobs: [JobItem] = []
    @Published var photos: [PhotoItem] = []
    @Published var errorMessage: String?
    @Published var isBusy = false
    @Published var publishPlan: PublishPlan?
    @Published var publishMessage: String?
    @Published var tasteStatus = "Не настроен"
    @Published var tasteExamples = 0

    let worker = NativeWorkerClient()
    private var pollTask: Task<Void, Never>?

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

    func setDecision(photoID: String, disposition: String?) {
        guard let project else { return }
        Task {
            do {
                var params: [String: Any] = ["project_id": project.id, "asset_uuid": photoID]
                params["disposition"] = disposition ?? NSNull()
                _ = try await call("decision", params)
                if let index = photos.firstIndex(where: { $0.id == photoID }) {
                    photos[index].disposition = disposition
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
            if let groups = rawAlbums as? [String: Any] {
                albums = (groups["regular"] as? [[String: Any]] ?? []).compactMap(AlbumItem.init)
                sharedAlbums = (groups["shared"] as? [[String: Any]] ?? []).compactMap(AlbumItem.init)
                if selectedAlbumID.isEmpty { selectedAlbumID = albums.first?.id ?? "" }
            }
            if let taste = rawTaste as? [String: Any] {
                tasteExamples = taste["preference_count"] as? Int ?? 0
                tasteStatus = taste["status"] as? String ?? "Не настроен"
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
        } catch { errorMessage = error.localizedDescription }
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
                    ForEach(model.albums) { album in
                        Text("\(album.name) · \(album.photoCount) фото").tag(album.id)
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
                Label("Общие альбомы появятся здесь после нативного PhotoKit intake в S6.", systemImage: "person.2")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var tasteSection: some View {
        StepCard(number: 2, title: "Персональный вкус — опционально", symbol: "heart.text.square") {
            Text(model.tasteExamples > 0
                 ? "Профиль \(model.tasteStatus), сравнений: \(model.tasteExamples). Swipe Score уже учитывает ваши предпочтения."
                 : "Можно начать без настройки. После первого анализа вы сможете сравнить пары кадров, и приложение запомнит ваш вкус.")
                .foregroundStyle(.secondary)
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
                    PhotoCard(photo: photo) { disposition in
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
    let decide: (String?) -> Void
    @State private var showDetails = false

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
                Text(photo.swipeScore.map(String.init) ?? "—")
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
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.separator.opacity(0.4)))
    }
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
