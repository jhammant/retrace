// retrace-sensitivity — on-device adult/sensitive image detection.
//
// Usage:  retrace-sensitivity /path/to/frame.png
// Output: {"ok":true,"available":true,"sensitive":false}
//
// Uses Apple's SensitiveContentAnalysis (the engine behind macOS "Sensitive
// Content Warning"). If the user hasn't enabled that feature the analysis policy
// is .disabled and we report available:false (caller then skips this layer).
// Nothing about the image leaves the device. Fails soft.

import Foundation

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"ok\":false}")
    }
}

guard CommandLine.arguments.count > 1 else {
    emit(["ok": false, "error": "no image path"])
    exit(0)
}
let path = CommandLine.arguments[1]

#if canImport(SensitiveContentAnalysis)
import SensitiveContentAnalysis

guard #available(macOS 14.0, *) else {
    emit(["ok": true, "available": false, "sensitive": false, "reason": "macOS < 14"])
    exit(0)
}

let analyzer = SCSensitivityAnalyzer()
if analyzer.analysisPolicy == .disabled {
    // The user hasn't turned on Sensitive Content Warning in System Settings.
    emit(["ok": true, "available": false, "sensitive": false, "reason": "policy disabled"])
    exit(0)
}

let sema = DispatchSemaphore(value: 0)
var output: [String: Any] = ["ok": false, "available": true, "error": "unknown"]
Task {
    do {
        let result = try await analyzer.analyzeImage(at: URL(fileURLWithPath: path))
        output = ["ok": true, "available": true, "sensitive": result.isSensitive]
    } catch {
        output = ["ok": false, "available": true, "error": "\(error.localizedDescription)"]
    }
    sema.signal()
}
if sema.wait(timeout: .now() + 10) == .timedOut {
    emit(["ok": false, "available": true, "error": "timeout"])
    exit(0)
}
emit(output)
#else
emit(["ok": true, "available": false, "sensitive": false, "reason": "framework unavailable"])
#endif
