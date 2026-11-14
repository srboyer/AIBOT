"""
codex_backend.py

ChatBackend for OpenAI Codex -- the Codex desktop app and the codex CLI both
write every session to a JSONL log under ~/.codex/sessions/YYYY/MM/DD/
(CODEX_HOME moves it). This follows the logs of recent sessions as they
grow; no UI automation, no setup.

What the log says (observed in real logs on this machine):
    event_msg task_started             a turn began          -> running
    response_item reasoning            it's reasoning        -> thinking
    response_item function_call / custom_tool_call / message ... -> running
    event_msg task_complete            the turn finished     -> idle (DONE)
    event_msg turn_aborted             you stopped it        -> idle, no DONE flash
    event_msg *_approval_request,
              request_user_input       it's asking you       -> question
                                       (names from Codex's protocol; not yet
                                        seen in a log here)
    response_item function_call        Codex asked you something and carries
      named request_user_input_async   on working (seen 2026-11-13): the
                                       question stays up in the app until you
                                       answer -> question, even mid-turn and
                                       after the turn ends
    response_item message, role user,  your answer: names the question's call
      "<send_user_message_question_    id -> that question is done. A prompt
       reply>..."                      you type instead also closes the
                                       questions asked before it.
    event_msg token_count              carries rate_limits: used_percent and
                                       resets_at for the 5-hour ("primary")
                                       and weekly ("secondary") windows, and
                                       rate_limit_reached_type once one runs
                                       out -> OUT OF TOKENS until resets_at.
                                       One record per limit ("limit_id": "codex",
                                       "premium", ...); each is kept separately.
    event_msg task_complete with       how running out actually shows (seen
      error.codex_error_info           2026-11-13: the last token_count said
      "usage_limit_exceeded"           97%): "You've hit your usage limit ...
                                       try again at 11:20 PM." -> OUT OF TOKENS
                                       until then (no DONE flash), or until a
                                       later turn completes without an error.

Several sessions at once count together (most active wins). Only message
TYPES are read -- never your prompts or Codex's replies; the one exception
is the question ids in an answer, and whether a message of yours is a
typed prompt (doesn't start with "<") or one of Codex's tagged ones.

A helper agent's log starts with a copy of its parent's conversation --
questions included -- while your answer lands in the parent's log, so
questions are tracked by call id across all logs (_Questions).
"""

import datetime as _dt
import json
import os
import re
import time

from chat_backend import ChatBackend
from level_tracker import LevelTracker, combine
from usage_limit import FALLBACK_HOLD, looks_like_limit_banner, parse_reset

ACTIVE_WITHIN = 3 * 3600      # follow sessions whose log changed this recently (seconds)
RESCAN_EVERY = 3.0            # look for new session logs this often (seconds)
STALE_BUSY = 30 * 60          # a session busy this long with no new lines is assumed dead
QUESTION_EVENTS = ("exec_approval_request", "apply_patch_approval_request", "request_user_input",
                   "elicitation_request")
QUESTION_TOOLS = ("request_user_input_async", "request_user_input")  # function_call names
QUESTION_REPLY_TAG = "<send_user_message_question_reply>"
QUESTION_MAX_AGE = 3 * 3600   # an unanswered question this old is let go (seconds)
_CALL_ID_RE = re.compile(r"call_[A-Za-z0-9]+")


def codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def _when(e) -> float:
    """A log line's timestamp as epoch seconds (now, if it has none)."""
    try:
        return _dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


def _text(p) -> str:
    content = p.get("content")
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ""


class _Questions:
    """Questions Codex asked that you haven't answered, by call id, shared by
    every log (see the module docstring)."""

    def __init__(self):
        self.asked = {}        # call id -> when first asked (epoch)
        self.answered = set()

    def ask(self, call_id, when):
        if call_id and call_id not in self.answered:
            self.asked[call_id] = min(when, self.asked.get(call_id, when))

    def close(self, call_ids):
        for c in call_ids:
            self.answered.add(c)
            self.asked.pop(c, None)

    def pending(self, now=None) -> bool:
        now = now or time.time()
        return any(now - t < QUESTION_MAX_AGE for t in self.asked.values())


class _Session:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.level = "idle"
        self.ended = None          # "finished" / "failed" since the last poll
        self.last_line_at = time.time()
        self.partial = b""
        self.asked_here = set()    # questions asked in this log since your last prompt here
        self.questions = _Questions()

    def read_new(self, limits, questions=None):
        """Read lines appended since last time; update level. `limits` collects
        the newest rate_limits seen, `questions` the open questions (both
        shared across sessions)."""
        if questions is not None:
            self.questions = questions
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.offset:  # rewritten (compaction): start over
            self.offset, self.partial = 0, b""
        if size == self.offset:
            return
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = self.partial + f.read(size - self.offset)
            self.offset = size
        lines = data.split(b"\n")
        self.partial = lines.pop()  # an unfinished last line waits for the rest
        for raw in lines:
            if raw.strip():
                self._line(raw, limits)
        self.last_line_at = time.time()

    def _line(self, raw, limits):
        try:
            e = json.loads(raw)
        except Exception:
            return
        kind = e.get("type")
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        t = p.get("type")
        if kind == "event_msg":
            if t == "task_started":
                self.level = "running"
            elif t in ("task_complete", "turn_aborted"):
                err = p.get("error")
                if self.level != "idle":
                    self.ended = "finished" if t == "task_complete" and not err else "failed"
                self.level = "idle"
                _note_limit_error(limits, err, _when(e), t == "task_complete", p.get("started_at"))
            elif t in QUESTION_EVENTS and self.level != "idle":
                self.level = "question"
            elif t == "token_count" and isinstance(p.get("rate_limits"), dict):
                stamp = e.get("timestamp") or ""
                by_id = limits.setdefault("by_id", {})
                key = p["rate_limits"].get("limit_id") or "codex"
                if stamp >= by_id.get(key, ("", None))[0]:  # ISO times sort as text
                    by_id[key] = (stamp, p["rate_limits"])
        elif kind == "response_item":
            if t == "function_call" and p.get("name") in QUESTION_TOOLS:
                self.questions.ask(p.get("call_id"), _when(e))
                self.asked_here.add(p.get("call_id"))
            elif t == "message" and p.get("role") == "user":
                text = _text(p).lstrip()
                if text.startswith(QUESTION_REPLY_TAG):
                    self.questions.close(_CALL_ID_RE.findall(text))
                elif text and not text.startswith("<"):
                    # A prompt typed instead of answering: what was asked before it is done.
                    self.questions.close(self.asked_here)
                    self.asked_here = set()
            if self.level != "idle":
                self.level = "thinking" if t == "reasoning" else "running"


def is_limit_error(err) -> bool:
    """A turn's error that means your usage ran out (not a crash or a
    network blip)."""
    if not isinstance(err, dict):
        return False
    info = err.get("codex_error_info")
    info = info if isinstance(info, str) else json.dumps(info or "")
    return "usage_limit" in info or looks_like_limit_banner((err.get("message") or "")[:200])


def _note_limit_error(limits, err, when, completed, started_at=None):
    """Record (or clear) the limit a turn ended on. A turn that STARTED
    after it and completes without an error means there's usage again --
    not one that was already under way, like a helper agent finishing the
    task it had when the limit hit (seen 7 s after the error, 2026-11-13)."""
    if is_limit_error(err):
        start = _dt.datetime.fromtimestamp(when)
        until = parse_reset(err.get("message") or "", now=start)
        if until is None:  # no time given: the 5-hour window's reset, else a fixed hold
            resets = [w.get("resets_at") or 0 for _s, rl in limits.get("by_id", {}).values()
                      for w in (rl.get("primary"),) if isinstance(w, dict)]
            later = [r for r in resets if r > when]
            until_ts = min(later) if later else (start + FALLBACK_HOLD).timestamp()
        else:
            until_ts = until.timestamp()
        limits["error"] = (when, until_ts)
    elif completed and not err and limits.get("error") and isinstance(started_at, (int, float))             and started_at > limits["error"][0]:
        limits["error"] = None


def codex_limited(limits, now=None):
    """(limited, resets_at epoch or None) from everything the logs said: a
    turn that ended on the limit, or any limit's windows at 100%."""
    now = now or time.time()
    error = limits.get("error")
    if error and error[1] > now:
        return True, error[1]
    found = [limit_state(rl, now) for _stamp, rl in limits.get("by_id", {}).values()]
    hits = [r for limited, r in found if limited]
    if hits:
        return True, max((r for r in hits if r), default=None)
    return False, None


def limit_state(rate_limits, now=None):
    """(limited, resets_at epoch or None) from a token_count's rate_limits."""
    now = now or time.time()
    if not isinstance(rate_limits, dict):
        return False, None
    windows = [w for w in (rate_limits.get("primary"), rate_limits.get("secondary")) if isinstance(w, dict)]
    full = [w for w in windows if (w.get("used_percent") or 0) >= 100]
    if not full and rate_limits.get("rate_limit_reached_type"):
        full = windows  # reached, but it didn't say which window: wait for the later reset
    if not full:
        return False, None
    resets = max((w.get("resets_at") or 0 for w in full), default=0) or None
    if resets and resets <= now:
        return False, None  # the log is older than the reset
    return True, resets


class CodexBackend(ChatBackend):
    name = "codex"
    app_id = "codex"
    # The Codex app is ChatGPT.exe on Windows (package OpenAI.Codex, seen
    # 2026-11-13); Codex.exe in case a standalone build ships.
    focus_target = {"process": ["ChatGPT.exe", "Codex.exe"], "mac_app": "Codex"}

    def __init__(self):
        self.sessions = {}
        self.tracker = LevelTracker()
        self.limits = {}
        self.questions = _Questions()
        self.next_scan = 0.0

    def _scan(self):
        root = os.path.join(codex_home(), "sessions")
        if not os.path.isdir(root):
            return False
        cutoff = time.time() - ACTIVE_WITHIN
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".jsonl"):
                    p = os.path.join(dirpath, f)
                    try:
                        if os.path.getmtime(p) >= cutoff and p not in self.sessions:
                            self.sessions[p] = _Session(p)
                    except OSError:
                        pass
        return True

    def try_connect(self) -> bool:
        if not os.path.isdir(os.path.join(codex_home(), "sessions")):
            return False
        self.sessions = {}
        self.questions = _Questions()
        self.limits = {}
        self._scan()
        for s in self.sessions.values():
            s.read_new(self.limits, self.questions)
            s.ended = None
        self.tracker = LevelTracker()
        # Unlike a question read off a window, an open Codex question is
        # known to still be open (answers are in the log), so one already
        # waiting when MAICA starts is shown too -- baseline idle, not it.
        self.tracker.baseline("idle")
        return True

    def _level(self) -> str:
        levels = [s.level for s in self.sessions.values()]
        if self.questions.pending():
            levels.append("question")
        return combine(levels)

    def connect(self):
        while not self.try_connect():
            time.sleep(5)

    def check_alive(self):
        pass

    def poll(self, debug: bool = False) -> dict:
        now = time.monotonic()
        if now >= self.next_scan:
            self.next_scan = now + RESCAN_EVERY
            self._scan()
        failed = finished = False
        for path, s in list(self.sessions.items()):
            s.read_new(self.limits, self.questions)
            if s.level != "idle" and time.time() - s.last_line_at > STALE_BUSY:
                s.level = "idle"
            if s.ended == "failed":
                failed = True
            elif s.ended == "finished":
                finished = True
            s.ended = None
            if s.level == "idle" and time.time() - s.last_line_at > ACTIVE_WITHIN:
                del self.sessions[path]
        level = self._level()
        result = self.tracker.feed(level, failed=failed and not finished)
        limited, resets = codex_limited(self.limits)
        result["limited"] = limited
        if debug:
            print(f"[debug] codex: sessions={len(self.sessions)} level={level} limited={limited}")
        return result
