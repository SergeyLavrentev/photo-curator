import AppKit
import Combine
import Foundation
#if !GALLERY_BENCHMARK
import PhotoCuratorPublishHelper
#endif
import Photos
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
    case pick
    case alternative
    case review
    case reject

    var id: String { rawValue }
    var title: String {
        switch self {
        case .pick: return "Best"
        case .alternative: return "Альтернативы"
        case .review: return "Проверить"
        case .reject: return "Отклонённые"
        }
    }

    var symbol: String {
        switch self {
        case .pick: return "flag.fill"
        case .alternative: return "photo.stack"
        case .review: return "questionmark.circle"
        case .reject: return "xmark.circle"
        }
    }
}

enum WorkspaceMode: String, CaseIterable, Identifiable {
    case grid
    case loupe
    case compare
    case survey

    var id: String { rawValue }
    var title: String {
        switch self {
        case .grid: return "Grid"
        case .loupe: return "Loupe"
        case .compare: return "Compare"
        case .survey: return "Survey"
        }
    }

    var symbol: String {
        switch self {
        case .grid: return "square.grid.3x3"
        case .loupe: return "rectangle.inset.filled.and.person.filled"
        case .compare: return "rectangle.split.2x1"
        case .survey: return "rectangle.grid.2x2"
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
    @Published var engineApple: Bool {
        didSet { UserDefaults.standard.set(engineApple, forKey: "engineApple") }
    }
    @Published var engineNIMA: Bool {
        didSet { UserDefaults.standard.set(engineNIMA, forKey: "engineNIMA") }
    }
    @Published var engineMobileCLIP: Bool {
        didSet { UserDefaults.standard.set(engineMobileCLIP, forKey: "engineMobileCLIP") }
    }
    @Published var engineMUSIQ: Bool {
        didSet { UserDefaults.standard.set(engineMUSIQ, forKey: "engineMUSIQ") }
    }
    @Published var codexStatus: CodexConnectionStatus?
    @Published var isCheckingCodex = false
    @Published var codexConsentPending = false
    @Published var codexConsentAction = "new"
    @Published var projects: [ProjectItem] = []
    @Published var project: ProjectItem?
    @Published var analysisDraftActive = false
    @Published var qualityWizardActive = false
    @Published var jobs: [JobItem] = []
    @Published var photos: [PhotoItem] = []
    @Published var photosTotal = 0
    @Published var selectionBucket: SelectionBucket = .pick
    @Published var workspaceMode: WorkspaceMode = .grid
    @Published var keptTotal = 0
    @Published var pickedTotal = 0
    @Published var alternativeTotal = 0
    @Published var selectionReviewTotal = 0
    @Published var rejectedTotal = 0
    @Published var totalAssets = 0
    @Published var videosSkipped = 0
    @Published var previewReady = 0
    @Published var previewMissing = 0
    @Published var appleVisionReady = 0
    @Published var appleVisionErrors = 0
    @Published var codexReady = 0
    @Published var codexErrors = 0
    @Published var isLoadingPhotos = false
    @Published var unavailablePreviewFiles = 0
    @Published var errorMessage: String?
    @Published var isBusy = false
    @Published var operationMessage: String?
    @Published var publishPlan: PublishPlan?
    @Published var publishMessage: String?
    @Published var publishProcessed = 0
    @Published var publishTotal = 0
    @Published var tasteStatus = "loading"
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
    @Published var photoKitAcceptanceMessage: String?
    @Published var photoKitAcceptancePending = false
    @Published var isRunningPhotoKitAcceptance = false
    @Published var pendingPhotoKitAcceptance: PhotoKitAcceptancePreparedDTO?
    @Published var qualitySeriesSelection: Set<String> = []
    @Published var qualityManualLabels = 0
    @Published var qualityHeldOutPairs = 0
    @Published var qualityTopKCount = 0
    @Published var qualitySeriesCount = 0
    @Published var qualityDefectLabels = 0
    @Published var qualityReleaseReady = false
    @Published var qualityCandidates: [PhotoItem] = []
    @Published var qualityCandidateIndex = 0
    @Published var qualityCandidateRequested = 75
    @Published var qualityCandidateAvailable = 0
    @Published var qualityPair: QualityPairDTO.Pair?
    @Published var qualityPairCompleted = 0
    @Published var qualityPairEligible = 0
    @Published var isQualityBusy = false
    @Published var isTasteBusy = false
    @Published var selectedPhotoID: String?
    @Published var selectedPhotoIDs: Set<String> = []
    @Published var detailPhoto: PhotoItem?
    @Published var photoAccessNeedsAction = false
    @Published var photoAccessCanRequest = false
    @Published var hasCompletedOnboarding: Bool
    @Published var currentStep: WorkflowStep = .album
    @Published var qualityLabEnabled: Bool {
        didSet { UserDefaults.standard.set(qualityLabEnabled, forKey: "qualityLabEnabled") }
    }

    let worker = NativeWorkerClient()
    let developerToolsEnabled =
        ProcessInfo.processInfo.environment["PHOTO_CURATOR_DEVELOPER_TOOLS"] == "1"
    var qualityToolsEnabled: Bool { developerToolsEnabled || qualityLabEnabled }
    var tasteProfileApplies: Bool { tasteStatus == "ready" }
    var tasteProfileLoading: Bool { tasteStatus == "loading" }
    private var stageOrder: [String] {
        let mode = project?.analysisMode ?? analysisMode
        return mode == "codex"
            ? ["inventory", "previews", "metrics", "duplicates", "vision", "models", "codex", "scene_shadow", "decisions"]
            : ["inventory", "previews", "metrics", "duplicates", "vision", "models", "scene_shadow", "decisions"]
    }
    private var pollTask: Task<Void, Never>?
    private var permissionHelpTask: Task<Void, Never>?
    private var decisionHistory: [DecisionUndo] = []
    private var decisionGenerations: [String: Int] = [:]
    private var ratingGenerations: [String: Int] = [:]
    private var binaryProjects: Set<String> = []
    private var galleryCursor: GalleryCursor?
    private var projectRequestGeneration = 0
    private var galleryRequestGeneration = 0
    private var hasStarted = false
    private let galleryPageSize = 36
    private let retainedProjectDefaultsKey = "retainedProjectID"
    private let pendingPhotoKitAcceptanceDefaultsKey = "pendingPhotoKitAcceptanceV1"
    private let currentDecisionModelVersion = 6

    init() {
        selectedAlbumID = UserDefaults.standard.string(forKey: "selectedAlbumID") ?? ""
        let savedDensity = UserDefaults.standard.string(forKey: "selectionDensity") ?? "balanced"
        density = ["compact", "balanced", "broad"].contains(savedDensity)
            ? savedDensity : "balanced"
        let savedMode = UserDefaults.standard.string(forKey: "analysisMode") ?? "local"
        analysisMode = ["local", "codex"].contains(savedMode) ? savedMode : "local"
        engineApple = Self.savedEngineSetting("engineApple")
        engineNIMA = Self.savedEngineSetting("engineNIMA")
        engineMobileCLIP = Self.savedEngineSetting("engineMobileCLIP")
        engineMUSIQ = Self.savedEngineSetting("engineMUSIQ")
        tasteSourceAlbumID =
            UserDefaults.standard.string(forKey: "tasteSourceAlbumID") ?? ""
        hasCompletedOnboarding = UserDefaults.standard.bool(forKey: "didCompleteOnboardingV1")
        qualityLabEnabled = UserDefaults.standard.bool(forKey: "qualityLabEnabled")
        if let data = UserDefaults.standard.data(forKey: pendingPhotoKitAcceptanceDefaultsKey) {
            pendingPhotoKitAcceptance = try? JSONDecoder().decode(
                PhotoKitAcceptancePreparedDTO.self, from: data
            )
        }
    }

    private static func savedEngineSetting(_ key: String) -> Bool {
        guard UserDefaults.standard.object(forKey: key) != nil else { return true }
        return UserDefaults.standard.bool(forKey: key)
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
            return project?.state == "ready" ? "Все этапы завершены" : ""
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

    var publishUsesIndependentCopies: Bool {
        project?.albumID.hasPrefix("local-") == true
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
                if PHPhotoLibrary.authorizationStatus(for: .readWrite) == .notDetermined {
                    photoAccessCanRequest = true
                    photoAccessNeedsAction = true
                    workerStatus = "Разрешите доступ к Apple Photos"
                    return
                }
                guard await requestPhotoLibraryAccess() else { return }
            }
            await bootstrap()
        }
    }

    func requestPhotoLibraryAccessFromUI() {
        photoAccessCanRequest = false
        photoAccessNeedsAction = false
        Task {
            guard await requestPhotoLibraryAccess() else { return }
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
                let profile: TasteProfileDTO = try await callDTO(
                    "taste_status", TasteStatusParams(paused: paused), as: TasteProfileDTO.self
                )
                applyTasteProfile(profile)
                tasteMessage = paused ? "Профиль вкуса поставлен на паузу." : "Профиль вкуса снова активен."
                await refreshDecisionsForTaste()
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func exportTasteProfile() {
        Task {
            do {
                let result: JSONValue = try await callDTO(
                    "taste_export", EmptyWorkerParams(), as: JSONValue.self
                )
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                let data = try encoder.encode(result)
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
                _ = try await callDTO(
                    "taste_reset", EmptyWorkerParams(), as: WorkerOperationDTO.self
                )
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
                let value: QualityEvidenceDTO = try await callDTO(
                    "quality_export",
                    ProjectIDParams(projectID: project.id),
                    as: QualityEvidenceDTO.self
                )

                let panel = NSOpenPanel()
                panel.title = "Куда сохранить проверочный набор?"
                panel.prompt = "Сохранить"
                panel.canChooseFiles = false
                panel.canChooseDirectories = true
                panel.canCreateDirectories = true
                panel.allowsMultipleSelection = false
                guard panel.runModal() == .OK, let directory = panel.url else { return }

                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                let manifestData = try encoder.encode(value.manifest)
                let snapshotData = try encoder.encode(value.scoreSnapshot)
                try manifestData.write(
                    to: directory.appendingPathComponent("photo-curator-labels.json"),
                    options: .atomic
                )
                try snapshotData.write(
                    to: directory.appendingPathComponent("photo-curator-swipe-scores.json"),
                    options: .atomic
                )
                let labels = value.summary.manualLabels
                let heldOut = value.summary.heldOutPairs
                let topK = value.summary.expectedTopK
                let series = value.summary.humanDuplicateGroups
                let releaseReady = value.summary.releaseReady
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

                let decoder = JSONDecoder()
                let manifest = try decoder.decode(JSONValue.self, from: Data(contentsOf: labelsURL))
                let snapshot = try decoder.decode(JSONValue.self, from: Data(contentsOf: scoresURL))
                let report: QualityEvaluationDTO = try await callDTO(
                    "quality_evaluate",
                    QualityEvaluateParams(
                        projectID: project.id,
                        manifest: manifest,
                        scoreSnapshot: snapshot
                    ),
                    as: QualityEvaluationDTO.self
                )
                let passed = report.passed
                let eligible = report.releaseEligible
                let labels = report.labelledAssets
                qualityMessage = eligible
                    ? "Acceptance: \(passed ? "PASS" : "FAIL"), \(labels) фото."
                    : "Набор пока неполный: \(labels) фото; release gate не открыт."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func requestPhotoKitAcceptance() {
        guard project?.state == "ready" else {
            errorMessage = "Для PhotoKit acceptance нужен завершённый реальный проект."
            return
        }
        photoKitAcceptancePending = true
    }

    func runPhotoKitAcceptance() {
        guard let project, project.state == "ready", !isRunningPhotoKitAcceptance else { return }
        isRunningPhotoKitAcceptance = true
        photoKitAcceptanceMessage = "Создаём одноразовый альбом и готовим проверку cleanup…"
        Task {
            defer { isRunningPhotoKitAcceptance = false }
            do {
                let prepared: PhotoKitAcceptancePreparedDTO = try await callDTO(
                    "photokit_acceptance_prepare",
                    PhotoKitAcceptanceParams(projectID: project.id),
                    as: PhotoKitAcceptancePreparedDTO.self
                )
                rememberPendingPhotoKitAcceptance(prepared)
                try await finishPhotoKitAcceptance(prepared)
            } catch {
                photoKitAcceptanceMessage = pendingPhotoKitAcceptance == nil
                    ? nil
                    : "Cleanup не завершён. Повторите его в настройках; новый альбом не создавайте."
                errorMessage = error.localizedDescription
            }
        }
    }

    func retryPhotoKitAcceptanceCleanup() {
        guard let prepared = pendingPhotoKitAcceptance, !isRunningPhotoKitAcceptance else { return }
        isRunningPhotoKitAcceptance = true
        photoKitAcceptanceMessage = "Повторяем точечный cleanup одноразового альбома…"
        Task {
            defer { isRunningPhotoKitAcceptance = false }
            do {
                try await finishPhotoKitAcceptance(prepared)
            } catch {
                photoKitAcceptanceMessage =
                    "Cleanup не завершён. Одноразовый альбом сохранён для безопасного повтора."
                errorMessage = error.localizedDescription
            }
        }
    }

    private func finishPhotoKitAcceptance(
        _ prepared: PhotoKitAcceptancePreparedDTO
    ) async throws {
        photoKitAcceptanceMessage =
            "Подтвердите системное удаление только \(prepared.albumName)…"
#if GALLERY_BENCHMARK
        throw NSError(
            domain: "PhotoCurator.GalleryBenchmark",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "PhotoKit cleanup недоступен в benchmark harness"]
        )
#else
        try await deletePhotoCuratorAcceptanceAlbumInHostApplication(
            identifier: prepared.albumIdentifier
        )
        let result: PhotoKitAcceptanceDTO = try await callDTO(
            "photokit_acceptance_finalize",
            PhotoKitAcceptanceFinalizeParams(prepared: prepared),
            as: PhotoKitAcceptanceDTO.self
        )
        if result.acceptanceAlbumRemoved {
            forgetPendingPhotoKitAcceptance()
        }
        if result.passed && result.sourceAssetPreservedAfterCleanup {
            photoKitAcceptanceMessage =
                "PASS: \(result.albumName) создан и удалён; исходное фото сохранено."
        } else {
            photoKitAcceptanceMessage =
                "FAIL: cleanup или сохранность исходного фото не подтверждены."
        }
#endif
    }

    private func rememberPendingPhotoKitAcceptance(
        _ prepared: PhotoKitAcceptancePreparedDTO
    ) {
        pendingPhotoKitAcceptance = prepared
        if let data = try? JSONEncoder().encode(prepared) {
            UserDefaults.standard.set(data, forKey: pendingPhotoKitAcceptanceDefaultsKey)
        }
    }

    private func forgetPendingPhotoKitAcceptance() {
        pendingPhotoKitAcceptance = nil
        UserDefaults.standard.removeObject(forKey: pendingPhotoKitAcceptanceDefaultsKey)
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
        guard !selectedAlbumID.isEmpty else {
            errorMessage = "Сначала выберите альбом"
            return
        }
        isBusy = true
        operationMessage = "Создаём локальный проект…"
        errorMessage = nil
        jobs = []
        photos = []
        selectionBucket = .pick
        keptTotal = 0
        pickedTotal = 0
        alternativeTotal = 0
        selectionReviewTotal = 0
        rejectedTotal = 0
        totalAssets = 0
        videosSkipped = 0
        previewReady = 0
        previewMissing = 0
        appleVisionReady = 0
        appleVisionErrors = 0
        codexReady = 0
        codexErrors = 0
        binaryProjects.removeAll()
        selectedPhotoID = nil
        decisionHistory = []
        decisionGenerations = [:]
        tasteMessage = nil
        qualitySeriesSelection.removeAll()
        qualityMessage = nil
        qualityManualLabels = 0
        qualityHeldOutPairs = 0
        qualityTopKCount = 0
        qualitySeriesCount = 0
        qualityDefectLabels = 0
        qualityReleaseReady = false
        publishPlan = nil
        currentStep = .analysis
        UserDefaults.standard.removeObject(forKey: retainedProjectDefaultsKey)
        Task {
            do {
                let project: ProjectItem = try await callDTO(
                    "create_project",
                    CreateProjectParams(
                        albumID: selectedAlbumID,
                        selectionDensity: density,
                        analysisMode: analysisMode,
                        engineApple: engineApple,
                        engineNIMA: engineNIMA,
                        engineMobileCLIP: engineMobileCLIP,
                        engineMUSIQ: engineMUSIQ
                    ),
                    as: ProjectItem.self
                )
                self.project = project
                analysisDraftActive = false
                updateProjectInList(project)
                UserDefaults.standard.set(project.id, forKey: retainedProjectDefaultsKey)
                operationMessage = "Запускаем анализ…"
                _ = try await callDTO(
                    "start_analysis",
                    StartAnalysisParams(projectID: project.id, fromStage: nil),
                    as: WorkerOperationDTO.self
                )
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
            requestCodexConsentForNewAnalysis()
        } else {
            createAndAnalyze()
        }
    }

    private func requestCodexConsentForNewAnalysis() {
        guard !isCheckingCodex else { return }
        isCheckingCodex = true
        errorMessage = nil
        Task {
            defer { isCheckingCodex = false }
            do {
                let status: CodexConnectionStatus = try await callDTO(
                    "codex_status", EmptyWorkerParams(), as: CodexConnectionStatus.self
                )
                codexStatus = status
                guard status.ready else {
                    errorMessage = status.detail
                        ?? "Codex не подключён через ChatGPT. Проверьте подключение перед запуском."
                    return
                }
                codexConsentAction = "new"
                codexConsentPending = true
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func confirmCodexAnalysis() {
        codexConsentPending = false
        if codexConsentAction == "repair" {
            performPreviewRepair()
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
                let status: CodexConnectionStatus = try await callDTO(
                    "codex_status", EmptyWorkerParams(), as: CodexConnectionStatus.self
                )
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
                _ = try await callDTO(
                    "start_analysis",
                    StartAnalysisParams(projectID: project.id, fromStage: nil),
                    as: WorkerOperationDTO.self
                )
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
                _ = try await callDTO(
                    "resume_analysis",
                    ProjectIDParams(projectID: project.id),
                    as: WorkerOperationDTO.self
                )
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

    func repairUnavailablePreviews() {
        guard let project, unavailablePreviewFiles > 0, !isBusy else { return }
        if project.analysisMode == "codex" {
            // Preview repair is a local PhotoKit operation. Ask for renewed
            // consent because repaired review copies may later reach Codex, but
            // do not block the local repair on a stale connection snapshot. The
            // pipeline rechecks live authorization immediately before Codex and
            // skips that stage entirely when no analysis-grade previews exist.
            codexConsentAction = "repair"
            codexConsentPending = true
            return
        }
        performPreviewRepair()
    }

    private func performPreviewRepair() {
        guard let project, unavailablePreviewFiles > 0, !isBusy else { return }
        isBusy = true
        ThumbnailLoader.invalidateAll()
        operationMessage = "Восстанавливаем локальные превью…"
        errorMessage = nil
        currentStep = .analysis
        Task {
            do {
                _ = try await callDTO(
                    "start_analysis",
                    StartAnalysisParams(projectID: project.id, fromStage: "previews"),
                    as: WorkerOperationDTO.self
                )
                isBusy = false
                unavailablePreviewFiles = 0
                try await Task.sleep(nanoseconds: 250_000_000)
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
                _ = try await callDTO(
                    "cancel_analysis",
                    ProjectIDParams(projectID: project.id),
                    as: WorkerOperationDTO.self
                )
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
        let generation = (decisionGenerations[photoID] ?? 0) + 1
        decisionGenerations[photoID] = generation
        let previousManual = previousPhoto.manualDisposition
        let previousSelection = previousPhoto.selection
        let updatedSelection = disposition.flatMap {
            ["keep": "pick", "review": "review", "reject": "reject"][$0]
        }
        selectedPhotoID = photoID
        selectedPhotoIDs.remove(photoID)
        if let disposition {
            adjustDecisionCounts(from: previousPhoto.disposition, to: disposition)
            adjustSelectionCounts(from: previousSelection, to: updatedSelection)
            photos[initialIndex].disposition = disposition
            photos[initialIndex].manualDisposition = disposition
            photos[initialIndex].selection = updatedSelection
            photos[initialIndex].manualSelection = updatedSelection
            if updatedSelection != selectionBucket.rawValue {
                photos.remove(at: initialIndex)
                photosTotal = max(0, photosTotal - 1)
                selectedPhotoID = photos.indices.contains(initialIndex)
                    ? photos[initialIndex].id : photos.last?.id
            }
        }
        Task {
            do {
                let updated: PhotoItem = try await callDTO(
                    "decision",
                    DecisionMutationParams(
                        projectID: project.id,
                        assetUUID: photoID,
                        disposition: disposition,
                        mutationGeneration: generation
                    ),
                    as: PhotoItem.self
                )
                guard decisionGenerations[photoID] == generation else { return }
                if updated.mutationGeneration == generation {
                    if disposition == nil {
                        adjustDecisionCounts(
                            from: previousPhoto.disposition,
                            to: updated.disposition
                        )
                        adjustSelectionCounts(
                            from: previousPhoto.selection,
                            to: updated.selection
                        )
                    }
                    if let index = photos.firstIndex(where: { $0.id == photoID }) {
                        if updated.selection == selectionBucket.rawValue {
                            photos[index] = updated
                        } else {
                            photos.remove(at: index)
                            photosTotal = max(0, photosTotal - 1)
                        }
                    }
                    if recordUndo && previousManual != disposition {
                        decisionHistory.append(
                            DecisionUndo(
                                photoID: photoID,
                                previousManual: previousManual,
                                previousFinal: previousPhoto.disposition,
                                previousSelection: previousSelection,
                                changedTo: disposition,
                                changedSelection: updatedSelection
                            )
                        )
                    }
                }
                await loadQualityStatus(projectID: project.id)
            } catch {
                guard decisionGenerations[photoID] == generation else { return }
                if let disposition {
                    adjustDecisionCounts(from: disposition, to: previousPhoto.disposition)
                    adjustSelectionCounts(from: updatedSelection, to: previousSelection)
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
                _ = try await callDTO(
                    "quality_top_k",
                    QualityTopKParams(
                        projectID: project.id,
                        assetUUID: photoID,
                        selected: photo.qualityTopKRank == nil
                    ),
                    as: JSONValue.self
                )
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
                _ = try await callDTO(
                    "quality_series",
                    QualitySeriesParams(
                        projectID: project.id,
                        groupID: groupID,
                        leaderUUID: photoID
                    ),
                    as: JSONValue.self
                )
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
                _ = try await callDTO(
                    "quality_custom_series",
                    QualityCustomSeriesParams(
                        projectID: project.id,
                        memberUUIDs: members,
                        leaderUUID: leaderID
                    ),
                    as: JSONValue.self
                )
                qualitySeriesSelection.removeAll()
                await loadPhotos(projectID: project.id)
                qualityMessage = "Ручная серия сохранена; текущий кадр назначен лидером."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func movePhotoSelection(_ offset: Int) {
        guard !photos.isEmpty else { return }
        let current = selectedPhotoID.flatMap { id in photos.firstIndex(where: { $0.id == id }) } ?? 0
        let next = min(max(0, current + offset), photos.count - 1)
        selectedPhotoID = photos[next].id
        let paths = ((next - 2)...(next + 2)).compactMap { index in
            photos.indices.contains(index) ? photos[index].imagePath : nil
        }
        ThumbnailLoader.prefetch(paths: paths, maxPixelSize: 1400)
    }

    func decideSelected(_ disposition: String?) {
        guard let selectedPhotoID else { return }
        setDecision(photoID: selectedPhotoID, disposition: disposition)
    }

    func rateSelected(_ rating: Int?) {
        guard let selectedPhotoID else { return }
        setRating(photoID: selectedPhotoID, rating: rating)
    }

    func setRating(photoID: String, rating: Int?) {
        guard let project,
              rating == nil || (1...5).contains(rating!),
              let index = photos.firstIndex(where: { $0.id == photoID })
        else { return }
        let previous = photos[index].manualRating
        let generation = (ratingGenerations[photoID] ?? 0) + 1
        ratingGenerations[photoID] = generation
        photos[index].manualRating = rating
        Task {
            do {
                let updated: PhotoItem = try await callDTO(
                    "rating",
                    RatingMutationParams(
                        projectID: project.id,
                        assetUUID: photoID,
                        rating: rating
                    ),
                    as: PhotoItem.self
                )
                guard ratingGenerations[photoID] == generation else { return }
                if let current = photos.firstIndex(where: { $0.id == photoID }) {
                    photos[current] = updated
                }
            } catch {
                guard ratingGenerations[photoID] == generation else { return }
                if let current = photos.firstIndex(where: { $0.id == photoID }) {
                    photos[current].manualRating = previous
                }
                errorMessage = error.localizedDescription
            }
        }
    }

    func undoLastDecision() {
        guard let change = decisionHistory.popLast(), let project else { return }
        let generation = (decisionGenerations[change.photoID] ?? 0) + 1
        decisionGenerations[change.photoID] = generation
        Task {
            do {
                let restored: PhotoItem = try await callDTO(
                    "decision",
                    DecisionMutationParams(
                        projectID: project.id,
                        assetUUID: change.photoID,
                        disposition: change.previousManual,
                        mutationGeneration: generation
                    ),
                    as: PhotoItem.self
                )
                guard decisionGenerations[change.photoID] == generation,
                      restored.mutationGeneration == generation
                else {
                    throw NativeWorkerClientError.invalidResponse
                }
                adjustDecisionCounts(
                    from: change.changedTo,
                    to: restored.disposition ?? change.previousFinal
                )
                adjustSelectionCounts(
                    from: change.changedSelection,
                    to: restored.selection ?? change.previousSelection
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
              let photo = photos.first(where: { $0.id == selectedPhotoID }),
              let path = photo.reviewPath ?? photo.thumbnailPath
        else { return }
        QuickLookController.shared.show(path: path)
    }

    func openPhotoDetails(photoID: String) {
        selectedPhotoID = photoID
        guard let project else { return }
        Task {
            do {
                let photo: PhotoItem = try await callDTO(
                    "asset_details",
                    AssetIDParams(projectID: project.id, assetUUID: photoID),
                    as: PhotoItem.self
                )
                detailPhoto = photo
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func openGoodPhoto(photoID: String) {
        guard let project else { return }
        detailPhoto = nil
        selectionBucket = .pick
        photos = []
        galleryCursor = nil
        photosTotal = pickedTotal
        selectedPhotoID = photoID
        selectedPhotoIDs.removeAll()
        Task { await loadPhotos(projectID: project.id, focusPhotoID: photoID) }
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
        for id in ids { decisionGenerations[id] = (decisionGenerations[id] ?? 0) + 1 }
        isBusy = true
        Task {
            defer { isBusy = false }
            do {
                let response: BatchDecisionResponseDTO = try await callDTO(
                    "decisions_batch",
                    BatchDecisionParams(
                        projectID: project.id,
                        assetUUIDs: ids,
                        disposition: disposition
                    ),
                    as: BatchDecisionResponseDTO.self
                )
                applyProjectSummary(response.summary)
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
                let result: TasteRoundResponseDTO = try await callDTO(
                    "taste_round_prepare",
                    TasteRoundPrepareParams(
                        albumID: tasteSourceAlbumID,
                        mode: tasteOnboardingComplete ? "adjust" : "onboarding"
                    ),
                    as: TasteRoundResponseDTO.self,
                    progressAs: TasteProgressDTO.self
                ) { [weak self] event in
                    guard event.kind == "taste_progress" else { return }
                    Task { @MainActor [weak self] in
                        self?.tasteProgressProcessed = event.processed
                        self?.tasteProgressTotal = event.total
                    }
                }
                applyTasteProfile(result.profile)
                tasteRound = result.round
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
                let result: TasteProfileEnvelopeDTO = try await callDTO(
                    "taste_round_cancel",
                    EmptyWorkerParams(),
                    as: TasteProfileEnvelopeDTO.self
                )
                applyTasteProfile(result.profile)
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
                let result: TasteProfileEnvelopeDTO = try await callDTO(
                    "taste_round_submit",
                    TasteRoundSubmitParams(
                        roundID: round.id,
                        selectedUUIDs: selected,
                        rejectedUUIDs: rejected
                    ),
                    as: TasteProfileEnvelopeDTO.self
                )
                applyTasteProfile(result.profile)
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
        operationMessage = publishUsesIndependentCopies
            ? "Проверяем выбранные фото и готовим локальные копии…"
            : "Проверяем Pick и актуальность исходных Photos assets…"
        Task {
            defer {
                isBusy = false
                operationMessage = nil
            }
            do {
                publishPlan = try await callDTO(
                    "publish_dry_run",
                    PublishDryRunParams(projectID: project.id, kind: "best"),
                    as: PublishPlan.self
                )
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func applyPublish() {
        guard let publishPlan else { return }
        isBusy = true
        publishProcessed = 0
        publishTotal = publishPlan.itemCount
        operationMessage = publishUsesIndependentCopies
            ? "Импортируем \(publishPlan.itemCount) независимых копий в Photos…"
            : "Добавляем \(publishPlan.itemCount) существующих фото в Best‑альбом…"
        Task {
            defer {
                isBusy = false
                operationMessage = nil
            }
            do {
                let result: PublishApplyResponseDTO = try await callDTO(
                    "publish_apply",
                    PublishApplyParams(publishID: publishPlan.id, confirmed: true),
                    as: PublishApplyResponseDTO.self,
                    progressAs: PublishProgressDTO.self
                ) { [weak self] event in
                    guard event.kind == "publish_progress" else { return }
                    Task { @MainActor [weak self] in
                        self?.publishProcessed = event.processed
                        self?.publishTotal = event.total
                        self?.operationMessage = event.phase == "commit"
                            ? "PhotoKit сохраняет альбом…"
                            : "Подготовлено \(event.processed) из \(event.total) фото…"
                    }
                }
                publishMessage = result.status == "applied"
                    ? publishUsesIndependentCopies
                        ? "Готово: локальные копии сохранены в новом Best‑альбоме."
                        : "Готово: выбранные Photos assets добавлены в новый Best‑альбом без дублирования."
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
        currentStep = .album
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
        projectRequestGeneration += 1
        galleryRequestGeneration += 1
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
                _ = try await callDTO(
                    "delete_project",
                    ConfirmedProjectDeletionParams(projectID: target.id),
                    as: WorkerOperationDTO.self
                )
                projects.removeAll { $0.id == target.id }
                if project?.id == target.id {
                    pollTask?.cancel()
                    resetProject()
                    if let next = projects.first {
                        projectRequestGeneration += 1
                        galleryRequestGeneration += 1
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
            let status: WorkerStatusDTO = try await callDTO(
                "status", EmptyWorkerParams(), as: WorkerStatusDTO.self
            )
            guard status.status == "ready" else {
                throw NativeWorkerClientError.invalidResponse
            }
            workerStatus = "Локальный движок готов"
            let groups: AlbumGroupsDTO = try await callDTOWithRetry(
                "albums", as: AlbumGroupsDTO.self, attempts: 3
            )
            let taste: TasteProfileDTO = try await callDTO(
                "taste_profile", EmptyWorkerParams(), as: TasteProfileDTO.self
            )
            let retainedProjectID = UserDefaults.standard.string(
                forKey: retainedProjectDefaultsKey
            )
            projects = try await callDTO(
                "projects", EmptyWorkerParams(), as: [ProjectItem].self
            )
            albums = groups.regular
            sharedAlbums = groups.shared
            let availableIDs = Set((albums + sharedAlbums).map(\.id))
            if !availableIDs.contains(selectedAlbumID) {
                selectedAlbumID = albums.first?.id ?? sharedAlbums.first?.id ?? ""
            }
            if !availableIDs.contains(tasteSourceAlbumID) {
                tasteSourceAlbumID = (albums + sharedAlbums)
                    .filter { $0.photoCount >= 10 }
                    .max(by: { $0.photoCount < $1.photoCount })?.id ?? ""
            }
            applyTasteProfile(taste)
            if analysisMode == "codex" { refreshCodexStatus() }
            let restorable = projects.filter {
                ["created", "ready", "running", "interrupted", "error"].contains($0.state)
            }
            if let restored =
                restorable.first(where: { $0.id == retainedProjectID }) ?? restorable.first
            {
                projectRequestGeneration += 1
                galleryRequestGeneration += 1
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
            tasteStatus = "unavailable"
            workerStatus = "Ошибка: \(error.localizedDescription)"
            errorMessage = error.localizedDescription
        }
    }

    private func requestPhotoLibraryAccess() async -> Bool {
        workerStatus = "Запрашиваем доступ к Apple Photos…"
        photoAccessNeedsAction = false
        photoAccessCanRequest = false
        let status: PHAuthorizationStatus
        if PHPhotoLibrary.authorizationStatus(for: .readWrite) == .notDetermined {
            permissionHelpTask?.cancel()
            permissionHelpTask = Task {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard !Task.isCancelled else { return }
                photoAccessCanRequest = true
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
            photoAccessCanRequest = status == .notDetermined
            photoAccessNeedsAction = true
            errorMessage = "Разрешите Photo Curator доступ к Фото в Системных настройках → Конфиденциальность и безопасность → Фото."
            return false
        }
        photoAccessCanRequest = false
        photoAccessNeedsAction = false
        return true
    }

    private func startPolling(projectID: String) {
        pollTask?.cancel()
        let requestGeneration = projectRequestGeneration
        pollTask = Task {
            while !Task.isCancelled {
                do {
                    let response: ProjectResponseDTO = try await callDTO(
                        "project",
                        ProjectIDParams(projectID: projectID),
                        as: ProjectResponseDTO.self
                    )
                    guard requestGeneration == projectRequestGeneration,
                          project?.id == projectID else { return }
                    let updated = response.project
                    project = updated
                    updateProjectInList(updated)
                    jobs = response.jobs
                    applyProjectSummary(response.summary)
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
                    guard requestGeneration == projectRequestGeneration,
                          project?.id == projectID else { return }
                    operationMessage = nil
                    errorMessage = error.localizedDescription
                    return
                }
            }
        }
    }

    private func loadPhotos(
        projectID: String,
        append: Bool = false,
        focusPhotoID: String? = nil
    ) async {
        if append {
            guard !isLoadingPhotos else { return }
        } else {
            galleryRequestGeneration += 1
        }
        let requestGeneration = galleryRequestGeneration
        let requestedBucket = selectionBucket
        guard project?.id == projectID else { return }
        isLoadingPhotos = true
        defer {
            if requestGeneration == galleryRequestGeneration {
                isLoadingPhotos = false
            }
        }
        do {
            if !append { galleryCursor = nil }
            if !binaryProjects.contains(projectID) {
                let normalized: BinaryDecisionResponseDTO = try await callDTO(
                    "binary_decisions",
                    ProjectIDParams(projectID: projectID),
                    as: BinaryDecisionResponseDTO.self
                )
                guard requestGeneration == galleryRequestGeneration,
                      project?.id == projectID,
                      selectionBucket == requestedBucket else { return }
                applyProjectSummary(normalized.summary)
                binaryProjects.insert(projectID)
            }
            let response: AssetPageDTO = try await callDTO(
                "assets",
                GalleryPageParams(
                    projectID: projectID,
                    selection: selectionBucket.rawValue,
                    limit: galleryPageSize,
                    focusAssetUUID: focusPhotoID ?? "",
                    cursor: append ? galleryCursor : nil
                ),
                as: AssetPageDTO.self
            )
            guard requestGeneration == galleryRequestGeneration,
                  project?.id == projectID,
                  selectionBucket == requestedBucket else { return }
            let page = response.items
            galleryCursor = response.nextCursor
            photosTotal = response.total
            if append {
                let known = Set(photos.map(\.id))
                photos.append(contentsOf: page.filter { !known.contains($0.id) })
            } else {
                photos = page
            }
            await refreshUnavailablePreviewCount(
                requestGeneration: requestGeneration,
                projectID: projectID
            )
            if let focusPhotoID, photos.contains(where: { $0.id == focusPhotoID }) {
                selectedPhotoID = focusPhotoID
            } else if !photos.contains(where: { $0.id == selectedPhotoID }) {
                selectedPhotoID = photos.first?.id
            }
            if !append {
                UserDefaults.standard.set(projectID, forKey: retainedProjectDefaultsKey)
                selectedPhotoIDs.removeAll()
                currentStep = .selection
                await loadQualityStatus(projectID: projectID)
            }
        } catch {
            guard requestGeneration == galleryRequestGeneration,
                  project?.id == projectID else { return }
            errorMessage = error.localizedDescription
        }
    }

    func loadMorePhotos() {
        guard let project, galleryCursor != nil, photos.count < photosTotal, !isLoadingPhotos else {
            return
        }
        Task { await loadPhotos(projectID: project.id, append: true) }
    }

    private func refreshUnavailablePreviewCount(
        requestGeneration: Int,
        projectID: String
    ) async {
        let previews = photos.map {
            (path: $0.thumbnailPath ?? $0.reviewPath, analysisReady: $0.cacheState == "ready")
        }
        let unavailable = await Task.detached(priority: .utility) {
            previews.reduce(into: 0) { count, preview in
                guard preview.analysisReady, let path = preview.path else {
                    count += 1
                    return
                }
                if !FileManager.default.fileExists(atPath: path) { count += 1 }
            }
        }.value
        guard requestGeneration == galleryRequestGeneration,
              project?.id == projectID else { return }
        // Project summary contains the full-album count. Keep it when this
        // inexpensive page-level check only sees the currently loaded slice.
        unavailablePreviewFiles = max(unavailablePreviewFiles, unavailable)
    }

    func showSelection(_ bucket: SelectionBucket) {
        guard selectionBucket != bucket, let project else { return }
        selectionBucket = bucket
        photos = []
        galleryCursor = nil
        photosTotal = count(for: bucket)
        selectedPhotoID = nil
        selectedPhotoIDs.removeAll()
        Task { await loadPhotos(projectID: project.id) }
    }

    private func restoreProject(_ restored: ProjectItem) async {
        let requestGeneration = projectRequestGeneration
        do {
            let response: ProjectResponseDTO = try await callDTO(
                "project",
                ProjectIDParams(projectID: restored.id),
                as: ProjectResponseDTO.self
            )
            guard requestGeneration == projectRequestGeneration,
                  project?.id == restored.id else { return }
            let updated = response.project
            project = updated
            updateProjectInList(updated)
            jobs = response.jobs
            applyProjectSummary(response.summary)
            if updated.state == "running" {
                currentStep = .analysis
                startPolling(projectID: updated.id)
            } else if updated.state == "ready" {
                if updated.decisionModelVersion < currentDecisionModelVersion {
                    operationMessage = "Обновляем критерии отбора…"
                    _ = try await callDTO(
                        "start_analysis",
                        StartAnalysisParams(projectID: updated.id, fromStage: "decisions"),
                        as: WorkerOperationDTO.self
                    )
                    currentStep = .analysis
                    try await Task.sleep(nanoseconds: 250_000_000)
                    startPolling(projectID: updated.id)
                } else {
                    await loadPhotos(projectID: updated.id)
                }
            } else {
                currentStep = .analysis
            }
        } catch {
            guard requestGeneration == projectRequestGeneration,
                  project?.id == restored.id else { return }
            errorMessage = error.localizedDescription
        }
    }

    private func resetProject() {
        pollTask?.cancel()
        projectRequestGeneration += 1
        galleryRequestGeneration += 1
        project = nil
        jobs = []
        photos = []
        galleryCursor = nil
        photosTotal = 0
        unavailablePreviewFiles = 0
        selectionBucket = .pick
        keptTotal = 0
        pickedTotal = 0
        alternativeTotal = 0
        selectionReviewTotal = 0
        rejectedTotal = 0
        binaryProjects.removeAll()
        selectedPhotoID = nil
        selectedPhotoIDs.removeAll()
        decisionHistory = []
        decisionGenerations = [:]
        ratingGenerations = [:]
        publishPlan = nil
        publishMessage = nil
        qualitySeriesSelection.removeAll()
    }

    private func updateProjectInList(_ updated: ProjectItem) {
        projects.removeAll { $0.id == updated.id }
        projects.insert(updated, at: 0)
    }

    private func applyProjectSummary(_ summary: ProjectSummaryDTO?) {
        guard let summary else { return }
        keptTotal = summary.keep ?? keptTotal
        pickedTotal = summary.pick ?? pickedTotal
        alternativeTotal = summary.alternative ?? alternativeTotal
        selectionReviewTotal = summary.selectionReview ?? selectionReviewTotal
        rejectedTotal = summary.selectionReject ?? summary.reject ?? rejectedTotal
        totalAssets = summary.total ?? totalAssets
        videosSkipped = summary.videosSkipped ?? videosSkipped
        previewReady = summary.ready ?? previewReady
        previewMissing = summary.missing ?? previewMissing
        appleVisionReady = summary.appleVisionReady ?? appleVisionReady
        appleVisionErrors = summary.appleVisionError ?? appleVisionErrors
        codexReady = summary.codexReady ?? codexReady
        codexErrors = summary.codexError ?? codexErrors
        unavailablePreviewFiles = summary.unavailablePreviewFiles ?? unavailablePreviewFiles
    }

    private func adjustDecisionCounts(from previous: String?, to updated: String?) {
        guard previous != updated else { return }
        switch previous {
        case "keep": keptTotal = max(0, keptTotal - 1)
        default: break
        }
        switch updated {
        case "keep": keptTotal += 1
        default: break
        }
    }

    func count(for bucket: SelectionBucket) -> Int {
        switch bucket {
        case .pick: return pickedTotal
        case .alternative: return alternativeTotal
        case .review: return selectionReviewTotal
        case .reject: return rejectedTotal
        }
    }

    private func adjustSelectionCounts(from previous: String?, to updated: String?) {
        guard previous != updated else { return }
        func adjust(_ value: String?, delta: Int) {
            switch value {
            case "pick": pickedTotal = max(0, pickedTotal + delta)
            case "alternative": alternativeTotal = max(0, alternativeTotal + delta)
            case "review": selectionReviewTotal = max(0, selectionReviewTotal + delta)
            case "reject": rejectedTotal = max(0, rejectedTotal + delta)
            default: break
            }
        }
        adjust(previous, delta: -1)
        adjust(updated, delta: 1)
    }

    private func applyTasteProfile(_ value: TasteProfileDTO) {
        tasteExamples = value.preferenceCount
        tasteCalibrationExamples = value.calibrationCount
        tasteHeldOutExamples = value.heldOutCount
        tasteRoundsCompleted = value.onboardingRoundsCompleted
        tasteRoundsTotal = value.onboardingRoundsTotal
        tasteOnboardingComplete = value.onboardingComplete
        tasteStatus = value.status
    }

    func loadQualityStatus(projectID: String) async {
        do {
            let value: QualityStatusDTO = try await callDTO(
                "quality_status",
                ProjectIDParams(projectID: projectID),
                as: QualityStatusDTO.self
            )
            qualityManualLabels = value.manualLabels
            qualityHeldOutPairs = value.heldOutPairs
            qualityTopKCount = value.expectedTopK
            qualitySeriesCount = value.humanDuplicateGroups
            qualityDefectLabels = value.defectLabels
            qualityReleaseReady = value.releaseReady
        } catch { errorMessage = error.localizedDescription }
    }

    private func refreshDecisionsForTaste() async {
        guard let project, project.state == "ready" else { return }
        do {
            _ = try await callDTO(
                "start_analysis",
                StartAnalysisParams(projectID: project.id, fromStage: "decisions"),
                as: WorkerOperationDTO.self
            )
            startPolling(projectID: project.id)
        } catch { errorMessage = error.localizedDescription }
    }

    func callDTO<Params: Encodable, Response: Decodable>(
        _ method: String,
        _ params: Params,
        as responseType: Response.Type
    ) async throws -> Response {
        let worker = worker
        return try await Task.detached(priority: .userInitiated) {
            try worker.request(method: method, params: params, as: responseType)
        }.value
    }

    private func callDTO<Params: Encodable, Response: Decodable, Event: Decodable>(
        _ method: String,
        _ params: Params,
        as responseType: Response.Type,
        progressAs eventType: Event.Type,
        progress: @escaping @Sendable (Event) -> Void
    ) async throws -> Response {
        let worker = worker
        return try await Task.detached(priority: .userInitiated) {
            try worker.request(
                method: method,
                params: params,
                as: responseType,
                progressAs: eventType,
                progress: progress
            )
        }.value
    }

    private func callDTOWithRetry<Response: Decodable>(
        _ method: String,
        as responseType: Response.Type,
        attempts: Int
    ) async throws -> Response {
        var lastError: Error = NativeWorkerClientError.invalidResponse
        for attempt in 1...attempts {
            do {
                return try await callDTO(method, EmptyWorkerParams(), as: responseType)
            } catch {
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
            "models": "NIMA, MobileCLIP и MUSIQ оценивают кадры",
            "codex": "Codex понимает сюжет и сравнивает серии",
            "scene_shadow": "Engine v3 строит эпизоды и сцены",
            "decisions": "Формируем подборку",
        ][stage] ?? "Анализируем"
    }
}

private struct DecisionUndo {
    let photoID: String
    let previousManual: String?
    let previousFinal: String?
    let previousSelection: String?
    let changedTo: String?
    let changedSelection: String?
}
