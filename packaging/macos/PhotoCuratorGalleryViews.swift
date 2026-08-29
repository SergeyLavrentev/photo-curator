import AppKit
import Foundation
import SwiftUI

struct PublishPreviewSheet: View {
    let plan: PublishPlan
    let independentCopies: Bool
    let confirm: () -> Void
    let cancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Проверка точного состава Best").font(.title2.bold())
                    Text("\(plan.itemCount) фото → «\(plan.albumName)»")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(String(plan.assetSetSHA256.prefix(12)))
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .help("SHA‑256 неизменяемого списка Photos asset UUID")
            }
            ScrollView {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 130, maximum: 180), spacing: 6)],
                    spacing: 6
                ) {
                    ForEach(plan.items, id: \.id) { photo in
                        VStack(alignment: .leading, spacing: 4) {
                            if let path = photo.imagePath {
                                CachedThumbnail(path: path, maxPixelSize: 360)
                                    .frame(height: 105)
                                    .clipped()
                            } else {
                                Rectangle().fill(.quaternary).frame(height: 105)
                            }
                            Text(photo.filename).font(.caption).lineLimit(1)
                        }
                    }
                }
            }
            Text(
                independentCopies
                    ? "Будут импортированы независимые локальные копии Shared snapshot."
                    : "Будут добавлены именно эти существующие Photos assets; новые копии не создаются."
            )
            .font(.callout)
            .foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Отмена", action: cancel)
                Button(
                    independentCopies ? "Импортировать копии" : "Добавить в Best",
                    action: confirm
                )
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(22)
        .frame(minWidth: 760, minHeight: 580)
    }
}

struct WorkspaceCanvas: View {
    let mode: WorkspaceMode
    let photos: [PhotoItem]
    let selectedID: String?
    let multiSelectedIDs: Set<String>
    let select: (String) -> Void
    let decide: (String, String?) -> Void
    let rate: (String, Int?) -> Void
    private let canvasHeight: CGFloat = 450

    private var selected: PhotoItem? {
        photos.first(where: { $0.id == selectedID }) ?? photos.first
    }

    private var displayed: [PhotoItem] {
        guard let selected else { return [] }
        switch mode {
        case .grid, .loupe:
            return [selected]
        case .compare:
            let comparison = photos.first {
                $0.id != selected.id
                    && (multiSelectedIDs.contains($0.id)
                        || (selected.duplicateGroup != nil
                            && $0.duplicateGroup == selected.duplicateGroup))
            } ?? photos.first { $0.id != selected.id }
            return [selected, comparison].compactMap { $0 }
        case .survey:
            let explicit = photos.filter { multiSelectedIDs.contains($0.id) }
            if explicit.count >= 2 { return Array(explicit.prefix(6)) }
            if let group = selected.duplicateGroup {
                let series = photos.filter { $0.duplicateGroup == group }
                if series.count >= 2 { return Array(series.prefix(6)) }
            }
            return Array(photos.prefix(4))
        }
    }

    private var cellHeight: CGFloat {
        mode == .survey ? (canvasHeight - 2) / 2 : canvasHeight
    }

    var body: some View {
        VStack(spacing: 8) {
            HStack(spacing: 0) {
                ZStack {
                    Color.black
                    if displayed.isEmpty {
                        VStack(spacing: 10) {
                            Image(systemName: "photo").font(.largeTitle)
                            Text("Выберите фото")
                        }
                        .foregroundStyle(.secondary)
                    } else {
                        LazyVGrid(
                            columns: Array(
                                repeating: GridItem(.flexible(), spacing: 2),
                                count: mode == .survey ? 2 : displayed.count
                            ),
                            spacing: 2
                        ) {
                            ForEach(displayed) { photo in
                                ZStack(alignment: .bottom) {
                                    if let path = photo.reviewPath ?? photo.thumbnailPath {
                                        CachedThumbnail(
                                            path: path,
                                            maxPixelSize: mode == .loupe ? 2400 : 1400,
                                            contentMode: .fit
                                        )
                                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                                    } else {
                                        Image(systemName: "photo")
                                            .font(.largeTitle)
                                            .foregroundStyle(.secondary)
                                    }
                                    HStack {
                                        Text(photo.filename).lineLimit(1)
                                        Spacer()
                                        DecisionPicker(
                                            selection: photo.selection ?? "alternative",
                                            decide: { decide(photo.id, $0) }
                                        )
                                    }
                                    .font(.caption)
                                    .padding(8)
                                    .background(.ultraThinMaterial)
                                }
                                .frame(maxWidth: .infinity)
                                .frame(height: cellHeight)
                                .clipped()
                                .contentShape(Rectangle())
                                .onTapGesture { select(photo.id) }
                                .overlay(
                                    Rectangle().stroke(
                                        photo.id == selectedID ? Color.accentColor : .clear,
                                        lineWidth: 3
                                    )
                                )
                            }
                        }
                        .padding(2)
                        .frame(maxHeight: canvasHeight)
                    }
                }
                .frame(maxWidth: .infinity, minHeight: canvasHeight, maxHeight: canvasHeight)
                .clipped()
                Divider()
                if let selected {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Метаданные").font(.headline)
                            LabeledContent("Файл", value: selected.filename)
                            if selected.manualSelection != nil {
                                LabeledContent(
                                    "Решение пользователя",
                                    value: selectionTitle(selected.selection)
                                )
                                LabeledContent(
                                    "Рекомендация движка",
                                    value: selectionTitle(selected.autoSelection)
                                )
                            } else {
                                LabeledContent(
                                    "Рекомендация движка",
                                    value: selectionTitle(selected.autoSelection ?? selected.selection)
                                )
                            }
                            LabeledContent("Безопасность", value: dispositionTitle(selected.disposition))
                            if let score = selected.swipeScore {
                                LabeledContent("Swipe Score", value: "\(score)")
                            }
                            LabeledContent(
                                "Ваша оценка",
                                value: selected.manualRating.map { "\($0) ★" } ?? "—"
                            )
                            HStack(spacing: 5) {
                                ForEach(1...5, id: \.self) { value in
                                    Button {
                                        rate(selected.id, selected.manualRating == value ? nil : value)
                                    } label: {
                                        Image(systemName: value <= (selected.manualRating ?? 0) ? "star.fill" : "star")
                                    }
                                    .buttonStyle(.plain)
                                    .help("Оценка \(value)")
                                }
                            }
                            if selected.confidenceCalibrated, let confidence = selected.confidence {
                                LabeledContent(
                                    "Вероятность корректности",
                                    value: "\(Int(confidence * 100))%"
                                )
                            } else {
                                LabeledContent("Калибровка", value: "ожидает независимой разметки")
                            }
                            if let coverage = selected.evidenceCoverage {
                                LabeledContent("Полнота сигналов", value: "\(Int(coverage * 100))%")
                            }
                            if selected.modelCount >= 2,
                               let disagreement = selected.modelDisagreement {
                                LabeledContent(
                                    "Расхождение моделей",
                                    value: "\(Int(disagreement * 100))% · \(selected.modelCount) модели"
                                )
                            }
                            if let group = selected.duplicateGroup {
                                LabeledContent("Серия", value: group)
                            }
                            Divider()
                            Text("Причины рекомендации").font(.headline)
                            ForEach(selected.reasons.prefix(4)) { reason in
                                Label(reason.title, systemImage: "circle.fill")
                                    .font(.caption)
                            }
                        }
                        .padding(14)
                    }
                    .frame(width: 280)
                    .frame(height: canvasHeight)
                    .background(Color(nsColor: .controlBackgroundColor))
                }
            }
            .frame(height: canvasHeight)
            ScrollView(.horizontal) {
                LazyHStack(spacing: 4) {
                    ForEach(photos) { photo in
                        Group {
                            if let path = photo.imagePath {
                                CachedThumbnail(path: path, maxPixelSize: 240)
                            } else {
                                Rectangle().fill(.quaternary)
                            }
                        }
                        .frame(width: 110, height: 72)
                        .clipped()
                        .overlay(
                            RoundedRectangle(cornerRadius: 3).stroke(
                                photo.id == selectedID ? Color.accentColor : .clear,
                                lineWidth: 3
                            )
                        )
                        .onTapGesture { select(photo.id) }
                    }
                }
            }
            .frame(height: 76)
        }
        .background(Color.black.opacity(0.92), in: RoundedRectangle(cornerRadius: 8))
    }
}

// 128 pt yields at least 24 compact cards (six columns by four rows) in the normal desktop review window.
let selectionCardMinimumWidth: CGFloat = 96
let selectionCardDefaultWidth: CGFloat = 128
let selectionCardMaximumWidth: CGFloat = 260
let selectionCardZoomStep: CGFloat = 8

struct PhotoCard: View, Equatable {
    let photo: PhotoItem
    let selected: Bool
    let multiSelected: Bool
    let developerToolsEnabled: Bool
    let select: () -> Void
    let toggleMultiSelection: () -> Void
    let preview: () -> Void
    let openDetails: () -> Void
    let openGoodPhoto: (String) -> Void
    let seriesSelected: Bool
    let toggleTopK: () -> Void
    let toggleSeriesSelection: () -> Void
    let labelSeriesLeader: () -> Void
    let stackCount: Int
    let toggleStack: () -> Void
    let rate: (Int?) -> Void
    let decide: (String?) -> Void

    static func == (lhs: PhotoCard, rhs: PhotoCard) -> Bool {
        lhs.photo == rhs.photo
            && lhs.selected == rhs.selected
            && lhs.multiSelected == rhs.multiSelected
            && lhs.developerToolsEnabled == rhs.developerToolsEnabled
            && lhs.seriesSelected == rhs.seriesSelected
            && lhs.stackCount == rhs.stackCount
    }

    private var decisionTitle: String {
        selectionTitle(photo.selection)
    }

    private var recommendationSymbol: String {
        switch photo.selection {
        case "pick": return "flag.fill"
        case "reject": return "xmark"
        case "review": return "exclamationmark.triangle.fill"
        default: return "circle"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Group {
                if let path = photo.thumbnailPath ?? photo.reviewPath {
                    CachedThumbnail(path: path, maxPixelSize: 320)
                } else {
                    Rectangle().fill(.quaternary).overlay(Image(systemName: "photo"))
                }
            }
            .frame(maxWidth: .infinity)
            .aspectRatio(4 / 3, contentMode: .fit)
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .onTapGesture(count: 2, perform: openDetails)
            .onTapGesture(count: 1, perform: select)
            .contextMenu {
                Button("Быстрый просмотр", action: preview)
                Button("Открыть детали", action: openDetails)
                Divider()
                Button("Добавить в Best") { decide("keep") }
                    .disabled(photo.selection == "pick")
                Button("Отклонить") { decide("reject") }
                    .disabled(photo.selection == "reject")
                Button("Снять ручное решение") { decide(nil) }
                    .disabled(photo.manualSelection == nil)
                Divider()
                ForEach(1...5, id: \.self) { value in
                    Button("\(value) ★") { rate(value) }
                }
                if photo.manualRating != nil {
                    Button("Снять оценку") { rate(nil) }
                }
            }
            .help("Нажмите, чтобы выбрать; пробел — быстрый просмотр")
            .overlay(alignment: .topTrailing) {
                Image(systemName: multiSelected ? "checkmark.circle.fill" : "circle")
                    .font(.title3.weight(.semibold))
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(Color.white, multiSelected ? Color.green : Color.black.opacity(0.55))
                    .frame(width: 30, height: 30)
                    .contentShape(Circle())
                    .onTapGesture(perform: toggleMultiSelection)
                .background(
                    Color.black.opacity(multiSelected ? 0 : 0.24),
                    in: Circle()
                )
                .padding(8)
                .help(multiSelected ? "Снять отметку" : "Отметить для группового действия")
                .accessibilityLabel(multiSelected ? "Снять отметку" : "Отметить фотографию")
                .accessibilityAddTraits(.isButton)
            }
            .overlay(alignment: .bottomTrailing) {
                if stackCount > 1 {
                    Label("\(stackCount)", systemImage: "square.stack.3d.up.fill")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(Color.black.opacity(0.62), in: Capsule())
                        .contentShape(Capsule())
                        .onTapGesture(perform: toggleStack)
                    .padding(8)
                    .help("Развернуть или свернуть серию")
                    .accessibilityAddTraits(.isButton)
                }
            }
            .overlay(alignment: .topLeading) {
                if let rating = photo.manualRating {
                    Label("\(rating)", systemImage: "star.fill")
                        .font(.caption.weight(.bold))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 4)
                        .foregroundStyle(.yellow)
                        .background(Color.black.opacity(0.62), in: Capsule())
                        .padding(8)
                        .allowsHitTesting(false)
                }
            }
            .overlay(alignment: .bottomLeading) {
                Label(decisionTitle, systemImage: recommendationSymbol)
                    .labelStyle(.iconOnly)
                    .font(.caption.weight(.bold))
                    .frame(width: 26, height: 26)
                    .foregroundStyle(photo.selection == "pick" ? Color.green : Color.white)
                    .background(Color.black.opacity(0.62), in: Circle())
                    .padding(8)
                    .allowsHitTesting(false)
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
        .padding(2)
        .frame(maxWidth: .infinity)
        .background(.background, in: RoundedRectangle(cornerRadius: 6))
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .strokeBorder(
                    selected || multiSelected
                        ? Color.accentColor
                        : Color(nsColor: .separatorColor).opacity(0.4),
                    lineWidth: selected || multiSelected ? 1.5 : 0.5
                )
                .allowsHitTesting(false)
        )
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .contentShape(RoundedRectangle(cornerRadius: 6))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(photo.filename), решение: \(decisionTitle)")
        .accessibilityValue(selectionTitle(photo.selection))
        .accessibilityHint("P добавляет в Best, X отклоняет; пробел открывает быстрый просмотр")
    }
}

struct PhotoDetailView: View {
    let photo: PhotoItem
    let openGoodPhoto: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    private var recommendationTitle: String {
        selectionTitle(photo.selection)
    }

    var body: some View {
        HStack(spacing: 0) {
            ZStack {
                Color.black
                if let path = photo.reviewPath ?? photo.thumbnailPath {
                    CachedThumbnail(path: path, maxPixelSize: 2400, contentMode: .fit)
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
                    LabeledContent("Безопасность", value: dispositionTitle(photo.disposition))
                    if photo.manualDisposition != nil {
                        Label(
                            "Категория изменена вами вручную",
                            systemImage: "hand.tap.fill"
                        )
                        .foregroundStyle(.secondary)
                    } else {
                        Text("Почему принято это решение")
                            .font(.headline)
                        DecisionReasonsView(photo: photo, openGoodPhoto: openGoodPhoto)
                        if photo.confidenceCalibrated, let confidence = photo.confidence {
                            Text(
                                "Калиброванная вероятность корректности: \(confidence * 100, specifier: "%.0f")%"
                            )
                            .foregroundStyle(.secondary)
                        } else {
                            Text("Вероятность решения появится после независимой человеческой разметки нескольких альбомов.")
                                .foregroundStyle(.secondary)
                        }
                        if photo.modelCount >= 2,
                           let disagreement = photo.modelDisagreement {
                            Text(
                                "Расхождение независимых моделей: \(disagreement * 100, specifier: "%.0f")% (\(photo.modelCount))."
                            )
                            .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(24)
            }
            .frame(width: 360)
        }
        .frame(minWidth: 980, minHeight: 680)
    }
}

struct DecisionReasonsView: View {
    let photo: PhotoItem
    let openGoodPhoto: (String) -> Void

    var body: some View {
        ForEach(photo.reasons) { reason in
            DecisionReasonRow(
                reason: reason,
                betterDuplicate: photo.duplicateLeader,
                albumReferences: photo.albumReferences,
                openGoodPhoto: openGoodPhoto
            )
        }
    }
}

private struct DecisionReasonRow: View {
    let reason: DecisionReason
    let betterDuplicate: RelatedPhoto?
    let albumReferences: [RelatedPhoto]
    let openGoodPhoto: (String) -> Void
    @State private var popoverPresented = false

    var body: some View {
        if reason.code == "weaker_duplicate", reason.duplicateKind == "exact" {
            if let betterDuplicate {
                Button { openGoodPhoto(betterDuplicate.id) } label: {
                    Label(reason.title, systemImage: "doc.on.doc.fill")
                        .labelStyle(.titleAndIcon)
                }
                .buttonStyle(.link)
                .help("Открыть другой экземпляр в «Хороших»")
            }
        } else if reason.code == "weaker_duplicate", let betterDuplicate {
            Button {
                popoverPresented = true
            } label: {
                Label(reason.title, systemImage: "circle.fill")
                    .labelStyle(.titleAndIcon)
            }
            .buttonStyle(.link)
            .popover(isPresented: $popoverPresented) {
                RelatedPhotoPopover(photo: betterDuplicate, openGoodPhoto: openGoodPhoto)
            }
            .help("Показать более удачный кадр этой серии")
        } else if reason.code == "below_album_cutoff", !albumReferences.isEmpty {
            Button { popoverPresented = true } label: {
                Label(reason.title, systemImage: "circle.fill")
                    .labelStyle(.titleAndIcon)
            }
            .buttonStyle(.link)
            .popover(isPresented: $popoverPresented) {
                AlbumReferencesPopover(photos: albumReferences, openGoodPhoto: openGoodPhoto)
            }
            .help("Показать сильные кадры из «Хороших»")
        } else if reason.code == "technical_penalty", !reason.technicalDefects.isEmpty {
            Button {
                popoverPresented = true
            } label: {
                Label(reason.title, systemImage: "circle.fill")
                    .labelStyle(.titleAndIcon)
            }
            .buttonStyle(.link)
            .popover(isPresented: $popoverPresented) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Технический недостаток").font(.headline)
                    ForEach(reason.technicalDefects, id: \.self) { defect in
                        Label(defect, systemImage: "wrench.and.screwdriver")
                    }
                }
                .padding(16)
                .frame(width: 300, alignment: .leading)
            }
            .help("Показать, какой технический недостаток найден")
        } else {
            Label(reason.title, systemImage: "circle.fill")
                .labelStyle(.titleAndIcon)
        }
    }
}

private struct RelatedPhotoPopover: View {
    let photo: RelatedPhoto
    let openGoodPhoto: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Более удачный кадр").font(.headline)
            if let path = photo.imagePath {
                CachedThumbnail(path: path, maxPixelSize: 800, contentMode: .fit)
                    .frame(width: 300, height: 220)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "photo")
                        .font(.title)
                    Text("Предпросмотр недоступен")
                        .foregroundStyle(.secondary)
                }
                    .frame(width: 300, height: 160)
            }
            Text(photo.filename)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Button("Открыть в «Хороших»") { openGoodPhoto(photo.id) }
                .buttonStyle(.borderedProminent)
        }
        .padding(16)
        .frame(width: 332, alignment: .leading)
    }
}

private struct AlbumReferencesPopover: View {
    let photos: [RelatedPhoto]
    let openGoodPhoto: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Сильные кадры из «Хороших»").font(.headline)
            Text("Они стали ориентирами при отборе этого альбома.")
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach(photos, id: \.id) { photo in
                Button(photo.filename) { openGoodPhoto(photo.id) }
                    .buttonStyle(.link)
            }
        }
        .padding(16)
        .frame(width: 320, alignment: .leading)
    }
}

private struct DecisionPicker: View {
    let selection: String
    let decide: (String?) -> Void

    private let options = [
        ("keep", "Добавить в Best", "flag.fill", Color.green),
        ("reject", "Отклонить", "xmark", Color.red),
    ]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(options, id: \.0) { option in
                let value = option.0
                let title = option.1
                let symbol = option.2
                let tint = option.3
                let active = value == "keep" ? selection == "pick" : selection == value
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
                    active ? tint : Color.black.opacity(0.42),
                    in: Circle()
                )
                .overlay(Circle().strokeBorder(.white.opacity(0.65)))
                .accessibilityLabel(title)
                .accessibilityValue(active ? "Выбрано" : "")
                .disabled(active)
            }
        }
    }
}

struct TasteGridCard: View {
    let photo: PhotoItem
    let favorite: Bool
    let rejected: Bool
    let disabled: Bool
    let chooseFavorite: () -> Void
    let chooseRejected: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Group {
                if let path = photo.imagePath {
                    CachedThumbnail(path: path, maxPixelSize: 480, contentMode: .fill)
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

func dispositionTitle(_ disposition: String?) -> String {
    ["keep": "Не удалять", "review": "Проверить", "reject": "Можно отклонить"][disposition ?? ""]
        ?? "Без решения"
}

func selectionTitle(_ selection: String?) -> String {
    [
        "pick": "Best",
        "alternative": "Альтернатива",
        "review": "Проверить",
        "reject": "Отклонено",
    ][selection ?? ""] ?? "Без отбора"
}

func projectStateTitle(_ state: String) -> String {
    [
        "created": "Готов к запуску",
        "running": "Выполняется",
        "ready": "Готов",
        "interrupted": "Остановлен",
        "error": "Ошибка",
    ][state] ?? state
}

func jobStatusSymbol(_ status: String) -> String {
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

func jobStatusColor(_ status: String) -> Color {
    switch status {
    case "done": return .green
    case "warning": return .orange
    case "error": return .red
    case "running": return .accentColor
    default: return .secondary
    }
}
