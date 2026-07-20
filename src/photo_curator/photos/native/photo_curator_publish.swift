import Foundation
import Photos

struct PublishRequest: Decodable {
    let album_name: String
    let files: [String]?
    let asset_identifiers: [String]?
}

struct PublishResponse: Encodable {
    let album_identifier: String
    let imported: Int
    let reused: Int
}

enum PublishError: LocalizedError {
    case invalidArguments
    case authorizationDenied
    case albumMissing
    case ambiguousAlbumName
    case incompleteImport

    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "Invalid publisher arguments"
        case .authorizationDenied: return "Photo Library access was not granted"
        case .albumMissing: return "Published Photos album could not be found"
        case .ambiguousAlbumName: return "More than one Photos album has the publish name"
        case .incompleteImport: return "PhotoKit did not import every selected image"
        }
    }
}

func requestAuthorization() throws {
    var status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    if status == .notDetermined {
        var finished = false
        let lock = NSLock()
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { newStatus in
            lock.lock()
            status = newStatus
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
        throw PublishError.authorizationDenied
    }
}

func fetchAlbum(identifier: String) -> PHAssetCollection? {
    PHAssetCollection.fetchAssetCollections(
        withLocalIdentifiers: [identifier], options: nil
    ).firstObject
}

func fetchAlbum(named name: String) throws -> PHAssetCollection? {
    let options = PHFetchOptions()
    options.predicate = NSPredicate(format: "title == %@", name)
    let result = PHAssetCollection.fetchAssetCollections(
        with: .album, subtype: .albumRegular, options: options
    )
    if result.count > 1 { throw PublishError.ambiguousAlbumName }
    return result.firstObject
}

func createAlbum(named name: String) throws -> PHAssetCollection {
    var identifier: String?
    try PHPhotoLibrary.shared().performChangesAndWait {
        let request = PHAssetCollectionChangeRequest.creationRequestForAssetCollection(withTitle: name)
        identifier = request.placeholderForCreatedAssetCollection.localIdentifier
    }
    guard let identifier, let album = fetchAlbum(identifier: identifier) else {
        throw PublishError.albumMissing
    }
    return album
}

func existingFilenames(in album: PHAssetCollection) -> Set<String> {
    let assets = PHAsset.fetchAssets(in: album, options: nil)
    var names = Set<String>()
    assets.enumerateObjects { asset, _, _ in
        if let resource = PHAssetResource.assetResources(for: asset).first {
            names.insert(resource.originalFilename)
        }
    }
    return names
}

func publish(_ request: PublishRequest) throws -> PublishResponse {
    try requestAuthorization()
    let album = try fetchAlbum(named: request.album_name) ?? createAlbum(named: request.album_name)
    if let identifiers = request.asset_identifiers {
        let fetched = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
        var assetsByIdentifier: [String: PHAsset] = [:]
        fetched.enumerateObjects { asset, _, _ in assetsByIdentifier[asset.localIdentifier] = asset }
        guard assetsByIdentifier.count == Set(identifiers).count else {
            throw PublishError.incompleteImport
        }
        let existingFetch = PHAsset.fetchAssets(in: album, options: nil)
        var existingIdentifiers = Set<String>()
        existingFetch.enumerateObjects { asset, _, _ in
            existingIdentifiers.insert(asset.localIdentifier)
        }
        let pending = identifiers.compactMap {
            existingIdentifiers.contains($0) ? nil : assetsByIdentifier[$0]
        }
        if !pending.isEmpty {
            try PHPhotoLibrary.shared().performChangesAndWait {
                PHAssetCollectionChangeRequest(for: album)?.addAssets(pending as NSArray)
            }
        }
        return PublishResponse(
            album_identifier: album.localIdentifier,
            imported: pending.count,
            reused: identifiers.count - pending.count
        )
    }
    var existing = existingFilenames(in: album)
    var imported = 0
    var reused = 0
    let urls = (request.files ?? []).map { URL(fileURLWithPath: $0) }
    for start in stride(from: 0, to: urls.count, by: 50) {
        let end = min(start + 50, urls.count)
        let batch = Array(urls[start..<end])
        let pending = batch.filter { !existing.contains($0.lastPathComponent) }
        reused += batch.count - pending.count
        if pending.isEmpty { continue }
        var importedInBatch = 0
        try PHPhotoLibrary.shared().performChangesAndWait {
            let placeholders = pending.compactMap {
                PHAssetChangeRequest.creationRequestForAssetFromImage(atFileURL: $0)?
                    .placeholderForCreatedAsset
            }
            importedInBatch = placeholders.count
            PHAssetCollectionChangeRequest(for: album)?.addAssets(placeholders as NSArray)
        }
        if importedInBatch != pending.count { throw PublishError.incompleteImport }
        imported += importedInBatch
        existing.formUnion(pending.map { $0.lastPathComponent })
    }
    return PublishResponse(
        album_identifier: album.localIdentifier,
        imported: imported,
        reused: reused
    )
}

do {
    if CommandLine.arguments == [CommandLine.arguments[0], "--capability"] {
        print("photokit-publish")
        exit(0)
    }
    guard CommandLine.arguments.count == 2 else { throw PublishError.invalidArguments }
    let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
    let request = try JSONDecoder().decode(PublishRequest.self, from: data)
    let response = try publish(request)
    let output = try JSONEncoder().encode(response)
    print(String(data: output, encoding: .utf8)!)
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
