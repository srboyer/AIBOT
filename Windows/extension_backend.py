"""
extension_backend.py

ChatBackend for chat sites watched by the MAICA browser extension
(../extension). The extension reads each open chat tab's page directly --
on Windows and Mac, in Chrome, Edge, Brave, Firefox... -- and reports
through event_server.py:

    {"source": "ext", "key": "<tab id>", "site": "chatgpt",
     "level": "idle" | "running" | "thinking" | "question",
     "limited": bool, "focused": bool}

once whenever a tab's status changes and every few seconds as a heartbeat.
One BrowserSiteBackend per site in maica_rules.json ("web_chatgpt",
"web_claude", ...), so each site is its own app on the light, split faces
included. All of a site's tabs count together: the most active wins.

A site with no tab heard from for TAB_STALE seconds is "not open". While the
extension covers a site, the older UI-Automation backend for the same site
(claude_web / chatgpt_web) stands down -- see site_active().
"""

import time

import event_server
import rules
from chat_backend import ChatBackend
from level_tracker import LevelTracker, combine

SOURCE = "ext"
TAB_STALE = 20.0       # seconds without a heartbeat before a tab counts as closed
SITE_GONE = 30.0       # ...and a site with no live tabs disconnects


def site_tabs(site: str) -> dict:
    """This site's open tabs: reported recently, and not closed (the
    extension says goodbye when a tab closes)."""
    return {k: p for k, p in event_server.hub.current(SOURCE, max_age=TAB_STALE).items()
            if p.get("site") == site and not p.get("closed")}


def site_active(site: str) -> bool:
    """Whether the extension is covering this site right now."""
    return bool(site_tabs(site))


class BrowserSiteBackend(ChatBackend):
    def __init__(self, site: str):
        self.site = site
        info = next((s for s in rules.load()["sites"] if s["id"] == site), {"app": site})
        self.app_id = info.get("app", site)
        self.name = f"web_{site}"
        self.tracker = LevelTracker()
        self.last_tabs_at = 0.0
        self.last_focused_tab = None

    def try_connect(self) -> bool:
        tabs = site_tabs(self.site)
        if not tabs:
            return False
        self.tracker = LevelTracker()
        self.tracker.baseline(combine(p.get("level", "idle") for p in tabs.values()))
        self.last_tabs_at = time.monotonic()
        return True

    def connect(self):
        while not self.try_connect():
            time.sleep(2)

    def check_alive(self):
        if time.monotonic() - self.last_tabs_at > SITE_GONE:
            raise RuntimeError(f"no {self.site} tab reporting")

    def poll(self, debug: bool = False) -> dict:
        tabs = site_tabs(self.site)
        if tabs:
            self.last_tabs_at = time.monotonic()
        level = combine(p.get("level", "idle") for p in tabs.values())
        result = self.tracker.feed(level)
        result["limited"] = any(p.get("limited") for p in tabs.values()) if tabs else None
        if debug:
            print(f"[debug] {self.name}: tabs={ {k: p.get('level') for k, p in tabs.items()} } -> {level}")
        return result

    # -- for "done, unread" and tap-to-jump (see chat_watcher.py) -------------

    def is_focused(self):
        tabs = site_tabs(self.site)
        if not tabs:
            return None
        return any(p.get("focused") for p in tabs.values())

    def focus(self) -> bool:
        """Ask the extension to bring this site's most relevant tab forward."""
        tabs = site_tabs(self.site)
        if not tabs:
            return False
        rank = {"question": 3, "thinking": 2, "running": 1, "idle": 0}
        key = max(tabs, key=lambda k: (rank.get(tabs[k].get("level"), 0), tabs[k].get("t", 0)))
        # Addressed to the whole extension, so it rides back on whichever tab
        # reports next (every tab sends a heartbeat every couple of seconds).
        event_server.hub.queue_command(SOURCE, "*", {"cmd": "focus", "tab": key})
        return True


def backend_names():
    """BACKENDS entries for every site in the rules: {"web_chatgpt": site id}."""
    return {f"web_{s['id']}": s["id"] for s in rules.load()["sites"]}
