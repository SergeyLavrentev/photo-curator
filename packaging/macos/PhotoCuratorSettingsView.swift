import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow
    @State private var confirmsTasteReset = false

    var body: some View {
        ScrollView {
        Form {
            Section("Локальный движок") {
                LabeledContent("Статус", value: model.workerStatus)
                if model.photoAccessNeedsAction {
                    if model.photoAccessCanRequest {
                        Button("Разрешить доступ к Фото") {
                            model.requestPhotoLibraryAccessFromUI()
                        }
                    }
                    Button("Открыть настройки доступа к Фото") {
                        model.openPhotoPrivacySettings()
                    }
                }
                Button("Перезапустить движок") { model.restartWorker() }
            }
            Section("Движки оценки") {
                Toggle("Apple Vision", isOn: $model.engineApple)
                Text("Нативная эстетика, лица, композиция и признаки для персонального вкуса.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Toggle("NIMA", isOn: $model.engineNIMA)
                Text("Эстетическая оценка по распределению пользовательских рейтингов AVA.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Toggle("MobileCLIP S0", isOn: $model.engineMobileCLIP)
                Text("Семантика кадра, жанр и соответствие визуально приятным образам.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Toggle("MUSIQ", isOn: $model.engineMUSIQ)
                Text("Техническое и воспринимаемое качество изображения на разных масштабах.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Label(
                    "Все движки включены по умолчанию. Изменения применятся к следующему анализу.",
                    systemImage: "info.circle"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Section("Персональный вкус") {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 5) {
                    GridRow {
                        Text("Профиль")
                        Text(model.tasteStatusTitle)
                    }
                    GridRow {
                        Text("Обучающих сравнений")
                        Text("\(model.tasteCalibrationExamples)")
                    }
                    GridRow {
                        Text("Проверочных сравнений")
                        Text("\(model.tasteHeldOutExamples)")
                    }
                }
                Grid(alignment: .leading, horizontalSpacing: 8, verticalSpacing: 8) {
                    GridRow {
                        Button("Настроить вкус…") { model.openTasteEditor() }
                        Button(
                            model.tasteStatus == "paused" ? "Возобновить" : "Поставить на паузу"
                        ) {
                            model.toggleTasteProfile()
                        }
                        .disabled(model.tasteExamples == 0)
                    }
                    GridRow {
                        Button("Экспортировать…") { model.exportTasteProfile() }
                            .disabled(model.tasteExamples == 0)
                        Button("Удалить профиль…", role: .destructive) {
                            confirmsTasteReset = true
                        }
                        .disabled(model.tasteExamples == 0)
                    }
                }
            }
            Section("Проверка качества") {
                Toggle("Лаборатория качества", isOn: $model.qualityLabEnabled)
                Text("Пошаговый мастер собирает слепые решения, типы дефектов, A/B, Top‑K и серии. Автоматические решения не становятся эталоном.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if model.qualityToolsEnabled {
                    Button("Открыть мастер проверки качества…") {
                        openWindow(id: "photo-curator-quality-wizard")
                    }
                    .buttonStyle(.borderedProminent)
                    Text("Экспортирует явную разметку и замороженный Swipe Score.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    LabeledContent("Ручные решения", value: "\(model.qualityManualLabels) / 50–100")
                    LabeledContent("Проверочные A/B", value: "\(model.qualityHeldOutPairs) / 10+")
                    LabeledContent("Top‑K", value: "\(model.qualityTopKCount) / 5+")
                    LabeledContent("Подтверждённые серии", value: "\(model.qualitySeriesCount) / 1+")
                    LabeledContent("Метки дефектов", value: "\(model.qualityDefectLabels)")
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
                    Divider()
                    if let pending = model.pendingPhotoKitAcceptance {
                        Label(
                            "Незавершённый cleanup: \(pending.albumName)",
                            systemImage: "exclamationmark.triangle.fill"
                        )
                        .foregroundStyle(.orange)
                        Button("Завершить cleanup одноразового альбома…") {
                            model.retryPhotoKitAcceptanceCleanup()
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isRunningPhotoKitAcceptance)
                    }
                    Button("Проверить PhotoKit create/cleanup…") {
                        model.requestPhotoKitAcceptance()
                    }
                    .disabled(
                        model.project?.state != "ready"
                            || model.isRunningPhotoKitAcceptance
                            || model.pendingPhotoKitAcceptance != nil
                    )
                    Text(
                        "Создаёт уникальный одноразовый Best‑альбом с одним существующим фото, проверяет его через PhotoKit и удаляет только созданный контейнер."
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    if let message = model.photoKitAcceptanceMessage {
                        Text(message).font(.caption).foregroundStyle(.secondary)
                    }
                    if let message = model.qualityMessage {
                        Text(message).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Text("Все вычисления и предпочтения остаются на этом Mac.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
        .frame(maxWidth: .infinity)
        .padding(20)
        }
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
        .confirmationDialog(
            "Провести реальный PhotoKit acceptance?",
            isPresented: $model.photoKitAcceptancePending,
            titleVisibility: .visible
        ) {
            Button("Создать и удалить одноразовый Best‑альбом") {
                model.runPhotoKitAcceptance()
            }
            Button("Отмена", role: .cancel) {}
        } message: {
            Text(
                "Будет создан уникальный тестовый альбом с одним существующим фото. После проверки приложение удалит только этот альбом; исходный PHAsset и исходный альбом сохранятся."
            )
        }
    }
}
