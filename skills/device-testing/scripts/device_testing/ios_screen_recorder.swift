// Records a USB iPhone's screen the way QuickTime does: CoreMediaIO exposes the phone as an
// AVFoundation capture device once screen capture devices are allowed. Full resolution, up to 60 fps,
// H.264. Built and run by `record start` on iOS; stops on SIGINT or SIGTERM.
//
// usage: ios_screen_recorder <device name> <out.mov> <start file>
// exit:  0 finished, 2 Camera access not granted to the host app, 3 phone not found, 1 anything else
import AVFoundation
import CoreMediaIO

setvbuf(stdout, nil, _IONBF, 0)
let args = CommandLine.arguments
guard args.count == 4 else { print("usage: ios_screen_recorder <device name> <out.mov> <start file>"); exit(1) }
let wanted = args[1], out = URL(fileURLWithPath: args[2]), startFile = args[3]

// Capture devices count as cameras, and the permission belongs to the app hosting the terminal.
if AVCaptureDevice.authorizationStatus(for: .video) != .authorized {
    let done = DispatchSemaphore(value: 0)
    var granted = false
    AVCaptureDevice.requestAccess(for: .video) { granted = $0; done.signal() }
    if done.wait(timeout: .now() + 30) == .timedOut || !granted {
        print("Camera access is not granted to the app running this terminal: System Settings > Privacy & Security > Camera")
        exit(2)
    }
}

var property = CMIOObjectPropertyAddress(
    mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
    mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
    mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))
var allow: UInt32 = 1
CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &property, 0, nil,
                          UInt32(MemoryLayout<UInt32>.size), &allow)

// The phone shows up a moment after the property is set.
var device: AVCaptureDevice?
for _ in 0..<20 {
    let found = AVCaptureDevice.DiscoverySession(deviceTypes: [.external], mediaType: .muxed,
                                                 position: .unspecified).devices
    device = found.first { $0.localizedName == wanted } ?? (found.count == 1 && wanted.isEmpty ? found.first : nil)
    if device != nil { break }
    RunLoop.current.run(until: Date().addingTimeInterval(0.5))
}
guard let device else { print("no screen capture device named '\(wanted)'"); exit(3) }

let session = AVCaptureSession()
do { session.addInput(try AVCaptureDeviceInput(device: device)) } catch { print("input: \(error)"); exit(1) }
let output = AVCaptureMovieFileOutput()
session.addOutput(output)
// Video only, like Android's screenrecord.
for connection in output.connections where connection.inputPorts.contains(where: { $0.mediaType == .audio }) {
    connection.isEnabled = false
}

final class Delegate: NSObject, AVCaptureFileOutputRecordingDelegate {
    func fileOutput(_ o: AVCaptureFileOutput, didStartRecordingTo url: URL, from c: [AVCaptureConnection]) {
        let stamp = DateFormatter()
        stamp.dateFormat = "HH:mm:ss.SSS"
        try? (stamp.string(from: Date()) + "\n").write(toFile: startFile, atomically: true, encoding: .utf8)
        print("recording \(url.path)")
    }
    func fileOutput(_ o: AVCaptureFileOutput, didFinishRecordingTo url: URL, from c: [AVCaptureConnection],
                    error: Error?) {
        let ok = error == nil || (error as NSError?)?.userInfo[AVErrorRecordingSuccessfullyFinishedKey] as? Bool == true
        print(ok ? "finished" : "failed: \(error!)")
        exit(ok ? 0 : 1)
    }
}

let delegate = Delegate()
session.startRunning()
RunLoop.current.run(until: Date().addingTimeInterval(1))  // frames start flowing
try? FileManager.default.removeItem(at: out)
output.startRecording(to: out, recordingDelegate: delegate)
for sig in [SIGINT, SIGTERM] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { output.stopRecording() }
    source.resume()
    _ = Unmanaged.passRetained(source)
}
dispatchMain()
