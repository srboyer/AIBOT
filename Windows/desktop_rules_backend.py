"""
desktop_rules_backend.py

A generic ChatBackend for desktop chat apps described in maica_rules.json's
"desktop" list -- LM Studio, the Ollama app, Perplexity's app, and whatever
gets added there later -- so covering another app is a rules entry, not new
code. An app is RUNNING while a button whose name matches its "stop" pattern
exists in its window (nearly every chat app swaps Send for Stop while it
writes), and out of tokens while a usage-limit banner shows (usage_limit).

Windows: UI Automation (pywinauto), window found by its title ("win_title").
macOS:   Accessibility (mac_ax.py), app found by name ("mac_app").

The labels are unverified for each app until checked against it (run
inspect_window.py on Windows); a wrong label just means the app never shows
as busy.
"""

import re
import sys
import time

import rules
from chat_backend import ChatBackend
from level_tracker import LevelTracker
from usage_limit import LimitBannerWatch


class DesktopRuleBackend(ChatBackend):
    def __init__(self, rule_id: str):
        self.rule = next(r for r in rules.load()["desktop"] if r["id"] == rule_id)
        self.name = rule_id
        self.app_id = self.rule.get("app", rule_id)
        self.focus_target = {"title": self.rule.get("win_title"), "mac_app": self.rule.get("mac_app")}
        self.stop_re = re.compile(self.rule.get("stop", r"^Stop\b"), re.I)
        self.win = None
        self.tracker = LevelTracker()
        self.limit_watch = LimitBannerWatch()

    # -- platform plumbing ---------------------------------------------------

    def _find_window(self):
        if sys.platform == "win32":
            from pywinauto import Desktop
            spec = Desktop(backend="uia").window(title_re=self.rule["win_title"])
            return spec if spec.exists(timeout=0.2, retry_interval=0.1) else None
        if sys.platform == "darwin":
            import mac_ax
            return mac_ax.app_element(self.rule.get("mac_app", ""))
        return None

    def _stop_visible(self) -> bool:
        if sys.platform == "win32":
            try:
                return self.win.child_window(title_re=self.stop_re.pattern, control_type="Button").exists(
                    timeout=0.1, retry_interval=0.1)
            except Exception:
                return False
        import mac_ax
        return mac_ax.find_button(self.win, self.stop_re) is not None

    # -- ChatBackend interface --------------------------------------------

    def try_connect(self) -> bool:
        try:
            self.win = self._find_window()
        except Exception:
            self.win = None
        if self.win is None:
            return False
        self.tracker = LevelTracker()
        self.tracker.baseline("running" if self._stop_visible() else "idle")
        self.limit_watch = LimitBannerWatch()
        return True

    def connect(self):
        while not self.try_connect():
            time.sleep(5)

    def check_alive(self):
        if sys.platform == "win32":
            self.win.window_text()
        elif sys.platform == "darwin":
            import mac_ax
            if not mac_ax.app_running(self.rule.get("mac_app", "")):
                raise RuntimeError("app quit")

    def poll(self, debug: bool = False) -> dict:
        level = "running" if self._stop_visible() else "idle"
        result = self.tracker.feed(level)
        if sys.platform == "win32":
            result["limited"] = self.limit_watch.check(lambda: self.win, busy=level != "idle", debug=debug,
                                                       label=self.name)
        if debug:
            print(f"[debug] {self.name}: {level}")
        return result


def backend_names():
    """BACKENDS entries for every desktop rule this computer can follow --
    one with a window title on Windows, an app name on a Mac: {"lmstudio":
    rule id}."""
    key = "win_title" if sys.platform == "win32" else "mac_app" if sys.platform == "darwin" else None
    return {r["id"]: r["id"] for r in rules.load().get("desktop", []) if key and r.get(key)}
