import SwiftUI

struct BlindTastePhoto: Decodable, Identifiable {
    let id: String
    let reviewPath: String
    enum CodingKeys: String, CodingKey {
        case id
        case reviewPath = "review_path"
    }
}

struct BlindTasteSession: Decodable, Identifiable {
    struct Pair: Decodable { let left: BlindTastePhoto; let right: BlindTastePhoto }
    let id: String
    let projectID: String
    let albumName: String
    let completed: Int
    let total: Int
    let choices: Int
    let pair: Pair?
    enum CodingKeys: String, CodingKey {
        case id, completed, total, choices, pair
        case projectID = "project_id"
        case albumName = "album_name"
    }
}

private struct BlindTasteAnswer: Encodable {
    let sessionID: String
    let index: Int
    let choice: String
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case index, choice
    }
}

struct BlindTasteView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var selectedProjectID = ""
    @State private var session: BlindTasteSession?
    @State private var busy = false
    @State private var message: String?
    @State private var needsRefresh = false

    private var readyProjects: [ProjectItem] { model.projects.filter { $0.state == "ready" } }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Ваш вкус").font(.largeTitle.bold())
                    Text("Какой кадр этой серии вы бы оставили?")
                        .font(.title3).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Готово") { dismiss() }.disabled(busy)
            }
            if let session, let pair = session.pair {
                HStack {
                    Text(session.albumName)
                    Spacer()
                    Text("\(session.completed + 1) / \(session.total)").monospacedDigit()
                }.foregroundStyle(.secondary)
                ProgressView(value: Double(session.completed), total: Double(session.total))
                HStack(spacing: 16) {
                    choicePhoto(pair.left, title: "Лучше левая", choice: "left", key: "1")
                    choicePhoto(pair.right, title: "Лучше правая", choice: "right", key: "2")
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
                HStack {
                    Text("Оценки движка скрыты. Выбор обучает профиль; фотографии остаются.")
                        .font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Разные сцены") { answer("different_scene") }
                        .disabled(busy)
                    Button("Равноценны / пропустить") { answer("skip") }
                        .keyboardShortcut(.space, modifiers: []).disabled(busy)
                }
            } else {
                Spacer()
                if let session {
                    Label("Сохранено \(session.choices) предпочтений", systemImage: "checkmark.circle")
                        .font(.title2)
                    Text("Выборы сохраняются между запусками и после удаления анализа. Пропущенные пары не участвуют в обучении.")
                    Text("Это обучение на ваших ответах. Совпадение с ними не доказывает качество на других альбомах.")
                        .foregroundStyle(.secondary)
                } else {
                    Text("До 12 сравнений, без обязательных «плохих» кадров.").font(.title2)
                    Text("Сравниваем только похожие кадры одной сцены, снятые рядом по времени: тот же сюжет и место. Выбирайте по композиции, моменту и качеству. Если это разные сцены — сообщите об этом; если оба кадра равноценны — пропустите.")
                }
                if readyProjects.isEmpty {
                    Text("Сначала проанализируйте альбом. Затем здесь появятся готовые фотографии для быстрых сравнений.")
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Готовый анализ", selection: $selectedProjectID) {
                        ForEach(readyProjects) { project in
                            Text(project.albumName).tag(project.id)
                        }
                    }
                    Button(session == nil ? "Начать слепое сравнение" : "Ещё один набор") { prepare() }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || selectedProjectID.isEmpty)
                }
                Spacer()
            }
            if busy { ProgressView("Сохраняем и обновляем профиль…").controlSize(.small) }
            if let message { Text(message).foregroundStyle(.orange).textSelection(.enabled) }
        }
        .padding(24)
        .frame(minWidth: 820, idealWidth: 1080, minHeight: 620, idealHeight: 760)
        .onAppear {
            selectedProjectID = readyProjects.first(where: { $0.id == model.project?.id })?.id
                ?? readyProjects.first?.id ?? ""
        }
        .onDisappear {
            if needsRefresh { Task { await model.refreshDecisionsForTaste() } }
        }
    }

    private func choicePhoto(_ photo: BlindTastePhoto, title: String, choice: String, key: KeyEquivalent) -> some View {
        VStack(spacing: 10) {
            CachedThumbnail(path: photo.reviewPath, maxPixelSize: 1400, contentMode: .fit)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipped().background(Color.black.opacity(0.9))
            Button(title) { answer(choice) }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(key, modifiers: [])
                .disabled(busy)
        }
    }

    private func prepare() {
        guard !busy else { return }
        busy = true
        message = nil
        Task {
            defer { busy = false }
            do {
                session = try await model.callDTO(
                    "blind_taste_prepare", ProjectIDParams(projectID: selectedProjectID),
                    as: BlindTasteSession.self
                )
            } catch { message = error.localizedDescription }
        }
    }

    private func answer(_ choice: String) {
        guard !busy, let current = session, current.pair != nil else { return }
        busy = true
        message = nil
        Task {
            defer { busy = false }
            do {
                session = try await model.callDTO(
                    "blind_taste_answer",
                    BlindTasteAnswer(sessionID: current.id, index: current.completed, choice: choice),
                    as: BlindTasteSession.self
                )
                needsRefresh = needsRefresh || choice == "left" || choice == "right"
            } catch { message = error.localizedDescription }
        }
    }
}
