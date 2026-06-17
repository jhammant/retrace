// retrace-embed — on-device sentence embedding via Apple's NaturalLanguage.
//
// Usage:  retrace-embed "<text to embed>"
// Output: {"ok":true,"dim":512,"model":"nl-sentence-en","vec":[...]}
//
// Uses the bundled NLEmbedding (no model download). Prefers the sentence
// embedding; falls back to mean-pooled word embeddings for long/odd text.
// Fails soft with ok:false.

import Foundation
import NaturalLanguage

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"ok\":false}")
    }
}

guard CommandLine.arguments.count > 1 else {
    emit(["ok": false, "error": "no text"])
    exit(0)
}
let text = CommandLine.arguments[1].trimmingCharacters(in: .whitespacesAndNewlines)
if text.isEmpty {
    emit(["ok": false, "error": "empty text"])
    exit(0)
}

var vec: [Double]? = nil
var model = ""

// 1) Sentence embedding (fixed-dim, fast, no asset download).
if let se = NLEmbedding.sentenceEmbedding(for: .english),
   let v = se.vector(for: text) {
    vec = v
    model = "nl-sentence-en"
}

// 2) Fallback: mean-pool word embeddings.
if vec == nil, let we = NLEmbedding.wordEmbedding(for: .english) {
    let tokenizer = NLTokenizer(unit: .word)
    tokenizer.string = text
    var sum: [Double]? = nil
    var count = 0
    tokenizer.enumerateTokens(in: text.startIndex..<text.endIndex) { range, _ in
        let word = String(text[range]).lowercased()
        if let wv = we.vector(for: word) {
            if sum == nil {
                sum = wv
            } else {
                for i in 0..<sum!.count { sum![i] += wv[i] }
            }
            count += 1
        }
        return true
    }
    if let s = sum, count > 0 {
        vec = s.map { $0 / Double(count) }
        model = "nl-word-mean-en"
    }
}

guard let v = vec else {
    emit(["ok": false, "error": "no embedding for text"])
    exit(0)
}

emit(["ok": true, "dim": v.count, "model": model, "vec": v])
