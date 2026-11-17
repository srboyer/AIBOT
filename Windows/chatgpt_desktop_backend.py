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

import json
import os
import sys
import time

from chat_backend import ChatBackend
from usage_limit import LimitBannerWatch


def Desktop(*args, **kwargs):
    """pywinauto's Desktop, imported when first used -- so this module's
    plain helpers (in_conversation, codex_thread_names) and its tests load
    on a Mac too."""
    from pywinauto import Desktop as _Desktop
    return _Desktop(*args, **kwargs)


# Matches "Custom answer for Where are you going?", "Custom answer for
# What's your budget?", etc. -- see module docstring.
QUESTION_EDIT_TITLE_RE = r"^Custom answer for .*"


MODE_CHECK_EVERY = 1.0  # seconds between looks at which mode / conversation is showing


def codex_thread_names() -> set:
    """Titles of Codex's threads (~/.codex/session_index.jsonl), cached until
    the file changes. A ChatGPT conversation handed off to Codex ("thread_
    source": "chatgpt_handoff") keeps its title, which is the page's name."""
    path = os.path.join(os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex"),
                        "session_index.jsonl")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return set()
    cache = codex_thread_names.__dict__
    if cache.get("mtime") != mtime:
        names = set()
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        n = json.loads(line).get("thread_name")
                    except Exception:
                        continue
                    if n:
                        names.add(n.strip())
        except OSError:
            pass
        cache.update(mtime=mtime, names=names)
    return cache["names"]


def in_conversation(name: str, class_name: str) -> bool:
    """The conversation transcript (a region named "Conversation", class
    "... thread-scroll", seen live 2026-11-13). An old "You've hit your
    usage limit..." error stays in there after the limit resets, so limit
    texts inside it are history, not a live banner -- those sit by the
    message box. (Codex's limits come from its own logs: codex_backend.)"""
    return name == "Conversation" or "thread-scroll" in class_name


class ChatGPTDesktopBackend(ChatBackend):
    name = "chatgpt_desktop"

    def __init__(self):
        self.win = None
        self.armed = False  # True while a response is actively generating
        self.awaiting_answer = False  # True while an interactive question is up, unanswered
        self.limit_watch = LimitBannerWatch(skip_inside=in_conversation)

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
        self.limit_watch = LimitBannerWatch(skip_inside=in_conversation)
        return True

    def check_alive(self):
        self.win.window_text()  # raises if the window has gone stale

    def poll(self, debug: bool = False) -> dict:
        if self._codex_showing(debug):
            # Codex's work in this window -- Codex mode, or a conversation
            # handed off to Codex -- is the codex backend's (from its logs);
            # reading the same Stop button here too would show it twice.
            result = self._empty_result()
            if self.armed or self.awaiting_answer:
                self.armed = self.awaiting_answer = False
                result["failed"] = "Codex is showing in the window; the codex backend has it"
            return result
        result = self._poll_turn(debug=debug)
        result["limited"] = self.limit_watch.check(
            lambda: self.win if self._page_visible() else None,
            busy=self.armed or self.awaiting_answer, debug=debug, label=self.name)
        return result

    @staticmethod
    def _empty_result() -> dict:
        return {"prompt_sent": False, "question_started": False, "answer_received": False,
                "thinking_started": False, "thinking_ended": False, "finished": False}

    def _codex_showing(self, debug: bool = False) -> bool:
        """Whether the window is showing Codex rather than a ChatGPT chat:
        the mode button reads "Switch mode, current mode: <mode>", and a
        conversation handed off to Codex keeps its title as the page's name
        (codex_thread_names). Looked at every MODE_CHECK_EVERY seconds; two
        native UI Automation lookups, ~15 ms each. Unknown -> False (as before)."""
        now = time.monotonic()
        if now < getattr(self, "_mode_checked_at", 0.0) + MODE_CHECK_EVERY:
            return getattr(self, "_mode_codex", False)
        self._mode_checked_at = now
        codex = False
        try:
            from pywinauto.uia_defines import IUIA
            iuia = IUIA().iuia
            root = self.win.wrapper_object().element_info.element
            mode = root.FindFirst(4, iuia.CreatePropertyConditionEx(30005, "current mode:", 3))  # Name contains
            mode_name = (mode.CurrentName if mode else "") or ""
            if mode_name and not mode_name.rstrip().lower().endswith("chatgpt"):
                codex = True
            else:
                page = root.FindFirst(4, iuia.CreatePropertyCondition(30011, "RootWebArea"))  # AutomationId
                title = ((page.CurrentName if page else "") or "").strip()
                codex = bool(title) and title in codex_thread_names()
            if debug:
                print(f"[debug] chatgpt_desktop: mode={mode_name!r} codex_showing={codex}")
        except Exception as e:
            if debug:
                print(f"[debug] chatgpt_desktop: mode check failed ({e})")
        self._mode_codex = codex
        return codex

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
