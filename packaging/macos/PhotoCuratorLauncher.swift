import AppKit
import Darwin
import Foundation

private enum BackendState {
    case starting
    case running
    case stopping
    case stopped
    case failed(String)
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var backend: Process?
    private var outputPipe: Pipe?
    private var outputBuffer = ""
    private var serviceURL: URL?
    private var state: BackendState = .stopped
    private var terminationPending = false

    private var statusItem: NSStatusItem!
    private var statusMenuItem: NSMenuItem!
    private var openMenuItem: NSMenuItem!
    private var toggleMenuItem: NSMenuItem!
    private var statusLabel: NSTextField?
    private var openButton: NSButton?
    private var toggleButton: NSButton?
    private var settingsWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        configureMainMenu()
        configureStatusItem()
        updateState(.stopped)
        startBackend(openBrowser: true)
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        if serviceURL != nil {
            openService(nil)
        } else if backend?.isRunning != true {
            startBackend(openBrowser: true)
        }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard backend?.isRunning == true else { return .terminateNow }
        guard !terminationPending else { return .terminateLater }
        terminationPending = true
        stopBackend(nil)
        return .terminateLater
    }

    private func configureMainMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)

        let appMenu = NSMenu(title: "Photo Curator")
        appMenu.addItem(withTitle: "О Photo Curator", action: #selector(showAbout(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Открыть Photo Curator", action: #selector(openService(_:)), keyEquivalent: "o")
        appMenu.addItem(withTitle: "Настройки…", action: #selector(showSettings(_:)), keyEquivalent: ",")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Завершить Photo Curator", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        NSApp.mainMenu = mainMenu
    }

    private func configureStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "camera.aperture", accessibilityDescription: "Photo Curator")
        }
        let menu = NSMenu()
        statusMenuItem = NSMenuItem(title: "Backend остановлен", action: nil, keyEquivalent: "")
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)
        openMenuItem = menu.addItem(withTitle: "Открыть в браузере", action: #selector(openService(_:)), keyEquivalent: "")
        toggleMenuItem = menu.addItem(withTitle: "Запустить backend", action: #selector(toggleBackend(_:)), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Настройки…", action: #selector(showSettings(_:)), keyEquivalent: "")
        menu.addItem(withTitle: "Завершить Photo Curator", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        statusItem.menu = menu
    }

    @objc private func showAbout(_ sender: Any?) {
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Photo Curator",
            .applicationVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            .credits: NSAttributedString(string: "Локальный помощник для отбора фотографий Apple Photos."),
        ])
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func showSettings(_ sender: Any?) {
        if settingsWindow == nil { settingsWindow = makeSettingsWindow() }
        refreshControls()
        settingsWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func openService(_ sender: Any?) {
        guard let url = serviceURL else {
            if backend?.isRunning != true { startBackend(openBrowser: true) }
            return
        }
        NSWorkspace.shared.open(url)
    }

    @objc private func toggleBackend(_ sender: Any?) {
        if backend?.isRunning == true {
            stopBackend(sender)
        } else {
            startBackend(openBrowser: true)
        }
    }

    private func startBackend(openBrowser: Bool) {
        guard backend?.isRunning != true else {
            if openBrowser { openService(nil) }
            return
        }
        guard let resources = Bundle.main.resourceURL else {
            updateState(.failed("Не найден каталог ресурсов приложения"))
            return
        }
        let executable = resources.appendingPathComponent("backend/photo-curator-backend")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            updateState(.failed("Не найден встроенный backend"))
            showLaunchFailure()
            return
        }

        serviceURL = nil
        outputBuffer = ""
        updateState(.starting)
        let process = Process()
        let pipe = Pipe()
        process.executableURL = executable
        process.arguments = ["--no-browser"]
        process.currentDirectoryURL = resources.appendingPathComponent("backend")
        process.standardOutput = pipe
        process.standardError = pipe
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment

        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.consumeOutput(text, openBrowser: openBrowser) }
        }
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async { self?.backendDidExit(finished.terminationStatus) }
        }
        do {
            try process.run()
            backend = process
            outputPipe = pipe
        } catch {
            pipe.fileHandleForReading.readabilityHandler = nil
            backend = nil
            outputPipe = nil
            updateState(.failed(error.localizedDescription))
            showLaunchFailure()
        }
    }

    @objc private func stopBackend(_ sender: Any?) {
        guard let process = backend, process.isRunning else {
            backendDidExit(0)
            return
        }
        updateState(.stopping)
        process.terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak process] in
            guard let process, process.isRunning else { return }
            kill(process.processIdentifier, SIGKILL)
        }
    }

    private func consumeOutput(_ text: String, openBrowser: Bool) {
        outputBuffer += text
        while let newline = outputBuffer.firstIndex(of: "\n") {
            let line = String(outputBuffer[..<newline]).trimmingCharacters(in: .whitespacesAndNewlines)
            outputBuffer.removeSubrange(...newline)
            if line.hasPrefix("Photo Curator запущен: ") {
                let rawURL = String(line.dropFirst("Photo Curator запущен: ".count))
                if let url = URL(string: rawURL) {
                    serviceURL = url
                    updateState(.running)
                    if openBrowser { NSWorkspace.shared.open(url) }
                }
            } else if !line.isEmpty {
                appendToLauncherLog(line)
            }
        }
    }

    private func backendDidExit(_ code: Int32) {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
        backend = nil
        serviceURL = nil
        if terminationPending {
            NSApp.reply(toApplicationShouldTerminate: true)
            return
        }
        updateState(
            code == 0 || code == SIGTERM || stateIsStopping
                ? .stopped
                : .failed("Backend завершился с кодом \(code)")
        )
    }

    private var stateIsStopping: Bool {
        if case .stopping = state { return true }
        return false
    }

    private func updateState(_ newState: BackendState) {
        state = newState
        refreshControls()
    }

    private func refreshControls() {
        let text: String
        let isRunning: Bool
        switch state {
        case .starting:
            text = "Backend запускается…"
            isRunning = true
        case .running:
            text = "Backend запущен"
            isRunning = true
        case .stopping:
            text = "Backend останавливается…"
            isRunning = true
        case .stopped:
            text = "Backend остановлен"
            isRunning = false
        case .failed(let message):
            text = "Ошибка: \(message)"
            isRunning = false
        }
        statusMenuItem?.title = text
        statusLabel?.stringValue = text
        openMenuItem?.isEnabled = serviceURL != nil
        openButton?.isEnabled = serviceURL != nil
        toggleMenuItem?.title = isRunning ? "Остановить backend" : "Запустить backend"
        toggleMenuItem?.isEnabled = !(stateIsStopping || isStarting)
        toggleButton?.title = isRunning ? "Остановить backend" : "Запустить backend"
        toggleButton?.isEnabled = !(stateIsStopping || isStarting)
    }

    private var isStarting: Bool {
        if case .starting = state { return true }
        return false
    }

    private func makeSettingsWindow() -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 250),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Настройки Photo Curator"
        window.center()
        window.isReleasedWhenClosed = false
        window.delegate = self

        let title = NSTextField(labelWithString: "Локальный backend")
        title.font = .systemFont(ofSize: 22, weight: .bold)
        let status = NSTextField(labelWithString: "")
        status.textColor = .secondaryLabelColor
        statusLabel = status
        let copy = NSTextField(wrappingLabelWithString: "Backend работает только на 127.0.0.1. При завершении Photo Curator он будет остановлен автоматически.")
        copy.textColor = .secondaryLabelColor

        let open = NSButton(title: "Открыть в браузере", target: self, action: #selector(openService(_:)))
        open.bezelStyle = .rounded
        openButton = open
        let toggle = NSButton(title: "Остановить backend", target: self, action: #selector(toggleBackend(_:)))
        toggle.bezelStyle = .rounded
        toggleButton = toggle
        let buttons = NSStackView(views: [open, toggle])
        buttons.orientation = .horizontal
        buttons.spacing = 10

        let stack = NSStackView(views: [title, status, copy, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView?.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 28),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -28),
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 28),
        ])
        return window
    }

    private func showLaunchFailure() {
        showSettings(nil)
        let alert = NSAlert()
        alert.messageText = "Не удалось запустить Photo Curator"
        alert.informativeText = statusLabel?.stringValue ?? "Неизвестная ошибка"
        alert.alertStyle = .warning
        alert.runModal()
    }

    private func appendToLauncherLog(_ line: String) {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let directory = home.appendingPathComponent("Library/Logs/PhotoCurator", isDirectory: true)
        let file = directory.appendingPathComponent("launcher.log")
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let safeLine = line.replacingOccurrences(
            of: #"token=[^ &\"]+"#,
            with: "token=REDACTED",
            options: .regularExpression
        )
        let data = Data("\(ISO8601DateFormatter().string(from: Date())) \(safeLine)\n".utf8)
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: data)
            return
        }
        if let handle = try? FileHandle(forWritingTo: file) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
            try? handle.close()
        }
    }
}

@main
@MainActor
struct PhotoCuratorApplication {
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.run()
    }
}
