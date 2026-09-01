import SwiftUI

struct QualityWizardView: View {
    @EnvironmentObject private var model: AppModel
    @State private var step = 0
    @State private var sampleSize = 75
    @State private var defectCodes: Set<String> = []
    @State private var defectSeverity = 2
    @State private var defectConfidence = 0.85
    @State private var defectNote = ""
    @State private var seriesSelection: Set<String> = []
    @State private var seriesLeader: String?
    @State private var seriesTargetBudget = 1
    @State private var seriesEssential: Set<String> = []
    @State private var seriesRedundantGood: Set<String> = []
    @State private var seriesLeaderReasons: Set<String> = []

    private let steps = [
        ("Источник", "folder"),
        ("Слепая разметка", "eye.slash"),
        ("A/B", "rectangle.split.2x1"),
        ("Top‑K", "star"),
        ("Серия", "square.stack.3d.up"),
        ("Итог", "checklist"),
    ]

    private let defectOptions = [
        ("motion_blur", "Смаз движения"),
        ("defocus_blur", "Не в фокусе"),
        ("underexposed", "Слишком темно"),
        ("overexposed", "Пересвет"),
        ("low_contrast", "Низкий контраст"),
        ("poor_face_capture", "Неудачное лицо"),
        ("extreme_horizon", "Завален горизонт"),
        ("bad_angle", "Плохой ракурс"),
        ("blocked_subject", "Объект перекрыт"),
        ("exact_duplicate", "Точный дубль"),
        ("near_duplicate", "Слабый кадр серии"),
        ("other", "Другое"),
    ]

    private let seriesReasonOptions = [
        ("expression", "Выражение"),
        ("sharpness", "Резкость"),
        ("composition", "Композиция"),
        ("subject_visibility", "Объект виден"),
        ("moment", "Момент"),
        ("viewpoint", "Ракурс"),
        ("coverage", "Покрытие сцены"),
        ("other", "Другое"),
    ]

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            Group {
                switch step {
                case 0: sourceStep
                case 1: labelsStep
                case 2: pairStep
                case 3: topKStep
                case 4: seriesStep
                default: summaryStep
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 940, minHeight: 680)
        .onAppear {
            model.qualityLabEnabled = true
            model.qualityWizardActive = true
        }
        .onDisappear { model.qualityWizardActive = false }
        .onChange(of: model.qualityCandidateIndex) { _ in resetDefectForm() }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Quality Lab").font(.title2.bold())
                Spacer()
                if model.isQualityBusy { ProgressView().controlSize(.small) }
            }
            .padding(.bottom, 8)
            ForEach(Array(steps.enumerated()), id: \.offset) { index, item in
                Button {
                    openStep(index)
                } label: {
                    Label(item.0, systemImage: item.1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.plain)
                .foregroundStyle(step == index ? Color.accentColor : .primary)
                .disabled(index > 0 && model.project?.state != "ready")
            }
            Spacer()
            Text("Только локальная разметка. Photos и исходный альбом не изменяются.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(18)
        .frame(width: 210)
        .background(.quaternary.opacity(0.25))
    }

    private var sourceStep: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                wizardTitle("1. Выберите проверяемый анализ", "Мастер размечает конкретный альбом. Если анализа нет, создайте его здесь.")
                if let project = model.project, !model.analysisDraftActive {
                    GroupBox("Текущий анализ") {
                        VStack(alignment: .leading, spacing: 8) {
                            LabeledContent("Альбом", value: project.albumName)
                            LabeledContent("Состояние", value: project.state == "ready" ? "Готов" : project.state)
                            if project.state == "running" {
                                ProgressView(value: model.progress)
                                Text(model.progressMessage).foregroundStyle(.secondary)
                            } else if project.state == "interrupted" || project.state == "error" {
                                Button("Продолжить анализ") { model.resumeAnalysis() }
                            }
                        }.padding(8)
                    }
                    if project.state == "ready" {
                        if model.unavailablePreviewFiles > 0 {
                            Label(
                                "Локальные изображения отсутствуют: \(model.unavailablePreviewFiles). Это может произойти после очистки системного кэша.",
                                systemImage: "photo.badge.exclamationmark"
                            )
                            .foregroundStyle(.orange)
                            Button("Восстановить изображения") {
                                model.repairUnavailablePreviews()
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.isBusy)
                        } else {
                            sampleControls
                            Button("Начать слепую разметку") { openStep(1) }
                                .buttonStyle(.borderedProminent)
                        }
                    }
                    if project.state != "running" {
                        Button("Выбрать другой альбом") { model.beginNewAnalysis() }
                    }
                } else {
                    GroupBox("Новый локальный анализ") {
                        VStack(alignment: .leading, spacing: 12) {
                            if qualitySourceAlbums.isEmpty {
                                Label(
                                    "Сначала разрешите доступ к Photos — после этого здесь появятся альбомы.",
                                    systemImage: "photo.badge.exclamationmark"
                                )
                                .foregroundStyle(.secondary)
                            } else {
                                Picker("Альбом", selection: $model.selectedAlbumID) {
                                    Text("Выберите альбом").tag("")
                                    if selectedAlbumIsTemporarilyUnavailable {
                                        Text("Ранее выбранный альбом недоступен")
                                            .tag(model.selectedAlbumID)
                                    }
                                    if !model.albums.isEmpty {
                                        Section("Мои альбомы") {
                                            ForEach(model.albums) { album in
                                                Text("\(album.name) · \(album.photoCount) фото")
                                                    .tag(album.id)
                                            }
                                        }
                                    }
                                    if !model.sharedAlbums.isEmpty {
                                        Section("Общие альбомы") {
                                            ForEach(model.sharedAlbums) { album in
                                                Text("\(album.name) · \(album.photoCount) фото")
                                                    .tag(album.id)
                                            }
                                        }
                                    }
                                }
                                .pickerStyle(.menu)
                            }
                            Text("Личные и общие альбомы только читаются через PhotoKit. Результаты сохраняются в локальном проекте приложения.")
                                .font(.caption).foregroundStyle(.secondary)
                            Button("Создать и проанализировать") {
                                model.analysisMode = "local"
                                model.createAndAnalyze()
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(!selectedAlbumIsAvailable || model.isBusy)
                        }.padding(8)
                    }
                }
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .leading)
        }
    }

    private var sampleControls: some View {
        GroupBox("Размер проверочной выборки") {
            VStack(alignment: .leading, spacing: 10) {
                Picker("Фото", selection: $sampleSize) {
                    Text("50").tag(50)
                    Text("75 — рекомендуется").tag(75)
                    Text("100").tag(100)
                }.pickerStyle(.segmented)
                Text("Выборка фиксирована для проекта и не зависит от прогнозов движка.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(8)
        }
    }

    private var labelsStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            wizardTitle("2. Слепая разметка", "Оцените 50–100 фото. Оценка движка, причины и его решение здесь намеренно скрыты.")
            HStack {
                Text("Размечено \(model.qualityCandidateLabelled) из \(model.qualityCandidateAvailable)")
                Spacer()
                Picker("Выборка", selection: $sampleSize) {
                    Text("50").tag(50); Text("75").tag(75); Text("100").tag(100)
                }
                .frame(width: 190)
                .onChange(of: sampleSize) { value in model.loadQualityCandidates(limit: value) }
            }
            if let photo = currentCandidate {
                HStack(alignment: .top, spacing: 18) {
                    qualityImage(photo, height: 500)
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            Text(photo.filename).font(.headline).lineLimit(2)
                            Text("Если кадр плохой, отметьте конкретные причины.")
                                .font(.caption).foregroundStyle(.secondary)
                            defectPicker
                            Picker("Серьёзность", selection: $defectSeverity) {
                                Text("Небольшая").tag(1); Text("Заметная").tag(2); Text("Критичная").tag(3)
                            }.pickerStyle(.segmented)
                            Picker("Уверенность", selection: $defectConfidence) {
                                Text("Скорее да").tag(0.60); Text("Уверен").tag(0.85); Text("Очевидно").tag(1.0)
                            }.pickerStyle(.segmented)
                            TextField("Комментарий — необязательно", text: $defectNote)
                            Divider()
                            Button("Хорошее — оставить") { saveLabel(photo, "keep") }
                                .buttonStyle(.borderedProminent).tint(.green)
                                .keyboardShortcut(.rightArrow, modifiers: [])
                                .disabled(model.isQualityBusy)
                            Button("Сомнительное — проверить") { saveLabel(photo, "review") }
                                .buttonStyle(.bordered)
                                .disabled(model.isQualityBusy)
                            Button("Плохое — отклонить") { saveReject(photo) }
                                .buttonStyle(.borderedProminent).tint(.red)
                                .keyboardShortcut(.leftArrow, modifiers: [])
                                .disabled(model.isQualityBusy)
                            if defectCodes.isEmpty {
                                Text("→ хорошее · ← плохое. Без выбранной причины ← сохранит «Другое».")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }.frame(width: 330)
                }
            } else {
                if !model.qualityCandidates.isEmpty
                    && model.qualityCandidates.allSatisfy({ $0.qualityDisposition != nil })
                {
                    unavailable(
                        "Слепая разметка завершена",
                        symbol: "checkmark.seal.fill",
                        detail: "Все кадры получили ручное решение. Переходите к A/B-сравнениям."
                    )
                    Button("Перейти к A/B") { openStep(2) }
                        .buttonStyle(.borderedProminent)
                } else {
                    unavailable(
                        "Нет готовых кадров",
                        symbol: "photo.badge.exclamationmark",
                        detail: model.unavailablePreviewFiles > 0
                            ? "Локальные изображения были очищены. Вернитесь на шаг «Источник» и восстановите их."
                            : "Завершите анализ или выберите другой альбом."
                    )
                }
            }
        }
        .padding(24)
    }

    private var pairStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            wizardTitle("3. Проверочные A/B-сравнения", "Выберите более удачный кадр в 10 независимых парах. Эти ответы не обучают профиль вкуса.")
            Text("Готово: \(model.qualityPairCompleted) / 10+")
            if let pair = model.qualityPair {
                HStack(spacing: 14) {
                    pairCard(pair.left)
                    pairCard(pair.right)
                }
            } else if model.qualityPairCompleted >= 10 {
                unavailable(
                    "Минимум выполнен",
                    symbol: "checkmark.seal",
                    detail: "Можно продолжить или собрать дополнительные сравнения."
                )
            } else if model.qualityPairEligible < 2 {
                unavailable(
                    "Сначала завершите слепую разметку",
                    symbol: "eye.slash",
                    detail: "A/B-пары строятся только из вручную размеченной выборки шага 2."
                )
            } else {
                Button("Показать первую пару") { model.loadQualityPair() }
                    .buttonStyle(.borderedProminent)
            }
            Spacer()
        }.padding(24)
    }

    private var topKStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            wizardTitle("4. Выберите Top‑K", "Отметьте минимум пять лучших кадров в порядке выбора.")
            Text("Выбрано: \(model.qualityTopKCount) / 5+")
            qualityGrid { photo in
                Button { model.toggleWizardQualityTopK(photoID: photo.id) } label: {
                    wizardGridCard(
                        photo,
                        badge: photo.qualityDisposition == nil
                            ? "Сначала шаг 2" : photo.qualityTopKRank.map { "№\($0)" }
                    )
                }
                .buttonStyle(.plain)
                .disabled(photo.qualityDisposition == nil)
            }
        }.padding(24)
    }

    private var seriesStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            wizardTitle(
                "5. Разберите одну связанную серию",
                "Здесь показаны только кадры одной сцены или момента — не весь альбом. Выберите основной кадр и сколько разных удачных вариантов действительно стоит оставить."
            )
            if model.qualitySeriesCandidates.isEmpty {
                unavailable(
                    "Подходящая серия не найдена",
                    symbol: "square.stack.3d.up.slash",
                    detail: "В этом альбоме нет полной связной серии из 2–12 доступных фотографий. Не объединяйте несравнимые кадры вручную."
                )
            } else if model.qualitySeriesCandidates.contains(where: {
                $0.qualityDisposition == nil
            }) {
                unavailable(
                    "Сначала оцените кадры этой серии",
                    symbol: "eye.slash",
                    detail: "Мастер добавил целую серию в слепую выборку. Завершите оставшиеся ручные решения шага 2, затем вернитесь сюда."
                )
                Button("Вернуться к слепой разметке") { openStep(1) }
                    .buttonStyle(.borderedProminent)
            } else {
                HStack {
                    Text("Кадров в серии: \(seriesSelection.count)")
                    Text("Основной: \(seriesLeader.flatMap(nameForPhoto) ?? "не выбран")")
                    Stepper(
                        "Оставить: \(seriesTargetBudget)",
                        value: $seriesTargetBudget,
                        in: 1...max(1, seriesSelection.count)
                    )
                    .frame(width: 150)
                    Spacer()
                    Button("Сохранить разбор серии") {
                        guard let seriesLeader else { return }
                        model.saveWizardQualitySeries(
                            memberIDs: seriesSelection.sorted(),
                            leaderID: seriesLeader,
                            targetBudget: seriesTargetBudget,
                            essentialMemberIDs: seriesEssential.sorted(),
                            redundantGoodMemberIDs: seriesRedundantGood.sorted(),
                            leaderReasonCodes: seriesLeaderReasons.sorted(),
                            sourceGroupID: model.qualitySeriesCandidateGroupID
                        )
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(
                        seriesSelection.count < 2 || seriesLeader == nil
                            || seriesEssential.count > seriesTargetBudget
                            || seriesLeaderReasons.isEmpty
                    )
                }
                GroupBox("Почему этот кадр — основной представитель серии") {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 130))], spacing: 6) {
                        ForEach(seriesReasonOptions, id: \.0) { code, title in
                            Toggle(title, isOn: Binding(
                                get: { seriesLeaderReasons.contains(code) },
                                set: { selected in
                                    if selected { seriesLeaderReasons.insert(code) }
                                    else { seriesLeaderReasons.remove(code) }
                                }
                            ))
                            .toggleStyle(.button)
                            .controlSize(.small)
                        }
                    }
                    .padding(6)
                }
                qualityGrid(photos: model.qualitySeriesCandidates) { photo in
                    Button { setSeriesLeader(photo.id) } label: {
                        wizardGridCard(photo, badge: seriesBadge(photo.id))
                    }
                    .buttonStyle(.plain)
                    .contextMenu {
                        if seriesSelection.contains(photo.id) {
                            Button("Назначить основным") { setSeriesLeader(photo.id) }
                            Button("Обязательный отдельный момент") {
                                toggleSeriesEssential(photo.id)
                            }
                            Button("Хороший, но избыточный вариант") {
                                toggleSeriesRedundantGood(photo.id)
                            }
                        }
                    }
                }
                Text("Все карточки относятся к одной найденной серии. Клик назначает основной кадр; через контекстное меню отметьте обязательные отдельные моменты и хорошие избыточные варианты. Основной кадр всегда обязателен.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.padding(24)
    }

    private var summaryStep: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                wizardTitle("6. Проверка и экспорт", "Здесь видно, чего именно не хватает. Экспорт содержит только явные человеческие ответы и замороженные оценки движка.")
                summaryRow("Ручные решения", model.qualityManualLabels, "50–100")
                summaryRow("Проверочные A/B", model.qualityHeldOutPairs, "10+")
                summaryRow("Top‑K", model.qualityTopKCount, "5+")
                summaryRow("Полные серии", model.qualitySeriesCount, "1+")
                summaryRow("Серии с целевым бюджетом", model.qualityBudgetSeriesCount, "1+")
                summaryRow("Фото с типами дефектов", model.qualityDefectLabels, "информационно")
                Label(
                    model.qualityReleaseReady ? "Corpus структурно готов" : "Есть незавершённые обязательные шаги",
                    systemImage: model.qualityReleaseReady ? "checkmark.seal.fill" : "exclamationmark.triangle"
                )
                .font(.headline)
                .foregroundStyle(model.qualityReleaseReady ? .green : .orange)
                HStack {
                    Button("Экспортировать набор…") { model.exportQualityEvidence() }
                        .buttonStyle(.borderedProminent)
                    Button("Оценить текущий набор") { model.evaluateCurrentQualityEvidence() }
                        .disabled(!model.qualityReleaseReady)
                }
                if let message = model.qualityMessage { Text(message).foregroundStyle(.secondary) }
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .leading)
        }
    }

    private var currentCandidate: PhotoItem? {
        guard model.qualityCandidates.indices.contains(model.qualityCandidateIndex) else { return nil }
        return model.qualityCandidates[model.qualityCandidateIndex]
    }

    private var qualitySourceAlbums: [AlbumItem] {
        model.albums + model.sharedAlbums
    }

    private var selectedAlbumIsTemporarilyUnavailable: Bool {
        !model.selectedAlbumID.isEmpty
            && !qualitySourceAlbums.contains(where: { $0.id == model.selectedAlbumID })
    }

    private var selectedAlbumIsAvailable: Bool {
        qualitySourceAlbums.contains(where: { $0.id == model.selectedAlbumID })
    }

    private var defectPicker: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 140))], spacing: 6) {
            ForEach(defectOptions, id: \.0) { code, title in
                Toggle(title, isOn: Binding(
                    get: { defectCodes.contains(code) },
                    set: { selected in
                        if selected { defectCodes.insert(code) } else { defectCodes.remove(code) }
                    }
                ))
                .toggleStyle(.button)
                .controlSize(.small)
            }
        }
    }

    private func openStep(_ value: Int) {
        step = value
        if value > 0 && model.qualityCandidates.isEmpty {
            model.loadQualityCandidates(limit: sampleSize)
        }
        if value == 2 && model.qualityPair == nil { model.loadQualityPair() }
        if value == 4 { prepareSeriesCandidate() }
    }

    private func saveLabel(_ photo: PhotoItem, _ disposition: String) {
        model.saveQualityLabel(
            photoID: photo.id,
            disposition: disposition,
            defectCodes: disposition == "reject" ? defectCodes.sorted() : [],
            severity: disposition == "reject" ? defectSeverity : nil,
            confidence: disposition == "reject" ? defectConfidence : nil,
            note: defectNote
        )
    }

    private func saveReject(_ photo: PhotoItem) {
        let codes = defectCodes.isEmpty ? ["other"] : defectCodes.sorted()
        model.saveQualityLabel(
            photoID: photo.id,
            disposition: "reject",
            defectCodes: codes,
            severity: defectSeverity,
            confidence: defectConfidence,
            note: defectNote
        )
    }

    private func resetDefectForm() {
        defectCodes.removeAll(); defectSeverity = 2; defectConfidence = 0.85; defectNote = ""
    }

    private func toggleSeries(_ id: String) {
        if seriesSelection.contains(id) {
            seriesSelection.remove(id)
            seriesEssential.remove(id)
            seriesRedundantGood.remove(id)
            if seriesLeader == id { seriesLeader = nil }
        } else {
            seriesSelection.insert(id)
            if seriesLeader == nil { setSeriesLeader(id) }
        }
        seriesTargetBudget = min(max(1, seriesTargetBudget), max(1, seriesSelection.count))
    }

    private func setSeriesLeader(_ id: String) {
        seriesLeader = id
        seriesEssential.insert(id)
        seriesRedundantGood.remove(id)
    }

    private func toggleSeriesEssential(_ id: String) {
        if seriesEssential.contains(id) && seriesLeader != id {
            seriesEssential.remove(id)
        } else {
            seriesEssential.insert(id)
            seriesRedundantGood.remove(id)
            seriesTargetBudget = max(seriesTargetBudget, seriesEssential.count)
        }
    }

    private func toggleSeriesRedundantGood(_ id: String) {
        guard seriesLeader != id else { return }
        if seriesRedundantGood.contains(id) {
            seriesRedundantGood.remove(id)
        } else {
            seriesRedundantGood.insert(id)
            seriesEssential.remove(id)
        }
    }

    private func seriesBadge(_ id: String) -> String? {
        if seriesLeader == id { return "Лучший · обязателен" }
        if seriesEssential.contains(id) { return "Обязательный" }
        if seriesRedundantGood.contains(id) { return "Хороший дубль" }
        return seriesSelection.contains(id) ? "В серии" : nil
    }

    private func resetSeriesForm() {
        seriesSelection.removeAll()
        seriesLeader = nil
        seriesTargetBudget = 1
        seriesEssential.removeAll()
        seriesRedundantGood.removeAll()
        seriesLeaderReasons.removeAll()
    }

    private func prepareSeriesCandidate() {
        let identifiers = Set(model.qualitySeriesCandidates.map(\.id))
        if seriesSelection != identifiers {
            resetSeriesForm()
            seriesSelection = identifiers
            seriesTargetBudget = min(max(1, Int(Double(identifiers.count).squareRoot())), max(1, identifiers.count))
        }
    }

    private func nameForPhoto(_ id: String) -> String? {
        model.qualityCandidates.first(where: { $0.id == id })?.filename
    }

    private func wizardTitle(_ title: String, _ subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.title.bold())
            Text(subtitle).foregroundStyle(.secondary)
        }
    }

    private func qualityImage(_ photo: PhotoItem, height: CGFloat) -> some View {
        ZStack {
            Color.black
            if let path = photo.reviewPath ?? photo.thumbnailPath {
                CachedThumbnail(path: path, maxPixelSize: 2200, contentMode: .fit)
            } else {
                Image(systemName: "photo").font(.largeTitle).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: height)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func pairCard(_ photo: PhotoItem) -> some View {
        VStack(spacing: 10) {
            qualityImage(photo, height: 480)
            Text(photo.filename).font(.caption).lineLimit(1)
            Button("Этот кадр лучше") { model.chooseQualityPreference(photo.id) }
                .buttonStyle(.borderedProminent)
        }.frame(maxWidth: .infinity)
    }

    private func qualityGrid<Content: View>(
        photos: [PhotoItem]? = nil,
        @ViewBuilder content: @escaping (PhotoItem) -> Content
    ) -> some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150, maximum: 210))], spacing: 10) {
                ForEach(photos ?? model.qualityCandidates) { photo in content(photo) }
            }
        }
    }

    private func wizardGridCard(_ photo: PhotoItem, badge: String?) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            ZStack(alignment: .topTrailing) {
                if let path = photo.thumbnailPath ?? photo.reviewPath {
                    CachedThumbnail(path: path, maxPixelSize: 420, contentMode: .fill)
                } else { Rectangle().fill(.quaternary) }
                if let badge {
                    Text(badge).font(.caption.bold()).padding(6)
                        .background(.ultraThickMaterial, in: Capsule()).padding(6)
                }
            }
            .frame(height: 125).clipped().clipShape(RoundedRectangle(cornerRadius: 8))
            Text(photo.filename).font(.caption).lineLimit(1)
        }
    }

    private func summaryRow(_ title: String, _ count: Int, _ target: String) -> some View {
        HStack {
            Text(title); Spacer(); Text("\(count) / \(target)").monospacedDigit()
        }
        .padding(12)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
    }

    private func unavailable(_ title: String, symbol: String, detail: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: symbol).font(.largeTitle).foregroundStyle(.secondary)
            Text(title).font(.headline)
            Text(detail).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 260)
    }
}
