import Darwin
import Foundation

enum NativeWorkerClientError: LocalizedError {
    case missingExecutable
    case invalidResponse
    case worker(type: String?, message: String)

    var errorDescription: String? {
        switch self {
        case .missingExecutable:
            return "Не найден встроенный движок Photo Curator"
        case .invalidResponse:
            return "Движок вернул некорректный ответ"
        case .worker(_, let message):
            return message
        }
    }

    var workerType: String? {
        guard case .worker(let type, _) = self else { return nil }
        return type
    }
}

final class NativeWorkerClient: @unchecked Sendable {
    private let stateLock = NSLock()
    private let writeLock = NSLock()
    private let readLock = NSLock()
    private let responseCondition = NSCondition()
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var responseBuffer = Data()
    private var nextRequestID = 0
    private var workerPID: pid_t = 0
    private var completedResponses: [String: NativeWorkerResponseEnvelope] = [:]
    private var progressHandlers: [String: ([String: JSONValue]) -> Void] = [:]
    private var terminalError: NativeWorkerClientError?

    func start() throws {
        stateLock.lock()
        defer { stateLock.unlock() }
        try startLocked()
    }

    private func requestValue(
        method: String,
        params: JSONValue,
        progress: (([String: JSONValue]) -> Void)? = nil
    ) throws -> JSONValue {
        stateLock.lock()
        do {
            try startLocked()
            stateLock.unlock()
        } catch {
            stateLock.unlock()
            throw error
        }

        writeLock.lock()
        guard let input else {
            writeLock.unlock()
            throw NativeWorkerClientError.invalidResponse
        }
        nextRequestID += 1
        let requestID = "native-\(nextRequestID)"
        let request = NativeWorkerRequestEnvelope(
            schemaVersion: 1,
            id: requestID,
            method: method,
            params: params
        )
        var data: Data
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            data = try encoder.encode(request)
            data.append(0x0A)
        } catch {
            writeLock.unlock()
            throw error
        }
        responseCondition.lock()
        if let progress { progressHandlers[requestID] = progress }
        responseCondition.unlock()
        do {
            try writeAll(data, to: input)
            writeLock.unlock()
        } catch {
            writeLock.unlock()
            responseCondition.lock()
            progressHandlers.removeValue(forKey: requestID)
            responseCondition.unlock()
            throw error
        }
        defer {
            responseCondition.lock()
            progressHandlers.removeValue(forKey: requestID)
            responseCondition.unlock()
        }

        while true {
            responseCondition.lock()
            if let response = completedResponses.removeValue(forKey: requestID) {
                responseCondition.unlock()
                return try result(from: response, requestID: requestID)
            }
            if let terminalError {
                responseCondition.unlock()
                throw terminalError
            }
            responseCondition.unlock()

            if readLock.try() {
                // Another reader may have routed our reply between the first check
                // and acquiring readLock. Never block on the pipe with a ready reply.
                responseCondition.lock()
                let alreadyRouted = completedResponses[requestID] != nil || terminalError != nil
                responseCondition.unlock()
                if alreadyRouted {
                    readLock.unlock()
                    continue
                }
                let response: NativeWorkerResponseEnvelope
                do {
                    response = try readResponse()
                } catch {
                    readLock.unlock()
                    let failure = recordTerminal(error)
                    throw failure
                }
                do {
                    try route(response)
                    readLock.unlock()
                } catch {
                    readLock.unlock()
                    throw recordTerminal(error)
                }
            } else {
                responseCondition.lock()
                _ = responseCondition.wait(until: Date(timeIntervalSinceNow: 0.2))
                responseCondition.unlock()
            }
        }
    }

    func request<Params: Encodable, Response: Decodable>(
        method: String,
        params: Params,
        as responseType: Response.Type
    ) throws -> Response {
        do {
            let paramsValue = try jsonValue(from: params)
            let result = try requestValue(method: method, params: paramsValue)
            return try decode(responseType, from: result)
        } catch let error as NativeWorkerClientError {
            throw error
        } catch {
            throw NativeWorkerClientError.invalidResponse
        }
    }

    func request<Params: Encodable, Response: Decodable, Event: Decodable>(
        method: String,
        params: Params,
        as responseType: Response.Type,
        progressAs eventType: Event.Type,
        progress: @escaping (Event) -> Void
    ) throws -> Response {
        do {
            let paramsValue = try jsonValue(from: params)
            let result = try requestValue(method: method, params: paramsValue) { event in
                guard let decoded = try? self.decode(eventType, from: .object(event)) else {
                    return
                }
                progress(decoded)
            }
            return try decode(responseType, from: result)
        } catch let error as NativeWorkerClientError {
            throw error
        } catch {
            throw NativeWorkerClientError.invalidResponse
        }
    }

    func stop() {
        guard stateLock.try() else {
            if workerPID > 0 { Darwin.kill(workerPID, SIGTERM) }
            return
        }
        defer { stateLock.unlock() }
        guard let process else { return }
        if process.isRunning, let input {
            writeLock.lock()
            nextRequestID += 1
            let request = NativeWorkerRequestEnvelope(
                schemaVersion: 1,
                id: "shutdown-\(nextRequestID)",
                method: "shutdown",
                params: .object([:])
            )
            if var data = try? JSONEncoder().encode(request) {
                data.append(0x0A)
                try? writeAll(data, to: input)
            }
            writeLock.unlock()
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
        responseCondition.lock()
        terminalError = nil
        completedResponses.removeAll()
        progressHandlers.removeAll()
        responseCondition.unlock()
    }

    private func readResponse() throws -> NativeWorkerResponseEnvelope {
        guard let output else { throw NativeWorkerClientError.invalidResponse }
        while true {
            if let newline = responseBuffer.firstIndex(of: 0x0A) {
                let line = responseBuffer[..<newline]
                responseBuffer.removeSubrange(...newline)
                return try JSONDecoder().decode(NativeWorkerResponseEnvelope.self, from: Data(line))
            }
            var chunk = [UInt8](repeating: 0, count: 64 * 1024)
            let count = Darwin.read(output.fileDescriptor, &chunk, chunk.count)
            if count < 0 { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
            guard count > 0 else { throw NativeWorkerClientError.invalidResponse }
            responseBuffer.append(contentsOf: chunk.prefix(count))
        }
    }

    private func route(_ response: NativeWorkerResponseEnvelope) throws {
        guard response.schemaVersion == 1, !response.id.isEmpty else {
            throw NativeWorkerClientError.invalidResponse
        }
        let requestID = response.id
        responseCondition.lock()
        let handler = progressHandlers[requestID]
        if response.event == nil {
            completedResponses[requestID] = response
            responseCondition.broadcast()
        }
        responseCondition.unlock()
        if let event = response.event {
            handler?(event)
        }
    }

    private func result(
        from response: NativeWorkerResponseEnvelope, requestID: String
    ) throws -> JSONValue {
        guard response.schemaVersion == 1, response.id == requestID else {
            throw NativeWorkerClientError.invalidResponse
        }
        if let error = response.error {
            throw NativeWorkerClientError.worker(type: error.type, message: error.message)
        }
        guard let result = response.result else {
            throw NativeWorkerClientError.invalidResponse
        }
        return result
    }

    private func jsonValue<Params: Encodable>(from params: Params) throws -> JSONValue {
        let encoded = try JSONEncoder().encode(params)
        return try JSONDecoder().decode(JSONValue.self, from: encoded)
    }

    private func decode<Response: Decodable>(
        _ responseType: Response.Type,
        from value: JSONValue
    ) throws -> Response {
        let encoded = try JSONEncoder().encode(value)
        return try JSONDecoder().decode(responseType, from: encoded)
    }

    private func recordTerminal(_ error: Error) -> NativeWorkerClientError {
        let failure = error as? NativeWorkerClientError ?? .invalidResponse
        responseCondition.lock()
        terminalError = failure
        responseCondition.broadcast()
        responseCondition.unlock()
        return failure
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
        responseCondition.lock()
        terminalError = .invalidResponse
        responseCondition.broadcast()
        responseCondition.unlock()
    }
}
