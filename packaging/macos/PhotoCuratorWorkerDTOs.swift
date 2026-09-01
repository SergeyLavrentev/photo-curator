import Foundation

struct EmptyWorkerParams: Encodable {}

struct ProjectIDParams: Encodable {
    let projectID: String
    enum CodingKeys: String, CodingKey { case projectID = "project_id" }
}

struct ConfirmedProjectDeletionParams: Encodable {
    let projectID: String
    let confirmed = true
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case confirmed
    }
}

struct PhotoKitAcceptanceParams: Encodable {
    let projectID: String
    let confirmed = true
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case confirmed
    }
}

struct PhotoKitAcceptanceFinalizeParams: Encodable {
    let prepared: PhotoKitAcceptancePreparedDTO
    let confirmed = true
}

struct StartAnalysisParams: Encodable {
    let projectID: String
    let fromStage: String?
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case fromStage = "from_stage"
    }
}

struct CreateProjectParams: Encodable {
    let albumID: String
    let selectionDensity: String
    let analysisMode: String
    let engineApple: Bool
    let engineNIMA: Bool
    let engineMobileCLIP: Bool
    let engineMUSIQ: Bool

    enum CodingKeys: String, CodingKey {
        case albumID = "album_id"
        case selectionDensity = "selection_density"
        case analysisMode = "analysis_mode"
        case engineApple = "engine_apple"
        case engineNIMA = "engine_nima"
        case engineMobileCLIP = "engine_mobileclip"
        case engineMUSIQ = "engine_musiq"
    }
}

struct AssetIDParams: Encodable {
    let projectID: String
    let assetUUID: String
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case assetUUID = "asset_uuid"
    }
}

struct SeriesParams: Encodable {
    let projectID: String
    let groupID: String
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case groupID = "group_id"
    }
}

struct GalleryPageParams: Encodable {
    let projectID: String
    let selection: String
    let limit: Int
    let focusAssetUUID: String
    let cursor: GalleryCursor?

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case selection, limit, cursor
        case focusAssetUUID = "focus_asset_uuid"
    }
}

struct DecisionMutationParams: Encodable {
    let projectID: String
    let assetUUID: String
    let disposition: String?
    let mutationGeneration: Int

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case assetUUID = "asset_uuid"
        case disposition
        case mutationGeneration = "mutation_generation"
    }
}

struct SelectionMutationParams: Encodable {
    let projectID: String
    let assetUUID: String
    let selection: String?
    let mutationGeneration: Int

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case assetUUID = "asset_uuid"
        case selection
        case mutationGeneration = "mutation_generation"
    }
}

struct RatingMutationParams: Encodable {
    let projectID: String
    let assetUUID: String
    let rating: Int?

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case assetUUID = "asset_uuid"
        case rating
    }
}

struct TasteStatusParams: Encodable { let paused: Bool }

struct TasteRoundPrepareParams: Encodable {
    let albumID: String
    let mode: String
    enum CodingKeys: String, CodingKey {
        case albumID = "album_id"
        case mode
    }
}

struct TasteRoundSubmitParams: Encodable {
    let roundID: String
    let selectedUUIDs: [String]
    let rejectedUUIDs: [String]
    enum CodingKeys: String, CodingKey {
        case roundID = "round_id"
        case selectedUUIDs = "selected_uuids"
        case rejectedUUIDs = "rejected_uuids"
    }
}

struct QualityTopKParams: Encodable {
    let projectID: String
    let assetUUID: String
    let selected: Bool
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case assetUUID = "asset_uuid"
        case selected
    }
}

struct QualityCandidatesParams: Encodable {
    let projectID: String
    let limit: Int
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case limit
    }
}

struct QualityLabelParams: Encodable {
    let projectID: String
    let assetUUID: String
    let disposition: String
    let defectCodes: [String]
    let defectSeverity: Int?
    let defectConfidence: Double?
    let note: String?
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case assetUUID = "asset_uuid"
        case disposition
        case defectCodes = "defect_codes"
        case defectSeverity = "defect_severity"
        case defectConfidence = "defect_confidence"
        case note
    }
}

struct QualityPreferenceParams: Encodable {
    let projectID: String
    let leftUUID: String
    let rightUUID: String
    let preferredUUID: String
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case leftUUID = "left_uuid"
        case rightUUID = "right_uuid"
        case preferredUUID = "preferred_uuid"
    }
}

struct QualitySeriesParams: Encodable {
    let projectID: String
    let groupID: String
    let leaderUUID: String
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case groupID = "group_id"
        case leaderUUID = "leader_uuid"
    }
}

struct QualityCustomSeriesParams: Encodable {
    let projectID: String
    let memberUUIDs: [String]
    let leaderUUID: String
    let targetBudget: Int?
    let essentialMemberUUIDs: [String]?
    let redundantGoodMemberUUIDs: [String]?
    let leaderReasonCodes: [String]?
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case memberUUIDs = "member_uuids"
        case leaderUUID = "leader_uuid"
        case targetBudget = "target_budget"
        case essentialMemberUUIDs = "essential_member_uuids"
        case redundantGoodMemberUUIDs = "redundant_good_member_uuids"
        case leaderReasonCodes = "leader_reason_codes"
    }
}

struct QualityEvaluateParams: Encodable {
    let projectID: String
    let manifest: JSONValue
    let scoreSnapshot: JSONValue
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case manifest
        case scoreSnapshot = "score_snapshot"
    }
}

struct PublishDryRunParams: Encodable {
    let projectID: String
    let kind: String
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case kind
    }
}

struct PublishApplyParams: Encodable {
    let publishID: String
    let confirmed: Bool
    enum CodingKeys: String, CodingKey {
        case publishID = "publish_id"
        case confirmed
    }
}

struct BatchDecisionParams: Encodable {
    let projectID: String
    let assetUUIDs: [String]
    let disposition: String

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case assetUUIDs = "asset_uuids"
        case disposition
    }
}

struct ProjectSummaryDTO: Decodable {
    let keep: Int?
    let pick: Int?
    let alternative: Int?
    let selectionReview: Int?
    let selectionReject: Int?
    let reject: Int?
    let total: Int?
    let ready: Int?
    let missing: Int?
    let videosSkipped: Int?
    let appleVisionReady: Int?
    let appleVisionError: Int?
    let codexReady: Int?
    let codexError: Int?
    let unavailablePreviewFiles: Int?

    enum CodingKeys: String, CodingKey {
        case keep, pick, alternative, reject, total, ready, missing
        case videosSkipped = "videos_skipped"
        case selectionReview = "selection_review"
        case selectionReject = "selection_reject"
        case appleVisionReady = "apple_vision_ready"
        case appleVisionError = "apple_vision_error"
        case codexReady = "codex_ready"
        case codexError = "codex_error"
        case unavailablePreviewFiles = "unavailable_preview_files"
    }
}

struct BatchDecisionResponseDTO: Decodable {
    let status: String
    let updated: Int
    let summary: ProjectSummaryDTO
}

struct WorkerStatusDTO: Decodable {
    let status: String
    let workerSchemaVersion: Int
    let databaseSchemaVersion: Int

    enum CodingKeys: String, CodingKey {
        case status
        case workerSchemaVersion = "worker_schema_version"
        case databaseSchemaVersion = "database_schema_version"
    }
}

struct AlbumGroupsDTO: Decodable {
    let regular: [AlbumItem]
    let shared: [AlbumItem]
}

struct WorkerOperationDTO: Decodable {
    let status: String
    let projectID: String?
    let fromStage: String?

    enum CodingKeys: String, CodingKey {
        case status
        case projectID = "project_id"
        case fromStage = "from_stage"
    }
}

struct ProjectResponseDTO: Decodable {
    let project: ProjectItem
    let summary: ProjectSummaryDTO
    let jobs: [JobItem]
}

struct ProjectSummaryEnvelopeDTO: Decodable {
    let summary: ProjectSummaryDTO
}

struct BinaryDecisionResponseDTO: Decodable {
    let resolved: Int
    let summary: ProjectSummaryDTO
}

struct TasteProfileDTO: Decodable {
    let preferenceCount: Int
    let calibrationCount: Int
    let heldOutCount: Int
    let onboardingRoundsCompleted: Int
    let onboardingRoundsTotal: Int
    let onboardingComplete: Bool
    let status: String

    enum CodingKeys: String, CodingKey {
        case status
        case preferenceCount = "preference_count"
        case calibrationCount = "calibration_count"
        case heldOutCount = "held_out_count"
        case onboardingRoundsCompleted = "onboarding_rounds_completed"
        case onboardingRoundsTotal = "onboarding_rounds_total"
        case onboardingComplete = "onboarding_complete"
    }
}

struct TasteRoundResponseDTO: Decodable {
    let profile: TasteProfileDTO
    let round: TasteRound?
}

struct TasteProfileEnvelopeDTO: Decodable {
    let profile: TasteProfileDTO
}

struct TasteProgressDTO: Decodable {
    let kind: String
    let processed: Int
    let total: Int
}

struct QualityStatusDTO: Decodable {
    let manualLabels: Int
    let heldOutPairs: Int
    let expectedTopK: Int
    let humanDuplicateGroups: Int
    let budgetAnnotatedSeries: Int
    let defectLabels: Int
    let releaseReady: Bool

    enum CodingKeys: String, CodingKey {
        case manualLabels = "manual_labels"
        case heldOutPairs = "held_out_pairs"
        case expectedTopK = "expected_top_k"
        case humanDuplicateGroups = "human_duplicate_groups"
        case budgetAnnotatedSeries = "budget_annotated_series"
        case defectLabels = "defect_labels"
        case releaseReady = "release_ready"
    }
}

struct QualityCandidatesDTO: Decodable {
    let items: [PhotoItem]
    let requested: Int
    let available: Int
    let labelled: Int
}

struct QualityPairDTO: Decodable {
    struct Pair: Decodable {
        let left: PhotoItem
        let right: PhotoItem
    }
    let pair: Pair?
    let completed: Int
    let eligible: Int
    let remaining: Int
}

struct QualityPreferenceResponseDTO: Decodable {
    let exampleID: String
    let split: String
    let completed: Int
    enum CodingKeys: String, CodingKey {
        case exampleID = "example_id"
        case split, completed
    }
}

struct QualityEvidenceDTO: Decodable {
    let manifest: JSONValue
    let scoreSnapshot: JSONValue
    let summary: QualityStatusDTO
    enum CodingKeys: String, CodingKey {
        case manifest, summary
        case scoreSnapshot = "score_snapshot"
    }
}

struct QualityEvaluationDTO: Decodable {
    let passed: Bool
    let releaseEligible: Bool
    let labelledAssets: Int
    enum CodingKeys: String, CodingKey {
        case passed
        case releaseEligible = "release_eligible"
        case labelledAssets = "labelled_assets"
    }
}

struct PhotoKitAcceptanceDTO: Decodable {
    let passed: Bool
    let albumName: String
    let albumIdentifier: String
    let assetIdentifier: String
    let acceptanceAlbumRemoved: Bool
    let sourceAssetPreservedAfterCleanup: Bool

    enum CodingKeys: String, CodingKey {
        case passed
        case albumName = "album_name"
        case albumIdentifier = "album_identifier"
        case assetIdentifier = "asset_identifier"
        case acceptanceAlbumRemoved = "acceptance_album_removed"
        case sourceAssetPreservedAfterCleanup = "source_asset_preserved_after_cleanup"
    }
}

struct PhotoKitAcceptanceProgressEventDTO: Codable {
    let phase: String
    let processed: Int
    let total: Int
}

struct PhotoKitAcceptancePreparedDTO: Codable {
    let preparedSchemaVersion: Int
    let projectID: String
    let albumName: String
    let albumIdentifier: String
    let sourceAlbumIdentifier: String
    let assetIdentifier: String
    let sourceAlbumVisibleBeforeCreate: Bool
    let sourceAssetMembershipVerifiedBeforeCreate: Bool
    let albumVisible: Bool
    let sourceAssetReused: Bool
    let noDuplicateAssetCreated: Bool
    let added: Int
    let imported: Int
    let reused: Int
    let progressEvents: [PhotoKitAcceptanceProgressEventDTO]
    let verificationError: String?

    enum CodingKeys: String, CodingKey {
        case preparedSchemaVersion = "prepared_schema_version"
        case projectID = "project_id"
        case albumName = "album_name"
        case albumIdentifier = "album_identifier"
        case sourceAlbumIdentifier = "source_album_identifier"
        case assetIdentifier = "asset_identifier"
        case sourceAlbumVisibleBeforeCreate = "source_album_visible_before_create"
        case sourceAssetMembershipVerifiedBeforeCreate =
            "source_asset_membership_verified_before_create"
        case albumVisible = "album_visible"
        case sourceAssetReused = "source_asset_reused"
        case noDuplicateAssetCreated = "no_duplicate_asset_created"
        case added, imported, reused
        case progressEvents = "progress_events"
        case verificationError = "verification_error"
    }
}

struct PublishProgressDTO: Decodable {
    let kind: String
    let processed: Int
    let total: Int
    let phase: String
}

struct PublishApplyResponseDTO: Decodable { let status: String }
