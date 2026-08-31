import AppKit
import Foundation
import Photos

struct AlbumPayload: Encodable {
    let id: String
    let name: String
    let is_shared: Bool
    let photo_count: Int
    let video_count: Int
}

struct AssetPayload: Encodable {
    let uuid: String
    let original_filename: String?
    let current_filename: String?
    let taken_at: String?
    let creation_timestamp: Double?
    let modification_timestamp: Double?
    let width: Int
    let height: Int
    let orientation: Int?
    let favorite: Bool
    let hidden: Bool
    let has_adjustments: Bool
    let is_live_photo: Bool
    let is_burst: Bool
    let burst_key: String?
    let burst_default_pick: Bool
    let is_missing: Bool
    let is_photo: Bool
    let media_type: String
    let media_subtypes: UInt
    let edit_state: String
    let source_revision: String
    let source_path: String?
    let provider_error: String?
    let review_render: Bool
}

struct AssetIdentifiersRequest: Decodable {
    let asset_identifiers: [String]
}

struct AssetProgressFrame: Encodable {
    let type = "progress"
    let phase = "render"
    let processed: Int
    let total: Int
    let asset_identifier: String
}

struct AssetResultFrame: Encodable {
    let type = "result"
    let assets: [AssetPayload]
}

// Render workers report progress concurrently.  FileHandle writes are not a
// single atomic JSONL operation, so serialize each frame to keep the Python
// transport from receiving two adjacent JSON documents on one line.
let stdoutLock = NSLock()

enum PhotoKitError: LocalizedError {
    case invalidArguments
    case authorizationDenied
    case albumMissing

    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "Invalid PhotoKit helper arguments"
        case .authorizationDenied: return "Photo Library access was not granted"
        case .albumMissing: return "PhotoKit album could not be found"
        }
    }
}

func requestAuthorization() throws {
    var status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    if status == .notDetermined {
        var finished = false
        let lock = NSLock()
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { value in
            lock.lock()
            status = value
            finished = true
            lock.unlock()
        }
        while true {
            lock.lock()
            let done = finished
            lock.unlock()
            if done { break }
            _ = RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1))
        }
    }
    guard status == .authorized || status == .limited else {
        throw PhotoKitError.authorizationDenied
    }
}

func fetchAlbum(_ identifier: String) throws -> PHAssetCollection {
    guard let album = PHAssetCollection.fetchAssetCollections(
        withLocalIdentifiers: [identifier], options: nil
    ).firstObject else { throw PhotoKitError.albumMissing }
    return album
}

func albumPayloads(subtype: PHAssetCollectionSubtype, shared: Bool) -> [AlbumPayload] {
    let collections = PHAssetCollection.fetchAssetCollections(
        with: .album, subtype: subtype, options: nil
    )
    var result: [AlbumPayload] = []
    collections.enumerateObjects { collection, _, _ in
        let assets = PHAsset.fetchAssets(in: collection, options: nil)
        var photos = 0
        var videos = 0
        assets.enumerateObjects { asset, _, _ in
            if asset.mediaType == .image { photos += 1 }
            if asset.mediaType == .video { videos += 1 }
        }
        result.append(AlbumPayload(
            id: collection.localIdentifier,
            name: collection.localizedTitle ?? "Без названия",
            is_shared: shared,
            photo_count: photos,
            video_count: videos
        ))
    }
    return result.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
}

func safeStem(_ identifier: String) -> String {
    Data(identifier.utf8).base64EncodedString()
        .replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "=", with: "")
}

// Taste onboarding must never block on an iCloud download.  Project analysis,
// on the other hand, may obtain review copies from iCloud and reports progress
// for that work.  The mode forms part of the cache key so a local-only miss
// never prevents a later full analysis from obtaining the same photo.
let reviewRenderVersion = "review-v5-2048-q88-degraded-fallback"
let maximumConcurrentRenders = 3
let minimumAnalysisReviewShortEdge = 256
let minimumAnalysisReviewLongEdge = 512

func reviewRenderIsAnalysisGrade(_ image: NSImage) -> Bool {
    var pixelWidth = 0
    var pixelHeight = 0
    for representation in image.representations {
        pixelWidth = max(pixelWidth, representation.pixelsWide)
        pixelHeight = max(pixelHeight, representation.pixelsHigh)
    }
    return min(pixelWidth, pixelHeight) >= minimumAnalysisReviewShortEdge
        && max(pixelWidth, pixelHeight) >= minimumAnalysisReviewLongEdge
}

func renderCacheKey(_ asset: PHAsset, allowNetwork: Bool) -> String {
    let source = allowNetwork ? "network" : "local"
    return "\(reviewRenderVersion)|\(source)|\(sourceRevision(asset))"
}

func mediaTypeName(_ asset: PHAsset) -> String {
    switch asset.mediaType {
    case .image: return "image"
    case .video: return "video"
    case .audio: return "audio"
    default: return "unknown"
    }
}

func hasAdjustments(_ asset: PHAsset) -> Bool {
    PHAssetResource.assetResources(for: asset).contains {
        $0.type == .adjustmentData || $0.type == .fullSizePhoto
    }
}

func sourceRevision(_ asset: PHAsset) -> String {
    let created = asset.creationDate?.timeIntervalSince1970 ?? 0
    let modified = asset.modificationDate?.timeIntervalSince1970 ?? 0
    let adjusted = hasAdjustments(asset) ? 1 : 0
    return [
        asset.localIdentifier,
        String(created),
        String(modified),
        String(asset.mediaType.rawValue),
        String(asset.mediaSubtypes.rawValue),
        "\(asset.pixelWidth)x\(asset.pixelHeight)",
        String(adjusted),
    ].joined(separator: "|")
}

func exportReviewRender(
    _ asset: PHAsset,
    outputDirectory: URL,
    allowNetwork: Bool
) -> (String?, String?) {
    let destination = outputDirectory.appendingPathComponent(
        safeStem(renderCacheKey(asset, allowNetwork: allowNetwork)) + ".jpg"
    )
    if FileManager.default.fileExists(atPath: destination.path) {
        if let cached = NSImage(contentsOf: destination), reviewRenderIsAnalysisGrade(cached) {
            return (destination.path, nil)
        }
        // A tiny opportunistic iCloud preview is useful for UI fallback, but it
        // must not become a permanent cache hit after Photos obtains the real
        // asset. This path is service-owned and scoped to the exact cache key.
        try? FileManager.default.removeItem(at: destination)
    }
    let options = PHImageRequestOptions()
    options.isSynchronous = false
    options.isNetworkAccessAllowed = allowNetwork
    // Opportunistic delivery gives us the locally cached preview first and the
    // full-quality render later.  Photos can display that cached preview even
    // when iCloud cannot currently provide the original; keeping it is much
    // safer than classifying the asset as missing and discarding all analysis.
    options.deliveryMode = .opportunistic
    options.resizeMode = .exact
    let manager = PHImageManager.default()
    let completion = DispatchSemaphore(value: 0)
    let stateLock = NSLock()
    var rendered: NSImage?
    var requestError: String?
    var finished = false
    let requestID = manager.requestImage(
        for: asset,
        targetSize: NSSize(width: 2048, height: 2048),
        contentMode: .aspectFit,
        options: options
    ) { image, info in
        let isDegraded = info?[PHImageResultIsDegradedKey] as? Bool == true
        let error = info?[PHImageErrorKey] as? Error
        let isCancelled = info?[PHImageCancelledKey] as? Bool == true
        stateLock.lock()
        guard !finished else {
            stateLock.unlock()
            return
        }
        if isDegraded, let image {
            rendered = image
            stateLock.unlock()
            return
        }
        if let image { rendered = image }
        if let error { requestError = error.localizedDescription }
        if isCancelled { requestError = "PhotoKit request cancelled" }
        if info?[PHImageResultIsInCloudKey] as? Bool == true, image == nil {
            requestError = "Фото доступно только в iCloud. Откройте его в Photos, чтобы скачать локальную копию."
        }
        if image == nil, rendered != nil {
            let detail = requestError ?? "полноразмерная версия недоступна"
            requestError = "Используется локальный preview PhotoKit: \(detail)"
        }
        finished = true
        stateLock.unlock()
        completion.signal()
    }
    let timeout = allowNetwork ? 120.0 : 12.0
    if completion.wait(timeout: .now() + timeout) == .timedOut {
        stateLock.lock()
        let hasCachedPreview = rendered != nil
        if hasCachedPreview {
            requestError = "Используется локальный preview PhotoKit: загрузка полной версии превысила \(Int(timeout)) секунд"
        }
        finished = true
        stateLock.unlock()
        manager.cancelImageRequest(requestID)
        if !hasCachedPreview {
            return (nil, "PhotoKit render timed out after \(Int(timeout)) seconds")
        }
    }
    guard let image = rendered,
          let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let jpeg = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.88])
    else { return (nil, requestError ?? "PhotoKit did not return an image render") }
    do {
        try jpeg.write(to: destination, options: .atomic)
        return (destination.path, requestError)
    } catch {
        return (nil, error.localizedDescription)
    }
}

func payload(
    _ asset: PHAsset,
    outputDirectory: URL?,
    allowNetwork: Bool = true
) -> AssetPayload {
    let resources = PHAssetResource.assetResources(for: asset)
    let resource = resources.first
    let isPhoto = asset.mediaType == .image
    let adjusted = resources.contains { $0.type == .adjustmentData || $0.type == .fullSizePhoto }
    let shouldRender = isPhoto && outputDirectory != nil
    let render = shouldRender
        ? exportReviewRender(
            asset,
            outputDirectory: outputDirectory!,
            allowNetwork: allowNetwork
        ) : (nil, nil)
    return AssetPayload(
        uuid: asset.localIdentifier,
        original_filename: resource?.originalFilename,
        current_filename: resource?.originalFilename,
        taken_at: asset.creationDate.map { ISO8601DateFormatter().string(from: $0) },
        creation_timestamp: asset.creationDate?.timeIntervalSince1970,
        modification_timestamp: asset.modificationDate?.timeIntervalSince1970,
        width: asset.pixelWidth,
        height: asset.pixelHeight,
        orientation: nil,
        favorite: asset.isFavorite,
        hidden: asset.isHidden,
        has_adjustments: adjusted,
        is_live_photo: asset.mediaSubtypes.contains(.photoLive),
        is_burst: asset.burstIdentifier != nil,
        burst_key: asset.burstIdentifier,
        burst_default_pick: asset.representsBurst,
        is_missing: shouldRender && render.0 == nil,
        is_photo: isPhoto,
        media_type: mediaTypeName(asset),
        media_subtypes: asset.mediaSubtypes.rawValue,
        edit_state: adjusted ? "adjusted" : "original",
        source_revision: sourceRevision(asset),
        source_path: render.0,
        provider_error: render.1,
        review_render: shouldRender && render.0 != nil
    )
}

func albumAssets(_ album: PHAssetCollection) -> [PHAsset] {
    let options = PHFetchOptions()
    // A burst representative is not enough for best-in-series ranking. This is
    // read-only and keeps every member addressable by its original PHAsset id.
    options.includeAllBurstAssets = true
    let result = PHAsset.fetchAssets(in: album, options: options)
    var values: [PHAsset] = []
    values.reserveCapacity(result.count)
    result.enumerateObjects { asset, _, _ in values.append(asset) }
    return values
}

func renderPayloads(
    _ assets: [PHAsset],
    outputDirectory: URL,
    allowNetwork: Bool = true,
    progress: ((Int, Int, String) -> Void)? = nil
) throws -> [AssetPayload] {
    try FileManager.default.createDirectory(
        at: outputDirectory, withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    if assets.isEmpty { return [] }
    let group = DispatchGroup()
    let queue = DispatchQueue(
        label: "local.photo-curator.render",
        qos: .userInitiated,
        attributes: .concurrent
    )
    let lock = NSLock()
    let photoTotal = assets.reduce(0) { $0 + ($1.mediaType == .image ? 1 : 0) }
    var completedPhotos = 0
    var nextIndex = 0
    var values = Array<AssetPayload?>(repeating: nil, count: assets.count)
    let workerCount = min(maximumConcurrentRenders, assets.count)
    for _ in 0..<workerCount {
        group.enter()
        queue.async {
            while true {
                lock.lock()
                guard nextIndex < assets.count else {
                    lock.unlock()
                    break
                }
                let index = nextIndex
                nextIndex += 1
                lock.unlock()

                let asset = assets[index]
                let value = autoreleasepool {
                    payload(
                        asset,
                        outputDirectory: outputDirectory,
                        allowNetwork: allowNetwork
                    )
                }
                var progressUpdate: (Int, Int, String)?
                lock.lock()
                values[index] = value
                if asset.mediaType == .image {
                    completedPhotos += 1
                    progressUpdate = (
                        completedPhotos, photoTotal, asset.localIdentifier
                    )
                }
                lock.unlock()
                if let update = progressUpdate {
                    progress?(update.0, update.1, update.2)
                }
            }
            group.leave()
        }
    }
    while group.wait(timeout: .now() + 0.05) == .timedOut {
        _ = RunLoop.current.run(
            mode: .default,
            before: Date(timeIntervalSinceNow: 0.01)
        )
    }
    return values.compactMap { $0 }
}

func assets(in album: PHAssetCollection, outputDirectory: URL) throws -> [AssetPayload] {
    try renderPayloads(
        albumAssets(album).filter { $0.mediaType == .image },
        outputDirectory: outputDirectory
    )
}

func streamAssets(in album: PHAssetCollection, outputDirectory: URL) throws {
    let values = try renderPayloads(
        albumAssets(album).filter { $0.mediaType == .image },
        outputDirectory: outputDirectory
    ) { processed, total, identifier in
        try? printJSON(AssetProgressFrame(
            processed: processed,
            total: total,
            asset_identifier: identifier
        ))
    }
    try printJSON(AssetResultFrame(assets: values))
}

func streamAssetMetadata(in album: PHAssetCollection) throws {
    let sourceAssets = albumAssets(album)
    var values: [AssetPayload] = []
    values.reserveCapacity(sourceAssets.count)
    for (index, asset) in sourceAssets.enumerated() {
        values.append(payload(asset, outputDirectory: nil))
        try? printJSON(AssetProgressFrame(
            processed: index + 1,
            total: sourceAssets.count,
            asset_identifier: asset.localIdentifier
        ))
    }
    try printJSON(AssetResultFrame(assets: values))
}

func assetMetadata(in album: PHAssetCollection) -> [AssetPayload] {
    albumAssets(album).map { payload($0, outputDirectory: nil) }
}

func assets(
    with identifiers: [String],
    outputDirectory: URL,
    allowNetwork: Bool = false
) throws -> [AssetPayload] {
    let result = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
    var byIdentifier: [String: PHAsset] = [:]
    result.enumerateObjects { asset, _, _ in byIdentifier[asset.localIdentifier] = asset }
    let ordered = identifiers.compactMap { byIdentifier[$0] }
        .filter { $0.mediaType == .image }
    return try renderPayloads(
        ordered,
        outputDirectory: outputDirectory,
        allowNetwork: allowNetwork
    )
}

func streamAssets(with identifiers: [String], outputDirectory: URL) throws {
    try FileManager.default.createDirectory(
        at: outputDirectory, withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    let result = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
    var byIdentifier: [String: PHAsset] = [:]
    result.enumerateObjects { asset, _, _ in byIdentifier[asset.localIdentifier] = asset }
    let ordered = identifiers.compactMap { byIdentifier[$0] }
        .filter { $0.mediaType == .image }
    let values = try renderPayloads(
        ordered,
        outputDirectory: outputDirectory,
        allowNetwork: false
    ) { processed, total, identifier in
        try? printJSON(AssetProgressFrame(
            processed: processed,
            total: total,
            asset_identifier: identifier
        ))
    }
    try printJSON(AssetResultFrame(assets: values))
}

func identifiers(in album: PHAssetCollection) -> [String] {
    let result = PHAsset.fetchAssets(in: album, options: nil)
    var values: [String] = []
    result.enumerateObjects { asset, _, _ in values.append(asset.localIdentifier) }
    return values
}

func recentPhotoIdentifiers(in album: PHAssetCollection) -> [String] {
    let options = PHFetchOptions()
    options.predicate = NSPredicate(
        format: "mediaType == %d",
        PHAssetMediaType.image.rawValue
    )
    options.sortDescriptors = [
        NSSortDescriptor(key: "creationDate", ascending: false)
    ]
    let result = PHAsset.fetchAssets(in: album, options: options)
    var values: [String] = []
    result.enumerateObjects { asset, _, _ in values.append(asset.localIdentifier) }
    return values
}

func printJSON<T: Encodable>(_ value: T) throws {
    let data = try JSONEncoder().encode(value)
    stdoutLock.lock()
    defer { stdoutLock.unlock() }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

public func runPhotoCuratorSourceHelper(arguments: [String]) -> Int32 {
    do {
        if arguments == [arguments[0], "--capability"] {
            print("photokit-source-media-v3")
            return 0
        }
        try requestAuthorization()
        guard arguments.count >= 2 else { throw PhotoKitError.invalidArguments }
        switch arguments[1] {
        case "albums":
            let regular = albumPayloads(subtype: .albumRegular, shared: false)
            let shared = albumPayloads(subtype: .albumCloudShared, shared: true)
            NSLog("Photo Curator PhotoKit albums: regular=%d shared=%d", regular.count, shared.count)
            try printJSON([
                "regular": regular,
                "shared": shared,
            ])
        case "assets":
            guard arguments.count == 4 else { throw PhotoKitError.invalidArguments }
            let album = try fetchAlbum(arguments[2])
            try printJSON(assets(
                in: album,
                outputDirectory: URL(fileURLWithPath: arguments[3], isDirectory: true)
            ))
        case "assets-jsonl":
            guard arguments.count == 4 else { throw PhotoKitError.invalidArguments }
            try streamAssets(
                in: try fetchAlbum(arguments[2]),
                outputDirectory: URL(fileURLWithPath: arguments[3], isDirectory: true)
            )
        case "asset-metadata-jsonl":
            guard arguments.count == 3 else { throw PhotoKitError.invalidArguments }
            try streamAssetMetadata(in: try fetchAlbum(arguments[2]))
        case "asset-metadata":
            guard arguments.count == 3 else { throw PhotoKitError.invalidArguments }
            try printJSON(assetMetadata(in: try fetchAlbum(arguments[2])))
        case "assets-by-id":
            guard arguments.count == 4 else { throw PhotoKitError.invalidArguments }
            let data = try Data(contentsOf: URL(fileURLWithPath: arguments[2]))
            let request = try JSONDecoder().decode(AssetIdentifiersRequest.self, from: data)
            try printJSON(assets(
                with: request.asset_identifiers,
                outputDirectory: URL(fileURLWithPath: arguments[3], isDirectory: true)
            ))
        case "repair-assets-by-id":
            guard arguments.count == 4 else { throw PhotoKitError.invalidArguments }
            let data = try Data(contentsOf: URL(fileURLWithPath: arguments[2]))
            let request = try JSONDecoder().decode(AssetIdentifiersRequest.self, from: data)
            try printJSON(assets(
                with: request.asset_identifiers,
                outputDirectory: URL(fileURLWithPath: arguments[3], isDirectory: true),
                allowNetwork: true
            ))
        case "assets-by-id-jsonl":
            guard arguments.count == 4 else { throw PhotoKitError.invalidArguments }
            let data = try Data(contentsOf: URL(fileURLWithPath: arguments[2]))
            let request = try JSONDecoder().decode(AssetIdentifiersRequest.self, from: data)
            try streamAssets(
                with: request.asset_identifiers,
                outputDirectory: URL(fileURLWithPath: arguments[3], isDirectory: true)
            )
        case "album-identifiers":
            guard arguments.count == 3 else { throw PhotoKitError.invalidArguments }
            try printJSON(identifiers(in: try fetchAlbum(arguments[2])))
        case "album-photo-identifiers":
            guard arguments.count == 3 else { throw PhotoKitError.invalidArguments }
            try printJSON(recentPhotoIdentifiers(in: try fetchAlbum(arguments[2])))
        default:
            throw PhotoKitError.invalidArguments
        }
        return 0
    } catch {
        FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
        return 1
    }
}
