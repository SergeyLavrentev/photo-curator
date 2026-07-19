import Foundation
import Photos

struct ImportRequest: Decodable {
    let album_name: String
    let album_identifier: String?
    let files: [String]
}

struct ImportResponse: Encodable {
    let album_identifier: String
    let imported: Int
    let reused: Int
}

enum HelperError: LocalizedError {
    case invalidArguments
    case authorizationDenied
    case albumMissing
    case ambiguousAlbumName
    case incompleteImport

    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "Invalid helper arguments"
        case .authorizationDenied: return "Photo Library access was not granted"
        case .albumMissing: return "Created Photos album could not be found"
        case .ambiguousAlbumName: return "More than one Photos album has this name"
        case .incompleteImport: return "PhotoKit did not import every requested image"
        }
    }
}

func requestAuthorization() throws {
    let semaphore = DispatchSemaphore(value: 0)
    var status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    if status == .notDetermined {
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { newStatus in
            status = newStatus
            semaphore.signal()
        }
        semaphore.wait()
    }
    guard status == .authorized || status == .limited else {
        throw HelperError.authorizationDenied
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
    if result.count > 1 { throw HelperError.ambiguousAlbumName }
    return result.firstObject
}

func createAlbum(named name: String) throws -> PHAssetCollection {
    var identifier: String?
    try PHPhotoLibrary.shared().performChangesAndWait {
        let request = PHAssetCollectionChangeRequest.creationRequestForAssetCollection(withTitle: name)
        identifier = request.placeholderForCreatedAssetCollection.localIdentifier
    }
    guard let identifier, let album = fetchAlbum(identifier: identifier) else {
        throw HelperError.albumMissing
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

func performImport(_ request: ImportRequest) throws -> ImportResponse {
    try requestAuthorization()
    let album: PHAssetCollection
    if let identifier = request.album_identifier, let found = fetchAlbum(identifier: identifier) {
        album = found
    } else if let found = try fetchAlbum(named: request.album_name) {
        album = found
    } else {
        album = try createAlbum(named: request.album_name)
    }
    let existing = existingFilenames(in: album)
    let urls = request.files.map { URL(fileURLWithPath: $0) }
    let pending = urls.filter { !existing.contains($0.lastPathComponent) }
    var importedCount = 0
    if !pending.isEmpty {
        try PHPhotoLibrary.shared().performChangesAndWait {
            let placeholders = pending.compactMap {
                PHAssetChangeRequest.creationRequestForAssetFromImage(atFileURL: $0)?
                    .placeholderForCreatedAsset
            }
            importedCount = placeholders.count
            PHAssetCollectionChangeRequest(for: album)?.addAssets(placeholders as NSArray)
        }
    }
    if importedCount != pending.count { throw HelperError.incompleteImport }
    return ImportResponse(
        album_identifier: album.localIdentifier,
        imported: importedCount,
        reused: urls.count - pending.count
    )
}

do {
    if CommandLine.arguments == [CommandLine.arguments[0], "--capability"] {
        print("photokit")
        exit(0)
    }
    guard CommandLine.arguments.count == 2 else { throw HelperError.invalidArguments }
    let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
    let request = try JSONDecoder().decode(ImportRequest.self, from: data)
    let response = try performImport(request)
    let output = try JSONEncoder().encode(response)
    print(String(data: output, encoding: .utf8)!)
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
