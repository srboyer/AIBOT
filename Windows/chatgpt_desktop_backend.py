"""
chatgpt_desktop_backend.py

ChatBackend implementation for the ChatGPT desktop app (Windows Chat mode,
not Codex mode), via pywinauto UI Automation.

Detection strategy: ChatGPT swaps its composer's 'Send' button for a
'Stop' button the moment it starts generating a response, and swaps back
the moment it finishes. Confirmed live and stable across a real ~45s
generation with no flicker, so this is the sole signal used here --
simpler than what Claude Desktop needed, since ChatGPT doesn't require any
message-tree scanning or text-content reading at all.

Known gap: unlike Claude Desktop, there's no confirmed structural signal
for "waiting on an interactive clarifying question" -- if ChatGPT asks a
follow-up question, it likely does so as plain response text, which just
looks like a normal finished turn here. thinking_started/thinking_ended
are also not applicable (no observed equivalent of extended-thinking mode)
and are always reported False.

See chat_backend.py for the interface this implements.
"""

import sys
import time

from pywinauto import Desktop

from chat_backend import ChatBackend


class ChatGPTDesktopBackend(ChatBackend):
    name = "chatgpt_desktop"

    def __init__(self):
        self.win = None
        self.armed = False  # True while a response is actively generating

    # -- ChatBackend interface --------------------------------------------

    def connect(self):
        self.win = self._get_window()
        self.armed = False
        print("Watching ChatGPT Desktop (Chat mode). Type a prompt directly in the app.")

    def try_connect(self) -> bool:
        """Single, non-blocking attempt (see chat_backend.py)."""
        try:
            win = Desktop(backend="uia").window(title="ChatGPT")
            # .exists() answers instantly; forcing resolution on a missing
            # window blocks ~5s and starves the multi-app loop's heartbeat
            # (see the matching comment in claude_desktop_backend.py).
            if not win.exists(timeout=0.2, retry_interval=0.1):
                return False
        except Exception:
            return False
        self.win = win
        self.armed = False
        return True

    def check_alive(self):
        self.win.window_text()  # raises if the window has gone stale

    def poll(self, debug: bool = False) -> dict:
        result = {
            "prompt_sent": False,
            "question_started": False,
            "answer_received": False,
            "thinking_started": False,
            "thinking_ended": False,
            "finished": False,
        }

        stop_present = self._button_exists("Stop")

        if debug:
            print(f"[debug] armed={self.armed} stop_present={stop_present}")

        if stop_present and not self.armed:
            result["prompt_sent"] = True
            self.armed = True
        elif not stop_present and self.armed:
            result["finished"] = True
            self.armed = False

        return result

    # -- internal helpers --------------------------------------------------

    def _button_exists(self, title):
        # NOTE: do NOT use .wrapper_object() here -- when the element is
        # absent, pywinauto silently retries for its default find-timeout
        # (~5 seconds!) before raising. ChatGPT's Stop button is absent the
        # entire time the app is idle, so that turned every idle poll into
        # a ~5s stall -- harmless-ish in single-app mode, but in multi-app
        # mode it starved the other apps' polling and made their events lag
        # by many seconds. .exists() with a tiny timeout returns instantly.
        try:
            return self.win.child_window(title=title, control_type="Button").exists(timeout=0.1, retry_interval=0.1)
        except Exception:
            return False

    def _get_window(self):
        attempt = 0
        while True:
            try:
                win = Desktop(backend="uia").window(title="ChatGPT")
                win.window_text()  # forces resolution; raises if not found
                return win
            except Exception:
                attempt += 1
                if attempt == 1 or attempt % 12 == 0:
                    print("Waiting for ChatGPT to open...", file=sys.stderr)
                time.sleep(5)
