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
final class ThumbnailLoader: ObservableObject {
    private static let cache: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.countLimit = 500
        cache.totalCostLimit = 256 * 1024 * 1024
        return cache
    }()
    @Published var image: NSImage?
    @Published var failed = false

    static func invalidateAll() {
        cache.removeAllObjects()
    }

    static func prefetch(paths: [String], maxPixelSize: Int) {
        for path in Set(paths) {
            Task {
                let loader = ThumbnailLoader()
                await loader.load(
                    path: path,
                    maxPixelSize: maxPixelSize,
                    priority: .utility
                )
            }
        }
    }

    func load(
        path: String,
        maxPixelSize: Int,
        priority: TaskPriority = .userInitiated
    ) async {
        failed = false
        // Preview paths are service-owned and immutable between explicit repair runs.
        // Repair clears this cache before replacing any preview files.
        let key = "\(path)#\(maxPixelSize)" as NSString
        guard !Task.isCancelled else { return }
        if let cached = Self.cache.object(forKey: key) {
            image = cached
            return
        }
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
        let decoded = await withTaskCancellationHandler {
            await task.value
        } onCancel: {
            task.cancel()
        }
        guard !Task.isCancelled else { return }
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
        Self.cache.setObject(rendered, forKey: key, cost: max(1, pixels * 4))
        image = rendered
    }
}

struct CachedThumbnail: View {
    let path: String
    var maxPixelSize = 640
    var contentMode: ContentMode = .fill
    @StateObject private var loader = ThumbnailLoader()

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
        .task(id: path) {
            await loader.load(path: path, maxPixelSize: maxPixelSize)
        }
    }
}
