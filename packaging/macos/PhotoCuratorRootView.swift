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
        .sheet(item: $model.photoDeletionPlan) { plan in
            PhotoDeletionSheet(plan: plan).environmentObject(model)
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
                "Исходный альбом Photos не изменится. Будут удалены только результаты этого анализа и его локальный кэш. Ваши оценки сохранятся; для старого анализа может потребоваться отдельный архив с изображениями, который продолжит занимать место на диске."
            )
        }
        .sheet(item: $model.detailPhoto) { photo in
            PhotoDetailView(photo: photo, openGoodPhoto: model.openGoodPhoto)
        }
        .sheet(isPresented: $model.tasteEditorPresented) {
            BlindTasteView()
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


    private var sourceSection: some View {
        StepCard(number: 2, title: "Выберите альбом", symbol: "photo.on.rectangle.angled") {
            if model.albums.isEmpty && model.sharedAlbums.isEmpty {
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
                    if !model.albums.isEmpty {
                        Section("Мои альбомы") {
                            ForEach(model.albums) { album in
                                Text("\(album.name) · \(album.photoCount) фото").tag(album.id)
                            }
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

                Divider()
                Text("Исходный альбом анализируется целиком; размер подборки выбирать не нужно.")
                    .font(.caption).foregroundStyle(.secondary)
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
                            Text(jobProgressText(job))
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
                .disabled(model.isBusy || (model.selectedAlbumID.isEmpty && model.project?.state != "created") || model.project?.state == "running")
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

    private func jobProgressText(_ job: JobItem) -> String {
        var parts = [job.total > 0 ? "\(job.processed) / \(job.total)" : job.status]
        if job.warnings > 0 { parts.append("предупреждений: \(job.warnings)") }
        if job.errors > 0 { parts.append("ошибок: \(job.errors)") }
        return parts.joined(separator: " · ")
    }

    private var reviewSection: some View {
        ScrollViewReader { galleryProxy in
            VStack(spacing: 0) {
                galleryToolbar
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    .background(.regularMaterial)
                Divider()
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
                    Group {
                        Button { model.preparePhotoDeletion() } label: {
                            Label(!model.selectedPhotoIDs.isEmpty ? "Удалить выбранные (\(model.selectedPhotoIDs.count))…" : model.selectionBucket == .reject ? "Проверить удаление \(model.cullingRejectedTotal) фото…" : "Удалить фото…", systemImage: "trash")
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.red)
                        .disabled(model.isBusy || (model.selectedPhotoIDs.isEmpty && (model.selectionBucket == .reject ? model.cullingRejectedTotal == 0 : model.selectedPhotoID == nil)))
                    }

                }

                Text(model.project?.sourceAlbumShared == true ? "Общий альбом: подтверждённое удаление публикаций затронет всех участников. Личные оригиналы и сохранённые копии останутся." : "К удалению — предложение для вашей проверки. Фотографии удаляются только после отдельного подтверждения.")
                    .font(.callout).foregroundStyle(.secondary)
                if let message = model.deletionMessage { Text(message).foregroundStyle(.secondary) }
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
                                ? "Оставить" : "Пометить к удалению"
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

                if model.workspaceMode == .grid {
                    LazyVGrid(
                        columns: [
                            GridItem(
                                .adaptive(
                                    minimum: galleryCardWidth,
                                    maximum: galleryCardWidth
                                ),
                                spacing: 4,
                                alignment: .top
                            )
                        ],
                        alignment: .leading,
                        spacing: 4
                    ) {
                        ForEach(visibleGridPhotos) { photo in
                            PhotoCard(
                                photo: photo,
                                cardWidth: galleryCardWidth,
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
                                    model.toggleSeriesExpansion(groupID: group)
                                },
                                rate: { model.setRating(photoID: photo.id, rating: $0) },
                                deleteFromLibrary: { model.preparePhotoDeletion(assetIDs: [photo.id]) }
                            ) { disposition in
                                model.setDecision(photoID: photo.id, disposition: disposition)
                            }
                            .equatable()
                            .id(photo.id)
                        }
                        if model.canLoadMorePhotos,
                           !model.isLoadingPhotos,
                           model.galleryLoadError == nil {
                            Color.clear
                                .frame(maxWidth: .infinity, minHeight: 1, maxHeight: 1)
                                .id("gallery-load-sentinel-\(model.photos.count)")
                                .accessibilityHidden(true)
                                .onAppear { model.loadMorePhotosAutomatically() }
                        }
                    }
                } else {
                    WorkspaceCanvas(
                        mode: model.workspaceMode,
                        photos: model.workspacePhotos,
                        selectedID: model.selectedPhotoID,
                        multiSelectedIDs: model.selectedPhotoIDs,
                        select: { model.selectPhoto(photoID: $0) },
                        decide: { photoID, disposition in
                            model.setDecision(photoID: photoID, disposition: disposition)
                        },
                        markAlternative: { photoID in
                            model.setSelection(photoID: photoID, selection: "alternative")
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
                if model.photos.count < model.photosTotal,
                   let galleryLoadError = model.galleryLoadError {
                    VStack(spacing: 8) {
                        Text("Не удалось автоматически загрузить продолжение")
                            .font(.subheadline.weight(.semibold))
                        Text(galleryLoadError)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                        Button("Повторить загрузку") { model.retryGalleryLoad() }
                            .buttonStyle(.bordered)
                    }
                    .frame(maxWidth: .infinity, minHeight: 64)
                } else if model.isLoadingPhotos, !model.photos.isEmpty {
                    ProgressView("Загружаем следующие фотографии…")
                        .controlSize(.small)
                        .frame(maxWidth: .infinity, minHeight: 64)
                }
                Text(
                    "Загружено \(model.photos.count) из \(model.photosTotal) · "
                        + "остальные фотографии появятся автоматически"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                    }
                    .padding(20)
                    .frame(maxWidth: 1480, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .top)
                }
            }
            .onChange(of: model.selectedPhotoID) { photoID in
                guard model.workspaceMode == .grid, let photoID else { return }
                galleryProxy.scrollTo(photoID, anchor: .center)
            }
        }
    }

    private var galleryToolbar: some View {
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
    }

    private var visibleGridPhotos: [PhotoItem] {
        guard collapseSeries else { return model.photos }
        var visible: [PhotoItem] = []
        var emittedGroups = Set<String>()
        for photo in model.photos {
            guard let groupID = photo.duplicateGroup else {
                visible.append(photo)
                continue
            }
            guard emittedGroups.insert(groupID).inserted else { continue }
            if model.expandedSeriesIDs.contains(groupID) {
                visible.append(contentsOf: model.seriesMembers(groupID: groupID) ?? [photo])
            } else {
                visible.append(photo)
            }
        }
        return visible
    }

    private func stackCount(for photo: PhotoItem) -> Int {
        guard collapseSeries, photo.duplicateGroup != nil else { return 0 }
        return photo.duplicateMemberCount
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
                    title: "1. Выберите альбом",
                    detail: "Начните с обычного или общего альбома — движок подготовит первый отбор."
                )
                OnboardingPoint(
                    symbol: "photo.on.rectangle.angled",
                    title: "2. Покажите свой вкус",
                    detail: "После анализа сравните до 12 пар. Равноценные кадры можно пропустить."
                )
                OnboardingPoint(
                    symbol: "sparkles",
                    title: "3. Уберите неудачные кадры",
                    detail: "Проверьте предложенные неудачные кадры и повторы. Удаление всегда подтверждаете вы."
                )
            }
            .frame(maxWidth: 620, alignment: .leading)
            Button(action: continueAction) {
                Label("Выбрать альбом", systemImage: "arrow.right")
                    .frame(minWidth: 300)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            Text("Для чтения альбомов разрешите доступ к Фото. Локальный анализ не отправляет фотографии в интернет.")
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
