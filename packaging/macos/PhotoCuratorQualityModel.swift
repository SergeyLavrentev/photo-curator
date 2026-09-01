import AppKit
import Foundation
import UniformTypeIdentifiers

@MainActor
extension AppModel {
    func startQualityRound(split: String) {
        guard let project, ["training", "held_out"].contains(split) else { return }
        qualityCandidates = []
        qualitySeriesCandidates = []
        qualityPair = nil
        qualityCandidateIndex = 0
        isQualityBusy = true
        Task {
            defer { isQualityBusy = false }
            do {
                let started: QualityRoundStartDTO = try await callDTO(
                    "quality_round_start",
                    QualityRoundStartParams(projectID: project.id, split: split),
                    as: QualityRoundStartDTO.self
                )
                qualityMessage = split == "training"
                    ? "Создан обучающий раунд №\(started.attemptIndex)."
                    : "Создан замороженный проверочный раунд №\(started.attemptIndex)."
                await loadQualityStatus(projectID: project.id)
                loadQualityCandidates(limit: qualityCandidateRequested)
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func exportLearningCorpus() {
        Task {
            do {
                let corpus: JSONValue = try await callDTO(
                    "quality_learning_export", EmptyWorkerParams(), as: JSONValue.self
                )
                let panel = NSSavePanel()
                panel.title = "Экспорт накопленного обучения"
                panel.nameFieldStringValue = "photo-curator-learning-corpus.json"
                panel.allowedContentTypes = [.json]
                guard panel.runModal() == .OK, let url = panel.url else { return }
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                try encoder.encode(corpus).write(to: url, options: .atomic)
                qualityMessage = "Накопленное обучение экспортировано."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func importLearningCorpus() {
        Task {
            do {
                let panel = NSOpenPanel()
                panel.title = "Импорт накопленного обучения"
                panel.allowedContentTypes = [.json]
                panel.allowsMultipleSelection = false
                guard panel.runModal() == .OK, let url = panel.url else { return }
                let corpus = try JSONDecoder().decode(
                    JSONValue.self, from: Data(contentsOf: url)
                )
                _ = try await callDTO(
                    "quality_learning_import",
                    QualityLearningImportParams(corpus: corpus),
                    as: JSONValue.self
                )
                if let project { await loadQualityStatus(projectID: project.id) }
                qualityMessage = "Накопленное обучение импортировано и проверено."
            } catch { errorMessage = error.localizedDescription }
        }
    }

    func deleteAllLearningData() {
        isQualityBusy = true
        Task {
            defer { isQualityBusy = false }
            do {
                _ = try await callDTO(
                    "quality_learning_delete",
                    ConfirmedWorkerParams(confirmed: true),
                    as: JSONValue.self
                )
                qualityTrainingContexts = 0
                qualityHeldOutContexts = 0
                qualityActiveRound = nil
                qualityRankerVersion = nil
                qualityRankerStatus = "collecting"
                qualityTrainingExamples = 0
                qualityMessage = "Все накопленные learning data и локальный ранкер удалены."
                await refreshDecisionsForTaste()
            } catch { errorMessage = error.localizedDescription }
        }
    }

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
                qualitySeriesCandidateGroupID = response.series?.kind == "manual_album_order"
                    ? nil : response.series?.groupID
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
