import Foundation

extension AppModel {
    func setSelection(photoID: String, selection: String?, recordUndo: Bool = true) {
        guard let project,
              selection == nil || ["pick", "alternative", "review", "reject"].contains(selection!),
              let previousPhoto = photoItem(photoID: photoID)
        else { return }
        let projectID = project.id
        let mutationBucket = selectionBucket
        let mutationGalleryGeneration = galleryRequestGeneration
        let generation = max(
            decisionGenerations[photoID] ?? 0,
            previousPhoto.mutationGeneration ?? 0
        ) + 1
        decisionGenerations[photoID] = generation
        let previousManual = previousPhoto.manualSelection
        let previousSelection = previousPhoto.selection
        let targetSelection = selection ?? previousPhoto.autoSelection

        func reconcilePage(_ updated: PhotoItem, from priorSelection: String?) {
            guard selectionBucket == mutationBucket,
                  galleryRequestGeneration == mutationGalleryGeneration
            else { return }
            let wasInBucket = priorSelection == mutationBucket.rawValue
            let isInBucket = updated.selection == mutationBucket.rawValue
            if wasInBucket != isInBucket {
                photosTotal = max(0, photosTotal + (isInBucket ? 1 : -1))
            }
            if isInBucket {
                if let index = photos.firstIndex(where: { $0.id == photoID }) {
                    photos[index] = updated
                } else if !wasInBucket {
                    photos.append(updated)
                }
            } else {
                photos.removeAll { $0.id == photoID }
            }
        }

        selectedPhotoID = photoID
        selectedPhotoIDs.remove(photoID)
        adjustSelectionCounts(from: previousSelection, to: targetSelection)
        var optimistic = previousPhoto
        optimistic.selection = targetSelection
        optimistic.manualSelection = selection
        updateCachedPhoto(optimistic)
        reconcilePage(optimistic, from: previousSelection)
        if targetSelection != mutationBucket.rawValue,
           !seriesContexts.values.contains(where: { members in
               members.contains(where: { $0.id == photoID })
           }) {
            selectedPhotoID = photos.first?.id
        }

        Task {
            do {
                let updated: PhotoItem = try await callDTO(
                    "selection",
                    SelectionMutationParams(
                        projectID: projectID,
                        assetUUID: photoID,
                        selection: selection,
                        mutationGeneration: generation
                    ),
                    as: PhotoItem.self
                )
                guard decisionGenerations[photoID] == generation,
                      updated.mutationGeneration == generation
                else { return }
                let pageContextMatches = selectionBucket == mutationBucket
                    && galleryRequestGeneration == mutationGalleryGeneration
                updateCachedPhoto(updated, updateGallery: pageContextMatches)
                reconcilePage(updated, from: targetSelection)
                if recordUndo && previousManual != selection {
                    decisionHistory.append(
                        .selection(
                            photoID: photoID,
                            previousManual: previousManual,
                            previousSelection: previousSelection,
                            changedTo: targetSelection
                        )
                    )
                }
                if !pageContextMatches { await loadPhotos(projectID: projectID) }
            } catch {
                guard decisionGenerations[photoID] == generation else { return }
                adjustSelectionCounts(from: targetSelection, to: previousSelection)
                let pageContextMatches = selectionBucket == mutationBucket
                    && galleryRequestGeneration == mutationGalleryGeneration
                updateCachedPhoto(previousPhoto, updateGallery: pageContextMatches)
                reconcilePage(previousPhoto, from: targetSelection)
                if !pageContextMatches { await loadPhotos(projectID: projectID) }
                errorMessage = error.localizedDescription
            }
        }
    }
}
