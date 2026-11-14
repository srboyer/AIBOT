"""
level_tracker.py

Most sources can say what they're doing RIGHT NOW -- "idle", "running",
"thinking", "question" -- rather than reporting the moments things change
(the browser extension, Codex's session log, a desktop app's Stop button,
several Claude Code sessions taken together). LevelTracker turns that
current level into the one-tick event flags chat_watcher.py's BackendSlot
expects (see chat_backend.py), so every such backend shares the same
transition rules:

    idle -> running/thinking      prompt_sent (+ thinking_started)
    running <-> thinking          thinking_started / thinking_ended
    * -> question                 question_started
    question -> running/thinking  answer_received
    busy -> idle                  finished, or failed when the source says
                                  the turn ended on an error

A question that goes quiet is held for ANSWER_GRACE seconds first: right
after you answer, many apps pass through idle for a moment before they
carry on, and that blip shouldn't flash "done".
"""

import time

LEVELS = ("idle", "running", "thinking", "question")
ANSWER_GRACE = 4.0

EMPTY_RESULT = {
    "prompt_sent": False,
    "question_started": False,
    "answer_received": False,
    "thinking_started": False,
    "thinking_ended": False,
    "finished": False,
}

# For combining several sources (tabs, sessions) into one: the most active wins.
LEVEL_RANK = {"idle": 0, "running": 1, "thinking": 2, "question": 3}


def combine(levels):
    return max(levels, key=lambda l: LEVEL_RANK.get(l, 0), default="idle")


class LevelTracker:
    def __init__(self, level: str = "idle"):
        self.level = level if level in LEVELS else "idle"
        self.quiet_since = None

    def baseline(self, level: str):
        """On connect: a turn already running is live -- start from idle, so
        the first feed() reports it and it shows straight away; a question
        already waiting is history (it was asked before we were watching), so
        it's taken as the starting point and never announced."""
        self.level = "question" if level == "question" else "idle"
        self.quiet_since = None

    def feed(self, level: str, failed: bool = False, now: float = None) -> dict:
        now = time.monotonic() if now is None else now
        level = level if level in LEVELS else "idle"
        r = dict(EMPTY_RESULT)
        old = self.level

        if old == "question" and level == "idle" and not failed:
            if self.quiet_since is None:
                self.quiet_since = now
            if now - self.quiet_since < ANSWER_GRACE:
                return r
        self.quiet_since = None

        if level == old:
            return r
        busy = ("running", "thinking")
        if old == "idle":
            if level in busy or level == "question":
                r["prompt_sent"] = True
            if level == "thinking":
                r["thinking_started"] = True
            if level == "question":
                r["question_started"] = True
        elif level == "idle":
            r["failed" if failed else "finished"] = True
        elif level == "question":
            r["question_started"] = True
        elif old == "question":
            r["answer_received"] = True
            if level == "thinking":
                r["thinking_started"] = True
        elif level == "thinking":
            r["thinking_started"] = True
        else:  # thinking -> running
            r["thinking_ended"] = True
        self.level = level
        return r
