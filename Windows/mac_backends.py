"""
mac_backends.py -- the Claude and ChatGPT desktop apps on a Mac, through
Accessibility (mac_ax.py). Same signal as on Windows: while an app writes,
its message box's Send button turns into Stop. Questions: Claude's "Reply
directly..." placeholder, ChatGPT's "Custom answer for ..." box. Out of
tokens: a usage-limit banner (usage_limit.looks_like_limit_banner), looked
for every few seconds while idle.

Browser chats are the browser extension's job, and Claude Code / Codex are
followed through their hooks / logs -- all of which work on a Mac as they
are. UNTESTED on a real Mac so far: run with --debug and send the output if
an app isn't picked up.
"""

import re
import time

import mac_ax
from chat_backend import ChatBackend
from level_tracker import LevelTracker
from usage_limit import BANNER_SCAN_SECONDS, looks_like_limit_banner


class MacStopButtonBackend(ChatBackend):
    APP = ""
    APP_ID = ""
    STOP_RE = re.compile(r"^Stop\b", re.I)
    QUESTION_RE = None

    def __init__(self):
        self.root = None
        self.scope = None          # the container the Send/Stop button was last found in
        self.tracker = LevelTracker()
        self.limited = False
        self.next_banner_scan = 0.0
        self.app_id = self.APP_ID
        self.focus_target = {"mac_app": self.APP}

    def try_connect(self) -> bool:
        if not mac_ax.is_trusted():
            return False
        self.root = mac_ax.app_element(self.APP)
        if self.root is None:
            return False
        self.tracker = LevelTracker()
        self.tracker.baseline(self._level())
        return True

    def connect(self):
        while not self.try_connect():
            time.sleep(5)

    def check_alive(self):
        if not mac_ax.app_running(self.APP):
            raise RuntimeError(f"{self.APP} quit")

    def _level(self) -> str:
        stop = mac_ax.find_button(self.root, self.STOP_RE, self.scope)
        if stop is not None:
            self.scope = mac_ax.attr(stop, "AXParent") or self.scope
            return "running"
        if self.QUESTION_RE is not None:
            for el in mac_ax.walk(self.root, max_nodes=3000):
                if self.QUESTION_RE.search(mac_ax.attr(el, "AXPlaceholderValue") or mac_ax.label(el)):
                    return "question"
        return "idle"

    def poll(self, debug: bool = False) -> dict:
        level = self._level()
        result = self.tracker.feed(level)
        now = time.monotonic()
        if level == "idle" and now >= self.next_banner_scan:
            self.next_banner_scan = now + BANNER_SCAN_SECONDS
            texts = mac_ax.short_texts(self.root)
            self.limited = any(looks_like_limit_banner(t) for t in texts)
            if debug:
                print(f"[limit] {self.APP}: {texts[:6]}")
        result["limited"] = self.limited
        if debug:
            print(f"[debug] {self.APP} (mac): {level}")
        return result


class ClaudeDesktopMacBackend(MacStopButtonBackend):
    name = "claude_desktop_mac"
    APP = "Claude"
    APP_ID = "claude"
    STOP_RE = re.compile(r"^Stop( response)?\b", re.I)
    QUESTION_RE = re.compile(r"reply directly", re.I)


class ChatGPTDesktopMacBackend(MacStopButtonBackend):
    name = "chatgpt_desktop_mac"
    APP = "ChatGPT"
    APP_ID = "chatgpt"
    STOP_RE = re.compile(r"^Stop\b", re.I)
    QUESTION_RE = re.compile(r"^Custom answer for ", re.I)
