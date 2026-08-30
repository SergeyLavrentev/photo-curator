import Foundation

extension AppModel {
    var workspacePhotos: [PhotoItem] {
        guard let selectedPhotoID, let selected = photoItem(photoID: selectedPhotoID) else {
            return []
        }
        switch workspaceMode {
        case .grid:
            return photos
        case .loupe:
            return photos.contains(where: { $0.id == selectedPhotoID })
                ? photos : [selected] + photos
        case .compare, .survey:
            guard let groupID = selected.duplicateGroup,
                  let members = seriesContexts[groupID],
                  !members.isEmpty
            else { return [selected] }
            return members
        }
    }

    func seriesMembers(groupID: String) -> [PhotoItem]? {
        seriesContexts[groupID]
    }

    func photoItem(photoID: String) -> PhotoItem? {
        if let photo = photos.first(where: { $0.id == photoID }) { return photo }
        for members in seriesContexts.values {
            if let photo = members.first(where: { $0.id == photoID }) { return photo }
        }
        return nil
    }

    func updateCachedPhoto(_ updated: PhotoItem) {
        if let index = photos.firstIndex(where: { $0.id == updated.id }) {
            photos[index] = updated
        }
        var contexts = seriesContexts
        for groupID in contexts.keys {
            guard let index = contexts[groupID]?.firstIndex(where: { $0.id == updated.id }) else {
                continue
            }
            contexts[groupID]?[index] = updated
        }
        seriesContexts = contexts
    }

    func resetSeriesContexts() {
        seriesContextEpoch += 1
        seriesContexts.removeAll()
        loadingSeriesIDs.removeAll()
        expandedSeriesIDs.removeAll()
    }

    func toggleSeriesExpansion(groupID: String) {
        if expandedSeriesIDs.contains(groupID) {
            expandedSeriesIDs.remove(groupID)
            return
        }
        expandedSeriesIDs.insert(groupID)
        ensureSeriesContext(groupID: groupID)
    }

    func refreshSelectedSeriesContext() {
        guard let selectedPhotoID,
              let groupID = photoItem(photoID: selectedPhotoID)?.duplicateGroup
        else { return }
        ensureSeriesContext(groupID: groupID)
    }

    func ensureSeriesContext(groupID: String, force: Bool = false) {
        guard let project else { return }
        if !force, seriesContexts[groupID] != nil { return }
        guard !loadingSeriesIDs.contains(groupID) else { return }
        let projectID = project.id
        let epoch = seriesContextEpoch
        loadingSeriesIDs.insert(groupID)
        Task {
            defer {
                if self.project?.id == projectID, seriesContextEpoch == epoch {
                    loadingSeriesIDs.remove(groupID)
                }
            }
            do {
                let response: SeriesResponseDTO = try await callDTO(
                    "series",
                    SeriesParams(projectID: projectID, groupID: groupID),
                    as: SeriesResponseDTO.self
                )
                guard self.project?.id == projectID,
                      seriesContextEpoch == epoch,
                      response.groupID == groupID
                else { return }
                seriesContexts[groupID] = response.items
                prefetchSelectedNeighbours()
            } catch {
                guard self.project?.id == projectID, seriesContextEpoch == epoch else { return }
                expandedSeriesIDs.remove(groupID)
                errorMessage = error.localizedDescription
            }
        }
    }

    func movePhotoSelection(_ offset: Int) {
        let candidates = workspaceMode == .compare || workspaceMode == .survey
            ? workspacePhotos : photos
        guard !candidates.isEmpty else { return }
        let current = selectedPhotoID.flatMap { id in
            candidates.firstIndex(where: { $0.id == id })
        } ?? 0
        let next = min(max(0, current + offset), candidates.count - 1)
        selectPhoto(photoID: candidates[next].id)
    }

    func selectPhoto(photoID: String) {
        guard let photo = photoItem(photoID: photoID) else { return }
        selectedPhotoID = photoID
        if let groupID = photo.duplicateGroup { ensureSeriesContext(groupID: groupID) }
        prefetchSelectedNeighbours()
    }

    private func prefetchSelectedNeighbours() {
        guard let selectedPhotoID else { return }
        let candidates: [PhotoItem]
        if let groupID = photoItem(photoID: selectedPhotoID)?.duplicateGroup,
           let members = seriesContexts[groupID] {
            candidates = members
        } else {
            candidates = photos
        }
        guard let index = candidates.firstIndex(where: { $0.id == selectedPhotoID }) else { return }
        let nearby = ((index - 2)...(index + 2))
            .filter { candidates.indices.contains($0) }
            .sorted { abs($0 - index) < abs($1 - index) }
        let paths = nearby.compactMap {
            candidates[$0].reviewPath ?? candidates[$0].thumbnailPath
        }
        ThumbnailLoader.prefetch(paths: paths, maxPixelSize: 1400)
    }

    func decideSelected(_ disposition: String?) {
        guard let selectedPhotoID else { return }
        setDecision(photoID: selectedPhotoID, disposition: disposition)
    }

    func rateSelected(_ rating: Int?) {
        guard let selectedPhotoID else { return }
        setRating(photoID: selectedPhotoID, rating: rating)
    }

    func setRating(photoID: String, rating: Int?) {
        guard let project,
              rating == nil || (1...5).contains(rating!),
              var previous = photoItem(photoID: photoID)
        else { return }
        let previousRating = previous.manualRating
        let generation = (ratingGenerations[photoID] ?? 0) + 1
        ratingGenerations[photoID] = generation
        previous.manualRating = rating
        updateCachedPhoto(previous)
        Task {
            do {
                let updated: PhotoItem = try await callDTO(
                    "rating",
                    RatingMutationParams(projectID: project.id, assetUUID: photoID, rating: rating),
                    as: PhotoItem.self
                )
                guard ratingGenerations[photoID] == generation else { return }
                updateCachedPhoto(updated)
            } catch {
                guard ratingGenerations[photoID] == generation,
                      var restored = photoItem(photoID: photoID)
                else { return }
                restored.manualRating = previousRating
                updateCachedPhoto(restored)
                errorMessage = error.localizedDescription
            }
        }
    }

    func previewSelected() {
        guard let selectedPhotoID,
              let photo = photoItem(photoID: selectedPhotoID),
              let path = photo.reviewPath ?? photo.thumbnailPath
        else { return }
        QuickLookController.shared.show(path: path)
    }

    func openPhotoDetails(photoID: String) {
        selectPhoto(photoID: photoID)
        guard let project else { return }
        detailRequestGeneration += 1
        let requestGeneration = detailRequestGeneration
        let projectID = project.id
        Task {
            do {
                let photo: PhotoItem = try await callDTO(
                    "asset_details",
                    AssetIDParams(projectID: projectID, assetUUID: photoID),
                    as: PhotoItem.self
                )
                guard detailRequestGeneration == requestGeneration,
                      self.project?.id == projectID,
                      selectedPhotoID == photoID
                else { return }
                detailPhoto = photo
            } catch {
                guard detailRequestGeneration == requestGeneration else { return }
                errorMessage = error.localizedDescription
            }
        }
    }

    func togglePhotoSelection(photoID: String) {
        selectPhoto(photoID: photoID)
        if selectedPhotoIDs.contains(photoID) {
            selectedPhotoIDs.remove(photoID)
        } else {
            selectedPhotoIDs.insert(photoID)
        }
    }

    func clearPhotoSelection() {
        selectedPhotoIDs.removeAll()
    }
}
