"""
copilot_desktop_backend.py

ChatBackend implementation for the Windows 11 Copilot app, via pywinauto
UI Automation.

Detection strategy: Copilot's toolbar has a button that's titled 'Talk to
Copilot' while idle and swaps to 'Interrupt message' the instant a response
starts generating, swapping back the instant it finishes -- confirmed live
via a rapid button-title poll (0.3s interval) during a real ~2s generation.
Same idea as ChatGPT Desktop's Send/Stop toggle, just a different button
and different labels; unlike ChatGPT's, this one briefly reports an empty
title mid-transition, which is treated as "still generating" (see
_is_generating).

Known gap: no confirmed THINKING or QUESTION equivalent (same limitation as
chatgpt_desktop_backend.py) -- thinking_started/thinking_ended/
question_started/answer_received always report False here. The composer
also has a 'Submit message' button, but it only reflects whether the text
box currently has typed text, not generation state, so it isn't used for
detection.

See chat_backend.py for the interface this implements.
"""

import sys
import time

from pywinauto import Desktop

from chat_backend import ChatBackend

GENERATING_TITLE = "Interrupt message"
IDLE_TITLE = "Talk to Copilot"


class CopilotDesktopBackend(ChatBackend):
    name = "copilot_desktop"

    def __init__(self):
        self.win = None
        self.armed = False  # True while a response is actively generating

    # -- ChatBackend interface --------------------------------------------

    def connect(self):
        self.win = self._get_window()
        self.armed = False
        print("Watching Copilot. Type a prompt directly in the app.")

    def try_connect(self) -> bool:
        """Single, non-blocking attempt (see chat_backend.py)."""
        try:
            win = Desktop(backend="uia").window(title="Copilot")
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

        generating = self._is_generating()

        if debug:
            print(f"[debug] armed={self.armed} generating={generating}")

        if generating and not self.armed:
            result["prompt_sent"] = True
            self.armed = True
        elif not generating and self.armed:
            result["finished"] = True
            self.armed = False

        return result

    # -- internal helpers --------------------------------------------------

    def _is_generating(self) -> bool:
        # The toggling button briefly reports an empty title mid-transition
        # (observed live between the "Interrupt message" and "Talk to
        # Copilot" states) -- treated as still-generating so that blip
        # doesn't read as a premature "finished".
        title = self._toggle_button_title()
        return title != IDLE_TITLE

    def _toggle_button_title(self):
        # NOTE: do NOT use .wrapper_object() here -- when a title match is
        # absent, pywinauto silently retries for its default find-timeout
        # (~5 seconds!) before raising. See the matching comment in
        # chatgpt_desktop_backend.py for why that's fatal to multi-app
        # polling latency.
        try:
            if self.win.child_window(title=GENERATING_TITLE, control_type="Button").exists(timeout=0.1, retry_interval=0.1):
                return GENERATING_TITLE
            if self.win.child_window(title=IDLE_TITLE, control_type="Button").exists(timeout=0.1, retry_interval=0.1):
                return IDLE_TITLE
            return ""  # mid-transition blip: neither title currently matches
        except Exception:
            return ""

    def _get_window(self):
        attempt = 0
        while True:
            try:
                win = Desktop(backend="uia").window(title="Copilot")
                win.window_text()  # forces resolution; raises if not found
                return win
            except Exception:
                attempt += 1
                if attempt == 1 or attempt % 12 == 0:
                    print("Waiting for Copilot to open...", file=sys.stderr)
                time.sleep(5)
