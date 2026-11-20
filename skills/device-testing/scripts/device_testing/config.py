"""Config: ~/.config/device-testing/config.env, plain KEY="value" lines with $HOME expanded.

Lookup order for a key: the environment, then the file. An add-on can point the base at its own
config file and keep its own key names:
  DEVICE_TESTING_CONFIG       another config file to read
  DEVICE_TESTING_KEY_PREFIX   also try <prefix><KEY>, e.g. MYAPP_ makes DEVICE_DEFAULT also read MYAPP_DEVICE_DEFAULT
  DEVICE_TESTING_KEYMAP       KEY=OTHER_KEY,... for names the prefix does not cover

Keys the base reads (see config.example.env):
  DEVICE_<ALIAS>_SERIAL, DEVICE_<ALIAS>_NAV_PROFILE, DEVICE_DEFAULT, DEVICE_NAV_PROFILE,
  DEVICE_LOGCAT_BUFFER, SCRATCH_ROOT (default: the system temp dir), APP_ANDROID_PACKAGE, APP_IOS_BUNDLE_ID,
  APP_ANDROID_PREFS_FILE, APP_IOS_PREFS_FILE, APP_ANDROID_SYMBOL_FILE, APP_IOS_SYMBOL_FILE,
  INPUT_SERVER_PORT, INPUT_SERVER_ENV_VAR, INPUT_SERVER_INTENT_EXTRA, PYMOBILEDEVICE3,
  DEVICE_TESTING_ADDONS, DEVICE_QUIET_REQUIRED, SHOT_MAX_HEIGHT
"""
import os
import re
import tempfile

PATH = os.environ.get("DEVICE_TESTING_CONFIG", os.path.expanduser("~/.config/device-testing/config.env"))
PREFIX = os.environ.get("DEVICE_TESTING_KEY_PREFIX", "")
KEYMAP = dict(pair.split("=", 1) for pair in os.environ.get("DEVICE_TESTING_KEYMAP", "").split(",") if "=" in pair)

_values = None


def values():
    global _values
    if _values is None:
        _values = {}
        try:
            with open(PATH) as f:
                for line in f:
                    # Keys match in any case (some config writers use lowercase) and are read uppercase.
                    m = re.match(r'\s*([A-Za-z0-9_]+)="?([^"\n]*)"?\s*$', line)
                    if m:
                        _values[m.group(1).upper()] = m.group(2).replace("$HOME", os.path.expanduser("~"))
        except OSError:
            pass
    return _values


def _raw(key):
    return os.environ.get(key) or values().get(key)


def get(key, default=None):
    for candidate in (key, KEYMAP.get(key), PREFIX + key if PREFIX else None):
        if candidate:
            value = _raw(candidate)
            if value:
                return value
    return default


def aliases():
    """alias -> serial, from DEVICE_<ALIAS>_SERIAL (a UDID on iOS), with the prefix applied."""
    found = {}
    pattern = re.compile(rf"(?:{re.escape(PREFIX)})?DEVICE_(.+)_SERIAL$" if PREFIX else r"DEVICE_(.+)_SERIAL$")
    for key, value in list(values().items()) + list(os.environ.items()):
        m = pattern.match(key)
        if m and value:
            found[m.group(1).lower()] = value
    return found


def alias_for(serial):
    return next((alias for alias, s in aliases().items() if s == serial), None)


def per_device(alias, suffix, default=None):
    """DEVICE_<ALIAS>_<SUFFIX>, falling back to DEVICE_<SUFFIX>."""
    if alias:
        value = get(f"DEVICE_{re.sub(r'[^A-Z0-9]', '_', alias.upper())}_{suffix}")
        if value:
            return value
    return get(f"DEVICE_{suffix}", default)


def resolve_in_skills(path):
    """A relative path is tried as given, then against the folder the skills are installed in
    (e.g. my-app-addon/profiles/x.tsv). Absolute paths pass through unchanged."""
    if not path or os.path.isabs(path) or os.path.exists(path):
        return path
    skills = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidate = os.path.join(skills, path)
    return candidate if os.path.exists(candidate) else path


def scratch(*parts):
    root = os.path.join(get("SCRATCH_ROOT", os.path.join(tempfile.gettempdir(), "device-testing-scratch")), "device-testing")
    path = os.path.join(root, *parts)
    os.makedirs(os.path.dirname(path) if parts else path, exist_ok=True)
    return path


def require(key):
    value = get(key)
    if not value:
        raise RuntimeError(f"{key} is not set -- add it to {PATH}")
    return value
