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

# --- Claude Code: StopFailure through the backend ----------------------------
import claude_code_backend as ccb

tmp = tempfile.mkdtemp()
ccb.STATUS_FILE = os.path.join(tmp, "status.json")
stamp = [time.time()]


def hook(event, **fields):
    """Write the status file the way the hook does, with a newer mtime."""
    stamp[0] += 1
    with open(ccb.STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({"event": event, "host": "test", "written_at": time.time(), **fields}, f)
    os.utime(ccb.STATUS_FILE, (stamp[0], stamp[0]))


later = (dt.datetime.now() + dt.timedelta(hours=2)).strftime("%I:%M%p").lstrip("0").lower()
hook("SessionStart")
b = ccb.ClaudeCodeBackend()
check("connects", b.try_connect(), True)
hook("UserPromptSubmit")
r = b.poll()
check("prompt -> analyzing, not limited", (r["prompt_sent"], r["limited"]), (True, False))
hook("StopFailure", error="rate_limit", last_assistant_message=f"You've hit your 5-hour limit · resets {later}")
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
r = b.poll()
check("billing_error -> limited (fallback hold)", r["limited"], True)
b.limited_until = dt.datetime.now() - dt.timedelta(seconds=1)
check("hold expires", b.poll()["limited"], False)

# A limit still in force when the watcher starts is picked up.
hook("StopFailure", error="rate_limit", last_assistant_message=f"You've hit your weekly limit · resets {later}")
b2 = ccb.ClaudeCodeBackend()
b2.try_connect()
check("limit picked up on connect", b2.poll()["limited"], True)
# ...but not one that has already reset.
past = (dt.datetime.now() - dt.timedelta(hours=1))
with open(ccb.STATUS_FILE, "w", encoding="utf-8") as f:
    json.dump({"event": "StopFailure", "error": "rate_limit", "written_at": (past - dt.timedelta(hours=6)).timestamp(),
               "last_assistant_message": "You've hit your 5-hour limit"}, f)
b3 = ccb.ClaudeCodeBackend()
b3.try_connect()
check("expired limit ignored on connect", b3.poll()["limited"], False)

print(f"\n{'All usage-limit checks passed.' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
