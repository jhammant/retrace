// retrace-watch — stream frontmost-app change events for event-driven capture.
//
// Emits one JSON line per event (unbuffered) on stdout and runs until killed:
//   {"event":"app","app_name":"Safari","bundle_id":"com.apple.Safari","ts":...}
//   {"event":"heartbeat","ts":...}            // periodic liveness
//
// Subscribes to NSWorkspace activation notifications (delivered on the main run
// loop). The Python daemon debounces these and triggers a capture cycle.

import Foundation
import AppKit

setvbuf(stdout, nil, _IONBF, 0)  // unbuffered: Python sees each line immediately

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
}

func emitApp(_ app: NSRunningApplication?, event: String) {
    emit([
        "event": event,
        "app_name": app?.localizedName ?? "",
        "bundle_id": app?.bundleIdentifier ?? "",
        "pid": Int(app?.processIdentifier ?? -1),
        "ts": Date().timeIntervalSince1970,
    ])
}

let ws = NSWorkspace.shared
let nc = ws.notificationCenter

nc.addObserver(forName: NSWorkspace.didActivateApplicationNotification,
               object: nil, queue: .main) { note in
    let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
    emitApp(app ?? ws.frontmostApplication, event: "app")
}

// Also react to wake from sleep (a likely context change).
nc.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in
    emitApp(ws.frontmostApplication, event: "wake")
}

// Initial state so the consumer captures immediately on startup.
emitApp(ws.frontmostApplication, event: "app")

// Heartbeat so the consumer can detect a dead watcher.
Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { _ in
    emit(["event": "heartbeat", "ts": Date().timeIntervalSince1970])
}

RunLoop.main.run()
