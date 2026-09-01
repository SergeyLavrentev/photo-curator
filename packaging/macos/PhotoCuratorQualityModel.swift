import Foundation

@MainActor
extension AppModel {
    func loadQualityCandidates(limit: Int = 75) {
        guard let project, project.state == "ready" else { return }
        isQualityBusy = true
        Task {
            defer { isQualityBusy = false }
            do {
                let response: QualityCandidatesDTO = try await callDTO(
                    "quality_candidates",
                    QualityCandidatesParams(projectID: project.id, limit: limit),
                    as: QualityCandidatesDTO.self
                )
                qualityCandidates = response.items
                qualityCandidateRequested = response.requested
                qualityCandidateAvailable = response.available
                qualityCandidateLabelled = response.labelled
                qualitySeriesCandidateGroupID = response.series?.groupID
                qualitySeriesCandidateKind = response.series?.kind
                qualitySeriesCandidates = response.series?.items ?? []
                qualityCandidateIndex = response.items.firstIndex {
                    $0.qualityDisposition == nil
                } ?? response.items.count
                await loadQualityStatus(projectID: project.id)
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func saveQualityLabel(
        photoID: String,
        disposition: String,
        defectCodes: [String] = [],
        severity: Int? = nil,
        confidence: Double? = nil,
        note: String? = nil
    ) {
        guard let project else { return }
        isQualityBusy = true
        Task {
            defer { isQualityBusy = false }
            do {
                let updated: PhotoItem = try await callDTO(
                    "quality_label",
                    QualityLabelParams(
                        projectID: project.id,
                        assetUUID: photoID,
                        disposition: disposition,
                        defectCodes: defectCodes,
                        defectSeverity: severity,
                        defectConfidence: confidence,
                        note: note
                    ),
                    as: PhotoItem.self
                )
                if let index = qualityCandidates.firstIndex(where: { $0.id == photoID }) {
                    qualityCandidates[index] = updated
                    let following = qualityCandidates.indices.first {
                        $0 > index && qualityCandidates[$0].qualityDisposition == nil
                    }
                    let wrapped = qualityCandidates.indices.first {
                        $0 <= index && qualityCandidates[$0].qualityDisposition == nil
                    }
                    qualityCandidateIndex = following ?? wrapped ?? qualityCandidates.count
                }
                if let index = qualitySeriesCandidates.firstIndex(where: { $0.id == photoID }) {
                    qualitySeriesCandidates[index] = updated
                }
                qualityCandidateLabelled = qualityCandidates.filter {
                    $0.qualityDisposition != nil
                }.count
                await loadQualityStatus(projectID: project.id)
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func loadQualityPair() {
        guard let project, project.state == "ready" else { return }
        isQualityBusy = true
        Task {
            defer { isQualityBusy = false }
            do {
                let response: QualityPairDTO = try await callDTO(
                    "quality_pair", ProjectIDParams(projectID: project.id), as: QualityPairDTO.self
                )
                qualityPair = response.pair
                qualityPairCompleted = response.completed
                qualityPairEligible = response.eligible
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func chooseQualityPreference(_ preferredID: String) {
        guard let project, let pair = qualityPair else { return }
        isQualityBusy = true
        Task {
            defer { isQualityBusy = false }
            do {
                let response: QualityPreferenceResponseDTO = try await callDTO(
                    "quality_preference",
                    QualityPreferenceParams(
                        projectID: project.id,
                        leftUUID: pair.left.id,
                        rightUUID: pair.right.id,
                        preferredUUID: preferredID
                    ),
                    as: QualityPreferenceResponseDTO.self
                )
                qualityPairCompleted = response.completed
                await loadQualityStatus(projectID: project.id)
                loadQualityPair()
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func toggleWizardQualityTopK(photoID: String) {
        guard let project,
              let photo = qualityCandidates.first(where: { $0.id == photoID }) else { return }
        Task {
            do {
                _ = try await callDTO(
                    "quality_top_k",
                    QualityTopKParams(
                        projectID: project.id,
                        assetUUID: photoID,
                        selected: photo.qualityTopKRank == nil
                    ),
                    as: JSONValue.self
                )
                loadQualityCandidates(limit: qualityCandidateRequested)
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func saveWizardQualitySeries(
        memberIDs: [String],
        leaderID: String,
        targetBudget: Int,
        essentialMemberIDs: [String],
        redundantGoodMemberIDs: [String],
        leaderReasonCodes: [String],
        sourceGroupID: String?
    ) {
        guard let project else { return }
        Task {
            do {
                _ = try await callDTO(
                    "quality_custom_series",
                    QualityCustomSeriesParams(
                        projectID: project.id,
                        memberUUIDs: memberIDs,
                        leaderUUID: leaderID,
                        targetBudget: targetBudget,
                        essentialMemberUUIDs: essentialMemberIDs,
                        redundantGoodMemberUUIDs: redundantGoodMemberIDs,
                        leaderReasonCodes: leaderReasonCodes,
                        sourceGroupID: sourceGroupID
                    ),
                    as: JSONValue.self
                )
                qualityMessage = "Серия, целевой бюджет и причины выбора сохранены."
                loadQualityCandidates(limit: qualityCandidateRequested)
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func evaluateCurrentQualityEvidence() {
        guard let project else { return }
        isQualityBusy = true
        Task {
            defer { isQualityBusy = false }
            do {
                let evidence: QualityEvidenceDTO = try await callDTO(
                    "quality_export",
                    ProjectIDParams(projectID: project.id),
                    as: QualityEvidenceDTO.self
                )
                let report: QualityEvaluationDTO = try await callDTO(
                    "quality_evaluate",
                    QualityEvaluateParams(
                        projectID: project.id,
                        manifest: evidence.manifest,
                        scoreSnapshot: evidence.scoreSnapshot
                    ),
                    as: QualityEvaluationDTO.self
                )
                qualityMessage = report.releaseEligible
                    ? "Acceptance: \(report.passed ? "PASS" : "FAIL"), \(report.labelledAssets) фото."
                    : "Набор пока неполный: \(report.labelledAssets) фото."
            } catch { errorMessage = error.localizedDescription }
        }
    }
}
