// ocr_frames <width> <height> <fps> < raw rgb24 frames  ->  "<seconds>\t<text>" per recognized line
import Foundation
import Vision
import CoreGraphics

let a = CommandLine.arguments
let w = Int(a[1])!, h = Int(a[2])!, fps = Double(a[3])!
let size = w * h * 3
let input = FileHandle.standardInput
var index = 0
let space = CGColorSpaceCreateDeviceRGB()
while true {
    var data = Data()
    while data.count < size {
        let chunk = input.readData(ofLength: size - data.count)
        if chunk.isEmpty { break }
        data.append(chunk)
    }
    if data.count < size { break }
    guard let provider = CGDataProvider(data: data as CFData),
          let image = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 24, bytesPerRow: w * 3,
                              space: space, bitmapInfo: CGBitmapInfo(rawValue: 0), provider: provider,
                              decode: nil, shouldInterpolate: false, intent: .defaultIntent) else { break }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .fast
    try? VNImageRequestHandler(cgImage: image).perform([request])
    let t = Double(index) / fps
    for obs in request.results ?? [] {
        if let top = obs.topCandidates(1).first {
            print(String(format: "%.1f\t%@", t, top.string))
        }
    }
    index += 1
}
