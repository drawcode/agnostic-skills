// Device-testing media helper: puts host files into the Photos library of a real iPhone/iPad, and
// hides/unhides the rest of the library for a clean capture. There is no host-side route into Photos
// on a device (the simulator has `simctl addmedia`), so this app runs ON the device.
//
// Protocol (all under the app's Documents, written by the host with `devicectl device copy to`):
//   request.json  {"nonce": "...", "op": "push"|"hide-others"|"unhide", "album": "<name>"}
//   inbox/*       the files to add (push only)
// The app answers in result.json with the same nonce. Hiding NEVER deletes: every asset it hides is
// recorded in hidden-by-helper.json, and `unhide` restores exactly those.
import Photos
import PhotosUI
import UIKit

let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
let ledgerURL = docs.appendingPathComponent("hidden-by-helper.json")

final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    let label = UILabel()

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        let w = UIWindow(frame: UIScreen.main.bounds)
        let vc = UIViewController()
        vc.view.backgroundColor = .black
        label.textColor = .white
        label.numberOfLines = 0
        label.textAlignment = .center
        label.frame = vc.view.bounds.insetBy(dx: 24, dy: 24)
        label.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        label.text = "media helper: starting"
        vc.view.addSubview(label)
        w.rootViewController = vc
        w.makeKeyAndVisible()
        window = w
        NSLog("[media-helper] launched")
        DispatchQueue.global().async { self.run() }
        return true
    }

    func show(_ text: String) {
        NSLog("[media-helper] %@", text)
        DispatchQueue.main.async { self.label.text = text }
    }

    func run() {
        let requestURL = docs.appendingPathComponent("request.json")
        guard let data = try? Data(contentsOf: requestURL),
              let req = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let op = req["op"] as? String else {
            show("media helper: no request.json")
            return
        }
        let nonce = req["nonce"] as? String ?? ""
        let album = req["album"] as? String ?? "Device Testing"
        if op == "picker" {
            // Show the system photo picker (PHPicker, out of process, needs no Photos access) so a
            // screenshot proves what an app's picker will show before a capture.
            DispatchQueue.main.async {
                var config = PHPickerConfiguration()
                config.selectionLimit = 0
                let picker = PHPickerViewController(configuration: config)
                picker.delegate = self
                self.window?.rootViewController?.present(picker, animated: false) {
                    self.write(["nonce": nonce, "op": op, "ok": true, "shown": true])
                    NSLog("[media-helper] picker shown")
                }
            }
            return
        }
        show("media helper: \(op) waiting for Photos access")
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { status in
            var result: [String: Any] = ["nonce": nonce, "op": op, "album": album]
            if status != .authorized {
                result["ok"] = false
                result["error"] = "photos access is \(status.rawValue), need full access (authorized=3)"
            } else {
                do {
                    switch op {
                    case "push": result.merge(try self.push(album: album)) { $1 }
                    case "hide-others": result.merge(try self.hideOthers(album: album)) { $1 }
                    case "unhide": result.merge(try self.unhide()) { $1 }
                    case "pull": result.merge(try self.pullVideo(since: req["since"] as? Double ?? 0, name: req["name"] as? String ?? "recording")) { $1 }
                    default: throw NSError(domain: "media-helper", code: 2, userInfo: [NSLocalizedDescriptionKey: "unknown op \(op)"])
                    }
                    result["ok"] = true
                } catch {
                    result["ok"] = false
                    result["error"] = error.localizedDescription
                }
            }
            self.write(result)
            self.show("media helper: \(op) \((result["ok"] as? Bool) == true ? "done" : "FAILED")\n\(result["error"] ?? "")")
        }
    }

    func write(_ result: [String: Any]) {
        let url = docs.appendingPathComponent("result.json")
        let tmp = docs.appendingPathComponent("result.json.tmp")
        if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: tmp)
            _ = try? FileManager.default.replaceItemAt(url, withItemAt: tmp)
            if !FileManager.default.fileExists(atPath: url.path) { try? FileManager.default.moveItem(at: tmp, to: url) }
        }
    }

    func findOrCreateAlbum(_ name: String) throws -> PHAssetCollection {
        let opts = PHFetchOptions()
        opts.predicate = NSPredicate(format: "title = %@", name)
        if let found = PHAssetCollection.fetchAssetCollections(with: .album, subtype: .any, options: opts).firstObject {
            return found
        }
        var placeholder: PHObjectPlaceholder?
        try PHPhotoLibrary.shared().performChangesAndWait {
            placeholder = PHAssetCollectionChangeRequest.creationRequestForAssetCollection(withTitle: name).placeholderForCreatedAssetCollection
        }
        guard let id = placeholder?.localIdentifier,
              let made = PHAssetCollection.fetchAssetCollections(withLocalIdentifiers: [id], options: nil).firstObject else {
            throw NSError(domain: "media-helper", code: 3, userInfo: [NSLocalizedDescriptionKey: "could not create album \(name)"])
        }
        return made
    }

    func push(album: String) throws -> [String: Any] {
        let inbox = docs.appendingPathComponent("inbox")
        let files = ((try? FileManager.default.contentsOfDirectory(at: inbox, includingPropertiesForKeys: nil)) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix(".") }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        let collection = try findOrCreateAlbum(album)
        var added: [[String: String]] = []
        var errors: [[String: String]] = []
        for file in files {
            let isVideo = ["mp4", "mov", "m4v"].contains(file.pathExtension.lowercased())
            var id: String?
            do {
                try PHPhotoLibrary.shared().performChangesAndWait {
                    let req = PHAssetCreationRequest.forAsset()
                    req.addResource(with: isVideo ? .video : .photo, fileURL: file, options: nil)
                    if let ph = req.placeholderForCreatedAsset {
                        id = ph.localIdentifier
                        PHAssetCollectionChangeRequest(for: collection)?.addAssets([ph] as NSArray)
                    }
                }
                added.append(["file": file.lastPathComponent, "id": id ?? ""])
                try? FileManager.default.removeItem(at: file)
                show("media helper: added \(added.count) of \(files.count)")
            } catch {
                errors.append(["file": file.lastPathComponent, "error": error.localizedDescription])
            }
        }
        return ["added": added, "errors": errors]
    }

    /// Export the newest video created at or after `since` (unix seconds), e.g. the device's own
    /// Screen Recording, to Documents/out/<name>.<ext> for the host to copy off. Reads only.
    func pullVideo(since: Double, name: String) throws -> [String: Any] {
        let opts = PHFetchOptions()
        opts.predicate = NSPredicate(format: "mediaType == %d AND creationDate >= %@",
                                     PHAssetMediaType.video.rawValue, Date(timeIntervalSince1970: since) as NSDate)
        opts.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        guard let asset = PHAsset.fetchAssets(with: opts).firstObject else {
            throw NSError(domain: "media-helper", code: 4, userInfo: [NSLocalizedDescriptionKey: "no video created since \(since)"])
        }
        let resources = PHAssetResource.assetResources(for: asset)
        guard let res = resources.first(where: { $0.type == .video || $0.type == .fullSizeVideo }) ?? resources.first else {
            throw NSError(domain: "media-helper", code: 5, userInfo: [NSLocalizedDescriptionKey: "video has no resource"])
        }
        let ext = (res.originalFilename as NSString).pathExtension.isEmpty ? "mp4" : (res.originalFilename as NSString).pathExtension
        let outDir = docs.appendingPathComponent("out")
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let file = outDir.appendingPathComponent("\(name).\(ext)")
        try? FileManager.default.removeItem(at: file)
        let opt = PHAssetResourceRequestOptions()
        opt.isNetworkAccessAllowed = true
        let done = DispatchSemaphore(value: 0)
        var failure: Error?
        PHAssetResourceManager.default().writeData(for: res, toFile: file, options: opt) { err in failure = err; done.signal() }
        done.wait()
        if let failure { throw failure }
        let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? Int) ?? 0
        return ["file": "out/\(file.lastPathComponent)", "bytes": size, "duration": asset.duration,
                "created": asset.creationDate?.timeIntervalSince1970 ?? 0, "id": asset.localIdentifier]
    }

    func readLedger() -> [String] {
        guard let data = try? Data(contentsOf: ledgerURL),
              let ids = try? JSONSerialization.jsonObject(with: data) as? [String] else { return [] }
        return ids
    }

    func writeLedger(_ ids: [String]) {
        if let data = try? JSONSerialization.data(withJSONObject: ids) { try? data.write(to: ledgerURL, options: .atomic) }
    }

    func hideOthers(album: String) throws -> [String: Any] {
        let collection = try findOrCreateAlbum(album)
        var keep = Set<String>()
        PHAsset.fetchAssets(in: collection, options: nil).enumerateObjects { a, _, _ in keep.insert(a.localIdentifier) }
        var toHide: [PHAsset] = []
        PHAsset.fetchAssets(with: nil).enumerateObjects { a, _, _ in   // hidden assets are excluded by default
            if !keep.contains(a.localIdentifier) { toHide.append(a) }
        }
        // Record BEFORE changing anything, so a crash mid-change can still be undone.
        let ledger = Array(Set(readLedger() + toHide.map { $0.localIdentifier }))
        writeLedger(ledger)
        if !toHide.isEmpty {
            try PHPhotoLibrary.shared().performChangesAndWait {
                for a in toHide { PHAssetChangeRequest(for: a).isHidden = true }
            }
        }
        return ["hidden": toHide.count, "kept": keep.count, "ledger": ledger.count]
    }

    func unhide() throws -> [String: Any] {
        let ids = readLedger()
        let wanted = Set(ids)
        // ⚠ fetchAssets(withLocalIdentifiers:) does NOT return hidden assets even with
        // includeHiddenAssets (measured iPadOS 27.2, 2026-11-05): enumerate the hidden album.
        var assets: [PHAsset] = []
        if let hidden = PHAssetCollection.fetchAssetCollections(with: .smartAlbum, subtype: .smartAlbumAllHidden, options: nil).firstObject {
            PHAsset.fetchAssets(in: hidden, options: nil).enumerateObjects { a, _, _ in
                if wanted.contains(a.localIdentifier) { assets.append(a) }
            }
        }
        let opts = PHFetchOptions()
        opts.includeHiddenAssets = true
        PHAsset.fetchAssets(with: opts).enumerateObjects { a, _, _ in
            if a.isHidden && wanted.contains(a.localIdentifier) && !assets.contains(where: { $0.localIdentifier == a.localIdentifier }) {
                assets.append(a)
            }
        }
        if !assets.isEmpty {
            try PHPhotoLibrary.shared().performChangesAndWait {
                for a in assets { PHAssetChangeRequest(for: a).isHidden = false }
            }
        }
        // Keep every id not restored: an unhide that finds nothing must never forget what it hid.
        let restored = Set(assets.map { $0.localIdentifier })
        let left = ids.filter { !restored.contains($0) }
        writeLedger(left)
        return ["unhidden": assets.count, "ledger_was": ids.count, "still_in_ledger": left.count]
    }
}

extension AppDelegate: PHPickerViewControllerDelegate {
    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        picker.dismiss(animated: false)   // a check only: nothing picked is used
    }
}

UIApplicationMain(CommandLine.argc, CommandLine.unsafeArgv, nil, NSStringFromClass(AppDelegate.self))
