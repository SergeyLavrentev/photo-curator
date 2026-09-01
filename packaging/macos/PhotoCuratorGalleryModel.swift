import Foundation

@MainActor
extension AppModel {
    var canLoadMorePhotos: Bool {
        galleryCursor != nil && photos.count < photosTotal
    }

    func loadPhotos(
        projectID: String,
        append: Bool = false,
        focusPhotoID: String? = nil
    ) async {
        if append {
            guard !isLoadingPhotos else { return }
        } else {
            galleryRequestGeneration += 1
        }
        let requestGeneration = galleryRequestGeneration
        let requestedBucket = selectionBucket
        guard project?.id == projectID else { return }
        isLoadingPhotos = true
        defer {
            if requestGeneration == galleryRequestGeneration {
                isLoadingPhotos = false
            }
        }
        do {
            if !append {
                galleryCursor = nil
                galleryLoadError = nil
                resetSeriesContexts()
            }
            if !binaryProjects.contains(projectID) {
                let normalized: BinaryDecisionResponseDTO = try await callDTO(
                    "binary_decisions",
                    ProjectIDParams(projectID: projectID),
                    as: BinaryDecisionResponseDTO.self
                )
                guard requestGeneration == galleryRequestGeneration,
                      project?.id == projectID,
                      selectionBucket == requestedBucket else { return }
                applyProjectSummary(normalized.summary)
                binaryProjects.insert(projectID)
            }
            let response: AssetPageDTO = try await callDTO(
                "assets",
                GalleryPageParams(
                    projectID: projectID,
                    selection: selectionBucket.rawValue,
                    limit: galleryPageSize,
                    focusAssetUUID: focusPhotoID ?? "",
                    cursor: append ? galleryCursor : nil
                ),
                as: AssetPageDTO.self
            )
            guard requestGeneration == galleryRequestGeneration,
                  project?.id == projectID,
                  selectionBucket == requestedBucket else { return }
            let page = response.items
            galleryCursor = response.nextCursor
            photosTotal = response.total
            galleryLoadError = nil
            if append {
                let known = Set(photos.map(\.id))
                photos.append(contentsOf: page.filter { !known.contains($0.id) })
            } else {
                photos = page
            }
            await refreshUnavailablePreviewCount(
                requestGeneration: requestGeneration,
                projectID: projectID
            )
            if let focusPhotoID, photos.contains(where: { $0.id == focusPhotoID }) {
                selectedPhotoID = focusPhotoID
            } else if !photos.contains(where: { $0.id == selectedPhotoID }) {
                selectedPhotoID = photos.first?.id
            }
            refreshSelectedSeriesContext()
            if !append {
                UserDefaults.standard.set(projectID, forKey: retainedProjectDefaultsKey)
                selectedPhotoIDs.removeAll()
                currentStep = .selection
                await loadQualityStatus(projectID: projectID)
            }
        } catch {
            guard requestGeneration == galleryRequestGeneration,
                  project?.id == projectID else { return }
            if append {
                galleryLoadError = error.localizedDescription
            } else {
                errorMessage = error.localizedDescription
            }
        }
    }

    func loadMorePhotos(selectFirstNewPhoto: Bool = false) {
        guard let project, canLoadMorePhotos, !isLoadingPhotos else {
            return
        }
        let previousIDs = Set(photos.map(\.id))
        Task {
            await loadPhotos(projectID: project.id, append: true)
            guard selectFirstNewPhoto,
                  let firstNewPhoto = photos.first(where: { !previousIDs.contains($0.id) })
            else { return }
            selectPhoto(photoID: firstNewPhoto.id)
        }
    }

    func loadMorePhotosAutomatically() {
        guard galleryLoadError == nil else { return }
        loadMorePhotos()
    }

    func retryGalleryLoad() {
        galleryLoadError = nil
        loadMorePhotos()
    }

    private func refreshUnavailablePreviewCount(
        requestGeneration: Int,
        projectID: String
    ) async {
        let previews = photos.map {
            (path: $0.thumbnailPath ?? $0.reviewPath, analysisReady: $0.cacheState == "ready")
        }
        let unavailable = await Task.detached(priority: .utility) {
            previews.reduce(into: 0) { count, preview in
                guard preview.analysisReady, let path = preview.path else {
                    count += 1
                    return
                }
                if !FileManager.default.fileExists(atPath: path) { count += 1 }
            }
        }.value
        guard requestGeneration == galleryRequestGeneration,
              project?.id == projectID else { return }
        unavailablePreviewFiles = max(unavailablePreviewFiles, unavailable)
    }

    func showSelection(_ bucket: SelectionBucket) {
        guard selectionBucket != bucket, let project else { return }
        selectionBucket = bucket
        photos = []
        galleryCursor = nil
        photosTotal = count(for: bucket)
        selectedPhotoID = nil
        selectedPhotoIDs.removeAll()
        Task { await loadPhotos(projectID: project.id) }
    }
}
