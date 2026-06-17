// retrace-caption — 1-2 sentence "what the user is doing" via Apple Foundation Models.
//
// Input  (argv[1], JSON): {"app":..,"window":..,"url":..,"text":..}
// Output (stdout, JSON):   {"ok":true,"caption":"...","model":"apple-foundation-models"}
//
// Uses the on-device LanguageModelSession (text in / text out, no download). If
// Apple Intelligence / the system model is unavailable, reports ok:false so the
// caller falls back to a template caption. Nothing leaves the device. Fails soft.

import Foundation

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"ok\":false}")
    }
}

guard CommandLine.arguments.count > 1,
      let data = CommandLine.arguments[1].data(using: .utf8),
      let cfg = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
    emit(["ok": false, "error": "missing/invalid config"])
    exit(0)
}

let app = (cfg["app"] as? String) ?? ""
let window = (cfg["window"] as? String) ?? ""
let url = (cfg["url"] as? String) ?? ""
let text = String(((cfg["text"] as? String) ?? "").prefix(3000))

let prompt = """
Summarize, in one or two concise sentences, what the user is doing right now. \
Be specific and factual; do not start with "The user". \
App: \(app)
Window: \(window)
URL: \(url)
On-screen text (may be truncated):
\(text)
"""

#if canImport(FoundationModels)
import FoundationModels

guard #available(macOS 26.0, *) else {
    emit(["ok": false, "error": "FoundationModels requires macOS 26+"])
    exit(0)
}

switch SystemLanguageModel.default.availability {
case .available:
    break
case .unavailable(let reason):
    emit(["ok": false, "error": "model unavailable: \(reason)"])
    exit(0)
@unknown default:
    emit(["ok": false, "error": "model availability unknown"])
    exit(0)
}

let sema = DispatchSemaphore(value: 0)
var output: [String: Any] = ["ok": false, "error": "unknown"]
Task {
    do {
        let session = LanguageModelSession(
            instructions: "You write terse, specific 1-2 sentence activity summaries."
        )
        let response = try await session.respond(to: prompt)
        let caption = response.content.trimmingCharacters(in: .whitespacesAndNewlines)
        if caption.isEmpty {
            output = ["ok": false, "error": "empty caption"]
        } else {
            output = ["ok": true, "caption": caption, "model": "apple-foundation-models"]
        }
    } catch {
        output = ["ok": false, "error": "\(error.localizedDescription)"]
    }
    sema.signal()
}
if sema.wait(timeout: .now() + 25) == .timedOut {
    emit(["ok": false, "error": "caption timed out"])
    exit(0)
}
emit(output)
#else
emit(["ok": false, "error": "FoundationModels not available in SDK"])
#endif
