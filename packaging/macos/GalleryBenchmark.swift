import AppKit
import Darwin
import Foundation
import SwiftUI

private let pageSize = 36

private struct BenchmarkGrid: View {
    let photos: [PhotoItem]
    let selectedID: String?

    var body: some View {
        ScrollView {
            LazyVGrid(
                columns: [
                    GridItem(
                        .adaptive(minimum: 128, maximum: 128),
                        spacing: 4,
                        alignment: .top
                    )
                ],
                alignment: .leading,
                spacing: 4
            ) {
                ForEach(photos) { photo in
                    PhotoCard(
                        photo: photo,
                        cardWidth: 128,
                        selected: photo.id == selectedID,
                        multiSelected: false,
                        developerToolsEnabled: false,
                        select: {},
                        toggleMultiSelection: {},
                        preview: {},
                        openDetails: {},
                        openGoodPhoto: { _ in },
                        seriesSelected: false,
                        toggleTopK: {},
                        toggleSeriesSelection: {},
                        labelSeriesLeader: {},
                        stackCount: photo.duplicateMemberCount,
                        toggleStack: {},
                        rate: { _ in },
                        decide: { _ in }
                    )
                    .equatable()
                }
            }
            .padding(20)
        }
        .frame(width: 1440, height: 900)
    }
}

private func syntheticPhotos(offset: Int, count: Int, imagePaths: [String]) -> [PhotoItem] {
    (offset..<(offset + count)).compactMap { index in
        PhotoItem([
            "asset_uuid": .string("benchmark-\(index)"),
            "filename": .string("IMG_\(String(format: "%05d", index)).HEIC"),
            "thumbnail_path": .string(imagePaths[index % imagePaths.count]),
            "review_path": .string(imagePaths[index % imagePaths.count]),
            "swipe_score": .integer(100 - index % 100),
            "final_disposition": .string("keep"),
            "final_selection": .string(index % 5 == 0 ? "alternative" : "pick"),
            "duplicate_group": index % 7 == 0 ? .string("stack-\(index / 7)") : .null,
            "duplicate_member_count": .integer(index % 7 == 0 ? 4 : 0),
            "manual_rating": index % 11 == 0 ? .integer(4) : .null,
            "reasons": .array([.object(["code": .string("strong_aesthetics")])]),
        ])
    }
}

private func makeJPEGFixtures() throws -> (URL, [String]) {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("photo-curator-gallery-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let image = NSImage(size: NSSize(width: 640, height: 480))
    image.lockFocus()
    NSColor(calibratedRed: 0.28, green: 0.43, blue: 0.62, alpha: 1).setFill()
    NSRect(x: 0, y: 0, width: 640, height: 480).fill()
    NSColor.white.withAlphaComponent(0.75).setFill()
    NSBezierPath(ovalIn: NSRect(x: 180, y: 100, width: 280, height: 280)).fill()
    image.unlockFocus()
    guard let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let jpeg = bitmap.representation(
              using: .jpeg,
              properties: [.compressionFactor: 0.86]
          ) else {
        throw CocoaError(.fileWriteUnknown)
    }
    var paths: [String] = []
    for index in 0..<(pageSize * 3) {
        let file = directory.appendingPathComponent("fixture-\(index).jpg")
        try jpeg.write(to: file)
        paths.append(file.path)
    }
    return (directory, paths)
}

private func findScrollView(_ view: NSView) -> NSScrollView? {
    if let scroll = view as? NSScrollView { return scroll }
    for child in view.subviews {
        if let scroll = findScrollView(child) { return scroll }
    }
    return nil
}

private func percentile95(_ values: [Double]) -> Double {
    guard !values.isEmpty else { return .infinity }
    let sorted = values.sorted()
    return sorted[min(sorted.count - 1, Int(Double(sorted.count) * 0.95))]
}

private func trace(_ message: String) {
    FileHandle.standardError.write(Data("gallery-benchmark: \(message)\n".utf8))
}

private func benchmark(count: Int, imagePaths: [String]) -> [String: Any] {
    trace("start \(count)")
    let modelStart = CFAbsoluteTimeGetCurrent()
    let photos = syntheticPhotos(offset: 0, count: min(pageSize, count), imagePaths: imagePaths)
    let modelMilliseconds = (CFAbsoluteTimeGetCurrent() - modelStart) * 1000
    let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
        styleMask: [.borderless],
        backing: .buffered,
        defer: false
    )
    let host = NSHostingView(rootView: BenchmarkGrid(photos: photos, selectedID: nil))
    trace("host \(count)")
    window.contentView = host
    window.orderBack(nil)
    let layoutStart = CFAbsoluteTimeGetCurrent()
    host.layoutSubtreeIfNeeded()
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.35))
    trace("layout \(count)")
    let initialLayoutMilliseconds = (CFAbsoluteTimeGetCurrent() - layoutStart) * 1000

    var selectionSamples: [Double] = []
    for step in 0..<30 {
        let start = CFAbsoluteTimeGetCurrent()
        host.rootView = BenchmarkGrid(
            photos: photos,
            selectedID: photos[step % photos.count].id
        )
        host.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.001))
        selectionSamples.append((CFAbsoluteTimeGetCurrent() - start) * 1000)
    }

    guard let scroll = findScrollView(host) else {
        window.close()
        return ["count": count, "scroll_view_found": false, "passed": false]
    }
    let maximumY = max(0, (scroll.documentView?.bounds.height ?? 0) - scroll.contentSize.height)
    var scrollSamples: [Double] = []
    for step in 0..<30 {
        let start = CFAbsoluteTimeGetCurrent()
        scroll.contentView.scroll(to: NSPoint(x: 0, y: maximumY * Double(step) / 29.0))
        scroll.reflectScrolledClipView(scroll.contentView)
        host.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.005))
        scrollSamples.append((CFAbsoluteTimeGetCurrent() - start) * 1000)
    }
    var pageSamples: [Double] = []
    let pageCount = min(30, max(1, count / pageSize - 1))
    for page in 1...pageCount {
        let start = CFAbsoluteTimeGetCurrent()
        let offset = (page * pageSize) % max(pageSize, count - pageSize)
        host.rootView = BenchmarkGrid(
            photos: syntheticPhotos(
                offset: offset,
                count: min(pageSize, count - offset),
                imagePaths: imagePaths
            ),
            selectedID: nil
        )
        host.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.01))
        pageSamples.append((CFAbsoluteTimeGetCurrent() - start) * 1000)
    }
    window.close()
    trace("finish \(count)")
    let scrollP95 = percentile95(scrollSamples)
    let pageP95 = percentile95(pageSamples)
    let selectionP95 = percentile95(selectionSamples)
    let passed = modelMilliseconds < 100 && initialLayoutMilliseconds < 1_200
        && selectionP95 < 50 && scrollP95 < 100 && pageP95 < 450
    return [
        "count": count,
        "page_size": pageSize,
        "model_ms": modelMilliseconds,
        "initial_layout_and_decode_ms": initialLayoutMilliseconds,
        "selection_update_p95_ms": selectionP95,
        "scroll_p95_ms": scrollP95,
        "page_swap_p95_ms": pageP95,
        "scroll_view_found": true,
        "passed": passed,
    ]
}

@main
private struct GalleryBenchmark {
    static func main() throws {
        trace("main")
        _ = NSApplication.shared
        NSApplication.shared.setActivationPolicy(.prohibited)
        trace("application")
        let (fixtureDirectory, imagePaths) = try makeJPEGFixtures()
        trace("fixtures")
        defer { try? FileManager.default.removeItem(at: fixtureDirectory) }
        let results = [2_000, 5_000].map { benchmark(count: $0, imagePaths: imagePaths) }
        let report: [String: Any] = [
            "schema_version": 3,
            "surface": "production PhotoCard + CachedThumbnail + real JPEG paging",
            "viewport": ["width": 1440, "height": 900],
            "thresholds_ms": [
                "model": 100,
                "initial_layout_and_decode": 1_200,
                "selection_update_p95": 50,
                "scroll_p95": 100,
                "page_swap_p95": 450,
            ],
            "results": results,
            "passed": results.allSatisfy { $0["passed"] as? Bool == true },
        ]
        let data = try JSONSerialization.data(
            withJSONObject: report,
            options: [.prettyPrinted, .sortedKeys]
        )
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
        exit(report["passed"] as? Bool == true ? 0 : 1)
    }
}
