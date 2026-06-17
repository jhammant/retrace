// retrace-present — report user idle time + cheap permission preflight.
//
// Output (stdout): {"ok":true,"idle_seconds":12.3,"present":true,
//                   "screen_recording":true,"accessibility":true}
// Usage: retrace-present [idle_threshold_seconds]
//
// The permission fields use non-prompting preflight checks. Fails soft: on any
// error prints a valid JSON object with ok:false and exits 0.

import Foundation
import CoreGraphics
import ApplicationServices

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"ok\":false}")
    }
}

let threshold = CommandLine.arguments.count > 1 ? (Double(CommandLine.arguments[1]) ?? 120.0) : 120.0

// kCGAnyInputEventType (0xFFFFFFFF) gives idle time across all input event types.
guard let anyType = CGEventType(rawValue: ~UInt32(0)) else {
    emit(["ok": false, "error": "event type"])
    exit(0)
}

let idle = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: anyType)

// Is the screen locked or the display asleep? A logged-in-but-away machine should
// never be captured, even if the idle timer hasn't elapsed yet.
var screenLocked = false
if let dict = CGSessionCopyCurrentDictionary() as? [String: Any],
   let locked = dict["CGSSessionScreenIsLocked"] as? Int {
    screenLocked = locked == 1
}
let displayAsleep = CGDisplayIsAsleep(CGMainDisplayID()) != 0

// Non-prompting permission preflight (safe to call repeatedly, no TCC dialog).
let screenRecording = CGPreflightScreenCaptureAccess()
let accessibility = AXIsProcessTrusted()

let present = idle < threshold && !screenLocked && !displayAsleep

emit([
    "ok": true,
    "idle_seconds": idle,
    "present": present,
    "screen_locked": screenLocked,
    "display_asleep": displayAsleep,
    "screen_recording": screenRecording,
    "accessibility": accessibility,
])
