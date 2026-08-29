import Foundation

protocol JSONDictionaryDTO: Decodable {
    init?(_ value: [String: JSONValue])
}

extension JSONDictionaryDTO {
    init(from decoder: Decoder) throws {
        let value = try [String: JSONValue](from: decoder)
        guard let decoded = Self(value) else {
            throw DecodingError.dataCorrupted(
                .init(
                    codingPath: decoder.codingPath,
                    debugDescription: "Invalid \(String(describing: Self.self)) DTO"
                )
            )
        }
        self = decoded
    }
}

struct AlbumItem: Identifiable, Hashable, JSONDictionaryDTO {
    let id: String
    let name: String
    let photoCount: Int
    let videoCount: Int
    let isShared: Bool

    init?(_ value: [String: JSONValue]) {
        guard let id = value["id"]?.stringValue, let name = value["name"]?.stringValue else {
            return nil
        }
        self.id = id
        self.name = name
        photoCount = value["photo_count"]?.intValue ?? 0
        videoCount = value["video_count"]?.intValue ?? 0
        isShared = value["is_shared"]?.boolValue ?? false
    }
}

struct ProjectItem: Identifiable, Equatable, JSONDictionaryDTO {
    let id: String
    let name: String
    let state: String
    let albumID: String
    let albumName: String
    let decisionModelVersion: Int
    let analysisMode: String
    let createdAt: String
    let updatedAt: String

    init?(_ value: [String: JSONValue]) {
        guard let id = value["id"]?.stringValue,
              let name = value["name"]?.stringValue,
              let state = value["state"]?.stringValue,
              let albumID = value["album_id"]?.stringValue,
              let albumName = value["album_name"]?.stringValue
        else { return nil }
        self.id = id
        self.name = name
        self.state = state
        self.albumID = albumID
        self.albumName = albumName
        let settings = value["settings"]?.objectValue
        decisionModelVersion = settings?["decision_model_version"]?.intValue ?? 0
        analysisMode = settings?["analysis_mode"]?.stringValue ?? "local"
        createdAt = value["created_at"]?.stringValue ?? ""
        updatedAt = value["updated_at"]?.stringValue ?? ""
    }
}

struct CodexConnectionStatus: Equatable, JSONDictionaryDTO {
    let state: String
    let ready: Bool
    let version: String?
    let authKind: String?
    let detail: String?

    init?(_ value: [String: JSONValue]) {
        guard let state = value["state"]?.stringValue else { return nil }
        self.state = state
        ready = value["ready"]?.boolValue ?? false
        version = value["version"]?.stringValue
        authKind = value["auth_kind"]?.stringValue
        detail = value["detail"]?.stringValue
    }
}

struct JobItem: Identifiable, JSONDictionaryDTO {
    let id: String
    let stage: String
    let status: String
    let processed: Int
    let total: Int
    let warnings: Int
    let errors: Int
    let message: String

    init?(_ value: [String: JSONValue]) {
        guard let id = value["id"]?.stringValue,
              let stage = value["stage"]?.stringValue,
              let status = value["status"]?.stringValue
        else { return nil }
        self.id = id
        self.stage = stage
        self.status = status
        processed = value["processed_items"]?.intValue ?? 0
        total = value["total_items"]?.intValue ?? 0
        warnings = value["warnings"]?.intValue ?? 0
        errors = value["errors"]?.intValue ?? 0
        message = value["current_message"]?.stringValue ?? ""
    }

    var progress: Double {
        if status == "done" || status == "warning" { return 1 }
        guard total > 0 else { return 0 }
        return min(1, Double(processed) / Double(total))
    }
}

struct PhotoItem: Identifiable, Equatable, JSONDictionaryDTO {
    let id: String
    let filename: String
    let imagePath: String?
    let thumbnailPath: String?
    let reviewPath: String?
    let cacheState: String
    let swipeScore: Int?
    let genericScore: Double?
    let personalDelta: Double?
    let confidence: Double?
    let confidenceCalibrated: Bool
    let evidenceCoverage: Double?
    let modelCount: Int
    let modelDisagreement: Double?
    let reasons: [DecisionReason]
    var disposition: String?
    var manualDisposition: String?
    let autoSelection: String?
    var selection: String?
    var manualSelection: String?
    var manualRating: Int?
    let mutationGeneration: Int?
    let duplicateGroup: String?
    let duplicateMemberCount: Int
    let duplicateLeaderID: String?
    let duplicateLeader: RelatedPhoto?
    let albumReferences: [RelatedPhoto]
    let qualityTopKRank: Int?
    let qualityDuplicateGroup: String?
    let qualityExpectedLeader: Bool
    let qualityDefectCodes: [String]
    let qualityDefectSeverity: Int?
    let qualityDefectConfidence: Double?
    let qualityNote: String?
    let qualityLabSampled: Bool

    init?(_ value: [String: JSONValue]) {
        if let schema = value["payload_schema_version"]?.intValue, schema != 1 { return nil }
        guard let id = value["asset_uuid"]?.stringValue else { return nil }
        self.id = id
        filename = value["filename"]?.stringValue ?? id
        thumbnailPath = value["thumbnail_path"]?.stringValue
        reviewPath = value["review_path"]?.stringValue
        cacheState = value["cache_state"]?.stringValue ?? "ready"
        imagePath = thumbnailPath ?? reviewPath
        swipeScore = value["swipe_score"]?.intValue
        genericScore = value["generic_score"]?.doubleValue
        personalDelta = value["personal_delta"]?.doubleValue
        confidence = value["confidence"]?.doubleValue
        confidenceCalibrated = value["confidence_calibrated"]?.boolValue ?? false
        evidenceCoverage = value["evidence_coverage"]?.doubleValue
        modelCount = value["model_count"]?.intValue ?? 0
        modelDisagreement = value["model_disagreement"]?.doubleValue
        disposition = value["final_disposition"]?.stringValue
        manualDisposition = value["manual_disposition"]?.stringValue
        autoSelection = value["auto_selection"]?.stringValue
        selection = value["final_selection"]?.stringValue
        manualSelection = value["manual_selection"]?.stringValue
        manualRating = value["manual_rating"]?.intValue
        mutationGeneration = value["mutation_generation"]?.intValue
        duplicateGroup = value["duplicate_group"]?.stringValue
        duplicateMemberCount = value["duplicate_member_count"]?.intValue ?? 0
        duplicateLeaderID = value["duplicate_leader_uuid"]?.stringValue
        duplicateLeader = value["duplicate_leader"]?.objectValue.flatMap(RelatedPhoto.init)
        albumReferences = value["album_references"]?.arrayValue?
            .compactMap(\.objectValue).compactMap(RelatedPhoto.init) ?? []
        qualityTopKRank = value["quality_top_k_rank"]?.intValue
        qualityDuplicateGroup = value["quality_duplicate_group"]?.stringValue
        qualityExpectedLeader = value["quality_expected_leader"]?.boolValue ?? false
        qualityDefectCodes = value["quality_defect_codes"]?.arrayValue?
            .compactMap(\.stringValue) ?? []
        qualityDefectSeverity = value["quality_defect_severity"]?.intValue
        qualityDefectConfidence = value["quality_defect_confidence"]?.doubleValue
        qualityNote = value["quality_note"]?.stringValue
        qualityLabSampled = value["quality_lab_sampled"]?.boolValue ?? false
        reasons = value["reasons"]?.arrayValue?
            .compactMap(\.objectValue).compactMap(DecisionReason.init) ?? []
    }

}

struct RelatedPhoto: Equatable, JSONDictionaryDTO {
    let id: String
    let filename: String
    let imagePath: String?

    init?(_ value: [String: JSONValue]) {
        guard let id = value["asset_uuid"]?.stringValue else { return nil }
        self.id = id
        filename = value["filename"]?.stringValue ?? id
        imagePath = value["review_path"]?.stringValue ?? value["thumbnail_path"]?.stringValue
    }
}

struct GalleryCursor: Equatable, JSONDictionaryDTO {
    let score: Double
    let assetUUID: String

    init?(_ value: [String: JSONValue]) {
        guard let score = value["score"]?.doubleValue,
              let assetUUID = value["asset_uuid"]?.stringValue,
              !assetUUID.isEmpty
        else { return nil }
        self.score = score
        self.assetUUID = assetUUID
    }

}

extension GalleryCursor: Encodable {
    enum CodingKeys: String, CodingKey {
        case score
        case assetUUID = "asset_uuid"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(score, forKey: .score)
        try container.encode(assetUUID, forKey: .assetUUID)
    }
}

struct AssetPageDTO: Decodable {
    let items: [PhotoItem]
    let total: Int
    let nextCursor: GalleryCursor?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case items, total
        case nextCursor = "next_cursor"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(Int.self, forKey: .schemaVersion) == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported asset page schema",
            )
        }
        items = try container.decode([PhotoItem].self, forKey: .items)
        total = try container.decode(Int.self, forKey: .total)
        nextCursor = try container.decodeIfPresent(GalleryCursor.self, forKey: .nextCursor)
    }
}

struct DecisionReason: Identifiable, Hashable, JSONDictionaryDTO {
    let code: String
    let title: String
    let technicalDefects: [String]
    let duplicateKind: String?

    var id: String { code }

    init?(_ value: [String: JSONValue]) {
        guard let code = value["code"]?.stringValue,
              !["selection_score", "swipe_score"].contains(code)
        else { return nil }
        self.code = code
        duplicateKind = value["duplicate_kind"]?.stringValue
        title = code == "weaker_duplicate" && duplicateKind == "exact"
            ? "Это точная копия другого кадра"
            : reasonTitle(code)
        technicalDefects = value["technical_defect_codes"]?.arrayValue?
            .compactMap(\.stringValue).map(reasonTitle) ?? []
    }
}

struct TasteRound: JSONDictionaryDTO {
    let id: String
    let roundNumber: Int
    let roundTotal: Int
    let albumID: String
    let albumName: String
    let selectionLimit: Int
    let rejectionLimit: Int
    let isAdjustment: Bool
    let photos: [PhotoItem]

    init?(_ value: [String: JSONValue]) {
        guard let id = value["id"]?.stringValue,
              let albumID = value["album_id"]?.stringValue,
              let albumName = value["album_name"]?.stringValue
        else { return nil }
        self.id = id
        self.albumID = albumID
        self.albumName = albumName
        roundNumber = value["round_number"]?.intValue ?? 1
        roundTotal = value["round_total"]?.intValue ?? 3
        selectionLimit = value["selection_limit"]?.intValue ?? 3
        rejectionLimit = value["rejection_limit"]?.intValue ?? 3
        isAdjustment = value["is_adjustment"]?.boolValue ?? false
        photos = value["photos"]?.arrayValue?.compactMap(\.objectValue).compactMap(PhotoItem.init) ?? []
        guard photos.count == 10 else { return nil }
    }
}

struct PublishPlan: JSONDictionaryDTO {
    let id: String
    let itemCount: Int
    let albumName: String
    let assetSetSHA256: String
    let items: [RelatedPhoto]

    init?(_ value: [String: JSONValue]) {
        guard let id = value["id"]?.stringValue ?? value["publish_id"]?.stringValue else {
            return nil
        }
        self.id = id
        itemCount = value["item_count"]?.intValue ?? value["asset_count"]?.intValue ?? 0
        albumName = value["album_name"]?.stringValue ?? "Photo Curator — Best"
        assetSetSHA256 = value["asset_set_sha256"]?.stringValue ?? ""
        items = value["items"]?.arrayValue?.compactMap(\.objectValue).compactMap(RelatedPhoto.init) ?? []
    }
}

private func reasonTitle(_ code: String) -> String {
    [
        "strong_aesthetics": "Сильное первое впечатление",
        "strong_composition": "Удачная композиция",
        "interesting_subject": "Интересный сюжет",
        "strong_moment": "Удачный момент",
        "personal_taste_match": "Совпадает с вашим вкусом",
        "personal_taste_mismatch": "Меньше совпадает с вашим вкусом",
        "best_in_series": "Лучший кадр серии",
        "technical_penalty": "Есть технический недостаток",
        "similar_scene": "Похожая сцена уже представлена",
        "too_similar_to_selected": "В подборке уже достаточно похожих кадров",
        "adds_variety": "Добавляет разнообразие",
        "favorite_protected": "Отмечено как избранное",
        "edited_protected": "Ручная обработка сохранена",
        "analysis_unavailable_kept": "Оценка неполная — кадр сохранён из предосторожности",
        "above_album_cutoff": "Один из сильнейших кадров этого альбома",
        "below_album_cutoff": "Уступает другим кадрам этого альбома",
        "weaker_duplicate": "Есть более удачный похожий кадр",
        "possible_blur": "Недостаточная резкость",
        "poor_face_capture": "Лицо снято неразборчиво",
        "extreme_horizon": "Сильно завален горизонт",
        "bad_angle": "Неудачный ракурс",
        "blocked_subject": "Главный объект перекрыт",
        "codex_confirmed_defect": "Codex подтвердил явный дефект",
        "no_confirmed_defect": "Явных дефектов не найдено",
        "underexposed": "Слишком тёмный кадр",
        "overexposed": "Пересвеченный кадр",
        "low_contrast": "Слабый контраст",
        "apple_low_overall": "Слабая общая оценка относительно альбома",
        "weak_aesthetics": "Слабое визуальное впечатление",
        "weak_composition": "Композиция слабее других кадров",
        "weak_subject": "Сюжет выражен недостаточно",
        "weak_moment": "Момент слабее других кадров",
    ][code] ?? code.replacingOccurrences(of: "_", with: " ")
}
