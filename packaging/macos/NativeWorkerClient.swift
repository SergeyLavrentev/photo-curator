import Darwin
import Foundation

enum NativeWorkerClientError: LocalizedError {
    case missingExecutable
    case invalidResponse
    case worker(String)

    var errorDescription: String? {
        switch self {
        case .missingExecutable:
            return "Не найден встроенный движок Photo Curator"
        case .invalidResponse:
            return "Движок вернул некорректный ответ"
        case .worker(let message):
            return message
        }
    }
}

final class NativeWorkerClient: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var responseBuffer = Data()
    private var nextRequestID = 0
    private var workerPID: pid_t = 0

    func start() throws {
        lock.lock()
        defer { lock.unlock() }
        try startLocked()
    }

    func request(
        method: String,
        params: [String: Any] = [:],
        progress: (([String: Any]) -> Void)? = nil
    ) throws -> Any {
        lock.lock()
        defer { lock.unlock() }
        try startLocked()
        guard let input else { throw NativeWorkerClientError.invalidResponse }

        nextRequestID += 1
        let requestID = "native-\(nextRequestID)"
        let request: [String: Any] = [
            "schema_version": 1,
            "id": requestID,
            "method": method,
            "params": params,
        ]
        var data = try JSONSerialization.data(withJSONObject: request, options: [.sortedKeys])
        data.append(0x0A)
        try writeAll(data, to: input)

        while true {
            let response = try readResponseLocked()
            guard response["schema_version"] as? Int == 1,
                  response["id"] as? String == requestID
            else { throw NativeWorkerClientError.invalidResponse }
            if let event = response["event"] as? [String: Any] {
                progress?(event)
                continue
            }
            if let error = response["error"] as? [String: Any] {
                throw NativeWorkerClientError.worker(
                    error["message"] as? String ?? "Ошибка движка"
                )
            }
            guard let result = response["result"] else {
                throw NativeWorkerClientError.invalidResponse
            }
            return result
        }
    }

    func stop() {
        guard lock.try() else {
            if workerPID > 0 { Darwin.kill(workerPID, SIGTERM) }
            return
        }
        defer { lock.unlock() }
        guard let process else { return }
        if process.isRunning, let input {
            nextRequestID += 1
            let request: [String: Any] = [
                "schema_version": 1,
                "id": "shutdown-\(nextRequestID)",
                "method": "shutdown",
                "params": [:],
            ]
            if var data = try? JSONSerialization.data(withJSONObject: request) {
                data.append(0x0A)
                try? writeAll(data, to: input)
            }
            for _ in 0..<20 where process.isRunning {
                Thread.sleep(forTimeInterval: 0.1)
            }
            if process.isRunning { process.terminate() }
            process.waitUntilExit()
        }
        clearLocked()
    }

    private func startLocked() throws {
        if process?.isRunning == true { return }
        guard let resources = Bundle.main.resourceURL else {
            throw NativeWorkerClientError.missingExecutable
        }
        let executable = resources.appendingPathComponent("backend/photo-curator-backend")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw NativeWorkerClientError.missingExecutable
        }

        var inputDescriptors: [Int32] = [0, 0]
        var outputDescriptors: [Int32] = [0, 0]
        guard Darwin.pipe(&inputDescriptors) == 0, Darwin.pipe(&outputDescriptors) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        let childInput = FileHandle(fileDescriptor: inputDescriptors[0], closeOnDealloc: true)
        let parentInput = FileHandle(fileDescriptor: inputDescriptors[1], closeOnDealloc: true)
        let parentOutput = FileHandle(fileDescriptor: outputDescriptors[0], closeOnDealloc: true)
        let childOutput = FileHandle(fileDescriptor: outputDescriptors[1], closeOnDealloc: true)
        let process = Process()
        process.executableURL = executable
        process.arguments = ProcessInfo.processInfo.environment["PHOTO_CURATOR_NATIVE_DEMO"] == "1"
            ? ["native-worker", "--demo"] : ["native-worker"]
        process.currentDirectoryURL = resources.appendingPathComponent("backend")
        process.standardInput = childInput
        process.standardOutput = childOutput
        process.standardError = try logHandle()
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PHOTO_CURATOR_PHOTOKIT_HELPER"] = resources
            .appendingPathComponent("native/photo-curator-photokit").path
        environment["PHOTO_CURATOR_VISION_HELPER"] = resources
            .appendingPathComponent("native/photo-curator-vision").path
        environment["PHOTO_CURATOR_PUBLISH_HELPER"] = resources
            .appendingPathComponent("native/photo-curator-publish").path
        process.environment = environment
        do {
            try process.run()
        } catch {
            try? childInput.close()
            try? parentInput.close()
            try? parentOutput.close()
            try? childOutput.close()
            throw error
        }
        try childInput.close()
        try childOutput.close()
        self.process = process
        workerPID = process.processIdentifier
        input = parentInput
        output = parentOutput
        responseBuffer.removeAll(keepingCapacity: true)
    }

    private func readResponseLocked() throws -> [String: Any] {
        guard let output else { throw NativeWorkerClientError.invalidResponse }
        while true {
            if let newline = responseBuffer.firstIndex(of: 0x0A) {
                let line = responseBuffer[..<newline]
                responseBuffer.removeSubrange(...newline)
                let value = try JSONSerialization.jsonObject(with: Data(line))
                guard let response = value as? [String: Any] else {
                    throw NativeWorkerClientError.invalidResponse
                }
                return response
            }
            var chunk = [UInt8](repeating: 0, count: 64 * 1024)
            let count = Darwin.read(output.fileDescriptor, &chunk, chunk.count)
            if count < 0 { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
            guard count > 0 else { throw NativeWorkerClientError.invalidResponse }
            responseBuffer.append(contentsOf: chunk.prefix(count))
        }
    }

    private func writeAll(_ data: Data, to handle: FileHandle) throws {
        try data.withUnsafeBytes { rawBuffer in
            guard var pointer = rawBuffer.baseAddress else { return }
            var remaining = rawBuffer.count
            while remaining > 0 {
                let written = Darwin.write(handle.fileDescriptor, pointer, remaining)
                if written < 0 { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
                remaining -= written
                pointer = pointer.advanced(by: written)
            }
        }
    }

    private func logHandle() throws -> FileHandle {
        let maxLogBytes: UInt64 = 5 * 1024 * 1024
        let directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/PhotoCurator", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("native-worker.log")
        let previous = directory.appendingPathComponent("native-worker.previous.log")
        let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize
        if UInt64(size ?? 0) >= maxLogBytes {
            try? FileManager.default.removeItem(at: previous)
            try FileManager.default.moveItem(at: file, to: previous)
        }
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: file)
        try handle.seekToEnd()
        return handle
    }

    private func clearLocked() {
        try? input?.close()
        try? output?.close()
        input = nil
        output = nil
        process = nil
        workerPID = 0
        responseBuffer.removeAll()
    }
}
