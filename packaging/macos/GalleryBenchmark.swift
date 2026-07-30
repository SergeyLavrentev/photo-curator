import AppKit
import Darwin
import Foundation
import SwiftUI

private struct BenchmarkGrid: View {
    let photos: [PhotoItem]

    var body: some View {
        ScrollView {
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 220), spacing: 16)],
                spacing: 16
            ) {
                ForEach(photos) { photo in
                    VStack(alignment: .leading, spacing: 10) {
                        Rectangle()
                            .fill(.quaternary)
                            .frame(height: 180)
                            .overlay(Image(systemName: "photo"))
                        Text(photo.filename).lineLimit(1)
                        Picker("Решение", selection: .constant(photo.disposition ?? "review")) {
                            Text("Оставить").tag("keep")
                            Text("Проверить").tag("review")
                            Text("Не брать").tag("reject")
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                    }
                    .padding(12)
                    .background(.background, in: RoundedRectangle(cornerRadius: 16))
                }
            }
            .padding(20)
        }
        .frame(width: 1440, height: 900)
    }
}

private func syntheticPhotos(count: Int) -> [PhotoItem] {
    (0..<count).compactMap { index in
        PhotoItem([
            "asset_uuid": "benchmark-\(index)",
            "filename": "IMG_\(String(format: "%05d", index)).HEIC",
            "swipe_score": 100 - index % 100,
            "final_disposition": index % 5 == 0 ? "review" : "keep",
            "reasons": [["code": "strong_aesthetics"]],
        ])
    }
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

private func benchmark(count: Int) -> [String: Any] {
    let modelStart = CFAbsoluteTimeGetCurrent()
    let photos = syntheticPhotos(count: count)
    let modelMilliseconds = (CFAbsoluteTimeGetCurrent() - modelStart) * 1000
    let pageSize = 100

    let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
        styleMask: [.borderless],
        backing: .buffered,
        defer: false
    )
    let host = NSHostingView(
        rootView: BenchmarkGrid(photos: Array(photos.prefix(pageSize)))
    )
    window.contentView = host
    window.orderBack(nil)
    let layoutStart = CFAbsoluteTimeGetCurrent()
    host.layoutSubtreeIfNeeded()
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
    let initialLayoutMilliseconds = (CFAbsoluteTimeGetCurrent() - layoutStart) * 1000

    guard let scroll = findScrollView(host) else {
        window.close()
        return [
            "count": count,
            "page_size": pageSize,
            "model_ms": modelMilliseconds,
            "initial_layout_ms": initialLayoutMilliseconds,
            "scroll_view_found": false,
            "passed": false,
        ]
    }
    let maximumY = max(
        0,
        (scroll.documentView?.bounds.height ?? 0) - scroll.contentSize.height
    )
    var samples: [Double] = []
    for step in 0..<30 {
        let start = CFAbsoluteTimeGetCurrent()
        let fraction = Double(step) / 29.0
        scroll.contentView.scroll(to: NSPoint(x: 0, y: maximumY * fraction))
        scroll.reflectScrolledClipView(scroll.contentView)
        host.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.005))
        samples.append((CFAbsoluteTimeGetCurrent() - start) * 1000)
    }
    window.close()
    let p95 = percentile95(samples)
    let passed =
        modelMilliseconds < 100
        && initialLayoutMilliseconds < 750
        && p95 < 100
    return [
        "count": count,
        "page_size": pageSize,
        "model_ms": modelMilliseconds,
        "initial_layout_ms": initialLayoutMilliseconds,
        "scroll_p95_ms": p95,
        "scroll_view_found": true,
        "passed": passed,
    ]
}

@main
private struct GalleryBenchmark {
    static func main() {
        _ = NSApplication.shared
        let counts = [2_000, 5_000]
        let results = counts.map(benchmark)
        let report: [String: Any] = [
            "schema_version": 1,
            "surface": "SwiftUI LazyVGrid paged synthetic gallery",
            "viewport": ["width": 1440, "height": 900],
            "thresholds_ms": [
                "model": 100,
                "initial_layout": 750,
                "scroll_p95": 100,
            ],
            "results": results,
            "passed": results.allSatisfy { $0["passed"] as? Bool == true },
        ]
        let data = try! JSONSerialization.data(
            withJSONObject: report,
            options: [.prettyPrinted, .sortedKeys]
        )
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
        exit(report["passed"] as? Bool == true ? 0 : 1)
    }
}
