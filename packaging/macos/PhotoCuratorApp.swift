import AppKit
import Combine
import Foundation
import Photos
#if !GALLERY_BENCHMARK
import PhotoCuratorPublishHelper
import PhotoCuratorSourceHelper
#endif
import QuickLookUI
import SwiftUI
import UniformTypeIdentifiers

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

#if !GALLERY_BENCHMARK
@main
struct PhotoCuratorApplication: App {
    @StateObject private var model: AppModel
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        let arguments = CommandLine.arguments
        if let marker = arguments.firstIndex(of: "--photo-curator-source-helper") {
            let helperArguments = [arguments[0]] + Array(arguments.dropFirst(marker + 1))
            exit(runPhotoCuratorSourceHelper(arguments: helperArguments))
        }
        if let marker = arguments.firstIndex(of: "--photo-curator-publish-helper") {
            let helperArguments = [arguments[0]] + Array(arguments.dropFirst(marker + 1))
            exit(runPhotoCuratorPublishHelper(arguments: helperArguments))
        }
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
                    .disabled(model.qualityWizardActive || model.photos.isEmpty)
                Button("Следующее фото") { model.movePhotoSelection(1) }
                    .keyboardShortcut(.rightArrow, modifiers: [])
                    .disabled(model.qualityWizardActive || model.photos.isEmpty)
                Button("Быстрый просмотр") { model.previewSelected() }
                    .keyboardShortcut(.space, modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Divider()
                Button("Добавить в Best") { model.decideSelected("keep") }
                    .keyboardShortcut("p", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Button("Снять ручной флаг") { model.decideSelected(nil) }
                    .keyboardShortcut("u", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Button("Отклонить") { model.decideSelected("reject") }
                    .keyboardShortcut("x", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Divider()
                ForEach(1...5, id: \.self) { rating in
                    Button("Оценка \(rating) ★") { model.rateSelected(rating) }
                        .keyboardShortcut(KeyEquivalent(Character(String(rating))), modifiers: [])
                        .disabled(model.selectedPhotoID == nil)
                }
                Button("Снять оценку") { model.rateSelected(nil) }
                    .keyboardShortcut("0", modifiers: [])
                    .disabled(model.selectedPhotoID == nil)
                Divider()
                Button("Отменить решение") { model.undoLastDecision() }
                    .keyboardShortcut("z", modifiers: .command)
                Divider()
                Button("Grid") { model.workspaceMode = .grid }
                    .keyboardShortcut("g", modifiers: [])
                Button("Loupe") { model.workspaceMode = .loupe }
                    .keyboardShortcut("e", modifiers: [])
                Button("Compare") { model.workspaceMode = .compare }
                    .keyboardShortcut("c", modifiers: [])
                Button("Survey") { model.workspaceMode = .survey }
                    .keyboardShortcut("n", modifiers: [])
            }
        }

        Settings {
            SettingsView()
                .environmentObject(model)
                .frame(minWidth: 720, idealWidth: 780, minHeight: 620, idealHeight: 760)
        }

        Window("Мастер проверки качества", id: "photo-curator-quality-wizard") {
            QualityWizardView()
                .environmentObject(model)
        }
        .defaultSize(width: 1120, height: 820)

        Window("Справка Photo Curator", id: "photo-curator-help") {
            HelpCenterView()
        }
        .defaultSize(width: 1060, height: 780)
    }
}
#endif
