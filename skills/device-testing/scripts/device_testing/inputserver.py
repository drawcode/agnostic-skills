"""Client for an app-side input server: an HTTP server inside the app, on the device's loopback,
that injects touches and text. Protocol in references/input-server.md; any app can implement it.

Config: INPUT_SERVER_PORT (device side, default 11010), INPUT_SERVER_ENV_VAR (iOS launch env var
that turns it on), INPUT_SERVER_INTENT_EXTRA (the Android string extra that does). Each device gets
its own Mac-side port so several can be forwarded at once; that mapping and other per-device state
live in one state file in the scratch dir.
"""
import json
import sys
import urllib.parse
import urllib.request

from . import config


class ServerError(Exception):
    pass


def device_port():
    return int(config.get("INPUT_SERVER_PORT", "11010"))


def launch_environment():
    """The env var (iOS) / string extra (Android) that turns the server on, keyed by platform."""
    port = str(device_port())
    return {"ios": {config.require("INPUT_SERVER_ENV_VAR"): port},
            "android": {config.require("INPUT_SERVER_INTENT_EXTRA"): port}}


def _state_path():
    return config.scratch("state.json")


def load_state():
    try:
        with open(_state_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(_state_path(), "w") as f:
        json.dump(state, f, indent=1)


def local_port(serial):
    state = load_state()
    ports = state.setdefault("ports", {})
    if serial not in ports:
        used = set(ports.values())
        port = device_port()
        while port in used:
            port += 1
        ports[serial] = port
        save_state(state)
    return ports[serial]


def call(serial, path, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"http://127.0.0.1:{local_port(serial)}{path}" + (f"?{query}" if query else "")
    try:
        with urllib.request.urlopen(url, timeout=40) as resp:
            return json.loads(resp.read())
    except OSError as e:
        print("is `forward` running and the app started with `launch --input-server`?", file=sys.stderr)
        raise ServerError(f"no input server at {url}: {e}") from e
