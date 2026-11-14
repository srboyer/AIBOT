"""
app_paths.py

Where the app keeps its files, the same on Windows and Mac:
  config_dir()   settings.json, a rules.json override, the watcher log
  bundled(name)  a file shipped next to the code (or inside the PyInstaller
                 bundle once frozen)
"""

import os
import sys

APP_DIR_NAME = "ClaudeStatusLight"  # kept from the first release so logs/settings stay put


def config_dir() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def bundled(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def log_path() -> str:
    return os.path.join(config_dir(), "watcher.log")
