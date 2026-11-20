"""Hold a DVT device condition (network link, thermal, GPU) for as long as this process runs.

Run with pymobiledevice3's own interpreter: <venv>/bin/python ios_condition_holder.py UDID PROFILE
[--launch BUNDLE] [--env KEY=VALUE ...]. A network condition only reaches processes launched
through the same DVT session (measured: an already-running app and one started by devicectl see no
loss), so `--launch` starts the app inside the session that sets the condition. Prints `ready` once
the condition is set (and the app launched, with `pid N`); SIGTERM or SIGINT clears the condition
and exits.
"""
import asyncio
import signal
import sys

from pymobiledevice3.remote.userspace_tunnel import establish_userspace_rsd
from pymobiledevice3.services.dvt.instruments.condition_inducer import ConditionInducer
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.process_control import ProcessControl


async def main(argv):
    udid, profile, rest = argv[0], argv[1], argv[2:]
    bundle = rest[rest.index("--launch") + 1] if "--launch" in rest else None
    env = dict(a.split("=", 1) for i, a in enumerate(rest) if i and rest[i - 1] == "--env")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    rsd = await establish_userspace_rsd(serial=udid)
    async with DvtProvider(rsd) as dvt, ConditionInducer(dvt) as inducer, ProcessControl(dvt) as control:
        await inducer.set(profile)
        if bundle:
            pid = await control.launch(bundle_id=bundle, arguments=[], kill_existing=True,
                                       start_suspended=False, environment=env)
            print(f"pid {pid}", flush=True)
        print("ready", flush=True)
        await stop.wait()
        await inducer.clear()


asyncio.run(main(sys.argv[1:]))
