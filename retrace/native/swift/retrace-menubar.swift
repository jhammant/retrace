// retrace-menubar — a macOS menu bar (status item) controller for Retrace.
//
// Usage: retrace-menubar [api_base_url]   (default http://127.0.0.1:8765)
//
// Polls the local Retrace API for status and shows an at-a-glance icon + menu to
// see what's being captured, capture now, start/pause, toggle Hidden mode, and
// open the dashboard/settings. It only talks to 127.0.0.1 — nothing leaves the Mac.

import Cocoa

let apiBase = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "http://127.0.0.1:8765"

final class Controller: NSObject {
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menu = NSMenu()

    let headerItem = NSMenuItem(title: "Retrace", action: nil, keyEquivalent: "")
    let countsItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let focusItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let lastItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    var toggleCaptureItem = NSMenuItem()
    var hiddenItem = NSMenuItem()

    var enabled = false
    var snoozed = false
    var timer: Timer?

    override init() {
        super.init()
        menu.autoenablesItems = false
        buildMenu()
        statusItem.menu = menu
        updateIcon(state: "loading")
        timer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in self?.refresh() }
        refresh()
    }

    private func info(_ item: NSMenuItem) {
        item.isEnabled = false
        menu.addItem(item)
    }

    private func action(_ title: String, _ sel: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        item.target = self
        item.isEnabled = true
        menu.addItem(item)
        return item
    }

    func buildMenu() {
        info(headerItem)
        info(countsItem)
        info(focusItem)
        info(lastItem)
        menu.addItem(.separator())
        _ = action("Capture now", #selector(captureNow))
        toggleCaptureItem = action("Start capture", #selector(toggleCapture))
        hiddenItem = action("Hidden mode", #selector(toggleHidden))
        menu.addItem(.separator())
        _ = action("Open Dashboard", #selector(openDash))
        _ = action("Settings…", #selector(openSettings))
        menu.addItem(.separator())
        // Closes only this menu bar UI — capture keeps running in the background.
        _ = action("Quit menu bar icon", #selector(quit), key: "q")
    }

    func updateIcon(state: String) {
        let symbol: String
        switch state {
        case "recording": symbol = "record.circle"
        case "paused":    symbol = "pause.circle"
        case "hidden":    symbol = "eye.slash"
        case "away":      symbol = "moon.zzz"
        case "offline":   symbol = "exclamationmark.triangle"
        default:          symbol = "clock.arrow.circlepath"
        }
        if let button = statusItem.button {
            let img = NSImage(systemSymbolName: symbol, accessibilityDescription: "Retrace")
            img?.isTemplate = true
            button.image = img
        }
    }

    func isSnoozed(_ v: Any?) -> Bool {
        guard let s = v as? String else { return false }
        if s == "indefinite" { return true }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        if let d = f.date(from: s) { return d > Date() }
        return false
    }

    func refresh() {
        guard let url = URL(string: apiBase + "/capture/status") else { return }
        var req = URLRequest(url: url)
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { [weak self] data, _, err in
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard let data = data, err == nil,
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    self.headerItem.title = "Retrace — offline"
                    self.countsItem.title = "Server not running"
                    self.focusItem.title = ""
                    self.lastItem.title = ""
                    self.toggleCaptureItem.title = "Start capture"
                    self.updateIcon(state: "offline")
                    return
                }
                self.enabled = (obj["enabled"] as? Bool) ?? false
                self.snoozed = self.isSnoozed(obj["snooze_until"])
                let presence = obj["presence"] as? [String: Any] ?? [:]
                let counters = obj["counters"] as? [String: Any] ?? [:]
                let lastApp = obj["last_app"] as? String ?? "—"

                var state = "paused"
                var label = "Paused"
                if !self.enabled {
                    state = "paused"; label = "Paused"
                } else if self.snoozed {
                    state = "hidden"; label = "Hidden mode"
                } else if (presence["screen_locked"] as? Bool) == true {
                    state = "away"; label = "Screen locked"
                } else if (presence["display_asleep"] as? Bool) == true {
                    state = "away"; label = "Display asleep"
                } else if (presence["present"] as? Bool) == false {
                    state = "away"; label = "Away (idle)"
                } else {
                    state = "recording"; label = "Recording"
                }

                let stored = counters["stored"] as? Int ?? 0
                let skipped = (counters["skipped_dupe"] as? Int ?? 0)
                    + (counters["skipped_denylist"] as? Int ?? 0)
                    + (counters["skipped_gated"] as? Int ?? 0)
                    + (counters["skipped_sensitive"] as? Int ?? 0)

                self.headerItem.title = "Retrace — \(label)"
                self.countsItem.title = "Today: \(stored) captured · \(skipped) skipped"
                self.focusItem.title = "Focus: \(lastApp)"
                if let lastAt = obj["last_capture_at"] as? String, !lastAt.isEmpty {
                    self.lastItem.title = "Last: \(self.relative(lastAt))"
                } else {
                    self.lastItem.title = "Last: —"
                }
                self.updateIcon(state: state)
                self.toggleCaptureItem.title = self.enabled ? "Pause capture" : "Start capture"
                self.hiddenItem.title = self.snoozed ? "Resume from Hidden mode" : "Hidden mode"
                self.hiddenItem.state = self.snoozed ? .on : .off
            }
        }.resume()
    }

    func relative(_ iso: String) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        guard let d = f.date(from: iso) else { return iso }
        let s = Int(Date().timeIntervalSince(d))
        if s < 45 { return "just now" }
        if s < 3600 { return "\(s / 60)m ago" }
        return "\(s / 3600)h ago"
    }

    func post(_ path: String) {
        guard let url = URL(string: apiBase + path) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 8
        URLSession.shared.dataTask(with: req) { [weak self] _, _, _ in
            DispatchQueue.main.async { self?.refresh() }
        }.resume()
    }

    @objc func captureNow() { post("/capture/tick?force=true") }
    @objc func toggleCapture() { post(enabled ? "/capture/stop" : "/capture/start") }
    @objc func toggleHidden() { post(snoozed ? "/capture/resume" : "/capture/pause") }
    @objc func openDash() { if let u = URL(string: apiBase) { NSWorkspace.shared.open(u) } }
    @objc func openSettings() { if let u = URL(string: apiBase + "/#/settings") { NSWorkspace.shared.open(u) } }
    @objc func quit() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // menu bar only, no Dock icon
let controller = Controller()
app.run()
