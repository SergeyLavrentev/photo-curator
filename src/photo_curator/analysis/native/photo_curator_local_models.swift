import AppKit
import CoreML
import Darwin
import Foundation
import Vision

struct AssetInput: Codable { let asset_uuid: String; let path: String }
struct ModelsInput: Codable {
    let schema_version: Int
    let model_root: String
    let compiled_cache_root: String
    let model_digests: [String: String]
    let engines: [String]
    let assets: [AssetInput]
    let work_batch_size: Int?
    let max_concurrency: Int?
}

struct NIMASignal: Codable { let aesthetic_score: Double; let raw_score: Double }
struct MUSIQSignal: Codable { let quality_score: Double; let raw_score: Double }
struct GenreScore: Codable { let genre: String; let confidence: Double }
struct MobileCLIPSignal: Codable {
    let aesthetic_score: Double
    let genre: String
    let genre_confidence: Double
    let genre_scores: [GenreScore]
}
struct AssetOutput: Codable {
    let asset_uuid: String
    let nima: NIMASignal?
    let mobileclip: MobileCLIPSignal?
    let musiq: MUSIQSignal?
    let durations_ms: [String: Double]
    let errors: [String: String]
}
struct PromptManifest: Codable {
    let schema_version: Int
    let model: String
    let positive: [Double]
    let negative: [Double]
    let genres: [String: [Double]]
}
struct EngineOutput: Codable { let name: String; let version: String }
struct ModelsSummary: Codable {
    let asset_count: Double
    let successful_signals: Double
    let errors: Double
    let peak_rss_bytes: Double
    let wall_duration_ms: Double
    let thermal_state_before: String
    let thermal_state_after: String
    let energy_status: String
    let work_batch_size: Double
    let max_concurrency: Double
}
struct ModelsOutput: Codable {
    let schema_version: Int
    let engine: EngineOutput
    let enabled_engines: [String]
    let assets: [AssetOutput]
    let summary: ModelsSummary
}

enum ModelsError: LocalizedError {
    case invalidArguments, invalidSchema, imageUnreadable(String), missingFeature(String)
    var errorDescription: String? {
        switch self {
        case .invalidArguments: return "Usage: photo-curator-local-models input.json output.json"
        case .invalidSchema: return "Unsupported local model input"
        case .imageUnreadable(let path): return "Unable to decode image: \(path)"
        case .missingFeature(let name): return "Core ML output is missing: \(name)"
        }
    }
}

final class LoadedModel {
    let model: MLModel
    init(path: URL, cacheRoot: URL, digest: String) throws {
        let compiled: URL
        if path.pathExtension == "mlmodelc" {
            compiled = path
        } else {
            try FileManager.default.createDirectory(
                at: cacheRoot, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            let destination = cacheRoot.appendingPathComponent("\(digest).mlmodelc")
            if !FileManager.default.fileExists(atPath: destination.path) {
                let temporary = try MLModel.compileModel(at: path)
                let staging = cacheRoot.appendingPathComponent("\(digest)-\(UUID().uuidString).tmp")
                try FileManager.default.moveItem(at: temporary, to: staging)
                do {
                    try FileManager.default.moveItem(at: staging, to: destination)
                } catch {
                    try? FileManager.default.removeItem(at: staging)
                    if !FileManager.default.fileExists(atPath: destination.path) { throw error }
                }
            }
            compiled = destination
        }
        let configuration = MLModelConfiguration()
        configuration.computeUnits = .all
        model = try MLModel(contentsOf: compiled, configuration: configuration)
    }
}

func cgImage(_ path: String) throws -> CGImage {
    guard let image = NSImage(contentsOfFile: path) else { throw ModelsError.imageUnreadable(path) }
    var rect = CGRect(origin: .zero, size: image.size)
    guard let result = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw ModelsError.imageUnreadable(path)
    }
    return result
}

func vectorPrediction(_ model: MLModel, image: CGImage) throws -> [Double] {
    let request = VNCoreMLRequest(model: try VNCoreMLModel(for: model))
    request.imageCropAndScaleOption = .centerCrop
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    guard let observation = request.results?.compactMap({ $0 as? VNCoreMLFeatureValueObservation }).first,
          let array = observation.featureValue.multiArrayValue else {
        throw ModelsError.missingFeature("image output")
    }
    return (0..<array.count).map { array[$0].doubleValue }
}

func resizedRGB(_ image: CGImage, size: Int) throws -> [UInt8] {
    var bytes = [UInt8](repeating: 127, count: size * size * 4)
    guard let context = CGContext(
        data: &bytes,
        width: size,
        height: size,
        bitsPerComponent: 8,
        bytesPerRow: size * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { throw ModelsError.imageUnreadable("Core Graphics context") }
    context.interpolationQuality = .high
    context.draw(image, in: CGRect(x: 0, y: 0, width: size, height: size))
    return bytes
}

func aspectFitRGB(_ image: CGImage, size: Int) throws -> [UInt8] {
    var bytes = [UInt8](repeating: 127, count: size * size * 4)
    guard let context = CGContext(
        data: &bytes,
        width: size,
        height: size,
        bitsPerComponent: 8,
        bytesPerRow: size * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { throw ModelsError.imageUnreadable("Core Graphics context") }
    context.interpolationQuality = .high
    let scale = min(Double(size) / Double(image.width), Double(size) / Double(image.height))
    let width = Double(image.width) * scale
    let height = Double(image.height) * scale
    context.draw(
        image,
        in: CGRect(x: (Double(size) - width) / 2, y: (Double(size) - height) / 2,
                   width: width, height: height)
    )
    return bytes
}

func appendPatches(_ pixels: [UInt8], size: Int, to target: UnsafeMutablePointer<Float>, offset: inout Int) {
    let patches = size / 32
    for patchY in 0..<patches {
        for patchX in 0..<patches {
            for channel in 0..<3 {
                for y in 0..<32 {
                    for x in 0..<32 {
                        let pixel = (((patchY * 32 + y) * size) + patchX * 32 + x) * 4 + channel
                        target[offset] = (Float(pixels[pixel]) / 255.0 - 0.5) * 2.0
                        offset += 1
                    }
                }
            }
        }
    }
}

func musiqPrediction(_ model: MLModel, image: CGImage) throws -> Double {
    let small = try resizedRGB(image, size: 224)
    let large = try resizedRGB(image, size: 384)
    let fitted = try aspectFitRGB(image, size: 384)
    let patches = try MLMultiArray(shape: [1, 337, 3072], dataType: .float32)
    let pointer = patches.dataPointer.bindMemory(to: Float.self, capacity: patches.count)
    var offset = 0
    appendPatches(small, size: 224, to: pointer, offset: &offset)
    appendPatches(large, size: 384, to: pointer, offset: &offset)
    appendPatches(fitted, size: 384, to: pointer, offset: &offset)
    let provider = try MLDictionaryFeatureProvider(dictionary: ["patches": MLFeatureValue(multiArray: patches)])
    let result = try model.prediction(from: provider)
    guard let output = result.featureValue(for: "quality_score")?.multiArrayValue else {
        throw ModelsError.missingFeature("quality_score")
    }
    return output[0].doubleValue
}

func normalized(_ values: [Double]) -> [Double] {
    let magnitude = sqrt(values.reduce(0) { $0 + $1 * $1 })
    return magnitude > 0 ? values.map { $0 / magnitude } : values
}

func cosine(_ lhs: [Double], _ rhs: [Double]) -> Double {
    zip(lhs, rhs).reduce(0) { $0 + $1.0 * $1.1 }
}

func mobileCLIPValue(_ values: [Double], prompts: PromptManifest) -> MobileCLIPSignal {
    let embedding = normalized(values)
    let positive = cosine(embedding, prompts.positive)
    let negative = cosine(embedding, prompts.negative)
    let aesthetic = 100.0 / (1.0 + exp(-20.0 * (positive - negative)))
    let similarities = prompts.genres.map { ($0.key, cosine(embedding, $0.value)) }
    let maximum = similarities.map(\.1).max() ?? 0
    let exponentials = similarities.map { ($0.0, exp(15.0 * ($0.1 - maximum))) }
    let denominator = exponentials.reduce(0) { $0 + $1.1 }
    let ranked = exponentials.map { GenreScore(genre: $0.0, confidence: $0.1 / denominator) }
        .sorted { $0.confidence > $1.confidence }
    return MobileCLIPSignal(
        aesthetic_score: aesthetic,
        genre: ranked.first?.genre ?? "unknown",
        genre_confidence: ranked.first?.confidence ?? 0,
        genre_scores: Array(ranked.prefix(3))
    )
}

func timed<T>(_ operation: () throws -> T) rethrows -> (T, Double) {
    let start = DispatchTime.now().uptimeNanoseconds
    let value = try operation()
    return (value, Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000.0)
}

func peakRSSBytes() -> Double {
    var usage = rusage()
    guard getrusage(RUSAGE_SELF, &usage) == 0 else { return 0 }
    return Double(max(0, usage.ru_maxrss))
}

func thermalStateName(_ state: ProcessInfo.ThermalState) -> String {
    switch state {
    case .nominal: return "nominal"
    case .fair: return "fair"
    case .serious: return "serious"
    case .critical: return "critical"
    @unknown default: return "unknown"
    }
}

func analyzeAsset(
    _ asset: AssetInput,
    engines: [String],
    nima: LoadedModel?,
    mobileclip: LoadedModel?,
    musiq: LoadedModel?,
    prompts: PromptManifest?
) -> AssetOutput {
    var nimaValue: NIMASignal?
    var clipValue: MobileCLIPSignal?
    var musiqValue: MUSIQSignal?
    var durations: [String: Double] = [:]
    var errors: [String: String] = [:]
    do {
        let image = try cgImage(asset.path)
        if let nima {
            do {
                let (values, duration) = try timed { try vectorPrediction(nima.model, image: image) }
                guard let raw = values.first else { throw ModelsError.missingFeature("aesthetic_score") }
                nimaValue = NIMASignal(
                    aesthetic_score: min(100, max(0, (raw - 1) / 9 * 100)), raw_score: raw
                )
                durations["nima"] = duration
            } catch { errors["nima"] = error.localizedDescription }
        }
        if let mobileclip, let prompts {
            do {
                let (values, duration) = try timed {
                    try vectorPrediction(mobileclip.model, image: image)
                }
                clipValue = mobileCLIPValue(values, prompts: prompts)
                durations["mobileclip"] = duration
            } catch { errors["mobileclip"] = error.localizedDescription }
        }
        if let musiq {
            do {
                let (raw, duration) = try timed { try musiqPrediction(musiq.model, image: image) }
                musiqValue = MUSIQSignal(
                    quality_score: min(100, max(0, raw)), raw_score: raw
                )
                durations["musiq"] = duration
            } catch { errors["musiq"] = error.localizedDescription }
        }
    } catch {
        for engine in engines { errors[engine] = error.localizedDescription }
    }
    return AssetOutput(
        asset_uuid: asset.asset_uuid,
        nima: nimaValue,
        mobileclip: clipValue,
        musiq: musiqValue,
        durations_ms: durations,
        errors: errors
    )
}

do {
    let wallStart = DispatchTime.now().uptimeNanoseconds
    let thermalBefore = thermalStateName(ProcessInfo.processInfo.thermalState)
    guard CommandLine.arguments.count == 3 else { throw ModelsError.invalidArguments }
    let input = try JSONDecoder().decode(
        ModelsInput.self,
        from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
    )
    let allowed = Set(["nima", "mobileclip", "musiq"])
    let workBatchSize = input.work_batch_size ?? 16
    let maxConcurrency = input.max_concurrency ?? 2
    guard input.schema_version == 1, !input.assets.isEmpty,
          Set(input.engines).isSubset(of: allowed),
          workBatchSize >= 1, workBatchSize <= 256,
          maxConcurrency >= 1, maxConcurrency <= 4,
          maxConcurrency <= workBatchSize else { throw ModelsError.invalidSchema }
    let root = URL(fileURLWithPath: input.model_root, isDirectory: true)
    let compiledRoot = URL(fileURLWithPath: input.compiled_cache_root, isDirectory: true)
    let enabled = Set(input.engines)
    let nima = enabled.contains("nima")
        ? try LoadedModel(
            path: root.appendingPathComponent("nima-inception-v2-ava.mlpackage"),
            cacheRoot: compiledRoot,
            digest: input.model_digests["nima"] ?? "nima-unknown"
        ) : nil
    let mobileclip = enabled.contains("mobileclip")
        ? try LoadedModel(
            path: root.appendingPathComponent("mobileclip_s0_image.mlpackage"),
            cacheRoot: compiledRoot,
            digest: input.model_digests["mobileclip"] ?? "mobileclip-unknown"
        ) : nil
    let musiq = enabled.contains("musiq")
        ? try LoadedModel(
            path: root.appendingPathComponent("musiq-koniq10k.mlpackage"),
            cacheRoot: compiledRoot,
            digest: input.model_digests["musiq"] ?? "musiq-unknown"
        ) : nil
    let prompts = enabled.contains("mobileclip")
        ? try JSONDecoder().decode(
            PromptManifest.self,
            from: Data(contentsOf: root.appendingPathComponent("mobileclip-prompts.json"))
        ) : nil
    var rows = Array<AssetOutput?>(repeating: nil, count: input.assets.count)
    let resultLock = NSLock()
    for start in stride(from: 0, to: input.assets.count, by: workBatchSize) {
        let end = min(start + workBatchSize, input.assets.count)
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = maxConcurrency
        for index in start..<end {
            queue.addOperation {
                let row = analyzeAsset(
                    input.assets[index], engines: input.engines, nima: nima,
                    mobileclip: mobileclip, musiq: musiq, prompts: prompts
                )
                resultLock.lock()
                rows[index] = row
                resultLock.unlock()
            }
        }
        queue.waitUntilAllOperationsAreFinished()
    }
    let completedRows = rows.compactMap { $0 }
    guard completedRows.count == input.assets.count else { throw ModelsError.invalidSchema }
    let errorCount = completedRows.reduce(0) { $0 + $1.errors.count }
    let successful = completedRows.reduce(0) { $0 + input.engines.count - $1.errors.count }
    let report = ModelsOutput(
        schema_version: 1,
        engine: EngineOutput(name: "photo-curator-local-models", version: "2"),
        enabled_engines: input.engines.sorted(),
        assets: completedRows,
        summary: ModelsSummary(
            asset_count: Double(completedRows.count), successful_signals: Double(successful),
            errors: Double(errorCount), peak_rss_bytes: peakRSSBytes(),
            wall_duration_ms: Double(DispatchTime.now().uptimeNanoseconds - wallStart) / 1_000_000.0,
            thermal_state_before: thermalBefore,
            thermal_state_after: thermalStateName(ProcessInfo.processInfo.thermalState),
            energy_status: "not_measured",
            work_batch_size: Double(workBatchSize),
            max_concurrency: Double(maxConcurrency)
        )
    )
    let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    try encoder.encode(report).write(
        to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic
    )
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
