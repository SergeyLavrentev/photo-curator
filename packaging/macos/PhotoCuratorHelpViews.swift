import SwiftUI

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
struct PhotoCuratorHelpCommands: Commands {
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
struct HelpCenterView: View {
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
            Text(text)
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
                    details: "Определяются количество лиц, доступность landmark-точек и face capture quality. Наличие landmarks не выдаётся за открытые глаза и не влияет на лидерство серии; отдельный blink/open-eye detector пока не валидирован. Отсутствие лица не считается недостатком фотографии."
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
