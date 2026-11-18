#!/usr/bin/env python3
"""Screen recorder for iOS, run with pymobiledevice3's own Python by `record start`.

usage: ios_recorder.py SERIAL FRAMES_DIR     (--help prints this; there is no --json mode)
Exit codes: 0 ok (stopped by SIGINT/SIGTERM), 1 failed, 2 bad arguments.

Holds one screenshot channel open and writes PNG frames until SIGINT, then timing.json so
`record stop` can encode at the rate actually achieved. CoreDevice's video stream would replace
this from iOS 27 on, where the media stream is allowed.
"""
import sys

if len(sys.argv) != 3 or sys.argv[1] in ("-h", "--help"):  # before the imports: plain python3 can print this
    print(__doc__)
    sys.exit(0 if sys.argv[1:2] in (["-h"], ["--help"]) else 2)

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import time  # noqa: E402

from pymobiledevice3.remote import userspace_tunnel  # noqa: E402
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider  # noqa: E402
from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot  # noqa: E402


async def main(serial, frames):
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    rsd = await userspace_tunnel.establish_userspace_rsd(serial=serial)
    count, start = 0, time.time()
    async with DvtProvider(rsd) as dvt, Screenshot(dvt) as screenshot:
        while not stop.is_set():
            data = await screenshot.get_screenshot()
            with open(os.path.join(frames, f"frame_{count:06d}.png"), "wb") as f:
                f.write(data)
            count += 1
    with open(os.path.join(frames, "timing.json"), "w") as f:
        json.dump({"frames": count, "seconds": time.time() - start}, f)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
