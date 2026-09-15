import Foundation
import Photos
import SwiftUI

enum PhotoDeletionScope: String, Decodable {
    case library
    case sharedAlbum = "shared_album"

    var destination: String { self == .sharedAlbum ? "общего альбома" : "медиатеки" }
    var warning: String {
        self == .sharedAlbum
            ? "Публикации исчезнут из этого общего альбома у всех участников. Оригиналы в личных медиатеках и отдельно сохранённые копии останутся. Владелец альбома может удалять любые публикации, участник — только свои. Восстановление из «Недавно удалённых» для общего альбома не обещается."
            : "Фотографии исчезнут из медиатеки и всех её альбомов, а при синхронизации iCloud — и с других устройств. Это не просто удаление из выбранного альбома."
    }
}

struct PhotoDeletionPlan: Decodable, Identifiable {
    struct Item: Decodable, Identifiable {
        let id: String
        let filename: String
        let sourceRevision: String
        let favorite: Bool
        let hasAdjustments: Bool
        let reviewPath: String
        enum CodingKeys: String, CodingKey {
            case id, filename, favorite
            case sourceRevision = "source_revision"
            case hasAdjustments = "has_adjustments"
            case reviewPath = "review_path"
        }
    }
    let id: String
    let projectID: String
    let albumID: String
    let albumName: String
    let items: [Item]
    let scope: PhotoDeletionScope
    enum CodingKeys: String, CodingKey {
        case id, items, scope
        case projectID = "project_id"
        case albumID = "album_id"
        case albumName = "album_name"
    }
}

private struct DeletionPrepare: Encodable {
    let project_id: String
    let asset_uuids: [String]?
}
private struct DeletionBegin: Encodable { let plan_id: String; let confirmed: Bool }
private struct DeletionFinish: Encodable { let plan_id: String; let deleted_ids: [String]; let error: String? }
private struct DeletionResult: Decodable { let status: String; let deleted_count: Int; let summary: ProjectSummaryDTO }

private struct PhotoDeletionError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

enum PhotoDeletionHost {
    struct Validated {
        let album: PHAssetCollection
        let assets: [PHAsset]
    }

    static func validate(_ plan: PhotoDeletionPlan) throws -> Validated {
        let permission = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        guard permission == .authorized || permission == .limited else {
            throw PhotoDeletionError(message: "Для удаления требуется разрешение Apple Photos. Разрешите доступ и повторите проверку списка.")
        }
        let ids = plan.items.map(\.id)
        guard !ids.isEmpty, ids.count <= 10000, Set(ids).count == ids.count,
              let album = PHAssetCollection.fetchAssetCollections(withLocalIdentifiers: [plan.albumID], options: nil).firstObject else {
            throw PhotoDeletionError(message: "Исходный альбом недоступен. Обновите анализ.")
        }
        let isShared = album.assetCollectionSubtype == .albumCloudShared
        guard isShared == (plan.scope == .sharedAlbum) else {
            throw PhotoDeletionError(message: "Тип исходного альбома изменился. Выполните новый анализ.")
        }
        if isShared && !album.canPerform(.removeContent) {
            throw PhotoDeletionError(message: "Apple Photos не разрешает этому приложению удалять публикации из общего альбома. Откройте альбом в «Фото»: владелец может удалить любые публикации, участник — только свои. Личные оригиналы не будут удалены вместо публикаций.")
        }
        var membership = Set<String>()
        var assetsByID: [String: PHAsset] = [:]
        let selected = Set(ids)
        // Fetch in the exact album: global fetches may exclude cloud-shared assets.
        PHAsset.fetchAssets(in: album, options: nil).enumerateObjects { asset, _, _ in
            membership.insert(asset.localIdentifier)
            if selected.contains(asset.localIdentifier) { assetsByID[asset.localIdentifier] = asset }
        }
        guard Set(assetsByID.keys) == Set(ids), Set(ids).isSubset(of: membership) else {
            throw PhotoDeletionError(message: "Состав исходного альбома изменился. Обновите анализ перед удалением.")
        }
        let assets = try plan.items.map { item -> PHAsset in
            guard let asset = assetsByID[item.id], asset.mediaType == .image,
                  sourceRevision(asset) == item.sourceRevision,
                  asset.isFavorite == item.favorite,
                  hasAdjustments(asset) == item.hasAdjustments else {
                throw PhotoDeletionError(message: "Фото \(item.filename) изменилось. Обновите анализ.")
            }
            if isShared {
                guard asset.sourceType.contains(.typeCloudShared) else {
                    throw PhotoDeletionError(message: "Фото не является публикацией общего альбома. Обновите анализ.")
                }
            } else {
                guard asset.canPerform(.delete), asset.sourceType.contains(.typeUserLibrary) else {
                    throw PhotoDeletionError(message: "Photos не разрешает удалить фото \(item.filename).")
                }
            }
            return asset
        }
        return Validated(album: album, assets: assets)
    }

    static func delete(_ plan: PhotoDeletionPlan) async throws -> [String] {
        let validated = try await Task.detached(priority: .userInitiated) { try validate(plan) }.value
        let assets = validated.assets
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            // A nil collection request must not be reported as successful deletion.
            var submitted = false
            PHPhotoLibrary.shared().performChanges({
                switch plan.scope {
                case .sharedAlbum:
                    if let request = PHAssetCollectionChangeRequest(for: validated.album) {
                        request.removeAssets(assets as NSArray)
                        submitted = true
                    }
                case .library:
                    PHAssetChangeRequest.deleteAssets(assets as NSArray)
                    submitted = true
                }
            }) { success, error in
                if success && submitted { continuation.resume() }
                else { continuation.resume(throwing: error ?? PhotoDeletionError(message: "Photos не разрешил удаление. Для общего альбома проверьте права в приложении «Фото».")) }
            }
        }
        return try await Task.detached(priority: .userInitiated) {
            let ids = plan.items.map(\.id)
            let result: PHFetchResult<PHAsset>
            if plan.scope == .sharedAlbum {
                guard let album = PHAssetCollection.fetchAssetCollections(withLocalIdentifiers: [plan.albumID], options: nil).firstObject else {
                    throw PhotoDeletionError(message: "Не удалось проверить результат: общий альбом недоступен. Проверьте его в «Фото».")
                }
                result = PHAsset.fetchAssets(in: album, options: nil)
            } else {
                result = PHAsset.fetchAssets(withLocalIdentifiers: ids, options: nil)
            }
            var remaining = Set<String>()
            result.enumerateObjects { asset, _, _ in remaining.insert(asset.localIdentifier) }
            return ids.filter { !remaining.contains($0) }
        }.value
    }

    private static func hasAdjustments(_ asset: PHAsset) -> Bool {
        PHAssetResource.assetResources(for: asset).contains { $0.type == .adjustmentData || $0.type == .fullSizePhoto }
    }
    private static func sourceRevision(_ asset: PHAsset) -> String {
        // Same public PhotoKit fields and serialization as the inventory helper.
        [asset.localIdentifier,
         String(asset.creationDate?.timeIntervalSince1970 ?? 0),
         String(asset.modificationDate?.timeIntervalSince1970 ?? 0),
         String(asset.mediaType.rawValue), String(asset.mediaSubtypes.rawValue),
         "\(asset.pixelWidth)x\(asset.pixelHeight)", hasAdjustments(asset) ? "1" : "0",
         asset.location.map { String($0.coordinate.latitude) } ?? "",
         asset.location.map { String($0.coordinate.longitude) } ?? ""].joined(separator: "|")
    }
}

extension AppModel {
    func preparePhotoDeletion(assetIDs: [String]? = nil) {
        guard !isBusy, let current = project else { return }
        isBusy = true
        deletionMessage = nil
        let ids: [String]?
        if let assetIDs { ids = assetIDs }
        else if !selectedPhotoIDs.isEmpty { ids = selectedPhotoIDs.sorted() }
        else if selectionBucket == .reject { ids = nil }
        else if let selectedPhotoID { ids = [selectedPhotoID] }
        else { isBusy = false; return }
        Task {
            defer { isBusy = false }
            do {
                let plan: PhotoDeletionPlan = try await callDTO("delete_photos_prepare", DeletionPrepare(project_id: current.id, asset_uuids: ids), as: PhotoDeletionPlan.self)
                guard await requestPhotoLibraryAccess() else { return }
                _ = try await Task.detached(priority: .userInitiated) { try PhotoDeletionHost.validate(plan) }.value
                guard project?.id == current.id else { return }
                photoDeletionPlan = plan
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func confirmPhotoDeletion(_ plan: PhotoDeletionPlan) {
        guard !isBusy, project?.id == plan.projectID, photoDeletionPlan?.id == plan.id else { return }
        isBusy = true
        Task {
            defer { isBusy = false; photoDeletionPlan = nil }
            var started = false
            var deleted: [String] = []
            do {
                let confirmed: PhotoDeletionPlan = try await callDTO("delete_photos_begin", DeletionBegin(plan_id: plan.id, confirmed: true), as: PhotoDeletionPlan.self)
                started = true
                deleted = try await PhotoDeletionHost.delete(confirmed)
                let result: DeletionResult = try await callDTO("delete_photos_finish", DeletionFinish(plan_id: plan.id, deleted_ids: deleted, error: nil), as: DeletionResult.self)
                applyProjectSummary(result.summary)
                deletionMessage = result.status == "completed" ? (plan.scope == .sharedAlbum ? "Удалено из общего альбома у всех участников: \(result.deleted_count). Личные оригиналы и сохранённые копии не затронуты." : "Удалено из медиатеки: \(result.deleted_count). Восстановление доступно в «Недавно удалённых» приложения «Фото».") : "Photos подтвердил удаление \(result.deleted_count) из \(plan.items.count). Проверьте медиатеку; автоматического повтора не будет."
                selectedPhotoIDs.removeAll()
                await loadPhotos(projectID: plan.projectID)
            } catch {
                if started {
                    do {
                        let result: DeletionResult = try await callDTO("delete_photos_finish", DeletionFinish(plan_id: plan.id, deleted_ids: deleted, error: error.localizedDescription), as: DeletionResult.self)
                        applyProjectSummary(result.summary)
                        if !deleted.isEmpty {
                            selectedPhotoIDs.removeAll()
                            await loadPhotos(projectID: plan.projectID)
                        }
                    } catch {
                        deletionMessage = "Запрос уже передан Photos. Не повторяйте удаление: проверьте медиатеку и обновите анализ."
                    }
                }
                errorMessage = error.localizedDescription
            }
        }
    }
}

struct PhotoDeletionSheet: View {
    let plan: PhotoDeletionPlan
    @EnvironmentObject private var model: AppModel
    @State private var confirmed = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Удалить \(plan.items.count) фото из \(plan.scope.destination)?").font(.title.bold())
            Text(plan.albumName).foregroundStyle(.secondary)
            Text(plan.scope.warning)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(plan.items) { item in
                        HStack {
                            CachedThumbnail(path: item.reviewPath, maxPixelSize: 160, contentMode: .fit).frame(width: 76, height: 76)
                            Text(item.filename)
                            if item.favorite { Image(systemName: "heart.fill").accessibilityLabel("Избранное") }
                            if item.hasAdjustments { Text("С правками").foregroundStyle(.secondary) }
                        }
                    }
                }
            }.frame(height: 360)
            Toggle("Я проверил список и хочу удалить эти фотографии", isOn: $confirmed)
            HStack {
                Button("Отмена") { model.photoDeletionPlan = nil }.keyboardShortcut(.cancelAction)
                Spacer()
                if model.isBusy { ProgressView().controlSize(.small) }
                Button("Удалить \(plan.items.count) фото из \(plan.scope.destination)", role: .destructive) { model.confirmPhotoDeletion(plan) }
                    .disabled(!confirmed || model.isBusy)
            }
        }.padding(24).frame(width: 700)
        .interactiveDismissDisabled(model.isBusy)
        .disabled(model.isBusy)
    }
}
