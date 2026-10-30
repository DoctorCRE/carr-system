// Local PDF raster recognition. Native text regions are excluded before proposals are built.
import Foundation
import PDFKit
import Vision
import AppKit

let document = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1]))!
var regions = [[String: Any]]()
for index in 0..<min(document.pageCount, 1000) {
    guard let page = document.page(at: index) else { continue }
    let bounds = page.bounds(for: .mediaBox)
    guard bounds.width > 0, bounds.height > 0 else { continue }
    let scale = min(2.0, 2400.0 / max(bounds.width, bounds.height))
    let size = NSSize(width: bounds.width * scale, height: bounds.height * scale)
    let image = page.thumbnail(of: size, for: .mediaBox)
    var imageRect = NSRect(origin: .zero, size: image.size)
    guard let bitmap = image.cgImage(forProposedRect: &imageRect, context: nil, hints: nil) else { continue }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: bitmap).perform([request])
    var rows = [(Double, Double, String)]()
    for observation in request.results ?? [] {
        guard let text = observation.topCandidates(1).first?.string else { continue }
        let box = observation.boundingBox
        let rect = CGRect(x: bounds.minX + box.minX * bounds.width,
                          y: bounds.minY + box.minY * bounds.height,
                          width: box.width * bounds.width, height: box.height * bounds.height)
        if let native = page.selection(for: rect)?.string, !native.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
        rows.append((Double(box.midY), Double(box.minX), text))
    }
    rows.sort { abs($0.0 - $1.0) > 0.008 ? $0.0 > $1.0 : $0.1 < $1.1 }
    var grouped = [[(Double, Double, String)]]()
    for row in rows {
        if let last = grouped.last, abs(last[0].0 - row.0) < 0.008 {
            grouped[grouped.count - 1].append(row)
        } else { grouped.append([row]) }
    }
    for row in grouped {
        let text = row.sorted { $0.1 < $1.1 }.map { $0.2 }.joined(separator: " | ")
        regions.append(["page": index + 1, "method": "ocr", "region_id": "vision-\(index + 1)-\(row[0].0)", "text": text])
    }
}
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: regions))
