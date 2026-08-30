import AppKit
import Foundation
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var analysisDetailsExpanded = true
    @State private var processingDetailsExpanded = false
    @State private var confirmsAnalysisStop = false
    @State private var projectPendingDeletion: ProjectItem?
    @State private var galleryCardWidth: CGFloat = selectionCardDefaultWidth
    @State private var collapseSeries = true
    @State private var expandedSeriesIDs: Set<String> = []

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
                model.confirmCodexAnalysis()
            }
            Button("Отмена", role: .cancel) { model.codexConsentPending = false }
        } message: {
            Text(
                "Review‑копии фотографий будут отправлены в OpenAI через ваш локальный Codex. Прогон расходует лимиты подписки ChatGPT/Codex и может занять несколько часов. API key не используется. Исходный альбом Photos не изменяется."
            )
        }
        .sheet(
            isPresented: Binding(
                get: { model.publishPlan != nil },
                set: { if !$0 { model.publishPlan = nil } }
            )
        ) {
            if let plan = model.publishPlan {
                PublishPreviewSheet(
                    plan: plan,
                    independentCopies: model.publishUsesIndependentCopies,
                    confirm: model.applyPublish,
                    cancel: { model.publishPlan = nil }
                )
            }
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
            PhotoDetailView(photo: photo, openGoodPhoto: model.openGoodPhoto)
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
            if model.albums.isEmpty {
                if model.photoAccessNeedsAction {
                    Label(
                        "Разрешите доступ в системном запросе или настройках macOS.",
                        systemImage: "photo.badge.exclamationmark"
                    )
                    .foregroundStyle(.secondary)
                    if model.photoAccessCanRequest {
                        Button("Разрешить доступ к Фото") {
                            model.requestPhotoLibraryAccessFromUI()
                        }
                        .buttonStyle(.borderedProminent)
                    }
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
            if model.tasteProfileApplies {
                Label(
                    "Профиль вкуса активен: \(model.tasteCalibrationExamples) обучающих и \(model.tasteHeldOutExamples) проверочных сравнений.",
                    systemImage: "checkmark.circle.fill"
                )
                .font(.caption)
                .foregroundStyle(.green)
            } else if model.tasteProfileLoading {
                Label("Проверяем профиль вкуса…", systemImage: "heart.text.square")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Label(
                    "Персональный вкус пока не настроен: анализ можно запустить и без него, а профиль добавить позже.",
                    systemImage: "heart.text.square"
                )
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
                    Button("Настроить вкус") { model.openTasteEditor() }
                }
                Spacer()
                Button("Начать анализ") {
                    model.requestAnalysis()
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    model.selectedAlbumID.isEmpty
                        || model.isBusy
                )
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
            if model.project?.state == "interrupted" || model.project?.state == "error" {
                Label(
                    model.project?.state == "error"
                        ? "Один из этапов завершился с ошибкой. Готовые этапы и изображения сохранены; можно повторить только проблемный этап."
                        : "Анализ остановлен. Готовые этапы и изображения сохранены в локальном кэше; продолжение не начнёт их заново.",
                    systemImage: "externaldrive.badge.checkmark"
                )
                .font(.subheadline)
                .foregroundStyle(.secondary)
                Button {
                    model.resumeAnalysis()
                } label: {
                    Label(
                        model.project?.state == "error"
                            ? "Повторить проблемный этап"
                            : "Продолжить с прерванного этапа",
                        systemImage: "play.fill"
                    )
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
                                    count: model.count(for: bucket),
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
                    .disabled(model.isBusy || model.pickedTotal == 0)
                }

                DisclosureGroup(isExpanded: $processingDetailsExpanded) {
                    VStack(alignment: .leading, spacing: 8) {
                        ProcessingStatRow(
                            title: "Фото в альбоме",
                            detail: "\(model.totalAssets)",
                            symbol: "photo.on.rectangle"
                        )
                        if model.videosSkipped > 0 {
                            ProcessingStatRow(
                                title: "Видео пропущено",
                                detail: "\(model.videosSkipped)",
                                symbol: "video.slash"
                            )
                        }
                        ProcessingStatRow(
                            title: "Локальные review-копии",
                            detail: "\(model.previewReady) готовы · \(model.previewMissing) недоступны",
                            symbol: "internaldrive"
                        )
                        ProcessingStatRow(
                            title: "Apple Vision",
                            detail: "\(model.appleVisionReady) обработано · \(model.appleVisionErrors) ошибок",
                            symbol: "eye"
                        )
                        if model.project?.analysisMode == "codex" {
                            ProcessingStatRow(
                                title: "Codex Vision",
                                detail: "\(model.codexReady) обработано · \(model.codexErrors) ошибок",
                                symbol: "sparkles"
                            )
                        }
                        if model.previewMissing > 0 {
                            Text("Недоступные preview-копии не передаются в Apple Vision или Codex. Откройте нужные фото в Photos, чтобы iCloud завершил загрузку, затем повторите анализ.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.top, 6)
                } label: {
                    Label("Статус обработки", systemImage: "chart.bar.doc.horizontal")
                        .font(.subheadline.weight(.semibold))
                }
                .padding(10)
                .background(.quaternary.opacity(0.32), in: RoundedRectangle(cornerRadius: 10))

                if model.unavailablePreviewFiles > 0 {
                    HStack(spacing: 12) {
                        Label(
                            "Превью требуют восстановления: \(model.unavailablePreviewFiles) на загруженной странице",
                            systemImage: "photo.badge.exclamationmark"
                        )
                        .font(.subheadline.weight(.semibold))
                        Spacer()
                        Button("Восстановить превью") { model.repairUnavailablePreviews() }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.isBusy)
                    }
                    .padding(10)
                    .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
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
                            model.selectionBucket == .reject
                                ? "Вернуть в Best" : "Переместить в отклонённые"
                        ) {
                            model.setSelectedPhotosDecision(
                                model.selectionBucket == .reject ? "keep" : "reject"
                            )
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isBusy)
                    }
                    .padding(.vertical, 6)
                }
                if model.qualityToolsEnabled, !model.qualitySeriesSelection.isEmpty {
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

                HStack(spacing: 10) {
                    Picker("Режим", selection: $model.workspaceMode) {
                        ForEach(WorkspaceMode.allCases) { mode in
                            Label(mode.title, systemImage: mode.symbol).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 430)
                    Divider().frame(height: 24)
                    Toggle(isOn: $collapseSeries) {
                        Label("Стеки серий", systemImage: "square.stack.3d.up")
                    }
                    .toggleStyle(.button)
                    .disabled(model.workspaceMode != .grid)
                    Label("Размер фото", systemImage: "rectangle.grid.3x2")
                        .font(.subheadline)
                    Button {
                        galleryCardWidth = max(
                            selectionCardMinimumWidth,
                            galleryCardWidth - selectionCardZoomStep
                        )
                    } label: {
                        Image(systemName: "minus.magnifyingglass")
                    }
                    .help("Уменьшить фотографии")
                    .accessibilityLabel("Уменьшить фотографии")
                    .disabled(galleryCardWidth <= selectionCardMinimumWidth)

                    Slider(
                        value: $galleryCardWidth,
                        in: selectionCardMinimumWidth...selectionCardMaximumWidth,
                        step: selectionCardZoomStep
                    )
                    .frame(width: 150)
                    .accessibilityLabel("Размер фотографий в галерее")

                    Button {
                        galleryCardWidth = min(
                            selectionCardMaximumWidth,
                            galleryCardWidth + selectionCardZoomStep
                        )
                    } label: {
                        Image(systemName: "plus.magnifyingglass")
                    }
                    .help("Увеличить фотографии")
                    .accessibilityLabel("Увеличить фотографии")
                    .disabled(galleryCardWidth >= selectionCardMaximumWidth)

                    Text("\(Int(galleryCardWidth)) pt")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Spacer()
                }

                if model.workspaceMode == .grid {
                    LazyVGrid(
                    columns: [
                        GridItem(
                            .adaptive(
                                minimum: galleryCardWidth,
                                maximum: galleryCardWidth
                            ),
                            spacing: 4
                        )
                    ],
                    alignment: .leading,
                    spacing: 4
                    ) {
                        ForEach(visibleGridPhotos) { photo in
                        PhotoCard(
                            photo: photo,
                            selected: model.selectedPhotoID == photo.id,
                            multiSelected: model.selectedPhotoIDs.contains(photo.id),
                            developerToolsEnabled: model.qualityToolsEnabled,
                            select: { model.selectPhoto(photoID: photo.id) },
                            toggleMultiSelection: {
                                model.togglePhotoSelection(photoID: photo.id)
                            },
                            preview: {
                                model.selectPhoto(photoID: photo.id)
                                model.previewSelected()
                            },
                            openDetails: { model.openPhotoDetails(photoID: photo.id) },
                            openGoodPhoto: model.openGoodPhoto,
                            seriesSelected: model.qualitySeriesSelection.contains(photo.id),
                            toggleTopK: { model.toggleQualityTopK(photoID: photo.id) },
                            toggleSeriesSelection: {
                                model.toggleQualitySeriesSelection(photoID: photo.id)
                            },
                            labelSeriesLeader: { model.labelSeriesLeader(photoID: photo.id) },
                            stackCount: stackCount(for: photo),
                            toggleStack: {
                                guard let group = photo.duplicateGroup else { return }
                                if expandedSeriesIDs.contains(group) {
                                    expandedSeriesIDs.remove(group)
                                } else {
                                    expandedSeriesIDs.insert(group)
                                }
                            },
                            rate: { model.setRating(photoID: photo.id, rating: $0) }
                        ) { disposition in
                            model.setDecision(photoID: photo.id, disposition: disposition)
                        }
                        .equatable()
                        }
                    }
                } else {
                    WorkspaceCanvas(
                        mode: model.workspaceMode,
                        photos: model.photos,
                        selectedID: model.selectedPhotoID,
                        multiSelectedIDs: model.selectedPhotoIDs,
                        select: { model.selectPhoto(photoID: $0) },
                        decide: { photoID, disposition in
                            model.setDecision(photoID: photoID, disposition: disposition)
                        },
                        rate: { photoID, rating in
                            model.setRating(photoID: photoID, rating: rating)
                        }
                    )
                }
                if model.photos.isEmpty, !model.isLoadingPhotos {
                    VStack(spacing: 10) {
                        Image(systemName: model.selectionBucket.symbol)
                        .font(.system(size: 34))
                        .foregroundStyle(.secondary)
                        Text("Нет фото в разделе «\(model.selectionBucket.title)»")
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

    private var visibleGridPhotos: [PhotoItem] {
        guard collapseSeries else { return model.photos }
        var seen = Set<String>()
        return model.photos.filter { photo in
            guard let group = photo.duplicateGroup, !expandedSeriesIDs.contains(group) else {
                return true
            }
            return seen.insert(group).inserted
        }
    }

    private func stackCount(for photo: PhotoItem) -> Int {
        guard collapseSeries, photo.duplicateGroup != nil else { return 0 }
        return photo.duplicateMemberCount
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

private struct ProcessingStatRow: View {
    let title: String
    let detail: String
    let symbol: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol)
                .foregroundStyle(.secondary)
                .frame(width: 16)
            Text(title)
            Spacer()
            Text(detail)
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
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
        VStack(spacing: 6) {
            HStack(spacing: 7) {
                Label(
                    bucket.title,
                    systemImage: bucket.symbol
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
