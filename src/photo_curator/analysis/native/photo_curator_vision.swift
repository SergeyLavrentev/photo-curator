import CoreVideo
import Foundation
import Vision

private struct BenchmarkInput: Decodable {
    let schemaVersion: Int
    let warmupIterations: Int
    let measuredIterations: Int
    let assets: [InputAsset]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case warmupIterations = "warmup_iterations"
        case measuredIterations = "measured_iterations"
        case assets
    }
}

private struct InputAsset: Decodable {
    let assetUUID: String
    let path: String

    enum CodingKeys: String, CodingKey {
        case assetUUID = "asset_uuid"
        case path
    }
}

private struct EngineInfo: Codable {
    let name: String
    let version: String
    let osVersion: String
    let architecture: String
    let processorCount: Int
    let activeProcessorCount: Int
    let physicalMemoryBytes: UInt64

    enum CodingKeys: String, CodingKey {
        case name, version, architecture
        case osVersion = "os_version"
        case processorCount = "processor_count"
        case activeProcessorCount = "active_processor_count"
        case physicalMemoryBytes = "physical_memory_bytes"
    }
}

private struct Capabilities: Codable {
    let aesthetics: Bool
    let featurePrint: Bool
    let attentionSaliency: Bool
    let faces: Bool

    enum CodingKeys: String, CodingKey {
        case aesthetics
        case featurePrint = "feature_print"
        case attentionSaliency = "attention_saliency"
        case faces
    }
}

private struct AestheticsSignal: Codable {
    let overallScore: Float
    let isUtility: Bool
    let revision: Int

    enum CodingKeys: String, CodingKey {
        case overallScore = "overall_score"
        case isUtility = "is_utility"
        case revision
    }
}

private struct FeaturePrintSignal: Codable {
    let revision: Int
    let elementCount: Int
    let elementType: UInt
    let dataBase64: String

    enum CodingKeys: String, CodingKey {
        case revision
        case elementCount = "element_count"
        case elementType = "element_type"
        case dataBase64 = "data_base64"
    }
}

private struct NormalizedRect: Codable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

private struct SaliencySignal: Codable {
    let revision: Int
    let heatmapWidth: Int
    let heatmapHeight: Int
    let salientObjects: [NormalizedRect]

    enum CodingKeys: String, CodingKey {
        case revision
        case heatmapWidth = "heatmap_width"
        case heatmapHeight = "heatmap_height"
        case salientObjects = "salient_objects"
    }
}

private struct FacesSignal: Codable {
    let faceCount: Int
    let eyesDetected: Int
    let bestCaptureQuality: Float?
    let landmarkRevision: Int
    let qualityRevision: Int

    enum CodingKeys: String, CodingKey {
        case faceCount = "face_count"
        case eyesDetected = "eyes_detected"
        case bestCaptureQuality = "best_capture_quality"
        case landmarkRevision = "landmark_revision"
        case qualityRevision = "quality_revision"
    }
}

private struct AssetResult: Codable {
    let assetUUID: String
    var aesthetics: AestheticsSignal?
    var featurePrint: FeaturePrintSignal?
    var attentionSaliency: SaliencySignal?
    var faces: FacesSignal?
    var durationsMS: [String: Double]
    var errors: [String: String]

    enum CodingKeys: String, CodingKey {
        case assetUUID = "asset_uuid"
        case aesthetics
        case featurePrint = "feature_print"
        case attentionSaliency = "attention_saliency"
        case faces
        case durationsMS = "durations_ms"
        case errors
    }
}

private struct DurationSummary: Codable {
    let meanMS: Double
    let p95MS: Double

    enum CodingKeys: String, CodingKey {
        case meanMS = "mean_ms"
        case p95MS = "p95_ms"
    }
}

private struct BenchmarkSummary: Codable {
    let assetCount: Int
    let successfulAssets: Int
    let wallTimeMS: Double
    let stageDurations: [String: DurationSummary]

    enum CodingKeys: String, CodingKey {
        case assetCount = "asset_count"
        case successfulAssets = "successful_assets"
        case wallTimeMS = "wall_time_ms"
        case stageDurations = "stage_durations"
    }
}

private struct BenchmarkOutput: Codable {
    let schemaVersion: Int
    let engine: EngineInfo
    let capabilities: Capabilities
    let warmupIterations: Int
    let measuredIterations: Int
    let assets: [AssetResult]
    let summary: BenchmarkSummary

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case engine, capabilities
        case warmupIterations = "warmup_iterations"
        case measuredIterations = "measured_iterations"
        case assets, summary
    }
}

private enum BenchmarkError: LocalizedError {
    case usage
    case unsupportedSchema(Int)
    case invalidIterations

    var errorDescription: String? {
        switch self {
        case .usage:
            return "Usage: photo-curator-vision <input.json> [output.json]"
        case let .unsupportedSchema(version):
            return "Unsupported input schema_version=\(version)"
        case .invalidIterations:
            return "warmup_iterations must be >= 0 and measured_iterations must be >= 1"
        }
    }
}

private func measure<T>(_ operation: () throws -> T) rethrows -> (T, Double) {
    let start = ContinuousClock.now
    let value = try operation()
    let duration = start.duration(to: .now)
    let seconds = Double(duration.components.seconds)
    let attoseconds = Double(duration.components.attoseconds) / 1_000_000_000_000_000_000
    return (value, (seconds + attoseconds) * 1_000)
}

@available(macOS 15.0, *)
private func analyzeAesthetics(_ url: URL) throws -> AestheticsSignal {
    let request = VNCalculateImageAestheticsScoresRequest()
    let handler = VNImageRequestHandler(url: url)
    try handler.perform([request])
    guard let observation = request.results?.first else {
        throw NSError(domain: "PhotoCuratorVision", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Aesthetics returned no result"])
    }
    return AestheticsSignal(overallScore: observation.overallScore,
                            isUtility: observation.isUtility,
                            revision: request.revision)
}

private func analyzeFeaturePrint(_ url: URL) throws -> FeaturePrintSignal {
    let request = VNGenerateImageFeaturePrintRequest()
    let handler = VNImageRequestHandler(url: url)
    try handler.perform([request])
    guard let observation = request.results?.first else {
        throw NSError(domain: "PhotoCuratorVision", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Feature print returned no result"])
    }
    return FeaturePrintSignal(revision: request.revision,
                              elementCount: observation.elementCount,
                              elementType: UInt(observation.elementType.rawValue),
                              dataBase64: observation.data.base64EncodedString())
}

private func analyzeSaliency(_ url: URL) throws -> SaliencySignal {
    let request = VNGenerateAttentionBasedSaliencyImageRequest()
    let handler = VNImageRequestHandler(url: url)
    try handler.perform([request])
    guard let observation = request.results?.first else {
        throw NSError(domain: "PhotoCuratorVision", code: 3,
                      userInfo: [NSLocalizedDescriptionKey: "Saliency returned no result"])
    }
    let rectangles = (observation.salientObjects ?? []).map {
        NormalizedRect(x: $0.boundingBox.origin.x,
                       y: $0.boundingBox.origin.y,
                       width: $0.boundingBox.width,
                       height: $0.boundingBox.height)
    }
    return SaliencySignal(revision: request.revision,
                          heatmapWidth: CVPixelBufferGetWidth(observation.pixelBuffer),
                          heatmapHeight: CVPixelBufferGetHeight(observation.pixelBuffer),
                          salientObjects: rectangles)
}

private func analyzeFaces(_ url: URL) throws -> FacesSignal {
    let landmarksRequest = VNDetectFaceLandmarksRequest()
    let qualityRequest = VNDetectFaceCaptureQualityRequest()
    let handler = VNImageRequestHandler(url: url)
    try handler.perform([landmarksRequest, qualityRequest])
    let faces = landmarksRequest.results ?? []
    let eyes = faces.filter {
        $0.landmarks?.leftEye != nil && $0.landmarks?.rightEye != nil
    }.count
    let qualityFaces = qualityRequest.results.flatMap { $0 } ?? []
    let qualities = qualityFaces.compactMap { $0.faceCaptureQuality }
    return FacesSignal(faceCount: faces.count,
                       eyesDetected: eyes,
                       bestCaptureQuality: qualities.max(),
                       landmarkRevision: landmarksRequest.revision,
                       qualityRevision: qualityRequest.revision)
}

private func analyzeOnce(_ asset: InputAsset) -> AssetResult {
    let url = URL(fileURLWithPath: asset.path)
    var result = AssetResult(assetUUID: asset.assetUUID,
                             aesthetics: nil,
                             featurePrint: nil,
                             attentionSaliency: nil,
                             faces: nil,
                             durationsMS: [:],
                             errors: [:])
    if #available(macOS 15.0, *) {
        do {
            let (signal, duration) = try measure { try analyzeAesthetics(url) }
            result.aesthetics = signal
            result.durationsMS["aesthetics"] = duration
        } catch {
            result.errors["aesthetics"] = error.localizedDescription
        }
    } else {
        result.errors["aesthetics"] = "Requires macOS 15.0 or newer"
    }
    do {
        let (signal, duration) = try measure { try analyzeFeaturePrint(url) }
        result.featurePrint = signal
        result.durationsMS["feature_print"] = duration
    } catch {
        result.errors["feature_print"] = error.localizedDescription
    }
    do {
        let (signal, duration) = try measure { try analyzeSaliency(url) }
        result.attentionSaliency = signal
        result.durationsMS["attention_saliency"] = duration
    } catch {
        result.errors["attention_saliency"] = error.localizedDescription
    }
    do {
        let (signal, duration) = try measure { try analyzeFaces(url) }
        result.faces = signal
        result.durationsMS["faces"] = duration
    } catch {
        result.errors["faces"] = error.localizedDescription
    }
    return result
}

private func benchmark(_ asset: InputAsset, input: BenchmarkInput) -> AssetResult {
    for _ in 0..<input.warmupIterations {
        _ = analyzeOnce(asset)
    }
    var last = analyzeOnce(asset)
    var totals = last.durationsMS
    if input.measuredIterations > 1 {
        for _ in 1..<input.measuredIterations {
            let current = analyzeOnce(asset)
            for (stage, duration) in current.durationsMS {
                totals[stage, default: 0] += duration
            }
            last = current
        }
    }
    last.durationsMS = totals.mapValues { $0 / Double(input.measuredIterations) }
    return last
}

private func architecture() -> String {
#if arch(arm64)
    return "arm64"
#elseif arch(x86_64)
    return "x86_64"
#else
    return "unknown"
#endif
}

private func durationSummary(_ assets: [AssetResult]) -> [String: DurationSummary] {
    var values: [String: [Double]] = [:]
    for asset in assets {
        for (stage, duration) in asset.durationsMS {
            values[stage, default: []].append(duration)
        }
    }
    return values.mapValues { raw in
        let sorted = raw.sorted()
        let mean = sorted.reduce(0, +) / Double(sorted.count)
        let index = min(sorted.count - 1, Int(ceil(Double(sorted.count) * 0.95)) - 1)
        return DurationSummary(meanMS: mean, p95MS: sorted[index])
    }
}

private func run() throws {
    let arguments = Array(CommandLine.arguments.dropFirst())
    guard arguments.count == 1 || arguments.count == 2 else { throw BenchmarkError.usage }
    let inputURL = URL(fileURLWithPath: arguments[0])
    let input = try JSONDecoder().decode(BenchmarkInput.self, from: Data(contentsOf: inputURL))
    guard input.schemaVersion == 1 else {
        throw BenchmarkError.unsupportedSchema(input.schemaVersion)
    }
    guard input.warmupIterations >= 0, input.measuredIterations >= 1 else {
        throw BenchmarkError.invalidIterations
    }
    let wallStart = ContinuousClock.now
    let assets = input.assets.map { benchmark($0, input: input) }
    let wallDuration = wallStart.duration(to: .now)
    let wallMS = (Double(wallDuration.components.seconds)
        + Double(wallDuration.components.attoseconds) / 1_000_000_000_000_000_000) * 1_000
    let process = ProcessInfo.processInfo
    let output = BenchmarkOutput(
        schemaVersion: 1,
        engine: EngineInfo(name: "apple-vision-native",
                           version: "1",
                           osVersion: process.operatingSystemVersionString,
                           architecture: architecture(),
                           processorCount: process.processorCount,
                           activeProcessorCount: process.activeProcessorCount,
                           physicalMemoryBytes: process.physicalMemory),
        capabilities: Capabilities(aesthetics: {
            if #available(macOS 15.0, *) { return true }
            return false
        }(), featurePrint: true, attentionSaliency: true, faces: true),
        warmupIterations: input.warmupIterations,
        measuredIterations: input.measuredIterations,
        assets: assets,
        summary: BenchmarkSummary(assetCount: assets.count,
                                  successfulAssets: assets.filter(\.errors.isEmpty).count,
                                  wallTimeMS: wallMS,
                                  stageDurations: durationSummary(assets)))
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let data = try encoder.encode(output)
    if arguments.count == 2 {
        try data.write(to: URL(fileURLWithPath: arguments[1]), options: .atomic)
    } else {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
    }
}

do {
    try run()
} catch {
    FileHandle.standardError.write(Data("Vision benchmark error: \(error.localizedDescription)\n".utf8))
    exit(2)
}
