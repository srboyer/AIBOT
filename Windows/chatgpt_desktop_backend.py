"""
chatgpt_desktop_backend.py

ChatBackend implementation for the ChatGPT desktop app (Windows Chat mode,
not Codex mode), via pywinauto UI Automation.

Detection strategy: ChatGPT swaps its composer's 'Send' button for a
'Stop' button the moment it starts generating a response, and swaps back
the moment it finishes. Confirmed live and stable across a real ~45s
generation with no flicker, so this is the primary signal used here --
simpler than what Claude Desktop needed, since ChatGPT doesn't require any
message-tree scanning or text-content reading at all. thinking_started/
thinking_ended are not applicable (no observed equivalent of
extended-thinking mode) and are always reported False.

Interactive clarifying questions ARE structurally detectable, just not via
the Send/Stop toggle: ChatGPT renders them as a distinct widget (a
GroupBox named after the question, choice buttons, and -- the reliable
part -- an Edit control titled "Custom answer for <question text>"),
confirmed via inspect_window.py against a real "Where are you going?"
prompt. That Edit control's title PREFIX is question-agnostic and is what
QUESTION_EDIT_TITLE_RE below matches. Without this, the Stop button
reverting to Send while ChatGPT is just waiting on you (not actually
finished) was indistinguishable from a real finish -- see poll()'s
awaiting_answer hold, same "hold until it clears" shape as Claude
Desktop's is_question_hint handling.

Out of tokens (wording unverified): while nothing is generating, the window
is searched every few seconds for a plan-limit banner ("You've hit the Free
plan limit for...", "You've reached your limit...") -- see
usage_limit.LimitBannerWatch, and its --debug "[limit]" lines if a real
banner isn't recognised. A free-plan limit that falls back to a smaller
model still counts: ChatGPT keeps answering, and while it does the working
face shows over the out-of-tokens one anyway.

See chat_backend.py for the interface this implements.
"""

import sys
import time

from pywinauto import Desktop

from chat_backend import ChatBackend
from usage_limit import LimitBannerWatch

# Matches "Custom answer for Where are you going?", "Custom answer for
# What's your budget?", etc. -- see module docstring.
QUESTION_EDIT_TITLE_RE = r"^Custom answer for .*"


class ChatGPTDesktopBackend(ChatBackend):
    name = "chatgpt_desktop"

    def __init__(self):
        self.win = None
        self.armed = False  # True while a response is actively generating
        self.awaiting_answer = False  # True while an interactive question is up, unanswered
        self.limit_watch = LimitBannerWatch()

    # -- ChatBackend interface --------------------------------------------

    def connect(self):
        self.win = self._get_window()
        self.armed = False
        self.awaiting_answer = False
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
        self.awaiting_answer = False
        self.limit_watch = LimitBannerWatch()
        return True

    def check_alive(self):
        self.win.window_text()  # raises if the window has gone stale

    def poll(self, debug: bool = False) -> dict:
        result = self._poll_turn(debug=debug)
        result["limited"] = self.limit_watch.check(
            lambda: self.win if self._page_visible() else None,
            busy=self.armed or self.awaiting_answer, debug=debug, label=self.name)
        return result

    def _page_visible(self) -> bool:
        """Whether ChatGPT's page is what the window is showing (always, for
        the desktop app; the browser backend checks the active tab)."""
        return True

    def _poll_turn(self, debug: bool = False) -> dict:
        result = {
            "prompt_sent": False,
            "question_started": False,
            "answer_received": False,
            "thinking_started": False,
            "thinking_ended": False,
            "finished": False,
        }

        question_present = self._question_present()

        if debug:
            print(f"[debug] armed={self.armed} awaiting_answer={self.awaiting_answer} question_present={question_present}")

        if question_present and not self.awaiting_answer:
            result["question_started"] = True
            self.awaiting_answer = True
            # Not "generating" while it's sitting there waiting on you --
            # the Stop button is gone during this too, and without clearing
            # armed here the hold-exit below would immediately misread that
            # as a real finish the moment the question first appears.
            self.armed = False
            return result

        if self.awaiting_answer:
            # Hold here entirely -- skip Stop-button/finished detection
            # below -- until the question widget actually clears, meaning
            # you picked an option or typed a custom answer. Same shape as
            # Claude Desktop's is_question_hint hold in claude_desktop_backend.py.
            if not question_present:
                result["answer_received"] = True
                self.awaiting_answer = False
            return result

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

    def _question_present(self):
        # Same instant-return .exists() approach as _button_exists -- see
        # its comment for why .wrapper_object() must never be used here.
        try:
            return self.win.child_window(
                title_re=QUESTION_EDIT_TITLE_RE, control_type="Edit"
            ).exists(timeout=0.1, retry_interval=0.1)
        except Exception:
            return False

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
