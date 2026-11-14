"""
autostart.py -- "Start at login" for the desktop app, Windows and Mac.

Windows: a shortcut in the user's Startup folder -- the same one the
installer creates (ClaudeStatusLight.iss), so the two never double up.
macOS: a LaunchAgent (~/Library/LaunchAgents/<LABEL>.plist) with RunAtLoad.
"""

import os
import subprocess
import sys

SHORTCUT_NAME = "Claude Status Light.lnk"
LABEL = "com.maica.desktop"


def _command():
    """How to start this app: the frozen executable, or python + maica_app.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "maica_app.py")]


def _win_startup_dir():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def _mac_plist():
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def is_enabled() -> bool:
    if sys.platform == "win32":
        return os.path.exists(os.path.join(_win_startup_dir(), SHORTCUT_NAME))
    if sys.platform == "darwin":
        return os.path.exists(_mac_plist())
    return False


def enable():
    cmd = _command()
    if sys.platform == "win32":
        target, args = cmd[0], " ".join(f'"{a}"' for a in cmd[1:])
        link = os.path.join(_win_startup_dir(), SHORTCUT_NAME)
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
              "$s.TargetPath=$env:TGT;$s.Arguments=$env:ARGS;$s.WorkingDirectory=$env:WD;$s.Save()")
        env = dict(os.environ, LNK=link, TGT=target, ARGS=args, WD=os.path.dirname(target))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif sys.platform == "darwin":
        args = "".join(f"<string>{a}</string>" for a in cmd)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
"""
        os.makedirs(os.path.dirname(_mac_plist()), exist_ok=True)
        with open(_mac_plist(), "w") as f:
            f.write(plist)


def disable():
    path = os.path.join(_win_startup_dir(), SHORTCUT_NAME) if sys.platform == "win32" else _mac_plist()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
