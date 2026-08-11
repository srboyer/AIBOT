"""
watcher_app_entry.py

PyInstaller entry point for the packaged watcher app -- built --windowed
(no visible console), which is what makes this wrapper necessary rather
than just pointing PyInstaller at chat_watcher.py directly:

PyInstaller's --windowed mode sets sys.stdout/sys.stderr to None (there is
no console for them to point at). chat_watcher.py and every backend module
use plain print() throughout for status/diagnostics -- print()'ing to None
raises immediately, which would crash the packaged app the moment the
first status line is printed (i.e. immediately on startup). Redirecting
both to a log file BEFORE importing chat_watcher fixes this without
touching any of that existing print()-based code, and happens to also give
customers/support something inspectable instead of a console they'd never
see anyway. Not used for normal `py -3.11 chat_watcher.py` dev runs --
only the PyInstaller build targets this file.

Also fills in a sensible default (--classic-bt) when launched with no
arguments at all, since the packaged product is meant to be double-clicked
or run from a Task Scheduler entry, not typed into a terminal with flags.
"""

import os
import sys

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ClaudeStatusLight")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "watcher.log")

# line-buffered so a log tail shows activity promptly, not just on flush/exit
sys.stdout = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
sys.stderr = sys.stdout

if len(sys.argv) == 1:
    sys.argv.append("--classic-bt")

import chat_watcher

if __name__ == "__main__":
    chat_watcher.main()
