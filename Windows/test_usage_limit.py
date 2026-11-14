"""
test_usage_limit.py

Offline checks for the out-of-tokens detection (usage_limit.py) and Claude
Code's StopFailure handling (claude_code_backend.py). No pywinauto, apps or
hardware needed.

Run: py -3.11 test_usage_limit.py
"""

import datetime as dt
import json
import os
import tempfile
import time

import usage_limit as ul

failures = 0


def check(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {got!r}" + ("" if ok else f" (wanted {want!r})"))


# --- is_limit_error: which Claude Code StopFailures mean "out of usage" ----
check("plan limit", ul.is_limit_error("rate_limit", "You've hit your 5-hour limit · resets 3pm"), True)
check("out of usage credits", ul.is_limit_error("rate_limit", "You're out of usage credits · resets 9am"), True)
check("API credit", ul.is_limit_error("billing_error", "Credit balance is too low"), True)
check("throttle, not usage", ul.is_limit_error("rate_limit", "Server is temporarily limiting requests (not your usage limit)"), False)
check("model under load", ul.is_limit_error("rate_limit", "Opus is experiencing high load, please use /model to switch to Sonnet"), False)
check("overloaded", ul.is_limit_error("overloaded", "Overloaded"), False)
check("server error", ul.is_limit_error("server_error", ""), False)

# --- parse_reset ------------------------------------------------------------
now = dt.datetime(2026, 11, 12, 13, 20)  # a Thursday, 1:20pm
check("resets 3pm", ul.parse_reset("You've hit your 5-hour limit · resets 3pm", now), dt.datetime(2026, 11, 12, 15, 0))
check("resets 3:30pm (tz)", ul.parse_reset("limit · resets 3:30pm (America/New_York)", now), dt.datetime(2026, 11, 12, 15, 30))
check("resets 9am -> tomorrow", ul.parse_reset("resets 9am", now), dt.datetime(2026, 11, 13, 9, 0))
check("resets Nov 14, 9am", ul.parse_reset("weekly limit · resets Nov 14, 9am", now), dt.datetime(2026, 11, 14, 9, 0))
check("resets Jan 2 at 10am", ul.parse_reset("resets Jan 2 at 10am", dt.datetime(2026, 12, 30, 8, 0)), dt.datetime(2027, 1, 2, 10, 0))
check("resets Mon 9am", ul.parse_reset("resets Mon 9am", now), dt.datetime(2026, 11, 16, 9, 0))
check("resets in 2h 15m", ul.parse_reset("resets in 2h 15m", now), now + dt.timedelta(hours=2, minutes=15))
check("until 4:12 PM", ul.parse_reset("Responses will use another model until your limit resets after 4:12 PM.", now), dt.datetime(2026, 11, 12, 16, 12))
check("24h 17:45", ul.parse_reset("resets 17:45", now), dt.datetime(2026, 11, 12, 17, 45))
check("no time", ul.parse_reset("Credit balance is too low", now), None)
check("bare number", ul.parse_reset("resets 5", now), None)

# --- banner wording -----------------------------------------------------------
for text, want in [
    ("You've hit your limit · Resets 3:00 PM", True),
    ("You’ve hit your 5-hour limit", True),
    ("You've hit the Free plan limit for GPT-5.", True),
    ("You've reached your usage limit. Your limit will reset at 3:00 PM.", True),
    ("You're out of free messages until 5:00 PM", True),
    ("Out of free messages until 5 PM", True),
    ("Usage limit reached", True),
    ("Claude usage limit reached. Your limit will reset at 3pm.", True),
    ("Credit balance is too low", True),
    ("What does the 5-hour limit mean?", False),            # a message about limits
    ("Set a spending limit", False),
    ("x" * 250 + " usage limit reached", False),            # too long: a message, not a banner
]:
    check(f"banner? {text[:40]!r}", ul.looks_like_limit_banner(text), want)

# --- LimitBannerWatch: scan cadence and when to trust "not found" --------------
texts = []
ul._candidate_texts = lambda root: list(texts)
w = ul.LimitBannerWatch(every=1000)
root = object()
check("idle, no banner", w.check(lambda: root, busy=False), False)
texts[:] = ["You've hit your limit · Resets 3:00 PM"]
check("not rescanned before due", w.check(lambda: root, busy=False), False)
w.check(lambda: root, busy=True)                     # a turn runs...
check("turn ends -> scans at once", w.check(lambda: root, busy=False), True)
w.next_scan = 0
check("page hidden keeps last answer", w.check(lambda: None, busy=False), True)
texts[:] = []
w.next_scan = 0
check("banner gone", w.check(lambda: root, busy=False), False)

# --- Claude Code: per-session files, pushes, StopFailure -----------------------
import claude_code_backend as ccb
import event_server

tmp = tempfile.mkdtemp()
ccb.SESSIONS_DIR = os.path.join(tmp, "sessions")
ccb.STATUS_FILE = os.path.join(tmp, "status.json")
os.makedirs(ccb.SESSIONS_DIR)
clock = [time.time() - 100]


def hook(event, sid="s1", push=False, **fields):
    """What the hook does: a status with a fresh written_at, to the session's
    file (newer mtime) -- or pushed to the event server instead."""
    clock[0] += 1
    status = {"event": event, "host": "test", "session_id": sid, "written_at": clock[0], **fields}
    if push:
        event_server.hub.push_event("claude_code", status)
        return status
    path = os.path.join(ccb.SESSIONS_DIR, sid + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(status, f)
    os.utime(path, (clock[0], clock[0]))
    return status


later = (dt.datetime.now() + dt.timedelta(hours=2)).strftime("%I:%M%p").lstrip("0").lower()
b = ccb.ClaudeCodeBackend()
check("connects", b.try_connect(), True)
hook("UserPromptSubmit")
r = b.poll()
check("prompt -> analyzing, not limited", (r["prompt_sent"], r["limited"]), (True, False))
hook("StopFailure", error="rate_limit", last_assistant_message=f"You've hit your 5-hour limit \u00b7 resets {later}")
r = b.poll()
check("StopFailure(limit) -> failed + limited", (r["failed"], r["finished"], r["limited"]), (True, False, True))
check("reset time read", b.limited_until.strftime("%H:%M"),
      (dt.datetime.now() + dt.timedelta(hours=2)).strftime("%H:%M"))
check("still limited next tick", b.poll()["limited"], True)
hook("UserPromptSubmit")
check("new prompt: still limited until it gets through", b.poll()["limited"], True)
hook("Stop")
r = b.poll()
check("a normal Stop clears it", (r["finished"], r["limited"]), (True, False))

hook("UserPromptSubmit"); b.poll()
hook("StopFailure", error="overloaded", last_assistant_message="Overloaded")
r = b.poll()
check("other API error: failed, not limited", (r["failed"], r["limited"]), (True, False))
hook("StopFailure", error="billing_error", last_assistant_message="Credit balance is too low")
check("billing_error -> limited (fallback hold)", b.poll()["limited"], True)
b.limited_until = dt.datetime.now() - dt.timedelta(seconds=1)
check("hold expires", b.poll()["limited"], False)

# Two sessions at once: the most active wins, and one finishing while the
# other works is not "done".
hook("UserPromptSubmit", sid="s1")
check("s1 starts", b.poll()["prompt_sent"], True)
hook("Notification", sid="s2", notification_type="permission_prompt")
check("s2 asks -> question", b.poll()["question_started"], True)
hook("PostToolUse", sid="s2", tool_name="AskUserQuestion")
check("s2 answered -> back to working", b.poll()["answer_received"], True)
hook("Stop", sid="s1")
check("s1 done while s2 works: no DONE", b.poll()["finished"], False)
hook("Stop", sid="s2")
check("both done -> DONE", b.poll()["finished"], True)

# Pushed events: handled at once, and the same event read again from the file
# afterwards is not handled twice.
s = hook("UserPromptSubmit", sid="s3", push=True)
check("pushed prompt -> analyzing", b.poll()["prompt_sent"], True)
with open(os.path.join(ccb.SESSIONS_DIR, "s3.json"), "w", encoding="utf-8") as f:
    json.dump(s, f)
check("same event from the file: ignored", any(b.poll()[k] for k in ("prompt_sent", "finished")), False)
hook("PreToolUse", sid="s3", push=True)
hook("PostToolUse", sid="s3", push=True)
hook("Stop", sid="s3", push=True)
r = b.poll()
check("three quick pushes between polls: none lost", (r["finished"], b.tracker.level), (True, "idle"))
hook("SessionEnd", sid="s3", push=True)
b.poll()
check("SessionEnd forgets the session", "s3" in b.sessions, False)

# A limit still in force when the watcher starts is picked up...
hook("StopFailure", sid="s4", error="rate_limit", last_assistant_message=f"You've hit your weekly limit \u00b7 resets {later}")
b2 = ccb.ClaudeCodeBackend()
b2.try_connect()
check("limit picked up on connect", b2.poll()["limited"], True)
# ...but not one that has already reset.
past = dt.datetime.now() - dt.timedelta(hours=7)
with open(os.path.join(ccb.SESSIONS_DIR, "s4.json"), "w", encoding="utf-8") as f:
    json.dump({"event": "StopFailure", "session_id": "s4", "error": "rate_limit", "written_at": past.timestamp(),
               "last_assistant_message": "You've hit your 5-hour limit"}, f)
b3 = ccb.ClaudeCodeBackend()
b3.try_connect()
check("expired limit ignored on connect", b3.poll()["limited"], False)

print(f"\n{'All usage-limit checks passed.' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
