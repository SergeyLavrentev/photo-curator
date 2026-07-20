import AppKit
import Combine
import Photos
import QuickLookUI
import SwiftUI
import UniformTypeIdentifiers

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
    @Published var tasteCalibrationExamples = 0
    @Published var tasteHeldOutExamples = 0
    @Published var tastePair: TastePair?
    @Published var tasteRemaining = 0
    @Published var tasteMessage: String?
    @Published var qualityMessage: String?
    @Published var qualitySeriesSelection: Set<String> = []
    @Published var isTasteBusy = false
    @Published var selectedPhotoID: String?
    @Published var photoAccessNeedsAction = false

    let worker = NativeWorkerClient()
    private var pollTask: Task<Void, Never>?
    private var permissionHelpTask: Task<Void, Never>?
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

    var tasteStatusTitle: String {
        [
            "collecting": "Настраивается",
            "ready": "Активен",
            "stale": "Нужно переобучить",
            "paused": "На паузе",
            "incompatible": "Нужна повторная настройка после обновления анализа",
        ][tasteStatus] ?? tasteStatus
    }

    var progressMessage: String {
        if let active = jobs.last(where: { $0.status == "running" }) {
            return active.message.isEmpty ? stageName(active.stage) : active.message
        }
        if project?.state == "interrupted" { return "Анализ остановлен — его можно продолжить" }
        if project?.state == "ready" { return "Анализ завершён — подборка готова к проверке" }
        return "Подготовка анализа"
    }

    func start() {
        Task {
            if ProcessInfo.processInfo.environment["PHOTO_CURATOR_NATIVE_DEMO"] != "1" {
                guard await requestPhotoLibraryAccess() else { return }
            }
            await bootstrap()
        }
    }

    func shutdown() {
        pollTask?.cancel()
        permissionHelpTask?.cancel()
        worker.stop()
    }

    func openPhotoPrivacySettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Photos") else {
            return
        }
        NSWorkspace.shared.open(url)
    }

    func toggleTasteProfile() {
        let paused = tasteStatus != "paused"
        Task {
            do {
                let result = try await call("taste_status", ["paused": paused])
                if let value = result as? [String: Any] { applyTasteProfile(value) }
                tasteMessage = paused ? "Профиль вкуса поставлен на паузу." : "Профиль вкуса снова активен."
                await refreshDecisionsForTaste()
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func exportTasteProfile() {
        Task {
            do {
                let result = try await call("taste_export")
                let data = try JSONSerialization.data(
                    withJSONObject: result,
                    options: [.prettyPrinted, .sortedKeys]
                )
                let panel = NSSavePanel()
                panel.nameFieldStringValue = "photo-curator-taste-profile.json"
                panel.allowedContentTypes = [.json]
                guard panel.runModal() == .OK, let url = panel.url else { return }
                try data.write(to: url, options: .atomic)
                tasteMessage = "Профиль вкуса экспортирован."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func resetTasteProfile() {
        Task {
            do {
                _ = try await call("taste_reset")
                tasteStatus = "collecting"
                tasteExamples = 0
                tasteCalibrationExamples = 0
                tasteHeldOutExamples = 0
                tastePair = nil
                tasteMessage = "Профиль вкуса удалён. Используется общий Swipe Score."
                await refreshDecisionsForTaste()
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func exportQualityEvidence() {
        guard let project else { return }
        Task {
            do {
                let result = try await call("quality_export", ["project_id": project.id])
                guard
                    let value = result as? [String: Any],
                    let manifest = value["manifest"],
                    let snapshot = value["score_snapshot"],
                    let summary = value["summary"] as? [String: Any]
                else { throw NativeWorkerClientError.invalidResponse }

                let panel = NSOpenPanel()
                panel.title = "Куда сохранить проверочный набор?"
                panel.prompt = "Сохранить"
                panel.canChooseFiles = false
                panel.canChooseDirectories = true
                panel.canCreateDirectories = true
                panel.allowsMultipleSelection = false
                guard panel.runModal() == .OK, let directory = panel.url else { return }

                let manifestData = try JSONSerialization.data(
                    withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]
                )
                let snapshotData = try JSONSerialization.data(
                    withJSONObject: snapshot, options: [.prettyPrinted, .sortedKeys]
                )
                try manifestData.write(
                    to: directory.appendingPathComponent("photo-curator-labels.json"),
                    options: .atomic
                )
                try snapshotData.write(
                    to: directory.appendingPathComponent("photo-curator-swipe-scores.json"),
                    options: .atomic
                )
                let labels = summary["manual_labels"] as? Int ?? 0
                let heldOut = summary["held_out_pairs"] as? Int ?? 0
                let topK = summary["expected_top_k"] as? Int ?? 0
                let series = summary["human_duplicate_groups"] as? Int ?? 0
                let releaseReady = summary["release_ready"] as? Bool ?? false
                let instructions = """
                Photo Curator quality evidence

                Ручных решений: \(labels) (release gate: 50–100).
                Проверочных A/B-пар: \(heldOut) (release gate: не менее 10).
                Top-K: \(topK) (release gate: не менее 5).
                Подтверждённых серий: \(series) (release gate: не менее 1).
                Структура release fixture готова: \(releaseReady ? "да" : "нет").

                photo-curator-labels.json содержит только явные решения пользователя.
                Автоматические решения и найденные сервисом дубли не копируются в эталон.
                Top-K и лучший кадр серии задаются пользователем в review-галерее.
                Серия попадёт в manifest только после ручного решения для каждого её кадра.
                photo-curator-swipe-scores.json фиксирует оценки и версии текущего движка.
                """
                try Data(instructions.utf8).write(
                    to: directory.appendingPathComponent("README-quality-evidence.txt"),
                    options: .atomic
                )
                qualityMessage = releaseReady
                    ? "Quality fixture готов к evaluation."
                    : "Сохранено: \(labels) решений, \(heldOut) A/B, Top‑K \(topK), серий \(series)."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func evaluateQualityEvidence() {
        guard let project else { return }
        Task {
            do {
                let labelsPanel = NSOpenPanel()
                labelsPanel.title = "Выберите human-labelled manifest"
                labelsPanel.prompt = "Выбрать labels"
                labelsPanel.allowedContentTypes = [.json]
                labelsPanel.allowsMultipleSelection = false
                guard labelsPanel.runModal() == .OK, let labelsURL = labelsPanel.url else { return }

                let scoresPanel = NSOpenPanel()
                scoresPanel.title = "Выберите замороженный Swipe Score"
                scoresPanel.prompt = "Оценить"
                scoresPanel.allowedContentTypes = [.json]
                scoresPanel.allowsMultipleSelection = false
                guard scoresPanel.runModal() == .OK, let scoresURL = scoresPanel.url else { return }

                let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: labelsURL))
                let snapshot = try JSONSerialization.jsonObject(with: Data(contentsOf: scoresURL))
                let result = try await call("quality_evaluate", [
                    "project_id": project.id,
                    "manifest": manifest,
                    "score_snapshot": snapshot,
                ])
                guard let report = result as? [String: Any] else {
                    throw NativeWorkerClientError.invalidResponse
                }
                let passed = report["passed"] as? Bool ?? false
                let eligible = report["release_eligible"] as? Bool ?? false
                let labels = report["labelled_assets"] as? Int ?? 0
                qualityMessage = eligible
                    ? "Acceptance: \(passed ? "PASS" : "FAIL"), \(labels) фото."
                    : "Набор пока неполный: \(labels) фото; release gate не открыт."
            } catch { errorMessage = error.localizedDescription }
        }
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
        qualitySeriesSelection.removeAll()
        qualityMessage = nil
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

    func resumeAnalysis() {
        guard let project else { return }
        isBusy = true
        errorMessage = nil
        Task {
            do {
                _ = try await call("resume_analysis", ["project_id": project.id])
                isBusy = false
                try await Task.sleep(nanoseconds: 200_000_000)
                startPolling(projectID: project.id)
            } catch {
                isBusy = false
                errorMessage = error.localizedDescription
            }
        }
    }

    func cancelAnalysis() {
        guard let project else { return }
        Task {
            do {
                _ = try await call("cancel_analysis", ["project_id": project.id])
            } catch { errorMessage = error.localizedDescription }
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

    func toggleQualityTopK(photoID: String) {
        guard let project, let photo = photos.first(where: { $0.id == photoID }) else { return }
        Task {
            do {
                _ = try await call("quality_top_k", [
                    "project_id": project.id,
                    "asset_uuid": photoID,
                    "selected": photo.qualityTopKRank == nil,
                ])
                await loadPhotos(projectID: project.id)
                qualityMessage = photo.qualityTopKRank == nil
                    ? "Фото добавлено в ваш Top‑K."
                    : "Фото удалено из вашего Top‑K."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func labelSeriesLeader(photoID: String) {
        guard
            let project,
            let photo = photos.first(where: { $0.id == photoID }),
            let groupID = photo.duplicateGroup
        else { return }
        Task {
            do {
                _ = try await call("quality_series", [
                    "project_id": project.id,
                    "group_id": groupID,
                    "leader_uuid": photoID,
                ])
                await loadPhotos(projectID: project.id)
                qualityMessage = "Выбор лучшего кадра серии сохранён как человеческая разметка."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func toggleQualitySeriesSelection(photoID: String) {
        if qualitySeriesSelection.contains(photoID) {
            qualitySeriesSelection.remove(photoID)
        } else {
            qualitySeriesSelection.insert(photoID)
        }
    }

    func saveCustomQualitySeries() {
        guard
            let project,
            let leaderID = selectedPhotoID,
            qualitySeriesSelection.count >= 2,
            qualitySeriesSelection.contains(leaderID)
        else {
            qualityMessage = "Выберите минимум два кадра серии и один из них как текущий лидер."
            return
        }
        let members = qualitySeriesSelection.sorted()
        Task {
            do {
                _ = try await call("quality_custom_series", [
                    "project_id": project.id,
                    "member_uuids": members,
                    "leader_uuid": leaderID,
                ])
                qualitySeriesSelection.removeAll()
                await loadPhotos(projectID: project.id)
                qualityMessage = "Ручная серия сохранена; текущий кадр назначен лидером."
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
                if tasteCalibrationExamples >= 3 {
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
                    tasteMessage = "Выбор сохранён. Ещё \(max(0, 3 - tasteCalibrationExamples))."
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
            if let restored = projects.first(where: {
                $0.state == "ready" || $0.state == "running" || $0.state == "interrupted"
            }) {
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

    private func requestPhotoLibraryAccess() async -> Bool {
        workerStatus = "Запрашиваем доступ к Apple Photos…"
        photoAccessNeedsAction = false
        let status: PHAuthorizationStatus
        if PHPhotoLibrary.authorizationStatus(for: .readWrite) == .notDetermined {
            permissionHelpTask?.cancel()
            permissionHelpTask = Task {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard !Task.isCancelled else { return }
                photoAccessNeedsAction = true
                workerStatus = "Подтвердите доступ к Apple Photos"
            }
            status = await withCheckedContinuation { continuation in
                PHPhotoLibrary.requestAuthorization(for: .readWrite) { value in
                    continuation.resume(returning: value)
                }
            }
        } else {
            status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        }
        permissionHelpTask?.cancel()
        permissionHelpTask = nil
        guard status == .authorized || status == .limited else {
            workerStatus = "Нет доступа к Apple Photos"
            photoAccessNeedsAction = true
            errorMessage = "Разрешите Photo Curator доступ к Фото в Системных настройках → Конфиденциальность и безопасность → Фото."
            return false
        }
        photoAccessNeedsAction = false
        return true
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
                    if updated.state == "interrupted" { return }
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
        tasteCalibrationExamples = value["calibration_count"] as? Int ?? tasteCalibrationExamples
        tasteHeldOutExamples = value["held_out_count"] as? Int ?? tasteHeldOutExamples
        tasteStatus = value["status"] as? String ?? "Не настроен"
    }

    private func refreshDecisionsForTaste() async {
        guard let project, project.state == "ready" else { return }
        do {
            _ = try await call("start_analysis", [
                "project_id": project.id,
                "from_stage": "decisions",
            ])
            startPolling(projectID: project.id)
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
                .frame(width: 560, height: 300)
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
                if model.photoAccessNeedsAction {
                    Label(
                        "Разрешите доступ в системном запросе или настройках macOS.",
                        systemImage: "photo.badge.exclamationmark"
                    )
                    .foregroundStyle(.secondary)
                    Button("Открыть настройки доступа к Фото") {
                        model.openPhotoPrivacySettings()
                    }
                } else if model.workerStatus.hasPrefix("Ошибка:") {
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
                 ? "Профиль: \(model.tasteStatusTitle). Обучающих: \(model.tasteCalibrationExamples), проверочных: \(model.tasteHeldOutExamples)."
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
            if model.project?.state == "interrupted" {
                Button {
                    model.resumeAnalysis()
                } label: {
                    Label("Продолжить с прерванного этапа", systemImage: "play.fill")
                        .frame(maxWidth: .infinity).padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(model.isBusy)
            } else {
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
            if model.project?.state == "running" {
                Button(role: .destructive) {
                    model.cancelAnalysis()
                } label: {
                    Label("Остановить анализ", systemImage: "stop.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
        }
    }

    private var reviewSection: some View {
        StepCard(number: 4, title: "Проверьте и сохраните", symbol: "checkmark.rectangle.stack") {
            Text("Фото отсортированы по Swipe Score. Исправьте только спорные решения; причины спрятаны в ⓘ.")
                .foregroundStyle(.secondary)
            if !model.qualitySeriesSelection.isEmpty {
                HStack {
                    Text("В ручной серии: \(model.qualitySeriesSelection.count)")
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Button("Сбросить") { model.qualitySeriesSelection.removeAll() }
                    Button("Сохранить; текущий кадр — лидер") {
                        model.saveCustomQualitySeries()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(
                        model.qualitySeriesSelection.count < 2
                            || !model.qualitySeriesSelection.contains(model.selectedPhotoID ?? "")
                    )
                }
                .padding(10)
                .background(.tint.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 16)], spacing: 16) {
                ForEach(model.photos) { photo in
                    PhotoCard(
                        photo: photo,
                        selected: model.selectedPhotoID == photo.id,
                        select: { model.selectedPhotoID = photo.id },
                        preview: {
                            model.selectedPhotoID = photo.id
                            model.previewSelected()
                        },
                        seriesSelected: model.qualitySeriesSelection.contains(photo.id),
                        toggleTopK: { model.toggleQualityTopK(photoID: photo.id) },
                        toggleSeriesSelection: {
                            model.toggleQualitySeriesSelection(photoID: photo.id)
                        },
                        labelSeriesLeader: { model.labelSeriesLeader(photoID: photo.id) }
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
    let seriesSelected: Bool
    let toggleTopK: () -> Void
    let toggleSeriesSelection: () -> Void
    let labelSeriesLeader: () -> Void
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
                Button(action: toggleTopK) {
                    HStack(spacing: 3) {
                        Image(systemName: photo.qualityTopKRank == nil ? "star" : "star.fill")
                        if let rank = photo.qualityTopKRank {
                            Text("#\(rank)").font(.caption2.monospacedDigit())
                        }
                    }
                }
                .buttonStyle(.plain)
                .help(photo.qualityTopKRank == nil ? "Добавить в мой Top‑K" : "Убрать из моего Top‑K")
                .accessibilityLabel(
                    photo.qualityTopKRank.map { "Позиция \($0) в моём Top-K" }
                        ?? "Добавить в мой Top-K"
                )
                Button(action: toggleSeriesSelection) {
                    Image(systemName: seriesSelected ? "square.stack.3d.up.fill" : "square.stack.3d.up")
                        .foregroundStyle(seriesSelected ? Color.accentColor : Color.primary)
                }
                .buttonStyle(.plain)
                .help(seriesSelected ? "Убрать из ручной серии" : "Добавить в ручную серию")
                .accessibilityLabel(
                    seriesSelected ? "Убрать фото из ручной серии" : "Добавить фото в ручную серию"
                )
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
            if photo.duplicateGroup != nil {
                Button(action: labelSeriesLeader) {
                    Label(
                        photo.qualityExpectedLeader ? "Лучший кадр серии подтверждён" : "Это лучший кадр серии",
                        systemImage: photo.qualityExpectedLeader ? "checkmark.seal.fill" : "square.stack.3d.up"
                    )
                }
                .buttonStyle(.borderless)
                .font(.caption.weight(.semibold))
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
    @State private var confirmsTasteReset = false

    var body: some View {
        Form {
            Section("Локальный движок") {
                LabeledContent("Статус", value: model.workerStatus)
                if model.photoAccessNeedsAction {
                    Button("Открыть настройки доступа к Фото") {
                        model.openPhotoPrivacySettings()
                    }
                }
                Button("Перезапустить движок") { model.restartWorker() }
            }
            Section("Персональный вкус") {
                LabeledContent("Профиль", value: model.tasteStatusTitle)
                LabeledContent("Обучающих сравнений", value: "\(model.tasteCalibrationExamples)")
                LabeledContent("Проверочных сравнений", value: "\(model.tasteHeldOutExamples)")
                HStack {
                    Button(model.tasteStatus == "paused" ? "Возобновить" : "Поставить на паузу") {
                        model.toggleTasteProfile()
                    }
                    .disabled(model.tasteExamples == 0)
                    Button("Экспортировать…") { model.exportTasteProfile() }
                        .disabled(model.tasteExamples == 0)
                    Button("Удалить профиль…", role: .destructive) {
                        confirmsTasteReset = true
                    }
                    .disabled(model.tasteExamples == 0)
                }
            }
            Section("Проверка качества") {
                Text("Экспортирует только ваши явные решения и A/B-сравнения вместе с замороженным Swipe Score.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button("Экспортировать проверочный набор…") {
                    model.exportQualityEvidence()
                }
                .disabled(model.project?.state != "ready")
                Button("Оценить заполненный набор…") {
                    model.evaluateQualityEvidence()
                }
                .disabled(model.project?.state != "ready")
                if let message = model.qualityMessage {
                    Text(message).font(.caption).foregroundStyle(.secondary)
                }
            }
            Text("Все вычисления и предпочтения остаются на этом Mac.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(20)
        .confirmationDialog(
            "Удалить профиль вкуса?",
            isPresented: $confirmsTasteReset,
            titleVisibility: .visible
        ) {
            Button("Удалить профиль и все сравнения", role: .destructive) {
                model.resetTasteProfile()
            }
            Button("Отмена", role: .cancel) {}
        } message: {
            Text("Фотографии и альбомы не изменятся. Персональные оценки будут пересчитаны по общему Swipe Score.")
        }
    }
}
