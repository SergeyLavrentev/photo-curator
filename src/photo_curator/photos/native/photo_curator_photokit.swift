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
}

struct AssetIdentifiersRequest: Decodable {
    let asset_identifiers: [String]
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

func exportReviewRender(_ asset: PHAsset, outputDirectory: URL) -> (String?, String?) {
    let destination = outputDirectory.appendingPathComponent(safeStem(asset.localIdentifier) + ".jpg")
    if FileManager.default.fileExists(atPath: destination.path) {
        return (destination.path, nil)
    }
    let options = PHImageRequestOptions()
    options.isSynchronous = true
    options.isNetworkAccessAllowed = true
    options.deliveryMode = .highQualityFormat
    options.resizeMode = .exact
    var rendered: NSImage?
    var requestError: String?
    PHImageManager.default().requestImage(
        for: asset,
        targetSize: NSSize(width: 2560, height: 2560),
        contentMode: .aspectFit,
        options: options
    ) { image, info in
        rendered = image
        if let error = info?[PHImageErrorKey] as? Error { requestError = error.localizedDescription }
        if info?[PHImageCancelledKey] as? Bool == true { requestError = "PhotoKit request cancelled" }
    }
    guard let image = rendered,
          let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let jpeg = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.92])
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
    let render = isPhoto && outputDirectory != nil
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
        is_missing: isPhoto && render.0 == nil,
        is_photo: isPhoto,
        source_path: render.0,
        provider_error: render.1
    )
}

func assets(in album: PHAssetCollection, outputDirectory: URL) throws -> [AssetPayload] {
    try FileManager.default.createDirectory(
        at: outputDirectory, withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    let result = PHAsset.fetchAssets(in: album, options: nil)
    var values: [AssetPayload] = []
    result.enumerateObjects { asset, _, _ in
        values.append(payload(asset, outputDirectory: outputDirectory))
    }
    return values
}

func assets(with identifiers: [String], outputDirectory: URL) throws -> [AssetPayload] {
    try FileManager.default.createDirectory(
        at: outputDirectory, withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    let result = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
    var byIdentifier: [String: AssetPayload] = [:]
    result.enumerateObjects { asset, _, _ in
        byIdentifier[asset.localIdentifier] = payload(asset, outputDirectory: outputDirectory)
    }
    return identifiers.compactMap { byIdentifier[$0] }
}

func identifiers(in album: PHAssetCollection) -> [String] {
    let result = PHAsset.fetchAssets(in: album, options: nil)
    var values: [String] = []
    result.enumerateObjects { asset, _, _ in values.append(asset.localIdentifier) }
    return values
}

func printJSON<T: Encodable>(_ value: T) throws {
    let data = try JSONEncoder().encode(value)
    print(String(data: data, encoding: .utf8)!)
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
    case "assets-by-id":
        guard arguments.count == 4 else { throw PhotoKitError.invalidArguments }
        let data = try Data(contentsOf: URL(fileURLWithPath: arguments[2]))
        let request = try JSONDecoder().decode(AssetIdentifiersRequest.self, from: data)
        try printJSON(assets(
            with: request.asset_identifiers,
            outputDirectory: URL(fileURLWithPath: arguments[3], isDirectory: true)
        ))
    case "album-identifiers":
        guard arguments.count == 3 else { throw PhotoKitError.invalidArguments }
        try printJSON(identifiers(in: try fetchAlbum(arguments[2])))
    default:
        throw PhotoKitError.invalidArguments
    }
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
