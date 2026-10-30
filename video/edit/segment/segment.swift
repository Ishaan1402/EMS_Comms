// Person cut-out mask for a video, on-device (Vision person segmentation, Neural Engine).
//   segment <input.mp4> <mask.mp4> [fast|balanced|accurate]
// Writes a grayscale mask video (white = person) at the input's size and frame rate, for ffmpeg alphamerge.
import AVFoundation
import CoreImage
import Vision

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: segment in.mp4 mask.mp4 [fast|balanced|accurate]"); exit(2) }
let level: VNGeneratePersonSegmentationRequest.QualityLevel =
    args.count > 3 ? (args[3] == "fast" ? .fast : args[3] == "accurate" ? .accurate : .balanced) : .balanced

let asset = AVURLAsset(url: URL(fileURLWithPath: args[1]))
guard let track = asset.tracks(withMediaType: .video).first else { print("no video"); exit(1) }
let size = track.naturalSize.applying(track.preferredTransform)
let w = Int(abs(size.width)), h = Int(abs(size.height))
let reader = try AVAssetReader(asset: asset)
let out = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
out.alwaysCopiesSampleData = false
reader.add(out)

let dest = URL(fileURLWithPath: args[2])
try? FileManager.default.removeItem(at: dest)
let writer = try AVAssetWriter(outputURL: dest, fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 6_000_000]])
input.transform = track.preferredTransform
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA, kCVPixelBufferWidthKey as String: Int(track.naturalSize.width),
    kCVPixelBufferHeightKey as String: Int(track.naturalSize.height)])
writer.add(input)
reader.startReading()
writer.startWriting()
writer.startSession(atSourceTime: .zero)

let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = level
request.outputPixelFormat = kCVPixelFormatType_OneComponent8
let ctx = CIContext(options: [.useSoftwareRenderer: false])
var frames = 0
let t0 = Date()
while let sample = out.copyNextSampleBuffer() {
    guard let pb = CMSampleBufferGetImageBuffer(sample) else { continue }
    let pts = CMSampleBufferGetPresentationTimeStamp(sample)
    let handler = VNImageRequestHandler(cvPixelBuffer: pb, options: [:])
    try handler.perform([request])
    guard let mask = request.results?.first?.pixelBuffer else { continue }
    // Scale the mask to the frame and write it as a grey BGRA frame.
    let fw = CVPixelBufferGetWidth(pb), fh = CVPixelBufferGetHeight(pb)
    var m = CIImage(cvPixelBuffer: mask)
    m = m.transformed(by: CGAffineTransform(scaleX: CGFloat(fw) / m.extent.width, y: CGFloat(fh) / m.extent.height))
    var outBuf: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &outBuf)
    guard let ob = outBuf else { continue }
    ctx.render(m, to: ob)
    while !input.isReadyForMoreMediaData { usleep(1000) }
    adaptor.append(ob, withPresentationTime: pts)
    frames += 1
}
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
let secs = Date().timeIntervalSince(t0)
print(String(format: "%d frames in %.1f s (%.1f fps), %@", frames, secs, Double(frames) / secs, writer.status == .completed ? "ok" : "failed: \(String(describing: writer.error))"))
