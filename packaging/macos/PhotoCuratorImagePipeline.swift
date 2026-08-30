import AppKit
import ImageIO
import SwiftUI

private actor ThumbnailDecodeLimiter {
    private struct Waiter {
        let id: UUID
        let continuation: CheckedContinuation<Bool, Never>
    }

    private let limit: Int
    private var active = 0
    private var waiters: [Waiter] = []

    init(limit: Int) { self.limit = limit }

    func acquire() async -> Bool {
        guard !Task.isCancelled else { return false }
        if active < limit {
            active += 1
            return true
        }
        let id = UUID()
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                if Task.isCancelled {
                    continuation.resume(returning: false)
                } else {
                    waiters.append(Waiter(id: id, continuation: continuation))
                }
            }
        } onCancel: {
            Task { await self.cancelWaiter(id) }
        }
    }

    private func cancelWaiter(_ id: UUID) {
        guard let index = waiters.firstIndex(where: { $0.id == id }) else { return }
        waiters.remove(at: index).continuation.resume(returning: false)
    }

    func release() {
        if waiters.isEmpty {
            active = max(0, active - 1)
        } else {
            waiters.removeFirst().continuation.resume(returning: true)
        }
    }
}

private let thumbnailDecodeLimiter = ThumbnailDecodeLimiter(limit: 4)

@MainActor
private final class ThumbnailCacheEpoch: ObservableObject {
    static let shared = ThumbnailCacheEpoch()
    @Published private(set) var value = 0

    func advance() {
        value += 1
    }
}

private struct ThumbnailRequestIdentity: Hashable {
    let path: String
    let maxPixelSize: Int
    let cacheEpoch: Int
}

@MainActor
final class ThumbnailLoader: ObservableObject {
    private struct InFlightDecode {
        let id: UUID
        let priority: TaskPriority
        let task: Task<CGImage?, Never>
        var consumers: Set<UUID>
    }

    private struct PrefetchWork {
        let id: UUID
        let task: Task<Void, Never>
    }

    private static let thumbnailCache: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.countLimit = 500
        cache.totalCostLimit = 96 * 1024 * 1024
        return cache
    }()
    private static let reviewCache: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.countLimit = 32
        cache.totalCostLimit = 160 * 1024 * 1024
        return cache
    }()
    private static var inFlight: [String: InFlightDecode] = [:]
    private static var prefetchWork: [String: PrefetchWork] = [:]
    @Published var image: NSImage?
    @Published var failed = false
    private var currentRequestKey: String?

    static func invalidateAll() {
        thumbnailCache.removeAllObjects()
        reviewCache.removeAllObjects()
        prefetchWork.values.forEach { $0.task.cancel() }
        prefetchWork.removeAll()
        inFlight.values.forEach { $0.task.cancel() }
        inFlight.removeAll()
        ThumbnailCacheEpoch.shared.advance()
    }

    static func prefetch(paths: [String], maxPixelSize: Int) {
        var seen: Set<String> = []
        let cacheEpoch = ThumbnailCacheEpoch.shared.value
        let orderedPaths = paths.filter { seen.insert($0).inserted }
        let desiredKeys = Set(orderedPaths.map { "\(cacheEpoch)#\($0)#\(maxPixelSize)" })
        let obsoleteKeys = prefetchWork.keys.filter { !desiredKeys.contains($0) }
        for key in obsoleteKeys {
            prefetchWork.removeValue(forKey: key)?.task.cancel()
        }
        for path in orderedPaths {
            let key = "\(cacheEpoch)#\(path)#\(maxPixelSize)"
            guard prefetchWork[key] == nil else { continue }
            let id = UUID()
            let task = Task {
                let loader = ThumbnailLoader()
                await loader.load(
                    path: path,
                    maxPixelSize: maxPixelSize,
                    cacheEpoch: cacheEpoch,
                    priority: .utility
                )
                Self.finishPrefetch(key: key, id: id)
            }
            prefetchWork[key] = PrefetchWork(id: id, task: task)
        }
    }

    private static func finishPrefetch(key: String, id: UUID) {
        guard prefetchWork[key]?.id == id else { return }
        prefetchWork.removeValue(forKey: key)
    }

    private static func cache(maxPixelSize: Int) -> NSCache<NSString, NSImage> {
        maxPixelSize <= 640 ? thumbnailCache : reviewCache
    }

    private static func decode(
        path: String,
        maxPixelSize: Int,
        requestKey: String,
        priority: TaskPriority,
        consumerID: UUID
    ) -> InFlightDecode {
        if var existing = inFlight[requestKey],
           existing.priority.rawValue >= priority.rawValue {
            existing.consumers.insert(consumerID)
            inFlight[requestKey] = existing
            return existing
        }
        inFlight[requestKey]?.task.cancel()
        let task = Task.detached(priority: priority) { () -> CGImage? in
            guard await thumbnailDecodeLimiter.acquire() else { return nil }
            guard !Task.isCancelled else {
                await thumbnailDecodeLimiter.release()
                return nil
            }
            let decoded: CGImage?
            if let source = CGImageSourceCreateWithURL(
                URL(fileURLWithPath: path) as CFURL,
                nil
            ) {
                let options: [CFString: Any] = [
                    kCGImageSourceCreateThumbnailFromImageAlways: true,
                    kCGImageSourceCreateThumbnailWithTransform: true,
                    kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
                    kCGImageSourceShouldCacheImmediately: true,
                ]
                decoded = CGImageSourceCreateThumbnailAtIndex(
                    source, 0, options as CFDictionary
                )
            } else {
                decoded = nil
            }
            await thumbnailDecodeLimiter.release()
            return decoded
        }
        let request = InFlightDecode(
            id: UUID(),
            priority: priority,
            task: task,
            consumers: [consumerID]
        )
        inFlight[requestKey] = request
        return request
    }

    private static func cancelConsumer(
        requestKey: String,
        requestID: UUID,
        consumerID: UUID
    ) {
        guard var request = inFlight[requestKey], request.id == requestID else { return }
        request.consumers.remove(consumerID)
        if request.consumers.isEmpty {
            request.task.cancel()
            inFlight.removeValue(forKey: requestKey)
        } else {
            inFlight[requestKey] = request
        }
    }

    private static func finishConsumer(
        requestKey: String,
        requestID: UUID,
        consumerID: UUID
    ) {
        guard var request = inFlight[requestKey], request.id == requestID else { return }
        request.consumers.remove(consumerID)
        if request.consumers.isEmpty {
            inFlight.removeValue(forKey: requestKey)
        } else {
            inFlight[requestKey] = request
        }
    }

    func load(
        path: String,
        maxPixelSize: Int,
        cacheEpoch: Int,
        priority: TaskPriority = .userInitiated
    ) async {
        let key = "\(path)#\(maxPixelSize)"
        let requestKey = "\(cacheEpoch)#\(key)"
        if currentRequestKey != requestKey {
            image = nil
            currentRequestKey = requestKey
        }
        failed = false
        // Preview paths are service-owned and immutable between explicit repair runs.
        // Repair clears this cache before replacing any preview files.
        guard !Task.isCancelled else { return }
        let cache = Self.cache(maxPixelSize: maxPixelSize)
        if let cached = cache.object(forKey: key as NSString) {
            image = cached
            return
        }
        let consumerID = UUID()
        let request = Self.decode(
            path: path,
            maxPixelSize: maxPixelSize,
            requestKey: requestKey,
            priority: priority,
            consumerID: consumerID
        )
        let decoded = await withTaskCancellationHandler {
            await request.task.value
        } onCancel: {
            Task { @MainActor in
                Self.cancelConsumer(
                    requestKey: requestKey,
                    requestID: request.id,
                    consumerID: consumerID
                )
            }
        }
        Self.finishConsumer(
            requestKey: requestKey,
            requestID: request.id,
            consumerID: consumerID
        )
        guard !Task.isCancelled,
              !request.task.isCancelled,
              currentRequestKey == requestKey,
              ThumbnailCacheEpoch.shared.value == cacheEpoch
        else { return }
        guard let decoded else {
            image = nil
            failed = true
            return
        }
        let rendered = NSImage(
            cgImage: decoded,
            size: NSSize(width: decoded.width, height: decoded.height)
        )
        let pixels = decoded.width * decoded.height
        cache.setObject(rendered, forKey: key as NSString, cost: max(1, pixels * 4))
        image = rendered
    }
}

struct CachedThumbnail: View {
    let path: String
    var maxPixelSize = 640
    var contentMode: ContentMode = .fill
    @StateObject private var loader = ThumbnailLoader()
    @ObservedObject private var cacheEpoch = ThumbnailCacheEpoch.shared

    var body: some View {
        Group {
            if let image = loader.image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.medium)
                    .aspectRatio(contentMode: contentMode)
            } else if loader.failed {
                Rectangle()
                    .fill(.quaternary)
                    .overlay {
                        Image(systemName: "photo.badge.exclamationmark")
                            .font(.title2)
                            .foregroundStyle(.secondary)
                    }
            } else {
                Rectangle()
                    .fill(.quaternary)
                    .overlay(ProgressView().controlSize(.small))
            }
        }
        .task(
            id: ThumbnailRequestIdentity(
                path: path,
                maxPixelSize: maxPixelSize,
                cacheEpoch: cacheEpoch.value
            )
        ) {
            await loader.load(
                path: path,
                maxPixelSize: maxPixelSize,
                cacheEpoch: cacheEpoch.value
            )
        }
    }
}
