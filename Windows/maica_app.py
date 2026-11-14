"""
maica_app.py -- the MAICA desktop app.

A tray icon (Windows) / menu-bar icon (Mac) that runs the watcher in the
background and gives it a face on the computer too: the icon takes the
colour of what MAICA is showing, and its menu has

    what MAICA is showing / which apps are open
    Settings...                 name, colours, brightness, timings, behaviour
    Set up MAICA...             the first-run guide (pairing, Claude Code,
                                browser extension, Mac permissions)
    Update MAICA's firmware...  over USB, no Arduino needed
    Check for updates
    Start at login
    Open log / Quit

The first run opens the setup guide by itself. Only one copy runs at a time
(the second finds the first's event server port taken and just opens the
guide). Windows open in their own process (--settings / --setup / --firmware)
so they never fight the icon's event loop -- which on a Mac must own the
main thread.

    pip install pystray pillow pyserial pywinauto     (Windows)
    pip install pystray pillow pyserial pyobjc        (Mac)

    py -3.11 maica_app.py                 the app
    py -3.11 maica_app.py --settings      just the Settings window (also --setup, --firmware)
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser

import autostart
import chat_watcher
import event_server
import rules
import settings as settings_mod
from app_paths import log_path
from app_version import VERSION
from claude_light_tracker import ArduinoLight, BLELight, ConsoleLight, find_classic_bt_port

STATE_RGB = {
    "idle": (40, 220, 90), "done": (40, 220, 90), "unread": (40, 220, 90), "analyzing": (40, 120, 255),
    "thinking": (255, 205, 0), "question": (255, 64, 56), "limit": (150, 100, 220),
}
STATE_WORDS = {
    "idle": "idle", "done": "done", "unread": "done, unread", "analyzing": "working", "thinking": "thinking",
    "question": "asking you something", "limit": "out of tokens",
}


def make_light_factory(cfg):
    light = cfg.get("light", {})

    def make():
        mode = light.get("mode", "classic-bt")
        if mode == "port" and light.get("port"):
            return ArduinoLight(light["port"], 9600)
        if mode == "classic-bt":
            return ArduinoLight(find_classic_bt_port(light.get("name", "ClaudeStatusMatrix")), 9600)
        if mode == "ble":
            return BLELight(device_name=light.get("name", "ClaudeStatusMatrix"))
        return ConsoleLight()
    return make


def _relaunch(flag):
    """Open one of the windows in its own process."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, flag]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), flag]
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    subprocess.Popen(cmd, **kwargs)


def icon_image(state: str):
    from PIL import Image, ImageDraw
    s = 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((1, 1, s - 2, s - 2), fill=STATE_RGB.get(state, (150, 150, 150)) + (255,))
    for cx in (22, 42):
        d.rounded_rectangle((cx - 6, 18, cx + 6, 42), radius=6, fill=(10, 14, 12, 255))
    return img


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.status = {"showing": "idle", "light": False, "apps": []}
        self.stop = threading.Event()
        self.icon = None
        self.shown_state = None

    # -- the watcher, in the background ---------------------------------------------

    def start_watcher(self):
        names = chat_watcher.resolve_backends(self.cfg.get("backends", "all"))

        def run():
            while not self.stop.is_set():
                try:
                    chat_watcher.run_multi(names, make_light_factory(self.cfg), stop_event=self.stop,
                                           status_cb=self.on_status)
                except Exception as e:
                    print(f"[app] watcher error, restarting in 5s: {e}")
                    time.sleep(5)

        threading.Thread(target=run, name="maica-watcher", daemon=True).start()

    def on_status(self, snap):
        self.status = snap
        if self.icon is not None and snap.get("showing") != self.shown_state:
            self.shown_state = snap.get("showing")
            try:
                self.icon.icon = icon_image(self.shown_state)
                self.icon.title = f"MAICA: {STATE_WORDS.get(self.shown_state, self.shown_state)}"
            except Exception:
                pass

    # -- menu -----------------------------------------------------------------------

    def header(self, _item=None):
        showing = STATE_WORDS.get(self.status.get("showing"), "")
        link = "connected" if self.status.get("light") else "looking for MAICA..."
        return f"MAICA {link} -- {showing}"

    def app_items(self):
        import pystray
        open_apps = [a for a in self.status.get("apps", []) if a.get("connected")]
        if not open_apps:
            return [pystray.MenuItem("No AI apps open", None, enabled=False)]
        return [pystray.MenuItem(f"{rules.app_info(a['app']).get('name', a['name'])}: "
                                 f"{STATE_WORDS.get(a['state'], a['state'])}", None, enabled=False) for a in open_apps]

    def check_updates(self, *_):
        import updater
        newer, msg, url = updater.check()
        self.notify(msg)
        if newer and url:
            webbrowser.open(url)

    def notify(self, msg):
        print(f"[app] {msg}")
        try:
            self.icon.notify(msg, "MAICA")
        except Exception:
            pass

    def toggle_autostart(self, *_):
        (autostart.disable if autostart.is_enabled() else autostart.enable)()

    def open_log(self, *_):
        path = log_path()
        if sys.platform == "win32":
            os.startfile(path) if os.path.exists(path) else self.notify("No log yet.")
        else:
            subprocess.run(["open", path])

    def quit(self, *_):
        self.stop.set()
        event_server.hub.wake.set()
        if self.icon is not None:
            self.icon.stop()

    def run(self):
        import pystray
        from pystray import Menu, MenuItem as Item
        menu = Menu(
            Item(self.header, None, enabled=False),
            Item("AI apps", Menu(lambda: self.app_items())),
            Menu.SEPARATOR,
            Item("Settings...", lambda *_: _relaunch("--settings")),
            Item("Set up MAICA...", lambda *_: _relaunch("--setup")),
            Item("Update MAICA's firmware...", lambda *_: _relaunch("--firmware")),
            Item("Check for updates", self.check_updates),
            Item("Start at login", self.toggle_autostart, checked=lambda _: autostart.is_enabled()),
            Item("Open log", self.open_log),
            Menu.SEPARATOR,
            Item(f"Quit MAICA {VERSION}", self.quit),
        )
        self.icon = pystray.Icon("MAICA", icon_image("idle"), "MAICA", menu)
        self.start_watcher()
        self.icon.run()


def main(argv=None):
    ap = argparse.ArgumentParser(description="The MAICA desktop app.")
    ap.add_argument("--settings", action="store_true")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--firmware", action="store_true")
    a = ap.parse_args(argv)

    import maica_ui
    if a.settings:
        return maica_ui.run_settings()
    if a.setup:
        return maica_ui.run_setup()
    if a.firmware:
        return maica_ui.run_firmware()

    first_run = not os.path.exists(settings_mod.path())
    if not event_server.start():
        # Already running: this click probably meant "show me MAICA".
        _relaunch("--setup")
        return
    event_server.hub.rules_json = rules.as_json_bytes()
    cfg = settings_mod.load()
    if first_run:
        settings_mod.save(cfg)
        _relaunch("--setup")
    print(f"[app] MAICA {VERSION} starting")
    App(cfg).run()


if __name__ == "__main__":
    main()
