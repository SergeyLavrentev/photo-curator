import AppKit
import Combine
import Photos
import QuickLookUI
import SwiftUI
import UniformTypeIdentifiers

enum WorkflowStep: Int, CaseIterable, Identifiable {
    case album
    case analysis
    case selection

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .album: return "Альбом"
        case .analysis: return "Анализ"
        case .selection: return "Отбор"
        }
    }

    var symbol: String {
        switch self {
        case .album: return "photo.on.rectangle.angled"
        case .analysis: return "sparkles"
        case .selection: return "square.grid.3x3"
        }
    }
}

enum SelectionBucket: String, CaseIterable, Identifiable {
    case keep
    case reject

    var id: String { rawValue }
    var title: String { self == .keep ? "Хорошие" : "Плохие" }
}

private enum HelpSection: String, CaseIterable, Identifiable {
    case overview
    case howItWorks

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "О приложении"
        case .howItWorks: return "Как это работает"
        }
    }

    var symbol: String {
        switch self {
        case .overview: return "questionmark.circle"
        case .howItWorks: return "point.3.connected.trianglepath.dotted"
        }
    }
}

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
    @Published var analysisMode: String {
        didSet { UserDefaults.standard.set(analysisMode, forKey: "analysisMode") }
    }
    @Published var codexStatus: CodexConnectionStatus?
    @Published var isCheckingCodex = false
    @Published var codexConsentPending = false
    @Published var projects: [ProjectItem] = []
    @Published var project: ProjectItem?
    @Published var analysisDraftActive = false
    @Published var jobs: [JobItem] = []
    @Published var photos: [PhotoItem] = []
    @Published var photosTotal = 0
    @Published var selectionBucket: SelectionBucket = .keep
    @Published var keptTotal = 0
    @Published var rejectedTotal = 0
    @Published var isLoadingPhotos = false
    @Published var errorMessage: String?
    @Published var isBusy = false
    @Published var operationMessage: String?
    @Published var publishPlan: PublishPlan?
    @Published var publishMessage: String?
    @Published var publishProcessed = 0
    @Published var publishTotal = 0
    @Published var tasteStatus = "Не настроен"
    @Published var tasteExamples = 0
    @Published var tasteCalibrationExamples = 0
    @Published var tasteHeldOutExamples = 0
    @Published var tasteRound: TasteRound?
    @Published var tasteSelectedIDs: Set<String> = []
    @Published var tasteRejectedIDs: Set<String> = []
    @Published var tasteEditorPresented = false
    @Published var tasteSourceAlbumID: String {
        didSet {
            UserDefaults.standard.set(tasteSourceAlbumID, forKey: "tasteSourceAlbumID")
        }
    }
    @Published var tasteRoundsCompleted = 0
    @Published var tasteRoundsTotal = 3
    @Published var tasteOnboardingComplete = false
    @Published var tasteProgressProcessed = 0
    @Published var tasteProgressTotal = 0
    @Published var tasteMessage: String?
    @Published var qualityMessage: String?
    @Published var qualitySeriesSelection: Set<String> = []
    @Published var qualityManualLabels = 0
    @Published var qualityHeldOutPairs = 0
    @Published var qualityTopKCount = 0
    @Published var qualitySeriesCount = 0
    @Published var qualityReleaseReady = false
    @Published var isTasteBusy = false
    @Published var selectedPhotoID: String?
    @Published var selectedPhotoIDs: Set<String> = []
    @Published var detailPhoto: PhotoItem?
    @Published var photoAccessNeedsAction = false
    @Published var hasCompletedOnboarding: Bool
    @Published var currentStep: WorkflowStep = .album

    let worker = NativeWorkerClient()
    let developerToolsEnabled =
        ProcessInfo.processInfo.environment["PHOTO_CURATOR_DEVELOPER_TOOLS"] == "1"
    private var stageOrder: [String] {
        let mode = project?.analysisMode ?? analysisMode
        return mode == "codex"
            ? ["inventory", "previews", "metrics", "duplicates", "vision", "codex", "decisions"]
            : ["inventory", "previews", "metrics", "duplicates", "vision", "decisions"]
    }
    private var pollTask: Task<Void, Never>?
    private var permissionHelpTask: Task<Void, Never>?
    private var decisionHistory: [DecisionUndo] = []
    private var binaryProjects: Set<String> = []
    private var hasStarted = false
    private let galleryPageSize = 48
    private let retainedProjectDefaultsKey = "retainedProjectID"
    private let currentDecisionModelVersion = 3

    init() {
        selectedAlbumID = UserDefaults.standard.string(forKey: "selectedAlbumID") ?? ""
        let savedDensity = UserDefaults.standard.string(forKey: "selectionDensity") ?? "balanced"
        density = ["compact", "balanced", "broad"].contains(savedDensity)
            ? savedDensity : "balanced"
        let savedMode = UserDefaults.standard.string(forKey: "analysisMode") ?? "local"
        analysisMode = ["local", "codex"].contains(savedMode) ? savedMode : "local"
        tasteSourceAlbumID =
            UserDefaults.standard.string(forKey: "tasteSourceAlbumID") ?? ""
        hasCompletedOnboarding = UserDefaults.standard.bool(forKey: "didCompleteOnboardingV1")
    }

    var progress: Double {
        if project?.state == "ready" { return 1 }
        let latest = Dictionary(jobs.map { ($0.stage, $0) }, uniquingKeysWith: { _, new in new })
        let completed = stageOrder.reduce(0.0) { result, stage in
            result + (latest[stage]?.progress ?? 0)
        }
        return completed / Double(stageOrder.count)
    }

    var activeJob: JobItem? {
        jobs.last(where: { $0.status == "running" })
    }

    var progressDetail: String {
        guard let activeJob else {
            return project?.state == "ready" ? "Все 6 этапов завершены" : ""
        }
        let stage = (stageOrder.firstIndex(of: activeJob.stage) ?? 0) + 1
        guard activeJob.total > 0 else { return "Этап \(stage) из \(stageOrder.count)" }
        return "Этап \(stage) из \(stageOrder.count) · \(activeJob.processed) из \(activeJob.total)"
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
            return active.message.isEmpty ? stageTitle(active.stage) : active.message
        }
        if project?.state == "interrupted" { return "Анализ остановлен — его можно продолжить" }
        if project?.state == "ready" { return "Анализ завершён — подборка готова к проверке" }
        return "Подготовка анализа"
    }

    func canOpen(_ step: WorkflowStep) -> Bool {
        switch step {
        case .album:
            return project?.state != "running"
        case .analysis:
            return project != nil && !analysisDraftActive
        case .selection:
            return project?.state == "ready" && !analysisDraftActive
        }
    }

    func open(_ step: WorkflowStep) {
        guard canOpen(step) else { return }
        currentStep = step
    }

    func openTasteEditor() {
        guard project?.state != "running" else {
            errorMessage = "Настройку вкуса можно открыть после завершения или остановки анализа"
            return
        }
        tasteEditorPresented = true
    }

    func start() {
        guard hasCompletedOnboarding, !hasStarted else {
            if !hasCompletedOnboarding { workerStatus = "Готов к первому запуску" }
            return
        }
        hasStarted = true
        Task {
            if ProcessInfo.processInfo.environment["PHOTO_CURATOR_NATIVE_DEMO"] != "1" {
                guard await requestPhotoLibraryAccess() else { return }
            }
            await bootstrap()
        }
    }

    func completeOnboarding() {
        hasCompletedOnboarding = true
        UserDefaults.standard.set(true, forKey: "didCompleteOnboardingV1")
        start()
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
                tasteRound = nil
                tasteSelectedIDs = []
                tasteRejectedIDs = []
                tasteRoundsCompleted = 0
                tasteOnboardingComplete = false
                tasteMessage = "Профиль удалён. Настройте вкус заново перед новым анализом."
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
        hasStarted = false
        workerStatus = "Перезапуск…"
        start()
    }

    func createAndAnalyze() {
        if !analysisDraftActive, let project, project.state == "created" {
            startExistingProject(project)
            return
        }
        guard tasteOnboardingComplete else {
            tasteMessage = "Сначала завершите три раунда настройки вкуса."
            openTasteEditor()
            return
        }
        guard !selectedAlbumID.isEmpty else {
            errorMessage = "Сначала выберите альбом"
            return
        }
        isBusy = true
        operationMessage = "Создаём локальный проект…"
        errorMessage = nil
        photos = []
        selectionBucket = .keep
        keptTotal = 0
        rejectedTotal = 0
        binaryProjects.removeAll()
        selectedPhotoID = nil
        decisionHistory = []
        tasteMessage = nil
        qualitySeriesSelection.removeAll()
        qualityMessage = nil
        qualityManualLabels = 0
        qualityHeldOutPairs = 0
        qualityTopKCount = 0
        qualitySeriesCount = 0
        qualityReleaseReady = false
        publishPlan = nil
        currentStep = .analysis
        UserDefaults.standard.removeObject(forKey: retainedProjectDefaultsKey)
        Task {
            do {
                let created = try await call("create_project", [
                    "album_id": selectedAlbumID,
                    "selection_density": density,
                    "analysis_mode": analysisMode,
                ])
                guard let value = created as? [String: Any], let project = ProjectItem(value) else {
                    throw NativeWorkerClientError.invalidResponse
                }
                self.project = project
                analysisDraftActive = false
                updateProjectInList(project)
                UserDefaults.standard.set(project.id, forKey: retainedProjectDefaultsKey)
                operationMessage = "Запускаем анализ…"
                _ = try await call("start_analysis", ["project_id": project.id])
                isBusy = false
                operationMessage = nil
                startPolling(projectID: project.id)
            } catch {
                isBusy = false
                operationMessage = nil
                errorMessage = error.localizedDescription
            }
        }
    }

    func requestAnalysis() {
        if analysisMode == "codex" {
            guard codexStatus?.ready == true else {
                errorMessage = "Codex не подключён через ChatGPT. Проверьте подключение перед запуском."
                refreshCodexStatus()
                return
            }
            codexConsentPending = true
        } else {
            createAndAnalyze()
        }
    }

    func refreshCodexStatus() {
        guard !isCheckingCodex else { return }
        isCheckingCodex = true
        Task {
            defer { isCheckingCodex = false }
            do {
                let result = try await call("codex_status")
                guard let value = result as? [String: Any],
                      let status = CodexConnectionStatus(value)
                else { throw NativeWorkerClientError.invalidResponse }
                codexStatus = status
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func openCodexForSignIn() {
        let candidates = [
            URL(fileURLWithPath: "/Applications/ChatGPT.app"),
            URL(fileURLWithPath: "/Applications/Codex.app"),
        ]
        if let app = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) {
            NSWorkspace.shared.openApplication(
                at: app,
                configuration: NSWorkspace.OpenConfiguration()
            )
        } else if let url = URL(string: "https://chatgpt.com/download") {
            NSWorkspace.shared.open(url)
        }
    }

    private func startExistingProject(_ project: ProjectItem) {
        isBusy = true
        operationMessage = "Запускаем анализ…"
        currentStep = .analysis
        Task {
            do {
                _ = try await call("start_analysis", ["project_id": project.id])
                isBusy = false
                operationMessage = nil
                startPolling(projectID: project.id)
            } catch {
                isBusy = false
                operationMessage = nil
                errorMessage = error.localizedDescription
            }
        }
    }

    func resumeAnalysis() {
        guard let project else { return }
        isBusy = true
        operationMessage = "Возобновляем анализ…"
        errorMessage = nil
        Task {
            do {
                _ = try await call("resume_analysis", ["project_id": project.id])
                isBusy = false
                operationMessage = nil
                try await Task.sleep(nanoseconds: 200_000_000)
                startPolling(projectID: project.id)
            } catch {
                isBusy = false
                operationMessage = nil
                errorMessage = error.localizedDescription
            }
        }
    }

    func cancelAnalysis() {
        guard let project else { return }
        operationMessage = "Останавливаем после текущей безопасной операции…"
        Task {
            do {
                _ = try await call("cancel_analysis", ["project_id": project.id])
            } catch {
                operationMessage = nil
                errorMessage = error.localizedDescription
            }
        }
    }

    func setDecision(photoID: String, disposition: String?, recordUndo: Bool = true) {
        guard let project else { return }
        guard let initialIndex = photos.firstIndex(where: { $0.id == photoID }) else { return }
        let previousPhoto = photos[initialIndex]
        let previousManual = previousPhoto.manualDisposition
        selectedPhotoID = photoID
        selectedPhotoIDs.remove(photoID)
        if let disposition {
            adjustDecisionCounts(from: previousPhoto.disposition, to: disposition)
            photos[initialIndex].disposition = disposition
            photos[initialIndex].manualDisposition = disposition
            if disposition != selectionBucket.rawValue {
                photos.remove(at: initialIndex)
                photosTotal = max(0, photosTotal - 1)
                selectedPhotoID = photos.indices.contains(initialIndex)
                    ? photos[initialIndex].id : photos.last?.id
            }
        }
        Task {
            do {
                var params: [String: Any] = ["project_id": project.id, "asset_uuid": photoID]
                params["disposition"] = disposition ?? NSNull()
                let result = try await call("decision", params)
                if let value = result as? [String: Any],
                   let updated = PhotoItem(value)
                {
                    if let index = photos.firstIndex(where: { $0.id == photoID }) {
                        photos[index] = updated
                    }
                    if recordUndo && previousManual != disposition {
                        decisionHistory.append(
                            DecisionUndo(
                                photoID: photoID,
                                previousManual: previousManual,
                                previousFinal: previousPhoto.disposition,
                                changedTo: disposition
                            )
                        )
                    }
                }
                await loadQualityStatus(projectID: project.id)
            } catch {
                if let disposition {
                    adjustDecisionCounts(from: disposition, to: previousPhoto.disposition)
                }
                if let index = photos.firstIndex(where: { $0.id == photoID }) {
                    photos[index] = previousPhoto
                } else {
                    photos.insert(previousPhoto, at: min(initialIndex, photos.count))
                    photosTotal += 1
                }
                errorMessage = error.localizedDescription
            }
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
        guard let change = decisionHistory.popLast(), let project else { return }
        Task {
            do {
                let result = try await call("decision", [
                    "project_id": project.id,
                    "asset_uuid": change.photoID,
                    "disposition": change.previousManual ?? NSNull(),
                ])
                guard let value = result as? [String: Any], let restored = PhotoItem(value) else {
                    throw NativeWorkerClientError.invalidResponse
                }
                adjustDecisionCounts(
                    from: change.changedTo,
                    to: restored.disposition ?? change.previousFinal
                )
                await loadPhotos(projectID: project.id)
                selectedPhotoID = photos.contains(where: { $0.id == restored.id })
                    ? restored.id : photos.first?.id
            } catch {
                decisionHistory.append(change)
                errorMessage = error.localizedDescription
            }
        }
    }

    func previewSelected() {
        guard let selectedPhotoID,
              let path = photos.first(where: { $0.id == selectedPhotoID })?.imagePath
        else { return }
        QuickLookController.shared.show(path: path)
    }

    func openPhotoDetails(photoID: String) {
        selectedPhotoID = photoID
        detailPhoto = photos.first(where: { $0.id == photoID })
    }

    func togglePhotoSelection(photoID: String) {
        selectedPhotoID = photoID
        if selectedPhotoIDs.contains(photoID) {
            selectedPhotoIDs.remove(photoID)
        } else {
            selectedPhotoIDs.insert(photoID)
        }
    }

    func clearPhotoSelection() {
        selectedPhotoIDs.removeAll()
    }

    func setSelectedPhotosDecision(_ disposition: String) {
        guard let project, !selectedPhotoIDs.isEmpty else { return }
        let ids = Array(selectedPhotoIDs)
        isBusy = true
        Task {
            defer { isBusy = false }
            do {
                let result = try await call("decisions_batch", [
                    "project_id": project.id,
                    "asset_uuids": ids,
                    "disposition": disposition,
                ])
                if let value = result as? [String: Any] {
                    applyProjectSummary(value["summary"] as? [String: Any])
                }
                selectedPhotoIDs.removeAll()
                await loadPhotos(projectID: project.id)
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func prepareTasteRound() {
        guard !tasteSourceAlbumID.isEmpty, tasteRound == nil else { return }
        isTasteBusy = true
        tasteProgressProcessed = 0
        tasteProgressTotal = 10
        tasteMessage = nil
        Task {
            defer { isTasteBusy = false }
            do {
                let result = try await call(
                    "taste_round_prepare",
                    [
                        "album_id": tasteSourceAlbumID,
                        "mode": tasteOnboardingComplete ? "adjust" : "onboarding",
                    ]
                ) { [weak self] event in
                    guard event["kind"] as? String == "taste_progress" else { return }
                    let processed = event["processed"] as? Int ?? 0
                    let total = event["total"] as? Int ?? 10
                    Task { @MainActor [weak self] in
                        self?.tasteProgressProcessed = processed
                        self?.tasteProgressTotal = total
                    }
                }
                guard let value = result as? [String: Any] else {
                    throw NativeWorkerClientError.invalidResponse
                }
                if let profile = value["profile"] as? [String: Any] {
                    applyTasteProfile(profile)
                }
                tasteRound = (value["round"] as? [String: Any]).flatMap(TasteRound.init)
                tasteSelectedIDs = []
                tasteRejectedIDs = []
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func toggleTasteSelection(_ photoID: String) {
        guard let round = tasteRound else { return }
        if tasteSelectedIDs.contains(photoID) {
            tasteSelectedIDs.remove(photoID)
        } else if tasteSelectedIDs.count < round.selectionLimit {
            tasteRejectedIDs.remove(photoID)
            tasteSelectedIDs.insert(photoID)
        }
    }

    func toggleTasteRejection(_ photoID: String) {
        guard let round = tasteRound else { return }
        if tasteRejectedIDs.contains(photoID) {
            tasteRejectedIDs.remove(photoID)
        } else if tasteRejectedIDs.count < round.rejectionLimit {
            tasteSelectedIDs.remove(photoID)
            tasteRejectedIDs.insert(photoID)
        }
    }

    func cancelTasteRound() {
        guard tasteRound != nil, !isTasteBusy else { return }
        isTasteBusy = true
        Task {
            defer { isTasteBusy = false }
            do {
                let result = try await call("taste_round_cancel")
                if let value = result as? [String: Any],
                   let profile = value["profile"] as? [String: Any]
                {
                    applyTasteProfile(profile)
                }
                tasteRound = nil
                tasteSelectedIDs = []
                tasteRejectedIDs = []
                tasteMessage = "Раунд отменён. Выберите другой альбом."
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func submitTasteRound() {
        guard let round = tasteRound,
              tasteSelectedIDs.count == round.selectionLimit,
              tasteRejectedIDs.count == round.rejectionLimit
        else {
            return
        }
        isTasteBusy = true
        tasteMessage = nil
        let selected = Array(tasteSelectedIDs).sorted()
        let rejected = Array(tasteRejectedIDs).sorted()
        Task {
            defer { isTasteBusy = false }
            do {
                let result = try await call("taste_round_submit", [
                    "round_id": round.id,
                    "selected_uuids": selected,
                    "rejected_uuids": rejected,
                ])
                guard let value = result as? [String: Any],
                      let profile = value["profile"] as? [String: Any]
                else { throw NativeWorkerClientError.invalidResponse }
                applyTasteProfile(profile)
                tasteRound = nil
                tasteSelectedIDs = []
                tasteRejectedIDs = []
                tasteMessage = tasteOnboardingComplete
                    ? "Профиль обновлён: учтены и любимые, и не нравящиеся кадры."
                    : "Раунд сохранён. Продолжите настройку ещё в двух раундах."
                await refreshDecisionsForTaste()
                if tasteOnboardingComplete && analysisDraftActive {
                    tasteEditorPresented = false
                    currentStep = .album
                }
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func preparePublish() {
        guard let project else { return }
        isBusy = true
        publishProcessed = 0
        publishTotal = 0
        operationMessage = "Проверяем выбранные фото и готовим независимые копии…"
        Task {
            defer {
                isBusy = false
                operationMessage = nil
            }
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
        publishProcessed = 0
        publishTotal = publishPlan.itemCount
        operationMessage = "Создаём \(publishPlan.itemCount) независимых копий в Photos…"
        Task {
            defer {
                isBusy = false
                operationMessage = nil
            }
            do {
                let result = try await call("publish_apply", [
                    "publish_id": publishPlan.id,
                    "confirmed": true,
                ]) { [weak self] event in
                    guard event["kind"] as? String == "publish_progress" else { return }
                    let processed = event["processed"] as? Int ?? 0
                    let total = event["total"] as? Int ?? publishPlan.itemCount
                    let phase = event["phase"] as? String ?? "prepare"
                    Task { @MainActor [weak self] in
                        self?.publishProcessed = processed
                        self?.publishTotal = total
                        self?.operationMessage = phase == "commit"
                            ? "PhotoKit сохраняет альбом…"
                            : "Подготовлено \(processed) из \(total) фото…"
                    }
                }
                let value = result as? [String: Any]
                publishMessage = value?["status"] as? String == "applied"
                    ? "Готово: независимые копии сохранены в новом Best‑альбоме."
                    : "Публикация завершена."
                self.publishPlan = nil
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func beginNewAnalysis() {
        guard project?.state != "running" else { return }
        analysisDraftActive = true
        publishPlan = nil
        publishMessage = nil
        if tasteOnboardingComplete {
            currentStep = .album
        } else {
            tasteMessage = "Перед первым анализом создайте профиль вкуса."
            openTasteEditor()
        }
    }

    func cancelNewAnalysis() {
        analysisDraftActive = false
        guard let project else {
            currentStep = .album
            return
        }
        Task {
            await restoreProject(project)
        }
    }

    func selectProject(_ selected: ProjectItem) {
        guard project?.id != selected.id || analysisDraftActive else { return }
        analysisDraftActive = false
        pollTask?.cancel()
        project = selected
        UserDefaults.standard.set(selected.id, forKey: retainedProjectDefaultsKey)
        if (albums + sharedAlbums).contains(where: { $0.id == selected.albumID }) {
            selectedAlbumID = selected.albumID
        }
        Task { await restoreProject(selected) }
    }

    func deleteProject(_ target: ProjectItem) {
        guard target.state != "running" else {
            errorMessage = "Сначала остановите выполняющийся анализ"
            return
        }
        isBusy = true
        Task {
            defer { isBusy = false }
            do {
                _ = try await call("delete_project", ["project_id": target.id])
                projects.removeAll { $0.id == target.id }
                if project?.id == target.id {
                    pollTask?.cancel()
                    resetProject()
                    if let next = projects.first {
                        project = next
                        UserDefaults.standard.set(next.id, forKey: retainedProjectDefaultsKey)
                        await restoreProject(next)
                    } else {
                        UserDefaults.standard.removeObject(forKey: retainedProjectDefaultsKey)
                        analysisDraftActive = true
                        currentStep = .album
                    }
                }
            } catch {
                errorMessage = error.localizedDescription
            }
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
            let retainedProjectID = UserDefaults.standard.string(
                forKey: retainedProjectDefaultsKey
            )
            let rawProjects = try await call("projects")
            if let groups = rawAlbums as? [String: Any] {
                albums = (groups["regular"] as? [[String: Any]] ?? []).compactMap(AlbumItem.init)
                sharedAlbums = (groups["shared"] as? [[String: Any]] ?? []).compactMap(AlbumItem.init)
                let availableIDs = Set((albums + sharedAlbums).map(\.id))
                if !availableIDs.contains(selectedAlbumID) {
                    selectedAlbumID = albums.first?.id ?? sharedAlbums.first?.id ?? ""
                }
                if !availableIDs.contains(tasteSourceAlbumID) {
                    tasteSourceAlbumID = (albums + sharedAlbums)
                        .filter { $0.photoCount >= 10 }
                        .max(by: { $0.photoCount < $1.photoCount })?.id ?? ""
                }
            }
            if let taste = rawTaste as? [String: Any] { applyTasteProfile(taste) }
            projects = (rawProjects as? [[String: Any]] ?? []).compactMap(ProjectItem.init)
            if analysisMode == "codex" { refreshCodexStatus() }
            let restorable = projects.filter {
                ["created", "ready", "running", "interrupted", "error"].contains($0.state)
            }
            if let restored =
                restorable.first(where: { $0.id == retainedProjectID }) ?? restorable.first
            {
                project = restored
                if (albums + sharedAlbums).contains(where: { $0.id == restored.albumID }) {
                    selectedAlbumID = restored.albumID
                }
                await restoreProject(restored)
            } else {
                UserDefaults.standard.removeObject(forKey: retainedProjectDefaultsKey)
                analysisDraftActive = true
                currentStep = .album
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
                    updateProjectInList(updated)
                    jobs = (value["jobs"] as? [[String: Any]] ?? []).compactMap(JobItem.init)
                    applyProjectSummary(value["summary"] as? [String: Any])
                    if updated.state == "ready" {
                        operationMessage = nil
                        await loadPhotos(projectID: projectID)
                        return
                    }
                    if updated.state == "error" {
                        operationMessage = nil
                        errorMessage = "Анализ завершился с ошибкой. Детали сохранены в локальном журнале."
                        return
                    }
                    if updated.state == "interrupted" {
                        operationMessage = nil
                        return
                    }
                    try await Task.sleep(nanoseconds: 1_000_000_000)
                } catch is CancellationError {
                    return
                } catch {
                    operationMessage = nil
                    errorMessage = error.localizedDescription
                    return
                }
            }
        }
    }

    private func loadPhotos(projectID: String, append: Bool = false) async {
        guard !isLoadingPhotos else { return }
        isLoadingPhotos = true
        defer { isLoadingPhotos = false }
        do {
            if !binaryProjects.contains(projectID) {
                let normalized = try await call("binary_decisions", ["project_id": projectID])
                if let value = normalized as? [String: Any] {
                    applyProjectSummary(value["summary"] as? [String: Any])
                }
                binaryProjects.insert(projectID)
            }
            let offset = append ? photos.count : 0
            let result = try await call("assets", [
                "project_id": projectID,
                "disposition": selectionBucket.rawValue,
                "offset": offset,
                "limit": galleryPageSize,
            ])
            let value = result as? [String: Any]
            let page = (value?["items"] as? [[String: Any]] ?? []).compactMap(PhotoItem.init)
            photosTotal = value?["total"] as? Int ?? page.count
            if append {
                let known = Set(photos.map(\.id))
                photos.append(contentsOf: page.filter { !known.contains($0.id) })
            } else {
                photos = page
            }
            if !photos.contains(where: { $0.id == selectedPhotoID }) {
                selectedPhotoID = photos.first?.id
            }
            if !append {
                UserDefaults.standard.set(projectID, forKey: retainedProjectDefaultsKey)
                selectedPhotoIDs.removeAll()
                currentStep = .selection
                await loadQualityStatus(projectID: projectID)
            }
        } catch { errorMessage = error.localizedDescription }
    }

    func loadMorePhotos() {
        guard let project, photos.count < photosTotal, !isLoadingPhotos else { return }
        Task { await loadPhotos(projectID: project.id, append: true) }
    }

    func showSelection(_ bucket: SelectionBucket) {
        guard selectionBucket != bucket, let project else { return }
        selectionBucket = bucket
        photos = []
        photosTotal = bucket == .keep ? keptTotal : rejectedTotal
        selectedPhotoID = nil
        selectedPhotoIDs.removeAll()
        Task { await loadPhotos(projectID: project.id) }
    }

    private func restoreProject(_ restored: ProjectItem) async {
        do {
            let result = try await call("project", ["project_id": restored.id])
            guard let value = result as? [String: Any],
                  let rawProject = value["project"] as? [String: Any],
                  let updated = ProjectItem(rawProject)
            else { throw NativeWorkerClientError.invalidResponse }
            project = updated
            updateProjectInList(updated)
            jobs = (value["jobs"] as? [[String: Any]] ?? []).compactMap(JobItem.init)
            applyProjectSummary(value["summary"] as? [String: Any])
            if updated.state == "running" {
                currentStep = .analysis
                startPolling(projectID: updated.id)
            } else if updated.state == "ready" {
                if updated.decisionModelVersion < currentDecisionModelVersion {
                    operationMessage = "Обновляем критерии отбора…"
                    _ = try await call("start_analysis", [
                        "project_id": updated.id,
                        "from_stage": "decisions",
                    ])
                    currentStep = .analysis
                    try await Task.sleep(nanoseconds: 250_000_000)
                    startPolling(projectID: updated.id)
                } else {
                    await loadPhotos(projectID: updated.id)
                }
            } else {
                currentStep = .analysis
            }
        } catch { errorMessage = error.localizedDescription }
    }

    private func resetProject() {
        pollTask?.cancel()
        project = nil
        jobs = []
        photos = []
        photosTotal = 0
        selectionBucket = .keep
        keptTotal = 0
        rejectedTotal = 0
        binaryProjects.removeAll()
        selectedPhotoID = nil
        selectedPhotoIDs.removeAll()
        decisionHistory = []
        publishPlan = nil
        publishMessage = nil
        qualitySeriesSelection.removeAll()
    }

    private func updateProjectInList(_ updated: ProjectItem) {
        projects.removeAll { $0.id == updated.id }
        projects.insert(updated, at: 0)
    }

    private func applyProjectSummary(_ summary: [String: Any]?) {
        guard let summary else { return }
        keptTotal = summary["keep"] as? Int ?? keptTotal
        rejectedTotal = summary["reject"] as? Int ?? rejectedTotal
    }

    private func adjustDecisionCounts(from previous: String?, to updated: String?) {
        guard previous != updated else { return }
        switch previous {
        case "keep": keptTotal = max(0, keptTotal - 1)
        case "reject": rejectedTotal = max(0, rejectedTotal - 1)
        default: break
        }
        switch updated {
        case "keep": keptTotal += 1
        case "reject": rejectedTotal += 1
        default: break
        }
    }

    private func applyTasteProfile(_ value: [String: Any]) {
        tasteExamples = value["preference_count"] as? Int ?? tasteExamples
        tasteCalibrationExamples = value["calibration_count"] as? Int ?? tasteCalibrationExamples
        tasteHeldOutExamples = value["held_out_count"] as? Int ?? tasteHeldOutExamples
        tasteRoundsCompleted =
            value["onboarding_rounds_completed"] as? Int ?? tasteRoundsCompleted
        tasteRoundsTotal = value["onboarding_rounds_total"] as? Int ?? tasteRoundsTotal
        tasteOnboardingComplete =
            value["onboarding_complete"] as? Bool ?? tasteOnboardingComplete
        tasteStatus = value["status"] as? String ?? "Не настроен"
    }

    private func loadQualityStatus(projectID: String) async {
        do {
            let result = try await call("quality_status", ["project_id": projectID])
            guard let value = result as? [String: Any] else {
                throw NativeWorkerClientError.invalidResponse
            }
            qualityManualLabels = value["manual_labels"] as? Int ?? 0
            qualityHeldOutPairs = value["held_out_pairs"] as? Int ?? 0
            qualityTopKCount = value["expected_top_k"] as? Int ?? 0
            qualitySeriesCount = value["human_duplicate_groups"] as? Int ?? 0
            qualityReleaseReady = value["release_ready"] as? Bool ?? false
        } catch { errorMessage = error.localizedDescription }
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

    private func call(
        _ method: String,
        _ params: [String: Any] = [:],
        progress: (([String: Any]) -> Void)? = nil
    ) async throws -> Any {
        let worker = worker
        return try await Task.detached(priority: .userInitiated) {
            try worker.request(method: method, params: params, progress: progress)
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

    func stageTitle(_ stage: String) -> String {
        [
            "inventory": "Читаем альбом",
            "previews": "Готовим изображения",
            "metrics": "Проверяем качество",
            "duplicates": "Сравниваем серии",
            "vision": "Apple Vision оценивает кадры",
            "codex": "Codex понимает сюжет и сравнивает серии",
            "decisions": "Формируем подборку",
        ][stage] ?? "Анализируем"
    }
}

private struct DecisionUndo {
    let photoID: String
    let previousManual: String?
    let previousFinal: String?
    let changedTo: String?
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
            PhotoCuratorHelpCommands()
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
                Button("Отнести к хорошим") { model.decideSelected("keep") }
                    .keyboardShortcut("1", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Button("Отнести к плохим") { model.decideSelected("reject") }
                    .keyboardShortcut("2", modifiers: [])
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

        Window("Справка Photo Curator", id: "photo-curator-help") {
            HelpCenterView()
        }
        .defaultSize(width: 1060, height: 780)
    }
}

private struct PhotoCuratorHelpCommands: Commands {
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandGroup(replacing: .help) {
            Button("Справка Photo Curator") {
                openWindow(id: "photo-curator-help")
            }
            .keyboardShortcut("?", modifiers: .command)
        }
    }
}

private struct HelpCenterView: View {
    @State private var section: HelpSection? = .overview

    var body: some View {
        NavigationSplitView {
            List(HelpSection.allCases, selection: $section) { item in
                Label(item.title, systemImage: item.symbol)
                    .tag(item)
            }
            .navigationTitle("Справка")
            .frame(minWidth: 210)
        } detail: {
            Group {
                switch section ?? .overview {
                case .overview:
                    HelpOverviewView()
                case .howItWorks:
                    HowItWorksHelpView()
                }
            }
            .navigationTitle(section?.title ?? HelpSection.overview.title)
        }
    }
}

private struct HelpOverviewView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Photo Curator")
                    .font(.largeTitle.bold())
                Text("Локальный помощник для отбора фотографий в Apple Photos.")
                    .font(.title3)
                    .foregroundStyle(.secondary)

                GroupBox("Что делает приложение") {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("Анализирует выбранный альбом целиком", systemImage: "photo.on.rectangle.angled")
                        Label("Собирает две подборки: хорошие и плохие кадры", systemImage: "rectangle.3.group")
                        Label("Позволяет исправить каждую рекомендацию вручную", systemImage: "hand.tap")
                        Label("Создаёт Best-альбом только после вашего подтверждения", systemImage: "checkmark.seal")
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }

                GroupBox("Ваши данные") {
                    Text("Фото, превью, оценки и профиль вкуса остаются на этом Mac. Приложение не удаляет исходные фотографии и не публикует результат без явного подтверждения.")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                }

                GroupBox("Начните с раздела «Как это работает»") {
                    Text("Там показан весь путь: от безопасного чтения альбома и сравнения похожих кадров до итогового решения и создания Best-альбома.")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                }
            }
            .padding(28)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct HowItWorksHelpView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Как движок принимает решение")
                        .font(.largeTitle.bold())
                    Text("Каждая рекомендация — это объяснимое сравнение кадров внутри текущего альбома, а не универсальный вердикт о «красоте» фотографии.")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }

                GroupBox("Пайплайн анализа") {
                    AnalysisPipelineDiagram()
                        .padding(.vertical, 8)
                }

                VStack(alignment: .leading, spacing: 14) {
                    Text("Последовательность действий")
                        .font(.title2.bold())
                    ForEach(analysisHelpStages) { stage in
                        AnalysisHelpStageRow(stage: stage)
                    }
                }

                AppleVisionHelpView()

                GroupBox("Как рассчитывается Swipe Score") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Это относительная оценка для сортировки кадров именно в этом альбоме. Она не является вероятностью и не утверждает, что один кадр «объективно красивее» другого.")
                        ScoreWeightsView()
                        Text("После базовой оценки применяется локальный профиль вкуса: он может добавить или вычесть до 20 баллов. Профиль обучается только на ваших явных выборах и применяется лишь при совпадении версии Vision-признаков.")
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }

                GroupBox("Фильтры и правила отбора") {
                    VStack(alignment: .leading, spacing: 12) {
                        HelpRuleRow(
                            title: "Технические дефекты",
                            text: "Сигналы отмечают возможное размытие (нижние 10% резкости), слишком тёмный или светлый кадр (яркость ниже 0,12 или выше 0,90) и низкий контраст (нижние 8%). Они снижают оценку, но не заменяют вашу проверку."
                        )
                        HelpRuleRow(
                            title: "Дубли и серии",
                            text: "Точные копии определяются по нормализованным пикселям. Для близких кадров сопоставляются pHash, dHash, пропорции, цветовая гистограмма, пиксельная разница и время съёмки. В серии сохраняется лучший кадр; неоднозначную группу движок не выбрасывает автоматически."
                        )
                        HelpRuleRow(
                            title: "Размер подборки",
                            text: "Вы выбираете компактную, сбалансированную или широкую подборку. Цель — примерно 25%, 45% или 65% альбома, но одновременно действует минимальный порог 82, 74 или 58 баллов. Итоговый порог также адаптируется к распределению оценок в альбоме."
                        )
                        HelpRuleRow(
                            title: "Разнообразие",
                            text: "После первого отбора похожие сцены ограничиваются: не более трёх очень близких кадров в одном временном эпизоде или семантическом кластере. Изменённые, избранные и лучшие в серии кадры защищены от такого понижения."
                        )
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Почему решение можно проверить") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Для каждого кадра сохраняются использованные версии моделей, доступность сигналов, компоненты оценки и короткие причины. Для «хороших» показываются положительные основания: композиция, лучший кадр серии, соответствие вкусу или разнообразие. Для «плохих» — только релевантные причины: технический дефект, более сильный дубль, слабый сигнал или несоответствие вкусу.")
                        Text("Если превью или анализ недоступны, кадр остаётся в хороших — движок не исключает его на основании отсутствующих данных. Ваше ручное решение всегда имеет приоритет над автоматическим.")
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }

                GroupBox("Технологии в приложении") {
                    VStack(alignment: .leading, spacing: 10) {
                        HelpTechnologyRow(name: "SwiftUI и AppKit", purpose: "нативный интерфейс macOS, доступность, меню и просмотр фотографий")
                        HelpTechnologyRow(name: "PhotoKit", purpose: "безопасное чтение альбомов и подтверждённое создание Best-альбома")
                        HelpTechnologyRow(name: "Apple Vision", purpose: "эстетический сигнал, feature print, внимание и данные о лицах в изолированном Swift helper")
                        HelpTechnologyRow(name: "Core ML", purpose: "необязательное семантическое обогащение — только для одобренной и проверенной модели")
                        HelpTechnologyRow(name: "Локальный Python-движок и SQLite", purpose: "очередь этапов, кэш, версии сигналов, объяснения и возобновление после остановки")
                    }
                    .padding(.vertical, 4)
                }
            }
            .padding(28)
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct AnalysisHelpStage: Identifiable {
    let number: Int
    let title: String
    let symbol: String
    let purpose: String
    let analysis: String
    let engine: String
    let output: String

    var id: Int { number }
}

private let analysisHelpStages = [
    AnalysisHelpStage(
        number: 1,
        title: "Инвентаризация",
        symbol: "photo.stack",
        purpose: "Сначала приложение получает состав выбранного альбома, не меняя ни одного исходного файла.",
        analysis: "Тип объекта (берём только фото), неизменяемый local identifier, дата съёмки, размеры, burst-серия, избранное и наличие ручных правок.",
        engine: "PhotoKit в отдельном Swift source helper: PHAssetCollection и PHImageManager. Метаданные и прогресс записывает локальный Python-координатор.",
        output: "Зафиксированный список кадров, с которым далее работает один анализ."),
    AnalysisHelpStage(
        number: 2,
        title: "Безопасные превью",
        symbol: "rectangle.on.rectangle",
        purpose: "Движок создаёт рабочие копии разумного размера, а оригиналы остаются только в библиотеке Photos.",
        analysis: "Доступность render, тип источника, размер и время изменения; для готового review-изображения вычисляется source fingerprint.",
        engine: "PhotoKit получает render; Pillow и локальный Python pipeline создают JPEG-превью, thumbnail и кэш с ограниченной параллельностью.",
        output: "Возобновляемый кэш. При изменении источника инвалидируются только его производные результаты."),
    AnalysisHelpStage(
        number: 3,
        title: "Технические метрики",
        symbol: "scope",
        purpose: "Нормализованное изображение измеряется до эстетической оценки, чтобы выявить проблемы, а не угадывать их по сюжету.",
        analysis: "Резкость по дисперсии лапласиана, энергия границ, средняя яркость, клиппинг в тенях и светах, контраст, динамический диапазон, энтропия, цветовая гистограмма, dHash и pHash.",
        engine: "Pillow загружает и нормализует изображение; NumPy считает пиксельные метрики; собственные hash-алгоритмы готовят быстрые ключи сравнения.",
        output: "Процентильные метрики внутри текущего альбома и флаги: возможное размытие, недодержка, передержка, низкий контраст."),
    AnalysisHelpStage(
        number: 4,
        title: "Поиск дублей и серий",
        symbol: "rectangle.2.swap",
        purpose: "Похожие кадры не должны занять всю подборку, но слабое совпадение не должно автоматически исключить фотографию.",
        analysis: "Совпадение нормализованных пикселей, расстояния pHash/dHash, пропорции кадра, цветовая гистограмма, pixel MAE, burst key и время съёмки в окне до 120 секунд.",
        engine: "Локальный Python duplicate engine: сначала ограниченный поиск кандидатов по хешам/сериям/времени, затем подтверждение несколькими независимыми признаками.",
        output: "Точные копии и близкие серии, лучший кадр по избранному, правкам, default pick, разрешению и качеству; неоднозначная группа сохраняется для вас."),
    AnalysisHelpStage(
        number: 5,
        title: "Apple Vision",
        symbol: "eye",
        purpose: "Нативный helper добавляет системные visual-сигналы Apple отдельно от пользовательского интерфейса.",
        analysis: "Эстетический сигнал, feature print, области внимания, количество лиц, оба глаза у лица и максимальное качество захвата лица.",
        engine: "Swift + Apple Vision: VNCalculateImageAestheticsScoresRequest, VNGenerateImageFeaturePrintRequest, VNGenerateAttentionBasedSaliencyImageRequest, VNDetectFaceLandmarksRequest и VNDetectFaceCaptureQualityRequest.",
        output: "Версионированные локальные сигналы и длительность каждого запроса. Подробнее — в блоке Apple Vision ниже."),
    AnalysisHelpStage(
        number: 6,
        title: "Оценка и вкус",
        symbol: "heart.text.square",
        purpose: "Swipe Score сводит доступные сигналы в понятный относительный рейтинг и не выдаёт отсутствующий сигнал за ноль.",
        analysis: "Общая эстетика, сюжет, композиция и внимание, момент, лучший кадр серии, портретный сигнал, технические штрафы и личная поправка от −20 до +20.",
        engine: "Локальный Python Swipe Score engine; профиль вкуса — pairwise-linear-v1 на NumPy, обученный только на ваших явных предпочтениях и совместимый с текущей схемой feature print.",
        output: "Балл 0–100, общий балл без вкуса, личная поправка, уверенность, компоненты, причины и версии моделей."),
    AnalysisHelpStage(
        number: 7,
        title: "Отбор и разнообразие",
        symbol: "line.3.horizontal.decrease.circle",
        purpose: "Решение учитывает и качество, и выбранный вами размер будущего Best-альбома.",
        analysis: "Альбомный порог, расстояние до него, статус дубля, защита избранного и правок, близость Vision feature print и повторяемость сцены по времени.",
        engine: "Python decision engine v2 и diversity engine vision-feature-diversity-v1. Семантическая близость от 0,94 и больше трёх похожих кадров ограничивают автоматическую подборку.",
        output: "Хорошие и плохие кадры, объяснение решения, а также top 10% лучших кандидатов среди хороших."),
    AnalysisHelpStage(
        number: 8,
        title: "Ваша проверка",
        symbol: "hand.tap",
        purpose: "Автоматическая рекомендация — стартовая точка: последнее слово всегда за вами.",
        analysis: "Ваш перенос между хорошими и плохими, отмена последнего действия, выбор top-K и подтверждение лидера серии.",
        engine: "SwiftUI передаёт явное решение через JSONL в локальный native worker; SQLite хранит ручное решение отдельно от автоматического.",
        output: "Проверенная подборка. Ручное решение имеет приоритет и может стать примером для профиля вкуса."),
    AnalysisHelpStage(
        number: 9,
        title: "Публикация",
        symbol: "checkmark.seal",
        purpose: "Результат попадает в Photos только после того, как вы просмотрели план и подтвердили операцию.",
        analysis: "Количество выбранных кадров, имя назначения, source drift и другие блокеры перед применением.",
        engine: "Swift PhotoKit publish helper выполняет утверждённый план через публичный API Photos; SwiftUI показывает dry-run и диалог подтверждения.",
        output: "Созданный или обновлённый Best-альбом. Исходные фото не удаляются и автоматически не изменяются."),
]

private struct AnalysisPipelineDiagram: View {
    private let stages = [
        ("1", "Альбом", "photo.stack"),
        ("2", "Превью", "rectangle.on.rectangle"),
        ("3", "Метрики", "scope"),
        ("4", "Серии", "rectangle.2.swap"),
        ("5", "Vision", "eye"),
        ("6", "Оценка", "chart.bar"),
        ("7", "Отбор", "line.3.horizontal.decrease.circle"),
        ("8", "Проверка", "hand.tap"),
        ("9", "Best-альбом", "checkmark.seal"),
    ]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .center, spacing: 8) {
                ForEach(Array(stages.enumerated()), id: \.offset) { index, stage in
                    VStack(spacing: 6) {
                        Image(systemName: stage.2)
                            .font(.title3)
                        Text(stage.0)
                            .font(.caption.monospacedDigit().weight(.semibold))
                        Text(stage.1)
                            .font(.caption)
                            .multilineTextAlignment(.center)
                            .frame(width: 82)
                    }
                    .padding(10)
                    .background(.quaternary.opacity(0.55), in: RoundedRectangle(cornerRadius: 10))
                    if index < stages.count - 1 {
                        Image(systemName: "arrow.right")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.horizontal, 2)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Пайплайн: альбом, превью, метрики, серии, Apple Vision, оценка, отбор, ваша проверка, Best-альбом")
    }
}

private struct AnalysisHelpStageRow: View {
    let stage: AnalysisHelpStage

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Text("\(stage.number)")
                .font(.headline.monospacedDigit())
                .foregroundStyle(.white)
                .frame(width: 28, height: 28)
                .background(Color.accentColor, in: Circle())
            VStack(alignment: .leading, spacing: 5) {
                Label(stage.title, systemImage: stage.symbol)
                    .font(.headline)
                Text(stage.purpose)
                HelpStageFact(icon: "viewfinder", title: "Что анализируем", text: stage.analysis)
                HelpStageFact(icon: "cpu", title: "Движок и фреймворк", text: stage.engine)
                HelpStageFact(icon: "arrow.down.doc", title: "Результат", text: stage.output)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.quaternary.opacity(0.32), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct HelpStageFact: View {
    let icon: String
    let title: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 7) {
            Image(systemName: icon)
                .foregroundStyle(.secondary)
                .frame(width: 16)
            Text(title + ": ")
                .fontWeight(.semibold)
            + Text(text)
        }
        .font(.subheadline)
    }
}

private struct AppleVisionHelpView: View {
    var body: some View {
        GroupBox("Apple Vision: какие сигналы даёт система") {
            VStack(alignment: .leading, spacing: 14) {
                Text("Все Vision-запросы выполняются локально в изолированном Swift helper. Он получает только путь к рабочему review-изображению, возвращает структурированный результат и не отправляет фото во внешние сервисы.")

                VisionSignalRow(
                    request: "VNCalculateImageAestheticsScoresRequest",
                    title: "Общая эстетика",
                    details: "На macOS 15+ возвращает overall score в диапазоне от −1 до +1 и флаг utility. Балл переводится в шкалу 0–100 и является главным общим сигналом Swipe Score. Utility-кадры получают дополнительный штраф, чтобы, например, служебный снимок не побеждал выразительный кадр."
                )
                VisionSignalRow(
                    request: "VNGenerateImageFeaturePrintRequest",
                    title: "Feature print",
                    details: "Создаёт Float32-вектор с ревизией запроса. Он нужен для семантического сравнения сцен, ограничения похожих кадров и персонального профиля вкуса; при смене схемы старый профиль не применяется."
                )
                VisionSignalRow(
                    request: "VNGenerateAttentionBasedSaliencyImageRequest",
                    title: "Внимание и композиция",
                    details: "Возвращает карту внимания, размеры heatmap и прямоугольники заметных объектов. Количество выделенных объектов уточняет компонент композиции и внимания, но не распознаёт «правильный» сюжет."
                )
                VisionSignalRow(
                    request: "VNDetectFaceLandmarksRequest + VNDetectFaceCaptureQualityRequest",
                    title: "Лица и качество портрета",
                    details: "Определяются количество лиц, наличие обоих глаз и максимальное face capture quality. Портретный сигнал используется только если лицо найдено; отсутствие лица не считается недостатком фотографии."
                )

                Divider()
                VStack(alignment: .leading, spacing: 5) {
                    Text("Если Vision-сигнал недоступен")
                        .font(.headline)
                    Text("Для каждого запроса отдельно сохраняется статус ready, unavailable или error, версия и причина. Отсутствующий компонент не приравнивается к нулю: уменьшается уверенность, а кадр с недоступным превью или ошибкой анализа сохраняется в хороших до вашей проверки.")
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }
}

private struct VisionSignalRow: View {
    let request: String
    let title: String
    let details: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(request)
                .font(.subheadline.monospaced().weight(.semibold))
            Text(title)
                .font(.headline)
            Text(details)
                .foregroundStyle(.secondary)
        }
        .padding(12)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
    }
}

private struct ScoreWeightsView: View {
    private let weights = [
        ("Общая эстетика", "45%"),
        ("Смысл и привлекательность сюжета", "20%"),
        ("Композиция и внимание", "15%"),
        ("Момент и объект", "10%"),
        ("Лучший кадр серии", "10%"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(weights, id: \.0) { item in
                HStack(spacing: 10) {
                    Text(item.0)
                    Spacer()
                    Text(item.1)
                        .font(.subheadline.monospacedDigit().weight(.semibold))
                        .foregroundStyle(.secondary)
                }
            }
            Text("Если для портрета доступна оценка качества захвата лица, она добавляется как дополнительный сигнал. Технические проблемы и более слабые дубли вычитаются после объединения положительных сигналов.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .padding(.top, 2)
        }
        .padding(12)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
    }
}

private struct HelpRuleRow: View {
    let title: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.headline)
            Text(text)
        }
    }
}

private struct HelpTechnologyRow: View {
    let name: String
    let purpose: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(name).fontWeight(.semibold)
            Text("— \(purpose)")
                .foregroundStyle(.secondary)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var analysisDetailsExpanded = true
    @State private var confirmsAnalysisStop = false
    @State private var projectPendingDeletion: ProjectItem?

    var body: some View {
        Group {
            if model.hasCompletedOnboarding {
                workflow
            } else {
                OnboardingView { model.completeOnboarding() }
            }
        }
        .alert("Photo Curator", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
        .confirmationDialog(
            "Разрешить анализ через Codex?",
            isPresented: $model.codexConsentPending,
            titleVisibility: .visible
        ) {
            Button("Передать review‑копии в Codex и начать") {
                model.codexConsentPending = false
                model.createAndAnalyze()
            }
            Button("Отмена", role: .cancel) { model.codexConsentPending = false }
        } message: {
            Text(
                "Review‑копии фотографий будут отправлены в OpenAI через ваш локальный Codex. Прогон расходует лимиты подписки ChatGPT/Codex и может занять несколько часов. API key не используется. Исходный альбом Photos не изменяется."
            )
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
            Text(
                "В Photos будут созданы независимые копии. Их удаление из Best‑альбома не затронет фотографии исходного альбома."
            )
        }
        .confirmationDialog(
            "Остановить анализ?",
            isPresented: $confirmsAnalysisStop,
            titleVisibility: .visible
        ) {
            Button("Остановить и сохранить прогресс") {
                model.cancelAnalysis()
            }
            Button("Продолжить анализ", role: .cancel) {}
        } message: {
            Text("Завершённые этапы и подготовленные изображения останутся в локальном кэше. Анализ можно будет продолжить без повторной работы.")
        }
        .confirmationDialog(
            "Удалить анализ?",
            isPresented: Binding(
                get: { projectPendingDeletion != nil },
                set: { if !$0 { projectPendingDeletion = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Удалить анализ и его локальный кэш", role: .destructive) {
                if let target = projectPendingDeletion {
                    model.deleteProject(target)
                }
                projectPendingDeletion = nil
            }
            Button("Отмена", role: .cancel) { projectPendingDeletion = nil }
        } message: {
            Text(
                "Исходный альбом Photos не изменится. Будут удалены только результаты этого анализа и его локальный кэш."
            )
        }
        .sheet(item: $model.detailPhoto) { photo in
            PhotoDetailView(photo: photo)
        }
        .sheet(isPresented: $model.tasteEditorPresented) {
            TasteProfileEditorView()
                .environmentObject(model)
        }
    }

    private var workflow: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 16) {
                Label("Photo Curator", systemImage: "camera.aperture")
                    .font(.title2.bold())
                Divider()
                Text("АНАЛИЗЫ")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.tertiary)
                ScrollView {
                    LazyVStack(spacing: 6) {
                        ForEach(model.projects) { project in
                            ProjectSidebarRow(
                                project: project,
                                active: model.project?.id == project.id
                                    && !model.analysisDraftActive,
                                deleteDisabled: project.state == "running" || model.isBusy,
                                select: { model.selectProject(project) },
                                delete: { projectPendingDeletion = project }
                            )
                        }
                    }
                }
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
            VStack(spacing: 0) {
                workflowTabs
                Divider()
                currentStepContent
            }
            .background(Color(nsColor: .windowBackgroundColor))
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    model.beginNewAnalysis()
                } label: {
                    Label("Новый анализ", systemImage: "plus")
                }
                .disabled(model.isBusy || model.analysisDraftActive || model.project?.state == "running")

                Button {
                    model.openTasteEditor()
                } label: {
                    Label("Настроить вкус", systemImage: "heart.text.square")
                }
                .disabled(model.project?.state == "running")
                .help("Создать или изменить персональный профиль вкуса")
            }
        }
    }

    private var workflowTabs: some View {
        HStack(spacing: 4) {
            ForEach(WorkflowStep.allCases) { step in
                Button {
                    model.open(step)
                } label: {
                    VStack(spacing: 6) {
                        Label(step.title, systemImage: step.symbol)
                            .font(.subheadline.weight(
                                model.currentStep == step ? .semibold : .regular
                            ))
                        Capsule()
                            .fill(model.currentStep == step ? Color.accentColor : .clear)
                            .frame(height: 3)
                    }
                    .padding(.horizontal, 12)
                    .padding(.top, 10)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(
                    model.currentStep == step
                        ? Color.primary
                        : model.canOpen(step) ? Color.secondary : Color.secondary.opacity(0.4)
                )
                .disabled(!model.canOpen(step))
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20)
    }

    @ViewBuilder
    private var currentStepContent: some View {
        switch model.currentStep {
        case .album:
            setupScreen(
                title: "Выберите фотографии для анализа",
                subtitle: "Теперь персональный профиль будет применён к выбранному альбому."
            ) { sourceSection }
        case .analysis:
            setupScreen(
                title: "Анализ фотографий",
                subtitle: "Photo Curator локально сравнивает качество, серии и композицию."
            ) { analysisSection }
        case .selection:
            reviewSection
        }
    }

    private func setupScreen<Content: View>(
        title: String,
        subtitle: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                screenHeader(title: title, subtitle: subtitle)
                content()
            }
            .padding(32)
            .frame(maxWidth: 1120, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .top)
        }
    }

    private func screenHeader(title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.system(size: 30, weight: .bold, design: .rounded))
            Text(subtitle)
                .font(.title3)
                .foregroundStyle(.secondary)
        }
    }

    private var densityExplanation: String {
        switch model.density {
        case "compact":
            return "Самый строгий вариант: в будущий Best‑альбом сервис предложит меньше фотографий — только самые уверенные результаты. Исходный альбом анализируется целиком."
        case "broad":
            return "Более широкий вариант: в будущий Best‑альбом сервис предложит больше хороших и пограничных кадров. Исходный альбом анализируется целиком."
        default:
            return "Рекомендуемый баланс: сервис предложит в будущий Best‑альбом лучшие кадры и оставит спорные для вашей проверки. Исходный альбом анализируется целиком."
        }
    }

    private var sourceSection: some View {
        StepCard(number: 2, title: "Выберите альбом", symbol: "photo.on.rectangle.angled") {
            if !model.tasteOnboardingComplete {
                VStack(alignment: .leading, spacing: 12) {
                    Label("Сначала создайте профиль вкуса", systemImage: "heart.text.square")
                        .font(.headline)
                    Text("В нём вы отдельно отметите любимые и не нравящиеся кадры. После трёх коротких раундов станет доступен запуск анализа.")
                        .foregroundStyle(.secondary)
                    Button("Настроить вкус") { model.openTasteEditor() }
                        .buttonStyle(.borderedProminent)
                }
            } else if model.albums.isEmpty {
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
                Text("Размер итогового Best‑альбома")
                    .font(.subheadline.weight(.semibold))
                Picker("Размер итогового Best‑альбома", selection: $model.density) {
                    Text("Компактная").tag("compact")
                    Text("Сбалансированная").tag("balanced")
                    Text("Широкая").tag("broad")
                }
                .pickerStyle(.segmented)
                Text(densityExplanation)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Divider()
                Text("Движок анализа")
                    .font(.subheadline.weight(.semibold))
                Picker("Движок анализа", selection: $model.analysisMode) {
                    Text("Локальный").tag("local")
                    Text("Codex Vision · экспериментальный").tag("codex")
                }
                .pickerStyle(.segmented)
                if model.analysisMode == "codex" {
                    VStack(alignment: .leading, spacing: 10) {
                        Label(
                            "Фото будут переданы в OpenAI и израсходуют лимиты вашей подписки Codex.",
                            systemImage: "exclamationmark.triangle.fill"
                        )
                        .foregroundStyle(.orange)
                        Text("Photo Curator автоматически найдёт Codex на этом Mac и использует только вход через ChatGPT. Авторизация API key блокируется, чтобы исключить неожиданные списания.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        HStack {
                            if model.isCheckingCodex {
                                ProgressView().controlSize(.small)
                                Text("Проверяем Codex…")
                            } else if model.codexStatus?.ready == true {
                                Label(
                                    "Codex подключён через ChatGPT",
                                    systemImage: "checkmark.circle.fill"
                                )
                                .foregroundStyle(.green)
                            } else {
                                Label(
                                    model.codexStatus?.detail ?? "Codex ещё не проверен",
                                    systemImage: "exclamationmark.circle"
                                )
                                .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Проверить") { model.refreshCodexStatus() }
                            if model.codexStatus?.ready != true {
                                Button("Открыть ChatGPT / Codex") { model.openCodexForSignIn() }
                            }
                        }
                    }
                    .padding(12)
                    .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                }
            }
            if model.tasteOnboardingComplete, !model.sharedAlbums.isEmpty {
                Label("Для общего альбома PhotoKit подготовит локальные review‑копии; источник не изменится.", systemImage: "person.2")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if model.tasteOnboardingComplete {
                HStack {
                if model.analysisDraftActive {
                    Button("Отменить новый анализ") { model.cancelNewAnalysis() }
                } else {
                    Button("Настроить вкус") { model.openTasteEditor() }
                }
                Spacer()
                Button("Начать анализ") {
                    model.requestAnalysis()
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    model.selectedAlbumID.isEmpty
                        || !model.tasteOnboardingComplete
                        || model.isBusy
                )
                }
            }
        }
        .onChange(of: model.analysisMode) { mode in
            if mode == "codex" { model.refreshCodexStatus() }
        }
    }

    private var analysisSection: some View {
        StepCard(number: 3, title: "Проанализируйте", symbol: "sparkles") {
            if let operation = model.operationMessage {
                ProgressView(operation)
            }
            if model.project?.state == "running" || !model.jobs.isEmpty {
                ProgressView(value: model.progress) {
                    Text(model.progressMessage)
                } currentValueLabel: {
                    Text("\(Int(model.progress * 100))%")
                }
                .progressViewStyle(.linear)
                if !model.progressDetail.isEmpty {
                    Text(model.progressDetail)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                DisclosureGroup("Детали этапов", isExpanded: $analysisDetailsExpanded) {
                    ForEach(model.jobs) { job in
                        HStack {
                            Image(systemName: jobStatusSymbol(job.status))
                                .foregroundStyle(jobStatusColor(job.status))
                            Text(model.stageTitle(job.stage))
                            Spacer()
                            Text(job.total > 0 ? "\(job.processed) / \(job.total)" : job.status)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if model.project?.state == "interrupted" {
                Label(
                    "Анализ остановлен. Готовые этапы и изображения сохранены в локальном кэше; продолжение не начнёт их заново.",
                    systemImage: "externaldrive.badge.checkmark"
                )
                .font(.subheadline)
                .foregroundStyle(.secondary)
                Button {
                    model.resumeAnalysis()
                } label: {
                    Label("Продолжить с прерванного этапа", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(model.isBusy)
                Button("Начать другой анализ") {
                    model.beginNewAnalysis()
                }
                .buttonStyle(.bordered)
                .disabled(model.isBusy)
            } else if model.project?.state != "ready" {
                Button {
                    model.requestAnalysis()
                } label: {
                    Label("Начать анализ", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(model.isBusy || model.selectedAlbumID.isEmpty || model.project?.state == "running")
            }
            if model.project?.state == "running" {
                Button(role: .destructive) {
                    confirmsAnalysisStop = true
                } label: {
                    Label("Остановить анализ", systemImage: "stop.fill")
                }
                .buttonStyle(.bordered)
            }
        }
    }

    private var reviewSection: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .bottom, spacing: 16) {
                    HStack(spacing: 4) {
                        ForEach(SelectionBucket.allCases) { bucket in
                            Button {
                                model.showSelection(bucket)
                            } label: {
                                SelectionBucketTabLabel(
                                    bucket: bucket,
                                    count: bucket == .keep ? model.keptTotal : model.rejectedTotal,
                                    selected: model.selectionBucket == bucket
                                )
                            }
                            .buttonStyle(.plain)
                            .disabled(model.isLoadingPhotos)
                        }
                    }
                    Spacer(minLength: 0)
                    Button {
                        model.preparePublish()
                    } label: {
                        Label(
                            model.isBusy ? "Подождите…" : "Создать Best‑альбом",
                            systemImage: "photo.badge.plus"
                        )
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.green)
                    .disabled(model.isBusy || model.keptTotal == 0)
                }

                if model.isBusy, let operation = model.operationMessage {
                    if model.publishTotal > 0 {
                        ProgressView(
                            value: Double(model.publishProcessed),
                            total: Double(model.publishTotal)
                        ) {
                            Text(operation)
                        } currentValueLabel: {
                            Text("\(model.publishProcessed) из \(model.publishTotal)")
                        }
                    } else {
                        ProgressView(operation)
                    }
                }
                if let message = model.publishMessage {
                    Label(message, systemImage: "checkmark.seal.fill")
                        .foregroundStyle(.green)
                }
                if !model.selectedPhotoIDs.isEmpty {
                    HStack(spacing: 10) {
                        Text("Выбрано: \(model.selectedPhotoIDs.count)")
                            .font(.subheadline.weight(.semibold))
                        Spacer()
                        Button("Снять выбор") { model.clearPhotoSelection() }
                        Button(
                            model.selectionBucket == .keep
                                ? "Переместить в плохие" : "Переместить в хорошие"
                        ) {
                            model.setSelectedPhotosDecision(
                                model.selectionBucket == .keep ? "reject" : "keep"
                            )
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isBusy)
                    }
                    .padding(.vertical, 6)
                }
                if model.developerToolsEnabled, !model.qualitySeriesSelection.isEmpty {
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

                LazyVGrid(
                    columns: [
                        GridItem(
                            .adaptive(
                                minimum: selectionCardMinimumWidth,
                                maximum: selectionCardMaximumWidth
                            ),
                            spacing: 12
                        )
                    ],
                    alignment: .leading,
                    spacing: 12
                ) {
                    ForEach(model.photos) { photo in
                        PhotoCard(
                            photo: photo,
                            selected: model.selectedPhotoID == photo.id,
                            multiSelected: model.selectedPhotoIDs.contains(photo.id),
                            developerToolsEnabled: model.developerToolsEnabled,
                            select: { model.selectedPhotoID = photo.id },
                            toggleMultiSelection: {
                                model.togglePhotoSelection(photoID: photo.id)
                            },
                            preview: {
                                model.selectedPhotoID = photo.id
                                model.previewSelected()
                            },
                            openDetails: { model.openPhotoDetails(photoID: photo.id) },
                            seriesSelected: model.qualitySeriesSelection.contains(photo.id),
                            toggleTopK: { model.toggleQualityTopK(photoID: photo.id) },
                            toggleSeriesSelection: {
                                model.toggleQualitySeriesSelection(photoID: photo.id)
                            },
                            labelSeriesLeader: { model.labelSeriesLeader(photoID: photo.id) }
                        ) { disposition in
                            model.setDecision(photoID: photo.id, disposition: disposition)
                        }
                        .equatable()
                    }
                }
                if model.photos.isEmpty, !model.isLoadingPhotos {
                    VStack(spacing: 10) {
                        Image(
                            systemName: model.selectionBucket == .keep
                                ? "photo.stack" : "trash.slash"
                        )
                        .font(.system(size: 34))
                        .foregroundStyle(.secondary)
                        Text(
                            model.selectionBucket == .keep
                                ? "Нет хороших фотографий" : "Нет плохих фотографий"
                        )
                        .font(.headline)
                        Text("Переключитесь на соседнюю вкладку или измените решение на карточке.")
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, minHeight: 240)
                }
                if model.photos.count < model.photosTotal {
                    Button {
                        model.loadMorePhotos()
                    } label: {
                        Label(
                            model.isLoadingPhotos
                                ? "Загружаем…"
                                : "Показать ещё \(min(100, model.photosTotal - model.photos.count))",
                            systemImage: "square.grid.3x3.fill"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .disabled(model.isLoadingPhotos)
                    Text("Показано \(model.photos.count) из \(model.photosTotal)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(20)
            .frame(maxWidth: 1480, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .top)
        }
    }
}

private struct TasteProfileEditorView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Вкус куратора")
                            .font(.system(size: 30, weight: .bold, design: .rounded))
                        Text(
                            model.tasteOnboardingComplete
                                ? "Дополняйте профиль в любой момент: учитываются только кадры, которые вы явно отметили."
                                : "Создайте профиль за три коротких раунда, прежде чем запускать первый анализ."
                        )
                        .font(.title3)
                        .foregroundStyle(.secondary)
                    }

                    if !model.tasteOnboardingComplete {
                        HStack(spacing: 12) {
                            ForEach(1...model.tasteRoundsTotal, id: \.self) { index in
                                Label(
                                    "Раунд \(index)",
                                    systemImage: index <= model.tasteRoundsCompleted
                                        ? "checkmark.circle.fill" : "circle"
                                )
                                .foregroundStyle(
                                    index <= model.tasteRoundsCompleted ? Color.green : Color.secondary
                                )
                            }
                        }
                    } else {
                        Label(
                            "Профиль активен: \(model.tasteCalibrationExamples) обучающих и \(model.tasteHeldOutExamples) проверочных сравнений.",
                            systemImage: "checkmark.circle.fill"
                        )
                        .foregroundStyle(.green)
                    }

                    GroupBox {
                        if let round = model.tasteRound {
                            activeRound(round)
                        } else {
                            sourcePicker
                        }
                    }

                    if model.isTasteBusy {
                        VStack(alignment: .leading, spacing: 6) {
                            ProgressView(
                                value: Double(model.tasteProgressProcessed),
                                total: Double(max(1, model.tasteProgressTotal))
                            ) { Text("Подготавливаем фотографии локально…") }
                            Text("\(model.tasteProgressProcessed) из \(max(10, model.tasteProgressTotal))")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                    if let message = model.tasteMessage {
                        Label(message, systemImage: "heart.fill")
                            .foregroundStyle(.pink)
                    }
                }
                .padding(28)
                .frame(maxWidth: 940, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle("Настроить вкус")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Готово") { dismiss() }
                }
            }
        }
        .frame(minWidth: 720, minHeight: 680)
    }

    private func activeRound(_ round: TasteRound) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(round.isAdjustment ? "Корректировка вкуса" : "Раунд \(round.roundNumber) из \(round.roundTotal)")
                        .font(.headline)
                    Text("Источник: \(round.albumName)")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Сменить альбом") { model.cancelTasteRound() }
                    .disabled(model.isTasteBusy)
            }
            HStack(spacing: 16) {
                Label(
                    "Нравятся \(model.tasteSelectedIDs.count) из \(round.selectionLimit)",
                    systemImage: "hand.thumbsup.fill"
                )
                .foregroundStyle(model.tasteSelectedIDs.count == round.selectionLimit ? .green : .secondary)
                Label(
                    "Не нравятся \(model.tasteRejectedIDs.count) из \(round.rejectionLimit)",
                    systemImage: "hand.thumbsdown.fill"
                )
                .foregroundStyle(model.tasteRejectedIDs.count == round.rejectionLimit ? .red : .secondary)
            }
            Text("Отметьте три любимых и три явно не нравящихся кадра. Остальные останутся нейтральными и не станут отрицательными примерами.")
                .foregroundStyle(.secondary)
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 150, maximum: 195), spacing: 12)],
                spacing: 12
            ) {
                ForEach(round.photos) { photo in
                    TasteGridCard(
                        photo: photo,
                        favorite: model.tasteSelectedIDs.contains(photo.id),
                        rejected: model.tasteRejectedIDs.contains(photo.id),
                        disabled: model.isTasteBusy,
                        chooseFavorite: { model.toggleTasteSelection(photo.id) },
                        chooseRejected: { model.toggleTasteRejection(photo.id) }
                    )
                }
            }
            HStack {
                Spacer()
                Button("Сохранить предпочтения") { model.submitTasteRound() }
                    .buttonStyle(.borderedProminent)
                    .disabled(
                        model.tasteSelectedIDs.count != round.selectionLimit
                            || model.tasteRejectedIDs.count != round.rejectionLimit
                            || model.isTasteBusy
                    )
            }
        }
        .padding(8)
    }

    private var sourcePicker: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(model.tasteOnboardingComplete ? "Дополнить профиль" : "Первый раунд")
                .font(.headline)
            Text(
                model.tasteOnboardingComplete
                    ? "Добавьте ещё один набор явных предпочтений, чтобы скорректировать рекомендации будущих анализов."
                    : "Выберите альбом с фотографиями, которые вам знакомы. Исходники не изменяются."
            )
            .foregroundStyle(.secondary)
            Picker("Источник фотографий", selection: $model.tasteSourceAlbumID) {
                ForEach((model.albums + model.sharedAlbums).filter { $0.photoCount >= 10 }) { album in
                    Text("\(album.name) · \(album.photoCount) фото").tag(album.id)
                }
            }
            .pickerStyle(.menu)
            HStack {
                Spacer()
                Button("Показать 10 фотографий") { model.prepareTasteRound() }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.tasteSourceAlbumID.isEmpty || model.isTasteBusy)
            }
        }
        .padding(8)
    }
}

private struct ProjectSidebarRow: View {
    let project: ProjectItem
    let active: Bool
    let deleteDisabled: Bool
    let select: () -> Void
    let delete: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button(action: select) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(project.name)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                    Text(projectStateTitle(project.state))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button(action: delete) {
                Image(systemName: "trash")
                    .frame(width: 26, height: 26)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .disabled(deleteDisabled)
            .help("Удалить анализ")
        }
        .padding(10)
        .background(
            active
                ? Color.accentColor.opacity(0.16)
                : Color(nsColor: .controlBackgroundColor).opacity(0.65),
            in: RoundedRectangle(cornerRadius: 9)
        )
    }
}

private struct SelectionBucketTabLabel: View {
    let bucket: SelectionBucket
    let count: Int
    let selected: Bool

    var body: some View {
        VStack(spacing: 6) {
            HStack(spacing: 7) {
                Label(
                    bucket.title,
                    systemImage: bucket == .keep ? "checkmark.circle" : "xmark.circle"
                )
                Text("\(count)")
                    .font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            .font(.subheadline.weight(selected ? .semibold : .regular))
            Capsule()
                .fill(selected ? Color.accentColor : .clear)
                .frame(height: 3)
        }
        .foregroundStyle(selected ? Color.primary : Color.secondary)
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .contentShape(Rectangle())
    }
}

struct OnboardingView: View {
    let continueAction: () -> Void

    var body: some View {
        VStack(spacing: 28) {
            Image(systemName: "photo.stack")
                .font(.system(size: 54, weight: .medium))
                .foregroundStyle(.tint)
            VStack(spacing: 8) {
                Text("Разберите большой альбом спокойно")
                    .font(.system(size: 34, weight: .bold, design: .rounded))
                Text("Photo Curator работает локально и сначала только предлагает подборку.")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 20) {
                OnboardingPoint(
                    symbol: "heart.text.square",
                    title: "1. Покажите свой вкус",
                    detail: "В трёх коротких раундах выберите по три любимых кадра."
                )
                OnboardingPoint(
                    symbol: "photo.on.rectangle.angled",
                    title: "2. Выберите альбом",
                    detail: "Персональный профиль применяется к обычному или общему альбому."
                )
                OnboardingPoint(
                    symbol: "sparkles",
                    title: "3. Получите подборку",
                    detail: "Проверьте хорошие и плохие кадры и создайте Best‑альбом."
                )
            }
            .frame(maxWidth: 620, alignment: .leading)
            Button(action: continueAction) {
                Label("Продолжить и настроить вкус", systemImage: "arrow.right")
                    .frame(minWidth: 300)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            Text("На следующем шаге macOS попросит доступ к Фото. Данные не отправляются в интернет.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(48)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

struct OnboardingPoint: View {
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: symbol)
                .font(.title2)
                .frame(width: 32)
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.headline)
                Text(detail).foregroundStyle(.secondary)
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

@MainActor
private final class ThumbnailLoader: ObservableObject {
    private static let cache: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.countLimit = 500
        cache.totalCostLimit = 256 * 1024 * 1024
        return cache
    }()

    @Published var image: NSImage?

    func load(path: String) async {
        let key = path as NSString
        if let cached = Self.cache.object(forKey: key) {
            image = cached
            return
        }
        let data = await Task.detached(priority: .utility) {
            try? Data(contentsOf: URL(fileURLWithPath: path), options: [.mappedIfSafe])
        }.value
        guard !Task.isCancelled, let data, let decoded = NSImage(data: data) else { return }
        let pixels = Int(decoded.size.width * decoded.size.height)
        Self.cache.setObject(decoded, forKey: key, cost: max(1, pixels * 4))
        image = decoded
    }
}

private struct CachedThumbnail: View {
    let path: String
    @StateObject private var loader = ThumbnailLoader()

    var body: some View {
        Group {
            if let image = loader.image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.medium)
                    .scaledToFill()
            } else {
                Rectangle()
                    .fill(.quaternary)
                    .overlay(ProgressView().controlSize(.small))
            }
        }
        .task(id: path) {
            await loader.load(path: path)
        }
    }
}

private let selectionCardMinimumWidth: CGFloat = 210
private let selectionCardMaximumWidth: CGFloat = 260

struct PhotoCard: View, Equatable {
    let photo: PhotoItem
    let selected: Bool
    let multiSelected: Bool
    let developerToolsEnabled: Bool
    let select: () -> Void
    let toggleMultiSelection: () -> Void
    let preview: () -> Void
    let openDetails: () -> Void
    let seriesSelected: Bool
    let toggleTopK: () -> Void
    let toggleSeriesSelection: () -> Void
    let labelSeriesLeader: () -> Void
    let decide: (String?) -> Void
    @State private var showDetails = false

    static func == (lhs: PhotoCard, rhs: PhotoCard) -> Bool {
        lhs.photo == rhs.photo
            && lhs.selected == rhs.selected
            && lhs.multiSelected == rhs.multiSelected
            && lhs.developerToolsEnabled == rhs.developerToolsEnabled
            && lhs.seriesSelected == rhs.seriesSelected
    }

    private var recommendationTitle: String {
        dispositionTitle(photo.disposition)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Group {
                if let path = photo.imagePath {
                    CachedThumbnail(path: path)
                } else {
                    Rectangle().fill(.quaternary).overlay(Image(systemName: "photo"))
                }
            }
            .frame(maxWidth: .infinity)
            .aspectRatio(4 / 3, contentMode: .fit)
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .onTapGesture(count: 2, perform: openDetails)
            .onTapGesture(count: 1, perform: select)
            .contextMenu {
                Button("Быстрый просмотр", action: preview)
            }
            .help("Нажмите, чтобы выбрать; пробел — быстрый просмотр")
            .overlay(alignment: .topLeading) {
                Button {
                    select()
                    showDetails.toggle()
                } label: {
                    Image(systemName: "info.circle.fill")
                        .font(.system(size: 19, weight: .semibold))
                        .frame(width: 34, height: 34)
                        .contentShape(Circle())
                }
                    .buttonStyle(.plain)
                    .foregroundStyle(.primary)
                    .background(.ultraThickMaterial, in: Circle())
                    .padding(10)
                    .help("Показать оценку и причины решения")
                    .accessibilityLabel("Информация о решении")
                    .popover(isPresented: $showDetails) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(
                                photo.manualDisposition == nil
                                    ? "Почему фотография в «\(recommendationTitle)»"
                                    : "Ваше решение"
                            )
                                .font(.title3.weight(.semibold))
                            LabeledContent("Категория", value: recommendationTitle)
                                .font(.body.weight(.medium))
                            if photo.manualDisposition != nil {
                                Text("Вы изменили категорию вручную. Автоматические аргументы больше не описывают это решение.")
                                    .font(.body)
                                    .foregroundStyle(.secondary)
                            } else {
                                ForEach(photo.reasons, id: \.self) { reason in
                                    Text("• \(reason)").font(.body)
                                }
                                Text("Надёжность автоматического решения: \((photo.confidence ?? 0) * 100, specifier: "%.0f")%")
                                    .font(.body)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(18)
                        .frame(width: 380)
                    }
            }
            .overlay(alignment: .topTrailing) {
                Button(action: toggleMultiSelection) {
                    Image(systemName: "checkmark")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(Color.white)
                        .frame(width: 28, height: 28)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .background(
                    multiSelected ? Color.green : Color.black.opacity(0.42),
                    in: Circle()
                )
                .overlay(Circle().strokeBorder(.white.opacity(0.65)))
                .padding(8)
                .help(multiSelected ? "Снять отметку" : "Отметить для группового действия")
                .accessibilityLabel(multiSelected ? "Снять отметку" : "Отметить фотографию")
            }
            .overlay(alignment: .bottomLeading) {
                DecisionPicker(
                    selection: photo.disposition ?? "keep",
                    decide: decide
                )
                .padding(8)
            }
            .frame(maxWidth: .infinity)
            .clipped()
            if developerToolsEnabled {
                HStack {
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
                    Spacer()
                }
                .frame(maxWidth: .infinity)
            }
            if developerToolsEnabled, photo.duplicateGroup != nil {
                Button(action: labelSeriesLeader) {
                    Label(
                        photo.qualityExpectedLeader ? "Лучший кадр серии подтверждён" : "Это лучший кадр серии",
                        systemImage: photo.qualityExpectedLeader ? "checkmark.seal.fill" : "square.stack.3d.up"
                    )
                }
                .buttonStyle(.borderless)
                .font(.caption.weight(.semibold))
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity)
        .background(.background, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(
                    selected || multiSelected
                        ? Color.accentColor
                        : Color(nsColor: .separatorColor).opacity(0.4),
                    lineWidth: selected || multiSelected ? 3 : 1
                )
                .allowsHitTesting(false)
        )
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .contentShape(RoundedRectangle(cornerRadius: 12))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(photo.filename), рекомендация: \(recommendationTitle)")
        .accessibilityValue(dispositionTitle(photo.disposition))
        .accessibilityHint("Клавиши 1 и 2 меняют решение; пробел открывает быстрый просмотр")
    }
}

private struct PhotoDetailView: View {
    let photo: PhotoItem
    @Environment(\.dismiss) private var dismiss

    private var recommendationTitle: String {
        dispositionTitle(photo.disposition)
    }

    var body: some View {
        HStack(spacing: 0) {
            ZStack {
                Color.black
                if let path = photo.imagePath, let image = NSImage(contentsOfFile: path) {
                    Image(nsImage: image)
                        .resizable()
                        .scaledToFit()
                        .padding(20)
                } else {
                    Image(systemName: "photo")
                        .font(.system(size: 64))
                        .foregroundStyle(.secondary)
                }
            }
            .frame(minWidth: 620, maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        Text("Информация о фотографии")
                            .font(.title2.bold())
                        Spacer()
                        Button("Закрыть") { dismiss() }
                    }
                    LabeledContent("Категория", value: recommendationTitle)
                        .font(.headline)
                    if photo.manualDisposition != nil {
                        Label(
                            "Категория изменена вами вручную",
                            systemImage: "hand.tap.fill"
                        )
                        .foregroundStyle(.secondary)
                    } else {
                        Text("Почему принято это решение")
                            .font(.headline)
                        ForEach(photo.reasons, id: \.self) { reason in
                            Label(reason, systemImage: "circle.fill")
                                .labelStyle(.titleAndIcon)
                        }
                        Text(
                            "Надёжность автоматического решения: \((photo.confidence ?? 0) * 100, specifier: "%.0f")%"
                        )
                        .foregroundStyle(.secondary)
                    }
                }
                .padding(24)
            }
            .frame(width: 360)
        }
        .frame(minWidth: 980, minHeight: 680)
    }
}

private struct DecisionPicker: View {
    let selection: String
    let decide: (String?) -> Void

    private let options = [
        ("keep", "Хорошие", "checkmark", Color.green),
        ("reject", "Плохие", "xmark", Color.red),
    ]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(options, id: \.0) { option in
                let value = option.0
                let title = option.1
                let symbol = option.2
                let tint = option.3
                Button {
                    decide(value)
                } label: {
                    Image(systemName: symbol)
                        .font(.caption.weight(.bold))
                        .frame(width: 30, height: 30)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.white)
                .background(
                    selection == value ? tint : Color.black.opacity(0.42),
                    in: Circle()
                )
                .overlay(Circle().strokeBorder(.white.opacity(0.65)))
                .accessibilityLabel(title)
            }
        }
    }
}

private struct TasteGridCard: View {
    let photo: PhotoItem
    let favorite: Bool
    let rejected: Bool
    let disabled: Bool
    let chooseFavorite: () -> Void
    let chooseRejected: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Group {
                if let path = photo.imagePath, let image = NSImage(contentsOfFile: path) {
                    Image(nsImage: image).resizable().scaledToFill()
                } else {
                    Rectangle().fill(.quaternary).overlay(Image(systemName: "photo"))
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 132)
            .clipped()
            HStack(spacing: 8) {
                Button(action: chooseFavorite) {
                    Label("Нравится", systemImage: "hand.thumbsup.fill")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.bordered)
                .tint(favorite ? .green : .secondary)
                .accessibilityValue(favorite ? "Выбрано" : "Не выбрано")
                Button(action: chooseRejected) {
                    Label("Не нравится", systemImage: "hand.thumbsdown.fill")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.bordered)
                .tint(rejected ? .red : .secondary)
                .accessibilityValue(rejected ? "Выбрано" : "Не выбрано")
            }
            .padding(8)
        }
        .background(.background, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(favorite ? Color.green : rejected ? Color.red : Color.secondary.opacity(0.25), lineWidth: favorite || rejected ? 3 : 1)
        )
        .disabled(disabled)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(photo.filename)
    }
}

private func dispositionTitle(_ disposition: String?) -> String {
    ["keep": "Хорошие", "reject": "Плохие"][disposition ?? ""]
        ?? "Без решения"
}

private func projectStateTitle(_ state: String) -> String {
    [
        "created": "Готов к запуску",
        "running": "Выполняется",
        "ready": "Готов",
        "interrupted": "Остановлен",
        "error": "Ошибка",
    ][state] ?? state
}

private func jobStatusSymbol(_ status: String) -> String {
    [
        "pending": "circle",
        "running": "arrow.triangle.2.circlepath",
        "done": "checkmark.circle.fill",
        "warning": "exclamationmark.triangle.fill",
        "error": "xmark.octagon.fill",
        "interrupted": "pause.circle.fill",
        "cancelled": "pause.circle.fill",
    ][status] ?? "questionmark.circle"
}

private func jobStatusColor(_ status: String) -> Color {
    switch status {
    case "done": return .green
    case "warning": return .orange
    case "error": return .red
    case "running": return .accentColor
    default: return .secondary
    }
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
                    Button("Настроить вкус…") { model.openTasteEditor() }
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
            if model.developerToolsEnabled {
                Section("Проверка качества · Developer") {
                    Text("Экспортирует явную разметку и замороженный Swipe Score.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    LabeledContent("Ручные решения", value: "\(model.qualityManualLabels) / 50–100")
                    LabeledContent("Проверочные A/B", value: "\(model.qualityHeldOutPairs) / 10+")
                    LabeledContent("Top‑K", value: "\(model.qualityTopKCount) / 5+")
                    LabeledContent("Подтверждённые серии", value: "\(model.qualitySeriesCount) / 1+")
                    Label(
                        model.qualityReleaseReady ? "Структура corpus готова" : "Разметка ещё не завершена",
                        systemImage: model.qualityReleaseReady ? "checkmark.seal.fill" : "hourglass"
                    )
                    .foregroundStyle(model.qualityReleaseReady ? .green : .secondary)
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
