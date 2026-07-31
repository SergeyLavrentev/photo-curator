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
    let width: Int
    let height: Int
    let favorite: Bool
    let hidden: Bool
    let is_live_photo: Bool
    let is_burst: Bool
    let burst_key: String?
    let burst_default_pick: Bool
    let is_missing: Bool
    let is_photo: Bool
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

let reviewRenderVersion = "review-v2-2048-q88"
let maximumConcurrentRenders = 3
let reviewRenderTimeoutSeconds = 120.0

func renderCacheKey(_ asset: PHAsset) -> String {
    let modified = asset.modificationDate?.timeIntervalSince1970 ?? 0
    return "\(reviewRenderVersion)|\(asset.localIdentifier)|\(modified)|\(asset.pixelWidth)x\(asset.pixelHeight)"
}

func exportReviewRender(_ asset: PHAsset, outputDirectory: URL) -> (String?, String?) {
    let destination = outputDirectory.appendingPathComponent(safeStem(renderCacheKey(asset)) + ".jpg")
    if FileManager.default.fileExists(atPath: destination.path) {
        return (destination.path, nil)
    }
    let options = PHImageRequestOptions()
    options.isSynchronous = false
    options.isNetworkAccessAllowed = true
    options.deliveryMode = .highQualityFormat
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
        guard !isDegraded || error != nil || isCancelled else { return }
        stateLock.lock()
        guard !finished else {
            stateLock.unlock()
            return
        }
        rendered = image
        if let error { requestError = error.localizedDescription }
        if isCancelled { requestError = "PhotoKit request cancelled" }
        finished = true
        stateLock.unlock()
        completion.signal()
    }
    if completion.wait(timeout: .now() + reviewRenderTimeoutSeconds) == .timedOut {
        stateLock.lock()
        finished = true
        stateLock.unlock()
        manager.cancelImageRequest(requestID)
        return (nil, "PhotoKit render timed out after \(Int(reviewRenderTimeoutSeconds)) seconds")
    }
    guard let image = rendered,
          let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let jpeg = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.88])
    else { return (nil, requestError ?? "PhotoKit did not return an image render") }
    do {
        try jpeg.write(to: destination, options: .atomic)
        return (destination.path, nil)
    } catch {
        return (nil, error.localizedDescription)
    }
}

func payload(_ asset: PHAsset, outputDirectory: URL?) -> AssetPayload {
    let resource = PHAssetResource.assetResources(for: asset).first
    let isPhoto = asset.mediaType == .image
    let shouldRender = isPhoto && outputDirectory != nil
    let render = shouldRender
        ? exportReviewRender(asset, outputDirectory: outputDirectory!) : (nil, nil)
    return AssetPayload(
        uuid: asset.localIdentifier,
        original_filename: resource?.originalFilename,
        current_filename: resource?.originalFilename,
        taken_at: asset.creationDate.map { ISO8601DateFormatter().string(from: $0) },
        width: asset.pixelWidth,
        height: asset.pixelHeight,
        favorite: asset.isFavorite,
        hidden: asset.isHidden,
        is_live_photo: asset.mediaSubtypes.contains(.photoLive),
        is_burst: asset.burstIdentifier != nil,
        burst_key: asset.burstIdentifier,
        burst_default_pick: asset.representsBurst,
        is_missing: shouldRender && render.0 == nil,
        is_photo: isPhoto,
        source_path: render.0,
        provider_error: render.1,
        review_render: shouldRender && render.0 != nil
    )
}

func albumAssets(_ album: PHAssetCollection) -> [PHAsset] {
    let result = PHAsset.fetchAssets(in: album, options: nil)
    var values: [PHAsset] = []
    values.reserveCapacity(result.count)
    result.enumerateObjects { asset, _, _ in values.append(asset) }
    return values
}

func renderPayloads(
    _ assets: [PHAsset],
    outputDirectory: URL,
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
                    payload(asset, outputDirectory: outputDirectory)
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
    group.wait()
    return values.compactMap { $0 }
}

func assets(in album: PHAssetCollection, outputDirectory: URL) throws -> [AssetPayload] {
    try renderPayloads(albumAssets(album), outputDirectory: outputDirectory)
}

func streamAssets(in album: PHAssetCollection, outputDirectory: URL) throws {
    let values = try renderPayloads(
        albumAssets(album),
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

func assets(with identifiers: [String], outputDirectory: URL) throws -> [AssetPayload] {
    let result = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
    var byIdentifier: [String: PHAsset] = [:]
    result.enumerateObjects { asset, _, _ in byIdentifier[asset.localIdentifier] = asset }
    let ordered = identifiers.compactMap { byIdentifier[$0] }
    return try renderPayloads(ordered, outputDirectory: outputDirectory)
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
    let values = try renderPayloads(
        ordered,
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
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

do {
    let arguments = CommandLine.arguments
    if arguments == [arguments[0], "--capability"] {
        print("photokit-source-v1")
        exit(0)
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
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
