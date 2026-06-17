// retrace-context — read the real on-screen context via the Accessibility API.
//
// Output (stdout, JSON):
//   {"ok":true,"app_name":..,"bundle_id":..,"pid":..,"window_title":..,
//    "url":..|null,"doc_path":..|null,"text":"..","text_source":"accessibility"|"none",
//    "ax_trusted":bool,"private_browsing":bool}
//
// Optional argv[1] is a JSON object of caps: {"max_chars":Int,"max_nodes":Int,
// "timeout_ms":Int,"fetch_url":Bool}.
//
// App name / bundle id come from NSWorkspace (no permission needed). Window title,
// document path, and on-screen text need Accessibility. Browser URL + incognito
// detection use AppleScript (Automation permission). Everything fails soft.

import Foundation
import AppKit
import ApplicationServices

// MARK: - JSON output

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"ok\":false}")
    }
}

// MARK: - caps

var maxChars = 20000
var maxNodes = 6000
var timeoutMs = 1500
var fetchURL = true
var fetchPageText = false
var fetchPageHTML = false
let pageCap = 200000
if CommandLine.arguments.count > 1,
   let data = CommandLine.arguments[1].data(using: .utf8),
   let cfg = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
    if let v = cfg["max_chars"] as? Int { maxChars = v }
    if let v = cfg["max_nodes"] as? Int { maxNodes = v }
    if let v = cfg["timeout_ms"] as? Int { timeoutMs = v }
    if let v = cfg["fetch_url"] as? Bool { fetchURL = v }
    if let v = cfg["fetch_page_text"] as? Bool { fetchPageText = v }
    if let v = cfg["fetch_page_html"] as? Bool { fetchPageHTML = v }
}

// MARK: - AX helpers

func axCopy(_ el: AXUIElement, _ attr: String) -> AnyObject? {
    var value: AnyObject?
    let err = AXUIElementCopyAttributeValue(el, attr as CFString, &value)
    return err == .success ? value : nil
}

func axString(_ el: AXUIElement, _ attr: String) -> String? {
    if let s = axCopy(el, attr) as? String { return s }
    return nil
}

func axChildren(_ el: AXUIElement) -> [AXUIElement] {
    if let arr = axCopy(el, kAXChildrenAttribute as String) as? [AXUIElement] { return arr }
    return []
}

let textRoles: Set<String> = [
    "AXStaticText", "AXTextArea", "AXTextField", "AXText",
    "AXHeading", "AXLink", "AXCell",
]

func gatherText(_ root: AXUIElement, deadline: Date) -> String {
    var out = ""
    var visited = 0
    var queue: [AXUIElement] = [root]
    var seenChars = 0
    while !queue.isEmpty, visited < maxNodes, seenChars < maxChars {
        if Date() > deadline { break }
        let el = queue.removeFirst()
        visited += 1
        if let role = axString(el, kAXRoleAttribute as String), textRoles.contains(role) {
            for attr in [kAXValueAttribute, kAXTitleAttribute, kAXDescriptionAttribute] {
                if let v = axString(el, attr as String), !v.isEmpty {
                    out += v
                    out += "\n"
                    seenChars += v.count + 1
                    break
                }
            }
        }
        let kids = axChildren(el)
        if !kids.isEmpty { queue.append(contentsOf: kids) }
    }
    return out
}

// MARK: - AppleScript (browser URL + incognito)

func runAppleScript(_ source: String) -> String? {
    guard let script = NSAppleScript(source: source) else { return nil }
    var error: NSDictionary?
    let result = script.executeAndReturnError(&error)
    if error != nil { return nil }
    return result.stringValue
}

struct BrowserSpec {
    let appName: String
    let family: String   // "safari" | "chromium"
    let supportsMode: Bool
}

let browsers: [String: BrowserSpec] = [
    "com.apple.Safari": BrowserSpec(appName: "Safari", family: "safari", supportsMode: false),
    "com.google.Chrome": BrowserSpec(appName: "Google Chrome", family: "chromium", supportsMode: true),
    "com.google.Chrome.canary": BrowserSpec(appName: "Google Chrome Canary", family: "chromium", supportsMode: true),
    "com.brave.Browser": BrowserSpec(appName: "Brave Browser", family: "chromium", supportsMode: true),
    "com.microsoft.edgemac": BrowserSpec(appName: "Microsoft Edge", family: "chromium", supportsMode: true),
    "com.vivaldi.Vivaldi": BrowserSpec(appName: "Vivaldi", family: "chromium", supportsMode: true),
    "company.thebrowser.Browser": BrowserSpec(appName: "Arc", family: "chromium", supportsMode: false),
]

func urlScriptFor(_ spec: BrowserSpec) -> String {
    if spec.family == "safari" {
        return "tell application \"\(spec.appName)\" to return URL of front document"
    }
    return "tell application \"\(spec.appName)\" to return URL of active tab of front window"
}

func modeScriptFor(_ spec: BrowserSpec) -> String {
    return "tell application \"\(spec.appName)\" to return mode of front window"
}

func jsScriptFor(_ spec: BrowserSpec, _ js: String) -> String {
    if spec.family == "safari" {
        return "tell application \"\(spec.appName)\" to do JavaScript \"\(js)\" in front document"
    }
    return "tell application \"\(spec.appName)\" to execute front window's active tab javascript \"\(js)\""
}

// MARK: - main

let ws = NSWorkspace.shared
guard let front = ws.frontmostApplication else {
    emit(["ok": true, "app_name": NSNull(), "bundle_id": NSNull(), "text_source": "none",
          "ax_trusted": AXIsProcessTrusted(), "private_browsing": false])
    exit(0)
}

let appName = front.localizedName ?? "Unknown"
let bundleId = front.bundleIdentifier ?? ""
let pid = front.processIdentifier
let axTrusted = AXIsProcessTrusted()

var windowTitle: String? = nil
var docPath: String? = nil
var text = ""
var textSource = "none"

if axTrusted {
    let appEl = AXUIElementCreateApplication(pid)
    let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1000.0)

    if let focusedWindow = axCopy(appEl, kAXFocusedWindowAttribute as String) {
        let win = focusedWindow as! AXUIElement
        windowTitle = axString(win, kAXTitleAttribute as String)

        // Document path (document-based apps expose kAXDocument as a file URL string).
        if let doc = axString(win, kAXDocumentAttribute as String) {
            if let u = URL(string: doc), u.isFileURL {
                docPath = u.path
            } else {
                docPath = doc
            }
        }

        let gathered = gatherText(win, deadline: deadline)
        let trimmed = gathered.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            text = trimmed
            textSource = "accessibility"
        }
    }

    // Also pull the focused element's selected text (often the most relevant).
    if let focusedEl = axCopy(appEl, kAXFocusedUIElementAttribute as String) {
        let el = focusedEl as! AXUIElement
        if let sel = axString(el, kAXSelectedTextAttribute as String),
           sel.count > 2, !text.contains(sel) {
            text = sel + "\n" + text
            if textSource == "none" { textSource = "accessibility" }
        }
    }
}

// Browser URL + incognito detection + optional full-page text/HTML.
var url: String? = nil
var privateBrowsing = false
var pageText: String? = nil
var pageHTML: String? = nil
if let spec = browsers[bundleId] {
    if fetchURL {
        if let u = runAppleScript(urlScriptFor(spec)), !u.isEmpty, u != "missing value" {
            url = u
        }
        if spec.supportsMode, let mode = runAppleScript(modeScriptFor(spec)) {
            let m = mode.lowercased()
            if m.contains("incognito") || m.contains("private") {
                privateBrowsing = true
            }
        }
    }
    // Only read page content for non-private windows (defense in depth).
    if !privateBrowsing {
        if fetchPageText,
           let t = runAppleScript(jsScriptFor(spec, "document.body ? document.body.innerText : ''")),
           !t.isEmpty, t != "missing value" {
            pageText = String(t.prefix(pageCap))
        }
        if fetchPageHTML,
           let html = runAppleScript(jsScriptFor(spec, "document.documentElement.outerHTML")),
           !html.isEmpty, html != "missing value" {
            pageHTML = String(html.prefix(pageCap))
        }
    }
}

emit([
    "ok": true,
    "app_name": appName,
    "bundle_id": bundleId,
    "pid": Int(pid),
    "window_title": windowTitle ?? NSNull(),
    "url": url ?? NSNull(),
    "doc_path": docPath ?? NSNull(),
    "text": text,
    "text_len": text.count,
    "text_source": textSource,
    "ax_trusted": axTrusted,
    "private_browsing": privateBrowsing,
    "page_text": pageText ?? NSNull(),
    "page_html": pageHTML ?? NSNull(),
])
