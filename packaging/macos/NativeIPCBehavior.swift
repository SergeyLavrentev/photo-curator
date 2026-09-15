import Foundation
import Darwin

private struct Params: Encodable { let value: Int }
private struct Reply: Decodable { let value: Int }

@main
struct NativeIPCBehavior {
    static func main() throws {
        let worker = NativeWorkerClient()
        try worker.start()
        for round in 0..<20 {
            let group = DispatchGroup()
            for index in 0..<32 {
                group.enter()
                DispatchQueue.global().async {
                    defer { group.leave() }
                    do {
                        let value = round * 32 + index
                        let reply = try worker.request(method: "echo", params: Params(value: value), as: Reply.self)
                        guard reply.value == value else { fatalError("misrouted response") }
                    } catch { fatalError("IPC failure: \(error)") }
                }
            }
            guard group.wait(timeout: .now() + 10) == .success else {
                fputs("FAIL: multiplexed requests stalled\n", stderr)
                exit(1)
            }
        }
        worker.stop()
        print("PASS: 640 concurrent out-of-order replies, no stranded requests")
    }
}
