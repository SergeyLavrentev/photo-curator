import AppKit
import Combine
import Photos
import QuickLookUI
import SwiftUI
import UniformTypeIdentifiers

enum WorkflowStep: Int, CaseIterable, Identifiable {
    case taste
    case album
    case analysis
    case selection

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .taste: return "Персонализация"
        case .album: return "Альбом"
        case .analysis: return "Анализ"
        case .selection: return "Отбор"
        }
    }

    var symbol: String {
        switch self {
        case .taste: return "heart.text.square"
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
    @Published var currentStep: WorkflowStep = .taste

    let worker = NativeWorkerClient()
    let developerToolsEnabled =
        ProcessInfo.processInfo.environment["PHOTO_CURATOR_DEVELOPER_TOOLS"] == "1"
    private let stageOrder = ["inventory", "previews", "metrics", "duplicates", "vision", "decisions"]
    private var pollTask: Task<Void, Never>?
    private var permissionHelpTask: Task<Void, Never>?
    private var decisionHistory: [DecisionUndo] = []
    private var binaryProjects: Set<String> = []
    private var hasStarted = false
    private let galleryPageSize = 48
    private let retainedProjectDefaultsKey = "retainedProjectID"
    private let currentDecisionModelVersion = 2

    init() {
        selectedAlbumID = UserDefaults.standard.string(forKey: "selectedAlbumID") ?? ""
        let savedDensity = UserDefaults.standard.string(forKey: "selectionDensity") ?? "balanced"
        density = ["compact", "balanced", "broad"].contains(savedDensity)
            ? savedDensity : "balanced"
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
        case .taste:
            return project?.state != "running"
        case .album:
            return tasteOnboardingComplete && project?.state != "running"
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
            errorMessage = "Сначала завершите три раунда персонализации"
            currentStep = .taste
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

    func selectAllVisiblePhotos() {
        selectedPhotoIDs = Set(photos.map(\.id))
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
        guard !tasteSourceAlbumID.isEmpty, !tasteOnboardingComplete else { return }
        isTasteBusy = true
        tasteProgressProcessed = 0
        tasteProgressTotal = 10
        tasteMessage = nil
        Task {
            defer { isTasteBusy = false }
            do {
                let result = try await call(
                    "taste_round_prepare",
                    ["album_id": tasteSourceAlbumID]
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
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func toggleTasteSelection(_ photoID: String) {
        guard let round = tasteRound else { return }
        if tasteSelectedIDs.contains(photoID) {
            tasteSelectedIDs.remove(photoID)
        } else if tasteSelectedIDs.count < round.selectionLimit {
            tasteSelectedIDs.insert(photoID)
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
                tasteMessage = "Раунд отменён. Выберите другой альбом."
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func submitTasteRound() {
        guard let round = tasteRound, tasteSelectedIDs.count == round.selectionLimit else {
            return
        }
        isTasteBusy = true
        tasteMessage = nil
        let selected = Array(tasteSelectedIDs).sorted()
        Task {
            defer { isTasteBusy = false }
            do {
                let result = try await call("taste_round_submit", [
                    "round_id": round.id,
                    "selected_uuids": selected,
                ])
                guard let value = result as? [String: Any],
                      let profile = value["profile"] as? [String: Any]
                else { throw NativeWorkerClientError.invalidResponse }
                applyTasteProfile(profile)
                tasteRound = nil
                tasteSelectedIDs = []
                tasteMessage = tasteOnboardingComplete
                    ? "Профиль готов: модель обучена на ваших выборах."
                    : "Раунд сохранён. Можно выбрать другой альбом для следующего."
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
        currentStep = tasteOnboardingComplete ? .album : .taste
    }

    func cancelNewAnalysis() {
        analysisDraftActive = false
        guard let project else {
            currentStep = tasteOnboardingComplete ? .album : .taste
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
                        currentStep = tasteOnboardingComplete ? .album : .taste
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
                currentStep = tasteOnboardingComplete ? .album : .taste
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
    }

    private var workflow: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 16) {
                Label("Photo Curator", systemImage: "camera.aperture")
                    .font(.title2.bold())
                Divider()
                Text("ИНСТРУМЕНТЫ")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.tertiary)
                Button {
                    model.beginNewAnalysis()
                } label: {
                    Label("Новый анализ", systemImage: "plus")
                }
                .buttonStyle(.borderless)
                .disabled(
                    model.isBusy || model.analysisDraftActive || model.project?.state == "running"
                )
                if model.analysisDraftActive {
                    HStack {
                        Label("Новый анализ", systemImage: "doc.badge.plus")
                            .font(.subheadline.weight(.semibold))
                        Spacer()
                        Button("Отменить") { model.cancelNewAnalysis() }
                            .buttonStyle(.borderless)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                }
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
        case .taste:
            setupScreen(
                title: "Настройте вкус куратора",
                subtitle: "Три раза выберите три лучших кадра из десяти — модель запомнит, что нравится именно вам."
            ) { tasteSection }
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
            }
            if !model.sharedAlbums.isEmpty {
                Label("Для общего альбома PhotoKit подготовит локальные review‑копии; источник не изменится.", systemImage: "person.2")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack {
                if model.analysisDraftActive {
                    Button("Отменить новый анализ") { model.cancelNewAnalysis() }
                } else {
                    Button("Назад к персонализации") { model.open(.taste) }
                }
                Spacer()
                Button("Начать анализ") {
                    model.createAndAnalyze()
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

    private var tasteSection: some View {
        StepCard(number: 1, title: "Три коротких раунда", symbol: "heart.text.square") {
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
            if model.tasteOnboardingComplete {
                Label(
                    "Профиль готов: \(model.tasteCalibrationExamples) обучающих и \(model.tasteHeldOutExamples) контрольных сравнений.",
                    systemImage: "checkmark.circle.fill"
                )
                .foregroundStyle(.green)
                Text("Photo Curator будет учитывать этот профиль во всех следующих анализах.")
                    .foregroundStyle(.secondary)
                HStack {
                    Spacer()
                    Button("Выбрать альбом для анализа") { model.open(.album) }
                        .buttonStyle(.borderedProminent)
                }
            } else if let round = model.tasteRound {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Раунд \(round.roundNumber) из \(round.roundTotal)")
                                .font(.headline)
                            Text("Источник: \(round.albumName)")
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Сменить альбом") { model.cancelTasteRound() }
                            .disabled(model.isTasteBusy)
                        Text("Выбрано \(model.tasteSelectedIDs.count) из \(round.selectionLimit)")
                            .font(.headline.monospacedDigit())
                            .foregroundStyle(
                                model.tasteSelectedIDs.count == round.selectionLimit
                                    ? Color.green : Color.secondary
                            )
                    }
                    Text("Кликните на три фотографии, которые нравятся вам больше остальных.")
                        .foregroundStyle(.secondary)
                    LazyVGrid(
                        columns: [
                            GridItem(.adaptive(minimum: 140, maximum: 190), spacing: 12)
                        ],
                        spacing: 12
                    ) {
                        ForEach(round.photos) { photo in
                            TasteGridCard(
                                photo: photo,
                                selected: model.tasteSelectedIDs.contains(photo.id),
                                disabled: model.isTasteBusy
                            ) {
                                model.toggleTasteSelection(photo.id)
                            }
                        }
                    }
                    HStack {
                        Spacer()
                        Button("Сохранить выбор") { model.submitTasteRound() }
                            .buttonStyle(.borderedProminent)
                            .disabled(
                                model.tasteSelectedIDs.count != round.selectionLimit
                                    || model.isTasteBusy
                            )
                    }
                }
            } else {
                Text(
                    model.tasteRoundsCompleted == 0
                        ? "Для первого раунда выберите альбом с фотографиями, которые вам знакомы."
                        : "Можно продолжить с тем же альбомом или выбрать другой источник."
                )
                    .foregroundStyle(.secondary)
                Picker("Источник фотографий", selection: $model.tasteSourceAlbumID) {
                    ForEach((model.albums + model.sharedAlbums).filter { $0.photoCount >= 10 }) {
                        album in
                        Text("\(album.name) · \(album.photoCount) фото").tag(album.id)
                    }
                }
                .pickerStyle(.menu)
                HStack {
                    Text("В раундах используются разные фотографии; исходники не изменяются.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Показать 10 фотографий") { model.prepareTasteRound() }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.tasteSourceAlbumID.isEmpty || model.isTasteBusy)
                }
            }
            if model.isTasteBusy {
                VStack(alignment: .leading, spacing: 6) {
                    ProgressView(
                        value: Double(model.tasteProgressProcessed),
                        total: Double(max(1, model.tasteProgressTotal))
                    ) {
                        Text("Подготавливаем фотографии локально…")
                    }
                    Text(
                        "\(model.tasteProgressProcessed) из \(max(10, model.tasteProgressTotal))"
                    )
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                }
            }
            if let message = model.tasteMessage {
                Label(message, systemImage: "heart.fill").foregroundStyle(.pink)
            }
            if !model.tasteOnboardingComplete {
                Label(
                    "Альбом для анализа станет доступен после трёх раундов.",
                    systemImage: "lock.fill"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
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
                    model.createAndAnalyze()
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
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top, spacing: 20) {
                    screenHeader(
                        title: "Отбор фотографий",
                        subtitle: "Проверьте две готовые подборки и при необходимости перенесите кадры между ними."
                    )
                    Spacer()
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
                    .controlSize(.large)
                    .disabled(model.isBusy || model.keptTotal == 0)
                }
                HStack(spacing: 6) {
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
                .padding(4)
                .background(.quaternary.opacity(0.55), in: RoundedRectangle(cornerRadius: 12))

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
                HStack(spacing: 10) {
                    Text(
                        model.selectedPhotoIDs.isEmpty
                            ? "Отметьте фотографии галочками для группового действия"
                            : "Выбрано: \(model.selectedPhotoIDs.count)"
                    )
                    .font(.subheadline.weight(.semibold))
                    Spacer()
                    Button("Выбрать видимые") { model.selectAllVisiblePhotos() }
                        .disabled(model.photos.isEmpty || model.isBusy)
                    if !model.selectedPhotoIDs.isEmpty {
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
                }
                .padding(10)
                .background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 10))
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
                            .adaptive(minimum: selectionCardWidth, maximum: selectionCardWidth),
                            spacing: 16
                        )
                    ],
                    alignment: .leading,
                    spacing: 16
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
            .padding(32)
            .frame(maxWidth: 1480, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .top)
        }
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
        HStack(spacing: 8) {
            Text(bucket.title)
            Text("\(count)")
                .font(.caption.monospacedDigit().weight(.semibold))
                .padding(.horizontal, 7)
                .padding(.vertical, 3)
                .background(.quaternary, in: Capsule())
        }
        .font(.subheadline.weight(.semibold))
        .foregroundStyle(selected ? Color.white : Color.primary)
        .padding(.horizontal, 16)
        .padding(.vertical, 9)
        .background(
            selected ? Color.accentColor : Color.clear,
            in: RoundedRectangle(cornerRadius: 9)
        )
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

private let selectionCardWidth: CGFloat = 320

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
            .frame(height: 180)
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
                    Image(
                        systemName: multiSelected
                            ? "checkmark.circle.fill" : "circle"
                    )
                    .font(.system(size: 22, weight: .semibold))
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(
                        multiSelected ? Color.white : Color.primary,
                        multiSelected ? Color.accentColor : Color.white.opacity(0.9)
                    )
                    .frame(width: 38, height: 38)
                    .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .background(.ultraThickMaterial, in: Circle())
                .padding(10)
                .help(multiSelected ? "Снять отметку" : "Отметить для группового действия")
                .accessibilityLabel(multiSelected ? "Снять отметку" : "Отметить фотографию")
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
            DecisionPicker(
                selection: photo.disposition ?? "keep",
                decide: decide
            )
        }
        .padding(12)
        .frame(width: selectionCardWidth)
        .background(.background, in: RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(
                    selected || multiSelected
                        ? Color.accentColor
                        : Color(nsColor: .separatorColor).opacity(0.4),
                    lineWidth: selected || multiSelected ? 3 : 1
                )
                .allowsHitTesting(false)
        )
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .contentShape(RoundedRectangle(cornerRadius: 16))
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
        ("keep", "Хорошие"),
        ("reject", "Плохие"),
    ]

    var body: some View {
        GeometryReader { geometry in
            let buttonWidth = max(0, (geometry.size.width - 1) / 2)
            HStack(spacing: 1) {
                ForEach(options, id: \.0) { option in
                    let value = option.0
                    let title = option.1
                    Button {
                        decide(value)
                    } label: {
                        Text(title)
                            .font(.caption.weight(.semibold))
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                            .frame(width: buttonWidth, height: 34)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(selection == value ? Color.white : Color.primary)
                    .background(selection == value ? Color.accentColor : Color.clear)
                    .accessibilityLabel(title)
                }
            }
        }
        .frame(height: 34)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color(nsColor: .separatorColor).opacity(0.5))
                .allowsHitTesting(false)
        )
    }
}

struct TasteGridCard: View {
    let photo: PhotoItem
    let selected: Bool
    let disabled: Bool
    let toggle: () -> Void

    var body: some View {
        Button(action: toggle) {
            ZStack(alignment: .topTrailing) {
                Group {
                    if let path = photo.imagePath, let image = NSImage(contentsOfFile: path) {
                        Image(nsImage: image).resizable().scaledToFill()
                    } else {
                        Rectangle().fill(.quaternary).overlay(Image(systemName: "photo"))
                    }
                }
                .frame(maxWidth: .infinity)
                .frame(height: 135)
                .clipped()
                if selected {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.title2)
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, Color.accentColor)
                        .padding(8)
                        .shadow(radius: 2)
                }
            }
            .contentShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(selected ? Color.accentColor : Color.clear, lineWidth: 4)
                .allowsHitTesting(false)
        )
        .disabled(disabled)
        .accessibilityLabel(photo.filename)
        .accessibilityValue(selected ? "Выбрано" : "Не выбрано")
        .accessibilityHint("Добавить или убрать из трёх лучших фотографий")
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
