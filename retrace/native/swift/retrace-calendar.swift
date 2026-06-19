// retrace-calendar — read calendar events via EventKit (on-device).
//
// Usage:  retrace-calendar [days_back]      (default 7)
// Output: {"ok":true,"available":true,"events":[{title,start,end,location,calendar,allDay,id}]}
//
// Needs Calendar permission (prompted on first use). Fails soft.

import Foundation
import EventKit

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"ok\":false}")
    }
}

let days = CommandLine.arguments.count > 1 ? (Int(CommandLine.arguments[1]) ?? 7) : 7
let store = EKEventStore()

let sema = DispatchSemaphore(value: 0)
var granted = false
if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, _ in granted = ok; sema.signal() }
} else {
    store.requestAccess(to: .event) { ok, _ in granted = ok; sema.signal() }
}
_ = sema.wait(timeout: .now() + 20)

if !granted {
    emit(["ok": true, "available": false, "events": [], "reason": "calendar access not granted"])
    exit(0)
}

let end = Date()
guard let start = Calendar.current.date(byAdding: .day, value: -days, to: end) else {
    emit(["ok": false, "error": "date math"])
    exit(0)
}
let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
let events = store.events(matching: predicate)

var out: [[String: Any]] = []
for e in events {
    out.append([
        "title": e.title ?? "(untitled)",
        "start": e.startDate?.timeIntervalSince1970 ?? 0,
        "end": e.endDate?.timeIntervalSince1970 ?? 0,
        "location": e.location ?? "",
        "calendar": e.calendar?.title ?? "",
        "allDay": e.isAllDay,
        "id": e.eventIdentifier ?? "",
    ])
}
emit(["ok": true, "available": true, "events": out])
