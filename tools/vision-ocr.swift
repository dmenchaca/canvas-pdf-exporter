import Foundation
import Vision
import AppKit
let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { print("[]"); exit(1) }
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["nl-NL", "en-US"]
req.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
var out: [[String: Any]] = []
for o in req.results ?? [] {
  guard let c = o.topCandidates(1).first else { continue }
  let b = o.boundingBox
  out.append(["t": c.string, "c": c.confidence, "x": b.minX * Double(cg.width), "y": (1 - b.maxY) * Double(cg.height), "w": b.width * Double(cg.width), "h": b.height * Double(cg.height)])
}
let d = try JSONSerialization.data(withJSONObject: ["w": cg.width, "h": cg.height, "lines": out])
print(String(data: d, encoding: .utf8)!)
