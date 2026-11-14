"""
claude_code_backend.py

ChatBackend for Claude Code, the CLI/agent -- whatever editor, terminal or
app it runs in (VS Code, JetBrains, Cursor, the Claude desktop app's Code
tab, a bare terminal...). No UI automation: Claude Code's own hooks run
claude_code_status_hook.py on every event (registered in
~/.claude/settings.json by claude_code_hook_installer.py), which

  - pushes the event to the running watcher (event_server.py) -- the face
    reacts within milliseconds, and no event is lost however fast they come;
  - writes it to the session's own file in SESSIONS_DIR, which this backend
    reads on startup and as a fallback if a push was missed.

SESSIONS -- every Claude Code session is tracked separately (it used to be
one shared status file, so two sessions overwrote each other) and they're
combined, the most active winning: one session asking you something shows
QUESTION even while another is working.

STATE MAPPING, per session (hook event -> level):
    UserPromptSubmit           running     (ANALYZING)
    PreToolUse                 thinking    (a tool call in flight -- the
                               closest available proxy for "working hard";
                               hooks don't fire around plain reasoning)
    PostToolUse                running     (the tool call is over, whether it
                               succeeded, errored or was rejected -- so a
                               rejected tool self-heals instead of sticking)
    Notification               question    (QUESTION) -- except
                               notification_type "idle_prompt", the "still
                               waiting on you" nudge after ~60s idle, which is
                               NOT a question: mid-turn it means the turn
                               ended without a Stop (Escape mid-reply), so ->
                               idle (DONE)
    Stop                       idle        (DONE)
    StopFailure                idle, no DONE flash -- an API error ended the
                               turn (Claude Code fires it INSTEAD of Stop);
                               plus OUT OF TOKENS when the error means the
                               usage limit ran out (see below)
    SessionEnd                 the session is gone

QUESTION -> answered: no hook says "permission granted", so a question holds
until real progress (UserPromptSubmit / PreToolUse) or the AskUserQuestion
tool's own PostToolUse (that IS the answer arriving), or a Stop ends the turn.

STALE: a session busy with no event for STALE_AFTER_SECONDS (30 min -- far
longer than any real silent stretch; Bash tool calls cap at 10) is assumed
dead and goes idle. (A blind 90s timer used to cut genuine long tool calls
short.)

OUT OF TOKENS -- a StopFailure whose error is "rate_limit" (a plan's 5-hour
or weekly limit; Claude Code retries plain throttling itself) or
"billing_error" (an API key out of credit) puts Claude Code in LIMIT (see
usage_limit.is_limit_error). The limit is the account's, not the session's:
it lasts until the reset time in Claude Code's message ("You've hit your
5-hour limit · resets 3pm"), or usage_limit.FALLBACK_HOLD if there's none,
and ends early the moment any session gets through (a tool call or a normal
Stop). A limit still in force when the watcher starts is picked up from the
session files.
"""

import datetime
import json
import os
import time

import event_server
from chat_backend import ChatBackend
from claude_code_status_shared import SESSIONS_DIR, STATUS_FILE
from level_tracker import LevelTracker, combine
from usage_limit import FALLBACK_HOLD, is_limit_error, parse_reset

STALE_AFTER_SECONDS = 30 * 60.0
QUESTION_TOOL = "AskUserQuestion"   # its PostToolUse means you've answered
PRUNE_ENDED_AFTER = 60.0            # forget a SessionEnd'ed session's file after this (seconds)
PRUNE_ANY_AFTER = 2 * 24 * 3600.0   # ...and any session file this old

# Where a session's host lives, for "done, unread" and tap-to-jump.
HOST_TARGETS = {
    "claude-desktop": {"title": "^Claude$", "process": "Claude.exe", "mac_app": "Claude"},
    "claude-vscode": {"process": "Code.exe", "mac_app": "Code"},
    "vscode": {"process": "Code.exe", "mac_app": "Code"},
    "cursor": {"process": "Cursor.exe", "mac_app": "Cursor"},
    "windsurf": {"process": "Windsurf.exe", "mac_app": "Windsurf"},
    "jetbrains": {"title": "IntelliJ|PyCharm|WebStorm|GoLand|Rider|CLion|PhpStorm|RubyMine"},
    "Apple_Terminal": {"mac_app": "Terminal"},
    "iTerm.app": {"mac_app": "iTerm2"},
    "WezTerm": {"process": "wezterm-gui.exe", "mac_app": "WezTerm"},
}


class _Session:
    def __init__(self, sid):
        self.sid = sid
        self.level = "idle"
        self.awaiting_answer = False
        self.last_event_at = time.time()
        self.last_seq = 0.0          # written_at of the last event handled (dedupes push vs file)
        self.ended = None            # "finished" / "failed" since the last poll
        self.gone = False
        self.host = None

    def handle(self, status, backend):
        seq = status.get("written_at") or 0.0
        if seq and seq <= self.last_seq:
            return  # already seen (it came both pushed and from the file)
        self.last_seq = seq or self.last_seq
        self.last_event_at = time.time()
        self.host = status.get("host") or self.host
        event = status.get("event")
        busy = self.level != "idle"

        if event == "SessionEnd":
            if busy:
                self.ended = "failed"
            self.level, self.awaiting_answer, self.gone = "idle", False, True
            return
        if event == "StopFailure":
            if busy:
                self.ended = "failed"
            self.level, self.awaiting_answer = "idle", False
            backend.note_failure(status)
            return
        if event in ("PreToolUse", "PostToolUse", "Stop"):
            backend.clear_limit()  # the API answered

        if self.awaiting_answer:
            if event in ("UserPromptSubmit", "PreToolUse") or (
                    event == "PostToolUse" and status.get("tool_name") == QUESTION_TOOL):
                self.awaiting_answer = False
                self.level = "thinking" if event == "PreToolUse" else "running"
            elif event == "Stop":
                self.awaiting_answer = False
                self.level, self.ended = "idle", "finished"
            return

        if event == "UserPromptSubmit":
            self.level = "running"
        elif event == "PreToolUse":
            self.level = "thinking"
        elif event == "PostToolUse":
            self.level = "running"
        elif event == "Notification":
            if status.get("notification_type") != "idle_prompt":
                self.level, self.awaiting_answer = "question", True
            elif busy:
                self.level, self.ended = "idle", "finished"
        elif event == "Stop":
            if busy:
                self.ended = "finished"
            self.level = "idle"


class ClaudeCodeBackend(ChatBackend):
    name = "claude_code"
    app_id = "claude_code"

    def __init__(self):
        self.sessions = {}
        self.mtimes = {}
        self.tracker = LevelTracker()
        self.limited = False
        self.limited_until = None

    # -- ChatBackend interface --------------------------------------------

    def try_connect(self) -> bool:
        if not (os.path.isdir(SESSIONS_DIR) or os.path.exists(STATUS_FILE)):
            return False
        self.sessions, self.mtimes = {}, {}
        self.limited, self.limited_until = False, None
        event_server.hub.take_events("claude_code")  # pushes from before we were watching
        for sid, status, mtime, path in self._read_files():
            s = self.sessions.setdefault(sid, _Session(sid))
            self.mtimes[path] = mtime
            # History, not news: a turn that was going (recent progress event)
            # is live; a question already waiting is not ours to announce; a
            # recent StopFailure may mean a limit still in force.
            s.last_seq = status.get("written_at") or 0.0
            s.host = status.get("host")
            age = time.time() - (status.get("written_at") or 0)
            if status.get("event") in ("UserPromptSubmit", "PreToolUse", "PostToolUse") and age < STALE_AFTER_SECONDS:
                s.level = "running"
            if status.get("event") == "StopFailure":
                self.note_failure(status)
        self.tracker = LevelTracker()
        self.tracker.baseline(combine(s.level for s in self.sessions.values()))
        return True

    def connect(self):
        while not self.try_connect():
            time.sleep(5)

    def check_alive(self):
        pass

    def poll(self, debug: bool = False) -> dict:
        # Pushed events first (complete and in order), then any file that
        # changed without a push (the watcher was busy or restarting).
        for status in event_server.hub.take_events("claude_code"):
            self._session(status).handle(status, self)
        for sid, status, mtime, path in self._read_files(changed_only=True):
            self.mtimes[path] = mtime
            self._session(status).handle(status, self)

        failed = finished = False
        now = time.time()
        for sid, s in list(self.sessions.items()):
            if s.level != "idle" and not s.awaiting_answer and now - s.last_event_at > STALE_AFTER_SECONDS:
                s.level, s.ended = "idle", "finished"
            failed |= s.ended == "failed"
            finished |= s.ended == "finished"
            s.ended = None
            if s.gone:
                del self.sessions[sid]

        if self.limited and self.limited_until is not None and datetime.datetime.now() >= self.limited_until:
            self.clear_limit()
        level = combine(s.level for s in self.sessions.values())
        result = self.tracker.feed(level, failed=failed and not finished)
        result["limited"] = self.limited
        if debug:
            print(f"[debug] claude_code: sessions={ {k[:8]: s.level for k, s in self.sessions.items()} } "
                  f"-> {level} limited={self.limited}")
        return result

    # -- limits -------------------------------------------------------------

    def note_failure(self, status):
        """A StopFailure: if its error means the usage limit is used up, go
        LIMIT until the reset time in Claude Code's message (counted from
        when the hook wrote it), or FALLBACK_HOLD if it doesn't say."""
        message = " ".join(t for t in (status.get("last_assistant_message"), status.get("error_details")) if t)
        if not is_limit_error(status.get("error"), message):
            return
        written = status.get("written_at")
        when = datetime.datetime.fromtimestamp(written) if written else datetime.datetime.now()
        until = parse_reset(message, now=when) or when + FALLBACK_HOLD
        if until > datetime.datetime.now():
            self.limited, self.limited_until = True, until

    def clear_limit(self):
        self.limited, self.limited_until = False, None

    # -- for "done, unread" and tap-to-jump ---------------------------------

    @property
    def focus_target(self):
        rank = {"question": 3, "thinking": 2, "running": 1, "idle": 0}
        s = max(self.sessions.values(), key=lambda s: (rank.get(s.level, 0), s.last_event_at), default=None)
        return HOST_TARGETS.get((s.host if s else None) or "", None)

    # -- files ---------------------------------------------------------------

    def _session(self, status):
        sid = str(status.get("session_id") or "unknown")
        return self.sessions.setdefault(sid, _Session(sid))

    def _read_files(self, changed_only=False):
        """(session id, status, mtime, path) for each session file -- or, with no
        session folder yet (hooks from before it existed), the single
        STATUS_FILE as one session."""
        out = []
        now = time.time()
        paths = []
        if os.path.isdir(SESSIONS_DIR):
            paths = [os.path.join(SESSIONS_DIR, f) for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")]
        elif os.path.exists(STATUS_FILE):
            paths = [STATUS_FILE]
        for p in paths:
            try:
                mtime = os.path.getmtime(p)
                sid_key = os.path.splitext(os.path.basename(p))[0] if p != STATUS_FILE else "legacy"
                if changed_only and self.mtimes.get(p) == mtime:
                    continue
                with open(p, encoding="utf-8") as f:
                    status = json.load(f)
            except Exception:
                continue
            ended = status.get("event") == "SessionEnd"
            if (ended and now - mtime > PRUNE_ENDED_AFTER) or now - mtime > PRUNE_ANY_AFTER:
                if p != STATUS_FILE:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                continue
            status.setdefault("session_id", sid_key)
            out.append((str(status.get("session_id")), status, mtime, p))
        return out
