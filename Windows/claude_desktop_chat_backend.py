"""
claude_desktop_chat_backend.py

ChatBackend for the Chat (and Cowork) side of the current Claude desktop app
on Windows -- the same app whose Code tab hosts Claude Code -- via pywinauto
UI Automation.

WHY A NEW BACKEND: the desktop app was rebuilt around Chat / Cowork / Code
modes, and claude_desktop_backend.py's detection no longer matches anything
in it: the composer reports only "\\n" or its typed text (never the "Write a
message..." placeholder it looks for), and the reply being generated is a
group named "Currently streaming message", not "Message N of M". That
backend therefore never got past connecting, so Chat went undetected. It is
left untouched because claude_web_backend.py (claude.ai in a browser)
subclasses it.

SIGNAL -- the app's own status labels (written for screen readers), instead
of scraping message internals:
  - The mode switcher is a RadioButton named "Chat and Cowork[, <status>]",
    e.g. "Chat and Cowork, awaiting your input". It carries the Chat side's
    status even while the Code tab is showing.
  - While the Chat tab is showing, every chat in the sidebar is a Button
    named "<Status> <title>": "Running ...", "Awaiting answer ...",
    "Idle ...". This is per-chat and exact, so it's preferred whenever
    visible; the switcher's label is the fallback from the Code tab.
Code-tab sessions are deliberately ignored here -- the switcher label is
only parsed for the Chat entry and the sidebar only read while Chat is the
selected mode -- since Claude Code is claude_code_backend.py's job (hooks).

STATE MAPPING (per poll, the Chat side as a whole is idle / running /
question):
    idle -> running             prompt_sent      (ANALYZING)
    running -> question         question_started (QUESTION)
    question -> running         answer_received  (ANALYZING)
    running/question -> idle    finished         (DONE)

STALE QUESTIONS: a chat left waiting on a question keeps reporting
"Awaiting answer" / "awaiting your input" indefinitely. Treating that as
QUESTION would pin the light red forever, so a chat only counts as a
question if it was seen RUNNING while watched and then went to awaiting.
Questions already pending when the watcher connected are ignored -- the
same baseline-at-connect rule the other backends follow.

THINKING (unverified): while a chat runs with the Chat tab showing, any
element in the "Currently streaming message" group whose name starts with
"Thinking" counts as extended thinking. The label hasn't been confirmed
against the new app yet; if it differs, THINKING simply never fires and the
light stays on ANALYZING -- run with --debug to see the streaming message's
element names.
"""

import re
import sys
import time

from pywinauto import Desktop

from chat_backend import ChatBackend

WINDOW_TITLE = "Claude"
CHAT_MODE_PREFIX = "Chat"  # the mode switcher's Chat entry: "Chat and Cowork[, <status>]"
SESSION_RE = re.compile(r"^(Running|Awaiting answer|Idle) (.+)$")
STATUS_FROM_SIDEBAR = {"Running": "running", "Awaiting answer": "question", "Idle": "idle"}
STATUS_RANK = {"idle": 0, "running": 1, "question": 2}  # a title listed twice keeps its most active status
RUNNING_WORDS = ("running", "responding", "working", "thinking", "generating")
STREAMING_GROUP = "Currently streaming message"
THINKING_PREFIX = "Thinking"
LIVENESS_FAILURE_LIMIT = 10  # consecutive failed polls -> assume the app closed/reopened

EMPTY_RESULT = {
    "prompt_sent": False,
    "question_started": False,
    "answer_received": False,
    "thinking_started": False,
    "thinking_ended": False,
    "finished": False,
}


def radio_status(suffix: str) -> str:
    """'awaiting your input' -> question, a running-like word -> running,
    anything else (including no suffix) -> idle."""
    s = suffix.lower()
    if "awaiting" in s:
        return "question"
    if any(w in s for w in RUNNING_WORDS):
        return "running"
    return "idle"


class ChatSideTracker:
    """The state machine, separated from UI reading so it can be tested with
    scripted observations. feed() takes one poll's observation:
      chat_visible:  is the Chat mode selected (sidebar shows chats)?
      sessions:      {title: 'running' | 'question' | 'idle'} from the sidebar
                     (only meaningful when chat_visible)
      radio:         the Chat mode switcher's status ('running'/'question'/'idle')
      thinking:      a Thinking element is in the streaming message
    and returns the event flags for that tick."""

    def __init__(self):
        self.state = "idle"
        self.thinking = False
        self.seen_running = set()       # chats seen running while watched
        self.stale_question = False     # an ignored (pre-existing) question is pending
        self.last_radio = "idle"

    def baseline(self, obs):
        """Called once on connect: whatever is pending now is history."""
        self.state = "idle"
        self.thinking = False
        self.seen_running = {t for t, s in obs["sessions"].items() if s == "running"}
        self.last_radio = obs["radio"]
        if obs["chat_visible"]:
            self.stale_question = any(s == "question" for s in obs["sessions"].values())
        else:
            self.stale_question = obs["radio"] == "question"
        # Something already running when we connect is a real, live turn.
        if self.seen_running or obs["radio"] == "running":
            self.state = "running"

    def _area_state(self, obs) -> str:
        if obs["chat_visible"]:
            # Exact, per chat.
            for title, s in obs["sessions"].items():
                if s == "running":
                    self.seen_running.add(title)
                elif s == "idle":
                    self.seen_running.discard(title)
            q = [t for t, s in obs["sessions"].items() if s == "question"]
            self.stale_question = any(t not in self.seen_running for t in q)
            if any(t in self.seen_running for t in q):
                return "question"
            if any(s == "running" for s in obs["sessions"].values()):
                return "running"
            return "idle"

        # Code tab showing: only the switcher's aggregate label is visible.
        r = obs["radio"]
        if r == "running":
            return "running"
        if r == "question":
            if self.state == "question":
                return "question"
            if self.state == "running":
                if not self.stale_question:
                    return "question"       # unambiguous: this run asked something
                if self.last_radio == "running":
                    return "idle"           # saw the run end; label fell back to the old question
                return "running"            # label never showed this run: hold, don't guess
            return "idle"                   # an old question, nothing running
        self.stale_question = False         # label clear: no question pending anywhere
        return "idle"

    def feed(self, obs) -> dict:
        result = dict(EMPTY_RESULT)
        new = self._area_state(obs)
        self.last_radio = obs["radio"]
        old = self.state

        if new != old:
            if old == "idle" and new == "running":
                result["prompt_sent"] = True
            elif old == "idle" and new == "question":
                result["prompt_sent"] = True      # ran and asked between polls
                result["question_started"] = True
            elif old == "running" and new == "question":
                result["question_started"] = True
            elif old == "question" and new == "running":
                result["answer_received"] = True
            elif new == "idle":
                result["finished"] = True
            self.state = new

        thinking = obs.get("thinking", False) and self.state == "running"
        if thinking and not self.thinking:
            result["thinking_started"] = True
        elif self.thinking and not thinking and self.state == "running":
            result["thinking_ended"] = True
        self.thinking = thinking
        return result


class ClaudeDesktopChatBackend(ChatBackend):
    name = "claude_desktop"

    def __init__(self):
        self.win = None          # resolved wrapper -- calls on it fail fast once it's gone
        self.tracker = ChatSideTracker()
        self.failures = 0

    # -- ChatBackend interface --------------------------------------------

    def connect(self):
        attempt = 0
        while not self.try_connect():
            attempt += 1
            if attempt == 1 or attempt % 12 == 0:
                print("Waiting for the Claude desktop app...", file=sys.stderr)
            time.sleep(5)
        print("Watching Claude desktop Chat (app status labels).")

    def try_connect(self) -> bool:
        try:
            spec = Desktop(backend="uia").window(title=WINDOW_TITLE)
            # Never force-resolve an unresolved spec: when the app isn't
            # running that blocks ~5s (pywinauto's default find-timeout), and
            # multi-app mode calls this every 5s -- see claude_desktop_backend.
            if not spec.exists(timeout=0.2, retry_interval=0.1):
                return False
            win = spec.wrapper_object()
            obs = self._observe(win)
            if obs is None:  # no Chat mode switcher -- not the app we expect
                return False
        except Exception:
            return False
        self.win = win
        self.tracker = ChatSideTracker()
        self.tracker.baseline(obs)
        self.failures = 0
        return True

    def check_alive(self):
        self.win.window_text()  # a resolved wrapper raises at once if the window is gone

    def poll(self, debug: bool = False) -> dict:
        try:
            obs = self._observe(self.win, debug=debug)
            if obs is None:
                raise RuntimeError("Chat mode switcher not found")
            self.failures = 0
        except Exception as e:
            self.failures += 1
            if debug:
                print(f"[debug] claude_desktop: observe failed ({self.failures}): {e}")
            if self.failures >= LIVENESS_FAILURE_LIMIT:
                raise RuntimeError("Claude desktop app unreachable for too long -- reconnecting.")
            return dict(EMPTY_RESULT)

        before = self.tracker.state
        result = self.tracker.feed(obs)
        if debug:
            busy = {t: s for t, s in obs["sessions"].items() if s != "idle"}
            print(f"[debug] claude_desktop: chat_visible={obs['chat_visible']} radio={obs['radio_raw']!r} "
                  f"non-idle chats={busy} thinking={obs['thinking']} state {before}->{self.tracker.state}")
            if obs["streaming_labels"]:
                print(f"[debug] claude_desktop: streaming message elements: {obs['streaming_labels']}")
        return result

    # -- reading the app -----------------------------------------------------

    def _observe(self, win, debug: bool = False):
        """One poll's observation of the Chat side, or None if the window
        doesn't look like the Chat/Cowork/Code desktop app."""
        chat_radio = None
        for rb in win.descendants(control_type="RadioButton"):
            if rb.window_text().startswith(CHAT_MODE_PREFIX):
                chat_radio = rb
                break
        if chat_radio is None:
            return None
        raw = chat_radio.window_text()
        suffix = raw.split(", ", 1)[1] if ", " in raw else ""
        try:
            chat_visible = bool(chat_radio.is_selected())
        except Exception:
            chat_visible = False

        sessions = {}
        thinking = False
        streaming_labels = []
        if chat_visible:
            for b in win.descendants(control_type="Button"):
                m = SESSION_RE.match(b.window_text())
                if m:
                    status = STATUS_FROM_SIDEBAR[m.group(1)]
                    title = m.group(2)
                    if STATUS_RANK[status] >= STATUS_RANK[sessions.get(title, "idle")]:
                        sessions[title] = status
            if any(s == "running" for s in sessions.values()):
                for g in win.descendants(control_type="Group"):
                    if g.window_text() == STREAMING_GROUP:
                        for c in g.descendants():
                            if c.element_info.control_type == "Text":
                                continue  # never read message text
                            name = c.window_text()
                            if name:
                                if debug:
                                    streaming_labels.append(f"{c.element_info.control_type}:{name[:40]}")
                                if name.startswith(THINKING_PREFIX):
                                    thinking = True
                        break

        return {
            "chat_visible": chat_visible,
            "sessions": sessions,
            "radio": radio_status(suffix),
            "radio_raw": raw,
            "thinking": thinking,
            "streaming_labels": streaming_labels,
        }
