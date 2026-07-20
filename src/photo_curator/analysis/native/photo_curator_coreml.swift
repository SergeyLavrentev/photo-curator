import AppKit
import CoreML
import Darwin
import Foundation
import Vision

struct AssetInput: Codable {
    let asset_uuid: String
    let path: String
}

struct BenchmarkInput: Codable {
    let schema_version: Int
    let model_path: String
    let warmup_iterations: Int
    let measured_iterations: Int
    let assets: [AssetInput]
}

struct ClassificationValue: Codable {
    let identifier: String
    let confidence: Double
}

struct OutputSummary: Codable {
    let kind: String
    let feature_name: String?
    let dimension: Int?
    let minimum: Double?
    let maximum: Double?
    let mean: Double?
    let values: [Double]?
    let truncated: Bool
    let classifications: [ClassificationValue]?
}

struct AssetOutput: Codable {
    let asset_uuid: String
    let median_duration_ms: Double?
    let output: OutputSummary?
    let error: String?
}

struct ModelOutput: Codable {
    let source_path: String
    let compiled_path: String
    let compute_units: String
    let inputs: [String]
    let outputs: [String]
}

struct BenchmarkOutput: Codable {
    let schema_version: Int
    let engine: [String: String]
    let model: ModelOutput
    let assets: [AssetOutput]
    let summary: [String: Double]
}

enum BenchmarkError: LocalizedError {
    case invalidArguments
    case invalidSchema
    case imageUnreadable(String)
    case noOutput

    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "Usage: photo-curator-coreml input.json output.json"
        case .invalidSchema: return "Unsupported Core ML benchmark schema"
        case .imageUnreadable(let path): return "Unable to decode image: \(path)"
        case .noOutput: return "Core ML request returned no supported output"
        }
    }
}

func compiledModelURL(_ source: URL) throws -> URL {
    if source.pathExtension == "mlmodelc" { return source }
    return try MLModel.compileModel(at: source)
}

func image(_ path: String) throws -> CGImage {
    guard let value = NSImage(contentsOfFile: path) else {
        throw BenchmarkError.imageUnreadable(path)
    }
    var rect = CGRect(origin: .zero, size: value.size)
    guard let cgImage = value.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw BenchmarkError.imageUnreadable(path)
    }
    return cgImage
}

func outputSummary(_ observations: [VNObservation]?) throws -> OutputSummary {
    if let values = observations as? [VNClassificationObservation], !values.isEmpty {
        return OutputSummary(
            kind: "classification",
            feature_name: nil,
            dimension: values.count,
            minimum: nil,
            maximum: nil,
            mean: nil,
            values: nil,
            truncated: values.count > 10,
            classifications: values.prefix(10).map {
                ClassificationValue(identifier: $0.identifier, confidence: Double($0.confidence))
            }
        )
    }
    guard let observation = observations?.compactMap({ $0 as? VNCoreMLFeatureValueObservation }).first,
          let array = observation.featureValue.multiArrayValue else {
        throw BenchmarkError.noOutput
    }
    let count = array.count
    let limit = min(count, 4096)
    var values: [Double] = []
    values.reserveCapacity(limit)
    var minimum = Double.infinity
    var maximum = -Double.infinity
    var sum = 0.0
    for index in 0..<count {
        let value = array[index].doubleValue
        minimum = min(minimum, value)
        maximum = max(maximum, value)
        sum += value
        if index < limit { values.append(value) }
    }
    return OutputSummary(
        kind: "multi_array",
        feature_name: observation.featureName,
        dimension: count,
        minimum: count > 0 ? minimum : nil,
        maximum: count > 0 ? maximum : nil,
        mean: count > 0 ? sum / Double(count) : nil,
        values: values,
        truncated: count > limit,
        classifications: nil
    )
}

func median(_ values: [Double]) -> Double {
    let sorted = values.sorted()
    let middle = sorted.count / 2
    return sorted.count.isMultiple(of: 2)
        ? (sorted[middle - 1] + sorted[middle]) / 2.0
        : sorted[middle]
}

func peakRSSBytes() -> Double {
    var usage = rusage()
    guard getrusage(RUSAGE_SELF, &usage) == 0 else { return 0 }
    return Double(max(0, usage.ru_maxrss))
}

func runAsset(
    _ asset: AssetInput,
    model: VNCoreMLModel,
    warmup: Int,
    measured: Int
) -> AssetOutput {
    do {
        let cgImage = try image(asset.path)
        var durations: [Double] = []
        var latest: OutputSummary?
        for iteration in 0..<(warmup + measured) {
            let request = VNCoreMLRequest(model: model)
            request.imageCropAndScaleOption = .centerCrop
            let started = DispatchTime.now().uptimeNanoseconds
            try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
            let elapsed = Double(DispatchTime.now().uptimeNanoseconds - started) / 1_000_000.0
            latest = try outputSummary(request.results)
            if iteration >= warmup { durations.append(elapsed) }
        }
        return AssetOutput(
            asset_uuid: asset.asset_uuid,
            median_duration_ms: median(durations),
            output: latest,
            error: nil
        )
    } catch {
        return AssetOutput(
            asset_uuid: asset.asset_uuid,
            median_duration_ms: nil,
            output: nil,
            error: error.localizedDescription
        )
    }
}

do {
    if CommandLine.arguments == [CommandLine.arguments[0], "--capability"] {
        print("coreml-image-benchmark-v1")
        exit(0)
    }
    guard CommandLine.arguments.count == 3 else { throw BenchmarkError.invalidArguments }
    let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
    let input = try JSONDecoder().decode(BenchmarkInput.self, from: Data(contentsOf: inputURL))
    guard input.schema_version == 1,
          input.warmup_iterations >= 0,
          input.measured_iterations > 0,
          !input.assets.isEmpty else { throw BenchmarkError.invalidSchema }

    let sourceURL = URL(fileURLWithPath: input.model_path)
    let compiledURL = try compiledModelURL(sourceURL)
    let ownsCompiledCopy = sourceURL.pathExtension != "mlmodelc"
    defer {
        if ownsCompiledCopy { try? FileManager.default.removeItem(at: compiledURL) }
    }
    let configuration = MLModelConfiguration()
    configuration.computeUnits = .all
    let mlModel = try MLModel(contentsOf: compiledURL, configuration: configuration)
    let visionModel = try VNCoreMLModel(for: mlModel)
    let rows = input.assets.map {
        runAsset(
            $0,
            model: visionModel,
            warmup: input.warmup_iterations,
            measured: input.measured_iterations
        )
    }
    let successful = rows.compactMap(\.median_duration_ms)
    let report = BenchmarkOutput(
        schema_version: 1,
        engine: ["name": "apple-coreml-image", "version": "1"],
        model: ModelOutput(
            source_path: sourceURL.path,
            compiled_path: compiledURL.path,
            compute_units: "all",
            inputs: mlModel.modelDescription.inputDescriptionsByName.keys.sorted(),
            outputs: mlModel.modelDescription.outputDescriptionsByName.keys.sorted()
        ),
        assets: rows,
        summary: [
            "asset_count": Double(rows.count),
            "successful_assets": Double(successful.count),
            "median_inference_ms": successful.isEmpty ? -1 : median(successful),
            "peak_rss_bytes": peakRSSBytes(),
        ]
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    try encoder.encode(report).write(to: outputURL, options: .atomic)
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
