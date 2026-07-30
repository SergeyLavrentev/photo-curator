import Foundation
import Photos

struct PublishRequest: Decodable {
    let album_name: String
    let files: [String]?
    let duplicate_asset_identifiers: [String]?
}

struct PublishResponse: Encodable {
    let album_identifier: String
    let imported: Int
    let reused: Int
}

struct PublishProgressFrame: Encodable {
    let type = "progress"
    let phase: String
    let processed: Int
    let total: Int
}

struct PublishResultFrame: Encodable {
    let type = "result"
    let result: PublishResponse
}

enum PublishError: LocalizedError {
    case invalidArguments
    case authorizationDenied
    case albumMissing
    case ambiguousAlbumName
    case incompleteImport
    case sourceResourceMissing
    case sourceExportFailed

    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "Invalid publisher arguments"
        case .authorizationDenied: return "Photo Library access was not granted"
        case .albumMissing: return "Published Photos album could not be found"
        case .ambiguousAlbumName: return "More than one Photos album has the publish name"
        case .incompleteImport: return "PhotoKit did not import every selected image"
        case .sourceResourceMissing: return "A selected photo has no exportable source resource"
        case .sourceExportFailed: return "PhotoKit could not export a selected source photo"
        }
    }
}

struct PreparedDuplicate {
    let source: PHAsset
    let fileURL: URL
    let publishedFilename: String
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

func sourcePhotoResource(for asset: PHAsset) -> PHAssetResource? {
    let resources = PHAssetResource.assetResources(for: asset)
    return resources.first(where: { $0.type == .fullSizePhoto })
        ?? resources.first(where: { $0.type == .photo })
        ?? resources.first(where: { $0.type == .alternatePhoto })
}

func duplicateFilename(identifier: String, resource: PHAssetResource) -> String {
    let identifierPrefix = identifier.split(separator: "/").first.map(String.init) ?? identifier
    let safeIdentifier = identifierPrefix.replacingOccurrences(
        of: "[^A-Za-z0-9-]",
        with: "-",
        options: .regularExpression
    )
    let sourceExtension = URL(fileURLWithPath: resource.originalFilename).pathExtension
    let suffix = sourceExtension.isEmpty ? "jpg" : sourceExtension.lowercased()
    return "PhotoCurator-\(safeIdentifier).\(suffix)"
}

func exportResource(_ resource: PHAssetResource, to destination: URL) throws {
    try? FileManager.default.removeItem(at: destination)
    let options = PHAssetResourceRequestOptions()
    options.isNetworkAccessAllowed = true
    var finished = false
    var exportError: Error?
    let lock = NSLock()
    PHAssetResourceManager.default().writeData(
        for: resource,
        toFile: destination,
        options: options
    ) { error in
        lock.lock()
        exportError = error
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
    if exportError != nil || !FileManager.default.fileExists(atPath: destination.path) {
        throw PublishError.sourceExportFailed
    }
}

func publishDuplicates(
    identifiers: [String],
    into album: PHAssetCollection,
    progress: ((String, Int, Int) throws -> Void)?
) throws -> PublishResponse {
    let fetched = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
    var assetsByIdentifier: [String: PHAsset] = [:]
    fetched.enumerateObjects { asset, _, _ in assetsByIdentifier[asset.localIdentifier] = asset }
    guard assetsByIdentifier.count == Set(identifiers).count else {
        throw PublishError.incompleteImport
    }

    var existing = existingFilenames(in: album)
    let temporaryRoot = FileManager.default.temporaryDirectory
        .appendingPathComponent("PhotoCurator-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(
        at: temporaryRoot,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
    )
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }

    var prepared: [PreparedDuplicate] = []
    var reused = 0
    for (index, identifier) in identifiers.enumerated() {
        guard let asset = assetsByIdentifier[identifier],
              let resource = sourcePhotoResource(for: asset)
        else { throw PublishError.sourceResourceMissing }
        let publishedFilename = duplicateFilename(identifier: identifier, resource: resource)
        if existing.contains(publishedFilename) {
            reused += 1
        } else {
            let fileURL = temporaryRoot.appendingPathComponent(publishedFilename)
            try exportResource(resource, to: fileURL)
            prepared.append(
                PreparedDuplicate(
                    source: asset,
                    fileURL: fileURL,
                    publishedFilename: publishedFilename
                )
            )
        }
        try progress?("prepare", index + 1, identifiers.count)
    }

    var imported = 0
    for start in stride(from: 0, to: prepared.count, by: 25) {
        let end = min(start + 25, prepared.count)
        let batch = Array(prepared[start..<end])
        var created = 0
        try PHPhotoLibrary.shared().performChangesAndWait {
            var placeholders: [PHObjectPlaceholder] = []
            for item in batch {
                let creation = PHAssetCreationRequest.forAsset()
                creation.creationDate = item.source.creationDate
                creation.location = item.source.location
                let options = PHAssetResourceCreationOptions()
                options.originalFilename = item.publishedFilename
                creation.addResource(with: .photo, fileURL: item.fileURL, options: options)
                if let placeholder = creation.placeholderForCreatedAsset {
                    placeholders.append(placeholder)
                }
            }
            created = placeholders.count
            PHAssetCollectionChangeRequest(for: album)?.addAssets(placeholders as NSArray)
        }
        if created != batch.count { throw PublishError.incompleteImport }
        imported += created
        existing.formUnion(batch.map(\.publishedFilename))
        try progress?("commit", reused + imported, identifiers.count)
    }
    if prepared.isEmpty {
        try progress?("commit", identifiers.count, identifiers.count)
    }
    return PublishResponse(
        album_identifier: album.localIdentifier,
        imported: imported,
        reused: reused
    )
}

func publish(
    _ request: PublishRequest,
    progress: ((String, Int, Int) throws -> Void)? = nil
) throws -> PublishResponse {
    try requestAuthorization()
    let album = try fetchAlbum(named: request.album_name) ?? createAlbum(named: request.album_name)
    if let identifiers = request.duplicate_asset_identifiers {
        return try publishDuplicates(identifiers: identifiers, into: album, progress: progress)
    }
    var existing = existingFilenames(in: album)
    var imported = 0
    var reused = 0
    var prepared = 0
    let urls = (request.files ?? []).map { URL(fileURLWithPath: $0) }
    for start in stride(from: 0, to: urls.count, by: 50) {
        let end = min(start + 50, urls.count)
        let batch = Array(urls[start..<end])
        let pending = batch.filter { !existing.contains($0.lastPathComponent) }
        for _ in batch {
            prepared += 1
            try progress?("prepare", prepared, urls.count)
        }
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
    try progress?("commit", urls.count, urls.count)
    return PublishResponse(
        album_identifier: album.localIdentifier,
        imported: imported,
        reused: reused
    )
}

func printJSON<T: Encodable>(_ value: T) throws {
    let data = try JSONEncoder().encode(value)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

do {
    if CommandLine.arguments == [CommandLine.arguments[0], "--capability"] {
        print("photokit-publish-duplicates-v2")
        exit(0)
    }
    let jsonl = CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--jsonl"
    guard CommandLine.arguments.count == (jsonl ? 3 : 2) else {
        throw PublishError.invalidArguments
    }
    let requestPath = CommandLine.arguments[jsonl ? 2 : 1]
    let data = try Data(contentsOf: URL(fileURLWithPath: requestPath))
    let request = try JSONDecoder().decode(PublishRequest.self, from: data)
    let progressHandler: ((String, Int, Int) throws -> Void)? = jsonl
        ? { phase, processed, total in
            try printJSON(PublishProgressFrame(
                phase: phase, processed: processed, total: total
            ))
        }
        : nil
    let response = try publish(request, progress: progressHandler)
    if jsonl {
        try printJSON(PublishResultFrame(result: response))
    } else {
        try printJSON(response)
    }
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
