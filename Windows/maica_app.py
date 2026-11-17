"""
maica_app.py -- the MAICA desktop app.

A tray icon (Windows) / menu-bar icon (Mac) that runs the watcher in the
background and gives it a face on the computer too: the icon takes the
colour of what MAICA is showing, and its menu has

    what MAICA is showing / which apps are open
    Settings...                 name, colours, brightness, timings, behaviour
    Set up MAICA...             the first-run guide (pairing, Claude Code,
                                browser extension, Mac permissions)
    Flash MAICA's firmware...   over USB, no Arduino IDE needed: the bundled
                                firmware, or (running from source) built
                                fresh from the sketch
    Check for updates
    Start at login
    Open log
    Restart MAICA               a fresh copy -- picks up changed code
    Quit

The first run opens the setup guide by itself. Only one copy runs at a time
(the second finds the first's event server port taken and just opens the
guide). Windows open in their own process (--settings / --setup / --firmware)
so they never fight the icon's event loop -- which on a Mac must own the
main thread.

    pip install pystray pillow pyserial pywinauto     (Windows)
    pip install pystray pillow pyserial pyobjc        (Mac)

    py -3.11 maica_app.py                 the app
    py -3.11 maica_app.py --settings      just the Settings window (also --setup, --firmware)
    MAICA --claude-hook                   (Mac app) Claude Code's status hook, see below

On a Mac the packaged app is also Claude Code's hook: the setup guide
registers "<MAICA.app>/Contents/MacOS/MAICA --claude-hook", which runs
claude_code_status_hook and exits before anything heavier loads -- a Mac
has no Python of its own to run the hook script with. (Windows ships a
separate ClaudeCodeHook.exe.)

pystray builds the icon's menu once, so it's rebuilt when what it shows
changes. That happens on the icon's own thread (App.on_main): the Mac menu
bar may only be touched from the main thread, and on Windows a menu rebuilt
from the watcher thread could vanish from under you while it's open.
"""

import sys

if __name__ == "__main__" and "--claude-hook" in sys.argv:
    import claude_code_status_hook
    claude_code_status_hook.main()
    sys.exit(0)

import argparse
import os
import queue
import subprocess
import threading
import time
import webbrowser

import autostart
import chat_watcher
import event_server
import rules
import settings as settings_mod
from app_paths import log_path, running_from_disk_image
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


WM_MAICA_CALL = 0x0400 + 42  # WM_USER + 42: run what App.on_main queued, on the icon's thread (Windows)


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
        self.menu_sig = None
        self.calls = queue.Queue()  # for the icon's thread (Windows)

    def on_main(self, fn):
        """Run fn on the icon's thread: the Mac's main thread (AppKit's rule
        for anything in the menu bar), or the tray icon's message loop on
        Windows. Before the icon is up, right here."""
        if sys.platform == "darwin":
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(fn)
                return
            except Exception:
                pass
        hwnd = getattr(self.icon, "_hwnd", None)
        if sys.platform == "win32" and hwnd:
            import ctypes
            self.calls.put(fn)
            ctypes.windll.user32.PostMessageW(hwnd, WM_MAICA_CALL, 0, 0)
            return
        fn()

    def _run_calls(self, _wparam=0, _lparam=0):
        while True:
            try:
                fn = self.calls.get_nowait()
            except queue.Empty:
                return 0
            try:
                fn()
            except Exception as e:
                print(f"[app] menu update failed: {e}")

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

        self.watcher = threading.Thread(target=run, name="maica-watcher", daemon=True)
        self.watcher.start()

    def on_status(self, snap):
        self.status = snap
        if self.icon is None:
            return
        showing = snap.get("showing")
        menu_sig = (showing, snap.get("light"),
                    tuple((a["name"], a["state"]) for a in snap.get("apps", []) if a.get("connected")))
        icon_changed, menu_changed = showing != self.shown_state, menu_sig != self.menu_sig
        if not (icon_changed or menu_changed):
            return
        self.shown_state, self.menu_sig = showing, menu_sig

        def apply():
            try:
                if icon_changed:
                    self.icon.icon = icon_image(showing)
                    self.icon.title = f"MAICA: {STATE_WORDS.get(showing, showing)}"
                if menu_changed:
                    self.icon.update_menu()  # the header and the app list are worked out afresh
            except Exception:
                pass
        self.on_main(apply)

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
        # If anything is stuck (say a Bluetooth write), don't linger as an
        # invisible process still holding the port -- leave regardless.
        threading.Timer(3.0, lambda: os._exit(0)).start()
        if self.icon is not None:
            self.icon.stop()

    def restart(self, *_):
        """Start a fresh copy -- picking up changed code, rules or settings --
        and leave. The watcher is stopped first so it closes the Bluetooth
        link, and the event server so its port (the one-copy check) is free
        for the new copy; then it starts, and this one goes."""
        print("[app] restarting...")
        self.stop.set()
        event_server.hub.wake.set()
        watcher = getattr(self, "watcher", None)
        if watcher is not None:
            watcher.join(timeout=4)
        event_server.stop()
        if getattr(sys, "frozen", False):
            cmd, cwd = [sys.executable], None
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            cmd, cwd = [sys.executable, os.path.join(here, "maica_app.py")], here
        # Same console (if there is one), so a terminal you started it from
        # keeps showing the log.
        subprocess.Popen(cmd, cwd=cwd)
        threading.Timer(3.0, lambda: os._exit(0)).start()
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
            Item("Flash MAICA's firmware...", lambda *_: _relaunch("--firmware")),
            Item("Check for updates", self.check_updates),
            Item("Start at login", self.toggle_autostart, checked=lambda _: autostart.is_enabled()),
            Item("Open log", self.open_log),
            Menu.SEPARATOR,
            Item("Restart MAICA", self.restart),
            Item(f"Quit MAICA {VERSION}", self.quit),
        )
        self.icon = pystray.Icon("MAICA", icon_image("idle"), "MAICA", menu)
        handlers = getattr(self.icon, "_message_handlers", None)  # pystray's Windows message loop
        if isinstance(handlers, dict):
            handlers[WM_MAICA_CALL] = self._run_calls
        self.start_watcher()
        self.icon.run()


def self_test() -> int:
    """Load every backend this computer would run and every module the
    windows use -- what a packaged build leaves out shows up here, not in
    a customer's hands."""
    import tkinter
    import claude_code_hook_installer  # noqa: F401
    import claude_code_status_hook  # noqa: F401
    import esptool  # noqa: F401
    import firmware_update
    import maica_ui  # noqa: F401
    import updater  # noqa: F401
    from app_paths import bundled
    names = chat_watcher.resolve_backends("all")
    for name in names:
        chat_watcher.load_backend(name)
    tkinter.Tcl()  # Tcl/Tk is inside the bundle
    assert rules.load()["sites"], "no rules"
    extras = [label for label, ok in (("firmware", firmware_update.bundled_parts() is not None),
                                      ("browser extension", os.path.isdir(bundled(os.path.join("extension", "dist")))))
              if ok]
    print(f"MAICA {VERSION} self-test OK: {len(names)} backends load; bundled: {', '.join(extras) or 'nothing extra'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The MAICA desktop app.")
    ap.add_argument("--settings", action="store_true")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--firmware", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="load every part of the app and exit (checks a packaged build is complete)")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

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
    print(f"[app] MAICA {VERSION} starting")
    if running_from_disk_image():
        print("[app] running from the disk image / a temporary copy -- move MAICA to Applications")
    if first_run or running_from_disk_image():
        _relaunch("--setup")  # (its first page asks for the move to Applications, if needed)
    App(cfg).run()


if __name__ == "__main__":
    sys.exit(main())
