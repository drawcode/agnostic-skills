"""Photos library on a real iOS device, through the media helper app (scripts/ios_media_helper).

A device has no host-side route into Photos (the simulator's `simctl addmedia` has no device twin,
and AFC files under DCIM are never indexed). So a tiny development-signed app does it on the device:
the host copies files and a request into the app's Documents with `devicectl device copy to`,
launches it, answers its one-time Photos prompt through WebDriverAgent, and reads result.json back.

Ops: push (files into an album), hide-others (hide every asset outside the album, for a capture
that must not show the owner's photos), unhide (exactly the assets hide-others hid; never deletes).
Signed with the WDA team (DEVICE_IOS_WDA_TEAM) as <DEVICE_IOS_WDA_BUNDLE_PREFIX>.MediaHelper.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import uuid

from .. import config

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "ios_media_helper", "MediaHelper.xcodeproj")


class MediaError(RuntimeError):
    pass


def bundle():
    return config.require("DEVICE_IOS_WDA_BUNDLE_PREFIX") + ".MediaHelper"


def build_dir():
    return config.scratch("media-helper-build")


def app_path():
    return os.path.join(build_dir(), "Debug-iphoneos", "MediaHelper.app")


def build(backend):
    team = config.require("DEVICE_IOS_WDA_TEAM")
    backend.sh(["xcodebuild", "build", "-project", PROJECT, "-target", "MediaHelper", "-sdk", "iphoneos",
                "-configuration", "Debug", "-allowProvisioningUpdates", f"DEVELOPMENT_TEAM={team}",
                "CODE_SIGN_STYLE=Automatic", f"PRODUCT_BUNDLE_IDENTIFIER={bundle()}",
                f"SYMROOT={build_dir()}"], stdout=sys.stderr)
    return app_path()


def install(backend):
    if not os.path.isdir(app_path()):
        build(backend)
    backend.sh(backend.pmd3("apps", "install", app_path()), stdout=sys.stderr, env=backend.env())
    return app_path()


def _copy_to(backend, src, dest):
    backend.out(backend.devicectl("device copy to", "--domain-type", "appDataContainer",
                                  "--domain-identifier", bundle(), "--source", src, "--destination", dest))


def _read_result(backend, tmpdir):
    local = os.path.join(tmpdir, "result.json")
    if os.path.exists(local):
        os.remove(local)
    ok = backend.ok(backend.devicectl("device copy from", "--domain-type", "appDataContainer",
                                      "--domain-identifier", bundle(), "--source", "Documents/result.json",
                                      "--destination", local))
    if not ok or not os.path.exists(local):
        return None
    with open(local, encoding="utf-8") as f:
        try:
            return json.load(f)
        except ValueError:
            return None


def copy_from(backend, remote, local):
    backend.out(backend.devicectl("device copy from", "--domain-type", "appDataContainer",
                                  "--domain-identifier", bundle(), "--source", "Documents/" + remote,
                                  "--destination", local))
    return local


def run(backend, op, files=(), album="Device Testing", timeout=120, answer_alert=None, extra=None):
    """Send one request and wait for its result. `answer_alert` is called while waiting (WDA
    `alert accept`), because the first run raises the Photos access prompt."""
    if op not in ("push", "hide-others", "unhide", "picker", "pull"):
        raise MediaError(f"unknown media op {op}")
    for f in files:
        if not os.path.isfile(f):
            raise MediaError(f"no such file: {f}")
    nonce = uuid.uuid4().hex
    tmpdir = tempfile.mkdtemp(prefix="media-helper-")
    try:
        for f in files:
            _copy_to(backend, f, "Documents/inbox/" + os.path.basename(f))
        req = os.path.join(tmpdir, "request.json")
        with open(req, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(dict({"nonce": nonce, "op": op, "album": album}, **(extra or {})), fh)
        _copy_to(backend, req, "Documents/request.json")
        backend.out(backend.devicectl("device process launch", "--terminate-existing", bundle()))
        end = time.time() + timeout
        while time.time() < end:
            if answer_alert:
                try:
                    answer_alert()
                except Exception as e:  # an alert check that fails must not abort the wait
                    print(f"media    alert check failed: {e}", file=sys.stderr)
            result = _read_result(backend, tmpdir)
            if result and result.get("nonce") == nonce:
                if not result.get("ok"):
                    raise MediaError(f"media {op} failed: {result.get('error')}")
                return result
            time.sleep(1.5)
        raise MediaError(f"media {op}: no result within {timeout}s (is the helper installed and Photos access allowed?)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
