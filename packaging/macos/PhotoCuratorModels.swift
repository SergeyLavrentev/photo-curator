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

struct ProjectItem {
    let id: String
    let name: String
    let state: String

    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String,
              let name = value["name"] as? String,
              let state = value["state"] as? String
        else { return nil }
        self.id = id
        self.name = name
        self.state = state
    }
}

struct JobItem: Identifiable {
    let id: String
    let stage: String
    let status: String
    let processed: Int
    let total: Int
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
        message = value["current_message"] as? String ?? ""
    }

    var progress: Double {
        if status == "done" || status == "warning" { return 1 }
        guard total > 0 else { return 0 }
        return min(1, Double(processed) / Double(total))
    }
}

struct PhotoItem: Identifiable {
    let id: String
    let filename: String
    let imagePath: String?
    let swipeScore: Int?
    let genericScore: Double?
    let personalDelta: Double?
    let confidence: Double?
    let reasons: [String]
    var disposition: String?

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
        reasons = (value["reasons"] as? [[String: Any]] ?? []).compactMap {
            ($0["code"] as? String)?.replacingOccurrences(of: "_", with: " ")
        }
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
