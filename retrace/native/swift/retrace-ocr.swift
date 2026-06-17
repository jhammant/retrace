// retrace-ocr — on-device OCR of an image file via the Vision framework.
//
// Usage:  retrace-ocr /path/to/frame.png
// Output: {"ok":true,"text":"...","line_count":N,"lines":[{"text":..,"confidence":..,"bbox":[x,y,w,h]}]}
//
// Bounding boxes are in Vision's normalized coordinates (origin bottom-left, 0..1).
// Fails soft: prints ok:false JSON on any error and exits 0.

import Foundation
import Vision
import ImageIO
import CoreGraphics

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
let url = URL(fileURLWithPath: path)

guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
      let cgImage = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    emit(["ok": false, "error": "cannot load image"])
    exit(0)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    emit(["ok": false, "error": "vision: \(error.localizedDescription)"])
    exit(0)
}

var lines: [[String: Any]] = []
var pieces: [String] = []
if let results = request.results {
    for obs in results {
        guard let top = obs.topCandidates(1).first else { continue }
        pieces.append(top.string)
        let bb = obs.boundingBox
        lines.append([
            "text": top.string,
            "confidence": top.confidence,
            "bbox": [bb.origin.x, bb.origin.y, bb.size.width, bb.size.height],
        ])
    }
}

emit([
    "ok": true,
    "text": pieces.joined(separator: "\n"),
    "line_count": lines.count,
    "lines": lines,
])
