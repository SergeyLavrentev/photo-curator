import AppKit
import Foundation

@main
struct ImagePipelineBehavior {
    @MainActor
    static func main() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("photo-curator-image-pipeline-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let imagePath = directory.appendingPathComponent("fixture.png")
        try fixturePNG().write(to: imagePath)

        try await verifyCoalescing(path: imagePath.path)
        try await verifyConsumerCancellation(path: imagePath.path)
        try await verifyPriorityUpgrade(path: imagePath.path)
        try await verifyCacheSeparation(path: imagePath.path)
        print("image-pipeline-behavior: PASS")
    }

    @MainActor
    private static func verifyCoalescing(path: String) async throws {
        ThumbnailLoader.resetTestState()
        ThumbnailLoader.testDecodeDelayNanoseconds = 50_000_000
        let first = ThumbnailLoader()
        let second = ThumbnailLoader()
        async let firstLoad: Void = first.load(path: path, maxPixelSize: 320, cacheEpoch: 1)
        async let secondLoad: Void = second.load(path: path, maxPixelSize: 320, cacheEpoch: 1)
        _ = await (firstLoad, secondLoad)
        try require(ThumbnailLoader.testDecodeStarts == 1, "coalescing started multiple decodes")
        try require(first.image != nil && second.image != nil, "coalesced consumers missed image")
    }

    @MainActor
    private static func verifyConsumerCancellation(path: String) async throws {
        ThumbnailLoader.resetTestState()
        ThumbnailLoader.testDecodeDelayNanoseconds = 100_000_000
        let cancelledLoader = ThumbnailLoader()
        let retainedLoader = ThumbnailLoader()
        let cancelled = Task {
            await cancelledLoader.load(path: path, maxPixelSize: 321, cacheEpoch: 2)
        }
        let retained = Task {
            await retainedLoader.load(path: path, maxPixelSize: 321, cacheEpoch: 2)
        }
        await Task.yield()
        cancelled.cancel()
        await cancelled.value
        await retained.value
        try require(ThumbnailLoader.testDecodeStarts == 1, "consumer cancellation broke coalescing")
        try require(retainedLoader.image != nil, "one cancelled consumer cancelled shared decode")
    }

    @MainActor
    private static func verifyPriorityUpgrade(path: String) async throws {
        ThumbnailLoader.resetTestState()
        ThumbnailLoader.testDecodeDelayNanoseconds = 120_000_000
        let utilityLoader = ThumbnailLoader()
        let visibleLoader = ThumbnailLoader()
        let utility = Task {
            await utilityLoader.load(
                path: path, maxPixelSize: 900, cacheEpoch: 3, priority: .utility
            )
        }
        await Task.yield()
        let visible = Task {
            await visibleLoader.load(
                path: path, maxPixelSize: 900, cacheEpoch: 3, priority: .userInitiated
            )
        }
        await utility.value
        await visible.value
        try require(ThumbnailLoader.testDecodeStarts == 2, "visible request did not upgrade priority")
        try require(visibleLoader.image != nil, "priority-upgraded request missed image")
    }

    @MainActor
    private static func verifyCacheSeparation(path: String) async throws {
        ThumbnailLoader.resetTestState()
        let thumbnail = ThumbnailLoader()
        let review = ThumbnailLoader()
        await thumbnail.load(path: path, maxPixelSize: 320, cacheEpoch: 4)
        await review.load(path: path, maxPixelSize: 1_400, cacheEpoch: 4)
        try require(ThumbnailLoader.testDecodeStarts == 2, "thumbnail and review cache keys collided")
        try require(thumbnail.image != nil && review.image != nil, "separate caches missed image")
    }

    private static func fixturePNG() throws -> Data {
        guard let data = Data(
            base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR4nGP8z8DAwMDAxMDAwAAAHgABboVhmAAAAABJRU5ErkJggg=="
        ) else { throw BehaviorError("invalid PNG fixture") }
        return data
    }

    private static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw BehaviorError(message) }
    }
}

private struct BehaviorError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
