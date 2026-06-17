// retrace-capture — grab one frame via ScreenCaptureKit with native content exclusion.
//
// Input  (argv[1], JSON): {
//   "frame_path": "/.../tmp/uuid.png",   // full-res temp frame for OCR (deleted by caller)
//   "thumb_path": "/.../thumbs/.../uuid.jpg",
//   "max_edge": 1280, "jpeg_quality": 70,
//   "exclude_bundle_ids": ["com.1password.1password", ...],
//   "display": "main"                    // "main" or a numeric CGDirectDisplayID
// }
// Output (stdout, JSON): {"ok":true,"frame_path":..,"thumb_path":..,"width":W,"height":H,
//                         "display_id":N,"excluded":N}
//
// Excluded (denylisted) apps never enter the captured frame. Fails soft on any error.

import Foundation
import ScreenCaptureKit
import CoreGraphics
import ImageIO

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"ok\":false}")
    }
}

func fail(_ message: String) -> Never {
    emit(["ok": false, "error": message])
    exit(0)
}

// MARK: - image io

func writeImage(_ image: CGImage, to path: String, type: String, quality: Double? = nil) -> Bool {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let dest = CGImageDestinationCreateWithURL(url, type as CFString, 1, nil) else { return false }
    var opts: [CFString: Any] = [:]
    if let q = quality { opts[kCGImageDestinationLossyCompressionQuality] = q }
    CGImageDestinationAddImage(dest, image, opts as CFDictionary)
    return CGImageDestinationFinalize(dest)
}

func downscale(_ image: CGImage, maxEdge: Int) -> CGImage {
    let w = image.width, h = image.height
    let longest = max(w, h)
    if longest <= maxEdge || maxEdge <= 0 { return image }
    let scale = Double(maxEdge) / Double(longest)
    let nw = max(1, Int(Double(w) * scale))
    let nh = max(1, Int(Double(h) * scale))
    let cs = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(data: nil, width: nw, height: nh, bitsPerComponent: 8,
                              bytesPerRow: 0, space: cs,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return image }
    ctx.interpolationQuality = .high
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: nw, height: nh))
    return ctx.makeImage() ?? image
}

// MARK: - config

guard CommandLine.arguments.count > 1,
      let data = CommandLine.arguments[1].data(using: .utf8),
      let cfg = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
    fail("missing/invalid config")
}

let framePath = cfg["frame_path"] as? String ?? ""
let thumbPath = cfg["thumb_path"] as? String ?? ""
let maxEdge = cfg["max_edge"] as? Int ?? 1280
let jpegQuality = (cfg["jpeg_quality"] as? Int).map { Double($0) / 100.0 } ?? 0.7
let excludeSet = Set((cfg["exclude_bundle_ids"] as? [String] ?? []).map { $0.lowercased() })
let displayArg = cfg["display"] as? String ?? "main"

guard #available(macOS 14.0, *) else {
    fail("ScreenCaptureKit screenshot requires macOS 14+")
}

let sema = DispatchSemaphore(value: 0)
var output: [String: Any] = ["ok": false, "error": "unknown"]

Task {
    do {
        let content = try await SCShareableContent.current

        guard !content.displays.isEmpty else {
            output = ["ok": false, "error": "no displays"]
            sema.signal()
            return
        }

        let display: SCDisplay
        if let wanted = UInt32(displayArg),
           let match = content.displays.first(where: { $0.displayID == wanted }) {
            display = match
        } else if let main = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) {
            display = main
        } else {
            display = content.displays[0]
        }

        let excludedApps = content.applications.filter {
            excludeSet.contains($0.bundleIdentifier.lowercased())
        }

        let filter = SCContentFilter(display: display,
                                     excludingApplications: excludedApps,
                                     exceptingWindows: [])

        // Capture at native pixel resolution where possible.
        var pxW = display.width
        var pxH = display.height
        if let mode = CGDisplayCopyDisplayMode(display.displayID) {
            pxW = mode.pixelWidth
            pxH = mode.pixelHeight
        }

        let config = SCStreamConfiguration()
        config.width = pxW
        config.height = pxH
        config.showsCursor = false
        config.scalesToFit = true

        let image = try await SCScreenshotManager.captureImage(contentFilter: filter,
                                                               configuration: config)

        var wrote = false
        if !framePath.isEmpty {
            wrote = writeImage(image, to: framePath, type: "public.png")
        }
        var wroteThumb = false
        if !thumbPath.isEmpty {
            let thumb = downscale(image, maxEdge: maxEdge)
            wroteThumb = writeImage(thumb, to: thumbPath, type: "public.jpeg", quality: jpegQuality)
        }

        output = [
            "ok": (framePath.isEmpty || wrote) && (thumbPath.isEmpty || wroteThumb),
            "frame_path": framePath.isEmpty ? NSNull() : framePath,
            "thumb_path": thumbPath.isEmpty ? NSNull() : thumbPath,
            "width": image.width,
            "height": image.height,
            "display_id": Int(display.displayID),
            "excluded": excludedApps.count,
        ]
    } catch {
        output = ["ok": false, "error": "capture: \(error.localizedDescription)"]
    }
    sema.signal()
}

// Bound the wait so a hung capture cannot freeze the caller.
if sema.wait(timeout: .now() + 15) == .timedOut {
    fail("capture timed out")
}
emit(output)
