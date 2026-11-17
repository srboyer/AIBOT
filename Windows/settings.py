"""
settings.py

The user's settings (settings.json in the config folder -- see app_paths),
shared by the watcher and the tray app, with defaults for anything missing.
The "face" section is MAICA's personalisation; it's sent to the board on
every connect as one "{cfg:...}" frame (see face_config()) and the firmware
keeps it in flash, so it survives unplugging.
"""

import copy
import json
import os

from app_paths import config_dir

DEFAULTS = {
    "face": {
        "name": "MAICA",                 # shown on the boot screen
        "brightness": 100,               # backlight, percent
        "eye_size": 100,                 # percent of the standard eyes
        "bored_minutes": 5,              # idle this long -> bored
        "sleep_minutes": 30,             # ...this long -> asleep (backlight dims)
        "screen_off_minutes": 15,        # asleep this much longer -> screen off (0 = never)
        "no_computer_off_minutes": 30,   # rev-2 board: no computer for this long -> power off (0 = never)
        "colors": {                      # status colours, hex RRGGBB
            "idle": "28DC5A", "analyzing": "2878FF", "thinking": "FFCD00",
            "question": "FF4038", "limit": "9664DC",
        },
    },
    "unread": True,             # after a reply you haven't looked at, keep a "done, unread" look
    "unread_minutes": 20,       # ...for at most this long
    "cheer": True,              # celebrate long tasks finishing
    "accent": False,            # tint MAICA's rim with the colour of the app it's showing (off: on the
                                # square screen the bar under the eyes reads as a mouth)
    "double_tap_jump": True,    # double-tap MAICA -> bring forward the app that needs you
    "voice": {
        "enabled": True,        # push-to-talk on the rev-2 board
        "model": "base.en",     # faster-whisper model (downloaded on first use)
        "auto_send": False,     # press Enter after typing what you said
    },
    "light": {
        "mode": "classic-bt",   # classic-bt | port | ble | console
        "name": "ClaudeStatusMatrix",
        "port": "",
    },
    "update_check": True,
    "backends": "all",
}

_FACE_KEYS = {  # settings -> the firmware's short config keys (see the sketch's applyConfig)
    "brightness": "br", "eye_size": "es", "bored_minutes": "bo", "sleep_minutes": "sl",
    "screen_off_minutes": "so", "no_computer_off_minutes": "ao", "name": "nm",
}
_COLOR_KEYS = {"idle": "ci", "analyzing": "ca", "thinking": "ct", "question": "cq", "limit": "cl"}


def path() -> str:
    return os.path.join(config_dir(), "settings.json")


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load() -> dict:
    s = copy.deepcopy(DEFAULTS)
    try:
        with open(path(), encoding="utf-8") as f:
            _merge(s, json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[settings] couldn't read {path()} ({e}); using defaults")
    return s


def save(s: dict):
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, path())


def face_config(s: dict) -> str:
    """The face settings as the firmware's config string, e.g.
    "br=100;es=100;bo=5;...;ci=28DC5A;...". Values are plain: no ';' or '}'."""
    face = s.get("face", {})
    parts = []
    for k, short in _FACE_KEYS.items():
        if k in face:
            v = str(face[k]).replace(";", "").replace("}", "").replace("{", "")[:16]
            parts.append(f"{short}={v}")
    for k, short in _COLOR_KEYS.items():
        c = str(face.get("colors", {}).get(k, "")).lstrip("#")
        if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
            parts.append(f"{short}={c.upper()}")
    return ";".join(parts)
