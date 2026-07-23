"""
claude_desktop_backend.py

ChatBackend implementation for Claude Desktop (Windows), via pywinauto UI
Automation. This is a faithful port of everything from the original
claude_desktop_watcher.py -- every bug fix (the O(n) message-scan fix, the
input-box placeholder matching, the question-hold-until-answered logic, the
liveness check) is preserved exactly, just reorganized behind the
ChatBackend interface so a generic watcher can drive any chatbot the same
way.

See chat_backend.py for the interface this implements, and
inspect_window.py if you need to re-inspect the app after a UI update.
"""

import re
import sys
import time

from pywinauto import Desktop

from chat_backend import ChatBackend

MESSAGE_RE = re.compile(r"^Message (\d+) of (\d+)$")
PLACEHOLDER_TEXT = "Write a message…"  # the input box reports this, not "", when idle
QUESTION_PLACEHOLDER_HINT = "reply directly"  # shown when Claude is waiting on an interactive choice
LIVENESS_FAILURE_LIMIT = 10  # ~10 failed polls in a row -> assume the app was closed/reopened


def _is_effectively_empty(text: str) -> bool:
    cleaned = text.strip()
    return cleaned == "" or cleaned == PLACEHOLDER_TEXT or QUESTION_PLACEHOLDER_HINT in cleaned.lower()


class ClaudeDesktopBackend(ChatBackend):
    name = "claude_desktop"

    def __init__(self):
        self.win = None
        self.input_box = None
        self.chat_group = None
        self.last_input_text = ""
        self.armed = False
        self.last_message_n = -1
        self.awaiting_answer = False
        self.question_message_n = -1
        self.consecutive_input_failures = 0
        self._thinking_active = False
        self._seen_text = False

    # -- ChatBackend interface --------------------------------------------

    def connect(self):
        self.win = self._get_claude_window()
        self.input_box = self._wait_for(self._get_input_box, "input box")
        self.chat_group = self._wait_for(self._get_chat_messages_group, "'Chat messages' group")
        self._reset_poll_state()
        print("Watching Claude Desktop. Type a prompt directly in the app.")

    def try_connect(self) -> bool:
        """Single, non-blocking attempt (see chat_backend.py). Only commits
        self.win/input_box/chat_group once ALL of them resolved, so a
        half-found window can't leave the backend in a half-connected
        state that poll() would then trip over."""
        try:
            win = Desktop(backend="uia").window(title="Claude")
            # NEVER force-resolve an unresolved spec here (.window_text() /
            # .wrapper_object()) -- when the app isn't running, resolution
            # blocks ~5s (pywinauto's default find-timeout). Multi-app mode
            # calls try_connect for every closed app on a 5s cycle, and two
            # of those stalls landing in the same loop iteration starves the
            # BLE heartbeat long enough (>8s) for the firmware to declare a
            # ghost and drop the connection mid-state. .exists() with a tiny
            # timeout answers immediately.
            if not win.exists(timeout=0.2, retry_interval=0.1):
                return False
            input_box = self._get_input_box(win)
            chat_group = self._get_chat_messages_group(win)
            # Same landmine: this spec is unresolvable ~5s when Claude is
            # open but sitting on the new-chat screen (no conversation ->
            # no 'Chat messages' group yet).
            if not chat_group.exists(timeout=0.2, retry_interval=0.1):
                return False
        except Exception:
            self.win = None
            return False

        self.win = win
        self.input_box = input_box
        self.chat_group = chat_group
        self._reset_poll_state()
        return True

    def _reset_poll_state(self):
        self.last_input_text = ""
        self.armed = False
        self.last_message_n = -1
        self.awaiting_answer = False
        self.question_message_n = -1
        self.consecutive_input_failures = 0
        self._thinking_active = False
        self._seen_text = False

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

        try:
            current_input = self.input_box.window_text()
            self.consecutive_input_failures = 0
        except Exception as e:
            self.consecutive_input_failures += 1
            if debug:
                print(f"[debug] input_box.window_text() failed ({self.consecutive_input_failures}): {e}")
            current_input = self.last_input_text  # temporarily unavailable; skip this tick
            if self.consecutive_input_failures >= LIVENESS_FAILURE_LIMIT:
                raise RuntimeError(
                    "Input box unreachable for too long -- Claude Desktop was probably "
                    "closed and reopened. Restarting watcher to reconnect to the new window."
                )

        if debug and current_input != self.last_input_text:
            print(f"[debug] input box changed: {self.last_input_text!r} -> {current_input!r}")

        is_question_hint = QUESTION_PLACEHOLDER_HINT in current_input.lower()

        if debug:
            print(f"[debug] armed={self.armed} awaiting_answer={self.awaiting_answer} is_question_hint={is_question_hint}")

        # Claude is waiting on an interactive choice (buttons, not question-mark
        # text) -- the app itself signals this via the input placeholder.
        # NOTE: deliberately NOT gated on self.armed. The 'reply directly'
        # placeholder only ever appears when a question is actually pending,
        # so it's trustworthy on its own -- and if the watcher (re)connects
        # mid-question (BLE drop recovery, restart), armed is freshly False
        # while the question sits there unanswered. Requiring armed made
        # those questions get seen and silently discarded. Arm implicitly
        # instead, so the answered->ANALYZING->finished flow works after.
        if is_question_hint and not self.awaiting_answer:
            result["question_started"] = True
            self.awaiting_answer = True
            self.armed = True
            self.question_message_n, _ = self._get_latest_message_group()

        if self.awaiting_answer:
            # Hold here entirely -- skip prompt-detection and finished-check
            # below -- until the hint clears, meaning you actually answered.
            if not is_question_hint:
                result["answer_received"] = True
                self.awaiting_answer = False
                self.last_message_n = -1
            self.last_input_text = current_input
            return result

        # Prompt just went out: box had real text, now it's back to placeholder/empty.
        if not _is_effectively_empty(self.last_input_text) and _is_effectively_empty(current_input) and not self.armed:
            result["prompt_sent"] = True
            self.armed = True
            self.last_message_n = -1
            self._thinking_active = False
            self._seen_text = False
        self.last_input_text = current_input

        if self.armed:
            n, msg_ctrl = self._get_latest_message_group()
            if n != self.last_message_n:
                self.last_message_n = n
                self._seen_text = False  # fresh message: text-memo resets

            # ONE walk of the message subtree for buttons, shared by both
            # the thinking check and the finished check (previously these
            # each did their own full walk).
            button_titles = self._get_button_titles(msg_ctrl)
            has_thinking_btn = any(t.startswith("Thought for") for t in button_titles)

            # Extended thinking: the "Thought for Ns" button shows up before
            # any visible answer text streams in. Once real text starts
            # appearing, switch back to the normal analyzing animation.
            #
            # Perf: the text walk is by far the most expensive UIA call here
            # (it grows with response length -- hundreds of Text elements on
            # a long answer), but its ONLY purpose is distinguishing
            # "thinking, no text yet" from "text streaming". So it now runs
            # only while a thinking button is present AND we haven't seen
            # text yet; every other poll (i.e. almost all of them) skips it
            # entirely. Behavior is identical to the old
            # `has_button and not text` logic -- once text exists, thinking
            # could never re-trigger under the old check either.
            is_thinking = False
            if has_thinking_btn and not self._seen_text:
                if self._message_has_text(msg_ctrl):
                    self._seen_text = True
                else:
                    is_thinking = True

            if debug:
                print(f"[debug] latest message #{n}, buttons={button_titles!r}, seen_text={self._seen_text}")

            if is_thinking and not self._thinking_active:
                result["thinking_started"] = True
                self._thinking_active = True
            elif not is_thinking and self._thinking_active:
                result["thinking_ended"] = True
                self._thinking_active = False

            if n != self.question_message_n and "Retry" in button_titles:
                result["finished"] = True
                self.armed = False

        return result

    # -- internal helpers --------------------------------------------------

    def _get_claude_window(self):
        attempt = 0
        while True:
            try:
                win = Desktop(backend="uia").window(title="Claude")
                win.window_text()  # forces resolution; raises if not found
                return win
            except Exception:
                attempt += 1
                if attempt == 1 or attempt % 12 == 0:  # print immediately, then every ~60s
                    print("Waiting for Claude Desktop to open...", file=sys.stderr)
                time.sleep(5)

    def _get_input_box(self, win):
        # There can be more than one Edit-type control in the window (e.g. a
        # rename-chat-title box) -- blindly taking the first one found can
        # silently grab the wrong one. Only ever return a control that's
        # actually showing a recognized placeholder; raise otherwise so
        # _wait_for's retry loop tries again shortly rather than guessing.
        try:
            candidates = win.descendants(control_type="Edit")
        except Exception:
            candidates = []

        for c in candidates:
            try:
                text = c.window_text()
                if "Write a message" in text or QUESTION_PLACEHOLDER_HINT in text.lower():
                    return c
            except Exception:
                continue

        raise RuntimeError(
            "No Edit control currently shows a recognized placeholder "
            "('Write a message...' or 'Or reply directly...') -- Claude "
            "Desktop's UI may still be settling, or you'd already typed "
            "something before this ran."
        )

    def _get_chat_messages_group(self, win):
        return win.child_window(title="Chat messages", control_type="Group")

    def _wait_for(self, get_fn, label):
        attempt = 0
        while True:
            try:
                ctrl = get_fn(self.win)
                ctrl.window_text()  # forces resolution; raises if not ready
                print(f"[ok] Found {label}.")
                return ctrl
            except Exception:
                attempt += 1
                if attempt == 1 or attempt % 12 == 0:
                    print(f"Waiting for {label}...", file=sys.stderr)
                time.sleep(5)

    def _get_latest_message_group(self):
        """Finds the highest-numbered 'Message N of M' group control itself.
        Iterates from newest to oldest and stops at the first match, since
        messages are append-only -- scanning forward through the whole
        conversation instead scales badly and causes real, growing lag once
        a conversation has hundreds of messages."""
        try:
            children = self.chat_group.children(control_type="Group")
        except Exception:
            return -1, None

        for grp in reversed(children):
            try:
                match = MESSAGE_RE.match(grp.window_text())
            except Exception:
                continue
            if match:
                return int(match.group(1)), grp
        return -1, None

    def _get_button_titles(self, msg_ctrl):
        """One subtree walk collecting every Button title on the message.
        The thinking check ('Thought for Ns') and the finished check
        ('Retry' -- the action toolbar only appears once that specific
        message has fully finished rendering) both read from this single
        result instead of each walking the subtree themselves."""
        if msg_ctrl is None:
            return []
        try:
            return [b.window_text() for b in msg_ctrl.descendants(control_type="Button")]
        except Exception:
            return []

    def _message_has_text(self, msg_ctrl):
        """True if any visible response text has appeared yet. Only called
        while a thinking button is showing and no text has been seen -- the
        one situation where the answer matters (see poll())."""
        if msg_ctrl is None:
            return False
        try:
            for t in msg_ctrl.descendants(control_type="Text"):
                if t.window_text().strip():
                    return True
        except Exception:
            pass
        return False
