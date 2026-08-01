import Foundation

struct AlbumItem: Identifiable, Hashable {
    let id: String
    let name: String
    let photoCount: Int
    let videoCount: Int
    let isShared: Bool

    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String, let name = value["name"] as? String else {
            return nil
        }
        self.id = id
        self.name = name
        photoCount = value["photo_count"] as? Int ?? 0
        videoCount = value["video_count"] as? Int ?? 0
        isShared = value["is_shared"] as? Bool ?? false
    }
}

struct ProjectItem: Identifiable, Equatable {
    let id: String
    let name: String
    let state: String
    let albumID: String
    let albumName: String
    let decisionModelVersion: Int
    let analysisMode: String
    let createdAt: String
    let updatedAt: String

    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String,
              let name = value["name"] as? String,
              let state = value["state"] as? String,
              let albumID = value["album_id"] as? String,
              let albumName = value["album_name"] as? String
        else { return nil }
        self.id = id
        self.name = name
        self.state = state
        self.albumID = albumID
        self.albumName = albumName
        let settings = value["settings"] as? [String: Any]
        decisionModelVersion = settings?["decision_model_version"] as? Int ?? 0
        analysisMode = settings?["analysis_mode"] as? String ?? "local"
        createdAt = value["created_at"] as? String ?? ""
        updatedAt = value["updated_at"] as? String ?? ""
    }
}

struct CodexConnectionStatus: Equatable {
    let state: String
    let ready: Bool
    let version: String?
    let authKind: String?
    let detail: String?

    init?(_ value: [String: Any]) {
        guard let state = value["state"] as? String else { return nil }
        self.state = state
        ready = value["ready"] as? Bool ?? false
        version = value["version"] as? String
        authKind = value["auth_kind"] as? String
        detail = value["detail"] as? String
    }
}

struct JobItem: Identifiable {
    let id: String
    let stage: String
    let status: String
    let processed: Int
    let total: Int
    let warnings: Int
    let errors: Int
    let message: String

    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String,
              let stage = value["stage"] as? String,
              let status = value["status"] as? String
        else { return nil }
        self.id = id
        self.stage = stage
        self.status = status
        processed = value["processed_items"] as? Int ?? 0
        total = value["total_items"] as? Int ?? 0
        warnings = value["warnings"] as? Int ?? 0
        errors = value["errors"] as? Int ?? 0
        message = value["current_message"] as? String ?? ""
    }

    var progress: Double {
        if status == "done" || status == "warning" { return 1 }
        guard total > 0 else { return 0 }
        return min(1, Double(processed) / Double(total))
    }
}

struct PhotoItem: Identifiable, Equatable {
    let id: String
    let filename: String
    let imagePath: String?
    let swipeScore: Int?
    let genericScore: Double?
    let personalDelta: Double?
    let confidence: Double?
    let reasons: [String]
    var disposition: String?
    var manualDisposition: String?
    let duplicateGroup: String?
    let qualityTopKRank: Int?
    let qualityDuplicateGroup: String?
    let qualityExpectedLeader: Bool

    init?(_ value: [String: Any]) {
        guard let id = value["asset_uuid"] as? String else { return nil }
        self.id = id
        filename = value["filename"] as? String ?? id
        imagePath = value["review_path"] as? String ?? value["thumbnail_path"] as? String
        swipeScore = (value["swipe_score"] as? NSNumber)?.intValue
        genericScore = (value["generic_score"] as? NSNumber)?.doubleValue
        personalDelta = (value["personal_delta"] as? NSNumber)?.doubleValue
        confidence = (value["confidence"] as? NSNumber)?.doubleValue
        disposition = value["final_disposition"] as? String
        manualDisposition = value["manual_disposition"] as? String
        duplicateGroup = value["duplicate_group"] as? String
        qualityTopKRank = (value["quality_top_k_rank"] as? NSNumber)?.intValue
        qualityDuplicateGroup = value["quality_duplicate_group"] as? String
        qualityExpectedLeader = value["quality_expected_leader"] as? Bool ?? false
        reasons = (value["reasons"] as? [[String: Any]] ?? []).compactMap {
            guard let code = $0["code"] as? String,
                  !["selection_score", "swipe_score"].contains(code)
            else { return nil }
            return reasonTitle(code)
        }
    }
}

struct TasteRound {
    let id: String
    let roundNumber: Int
    let roundTotal: Int
    let albumID: String
    let albumName: String
    let selectionLimit: Int
    let rejectionLimit: Int
    let isAdjustment: Bool
    let photos: [PhotoItem]

    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String,
              let albumID = value["album_id"] as? String,
              let albumName = value["album_name"] as? String
        else { return nil }
        self.id = id
        self.albumID = albumID
        self.albumName = albumName
        roundNumber = value["round_number"] as? Int ?? 1
        roundTotal = value["round_total"] as? Int ?? 3
        selectionLimit = value["selection_limit"] as? Int ?? 3
        rejectionLimit = value["rejection_limit"] as? Int ?? 3
        isAdjustment = value["is_adjustment"] as? Bool ?? false
        photos = (value["photos"] as? [[String: Any]] ?? []).compactMap(PhotoItem.init)
        guard photos.count == 10 else { return nil }
    }
}

struct PublishPlan {
    let id: String
    let itemCount: Int
    let albumName: String

    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String ?? value["publish_id"] as? String else {
            return nil
        }
        self.id = id
        itemCount = value["item_count"] as? Int ?? value["asset_count"] as? Int ?? 0
        albumName = value["album_name"] as? String ?? "Photo Curator — Best"
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
