"""
test_platform_sim.py

Offline checks for the cross-platform plumbing: the local event server (as
the browser extension and Claude Code hook use it, over real HTTP), the
browser-site backends, level tracking, "done, unread", celebrations, rim
colours, double-tap-to-jump and the board's event stream. No apps, browser
or hardware needed.

Run: py -3.11 test_platform_sim.py
"""

import json
import sys
import time
import urllib.error
import urllib.request

import chat_watcher as cw
import event_server
import extension_backend as eb
from claude_light_tracker import Light, State
from level_tracker import LevelTracker

failures = 0


def check(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {got!r}" + ("" if ok else f" (wanted {want!r})"))


PORT = 47899
assert event_server.start(PORT)


def post(payload, origin=None, header=True):
    headers = {"Content-Type": "application/json"}
    if header:
        headers["X-MAICA"] = "1"
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/event", data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def tab(key, site, level, **kw):
    return post({"source": "ext", "key": key, "site": site, "level": level, **kw},
                origin="chrome-extension://abcdefghijklmnop")


# --- the server's gatekeeping ----------------------------------------------------
check("extension origin accepted", tab("1", "chatgpt", "idle")[0], 200)
check("a web page can't post", post({"source": "ext", "key": "x", "site": "chatgpt"}, origin="https://evil.example")[0], 403)
check("missing X-MAICA header refused", post({"source": "ext"}, header=False)[0], 403)
check("local process (no Origin) accepted", post({"source": "probe", "key": "k"})[0], 200)

# --- LevelTracker ------------------------------------------------------------------
t = LevelTracker()
r = t.feed("thinking")
check("idle -> thinking: prompt + thinking", (r["prompt_sent"], r["thinking_started"]), (True, True))
r = t.feed("question")
check("-> question", r["question_started"], True)
r = t.feed("idle", now=100.0)
check("question going quiet is held", any(r.values()), False)
r = t.feed("running", now=101.0)
check("...and resuming is an answer, not done + new prompt", (r["answer_received"], r["finished"]), (True, False))
r = t.feed("idle", failed=True)
check("busy -> idle on an error: failed, not finished", (r.get("failed"), r["finished"]), (True, False))

# --- browser sites through the watcher ---------------------------------------------
cw.UNREAD_ENABLED = True
cw.DONE_FLASH_SECONDS = 0.0
gpt = cw.BackendSlot("web_chatgpt")
claude_web = cw.BackendSlot("claude_web") if sys.platform == "win32" else None  # a Windows-only backend
slots = [gpt]

gpt.tick()
check("site with a live tab connects", gpt.connected, True)
tab("1", "chatgpt", "running", focused=False)
gpt.tick()
check("tab running -> ANALYZING", gpt.state, State.ANALYZING)
gpt.turn_started -= 300  # pretend it ran five minutes
tab("1", "chatgpt", "idle", focused=False)
gpt.tick()
check("tab idle -> DONE, with a big celebration", (gpt.state, gpt.cheer), (State.DONE, 2))
gpt.tick()
check("you were elsewhere -> UNREAD after the flash", gpt.state, State.UNREAD)
check("double-tap targets the unread reply", cw.jump_target(slots) is gpt, True)
check("tap-to-jump asks the extension to focus the tab", gpt.bring_to_front(), True)
status, reply = tab("2", "chatgpt", "idle", focused=False)
check("...and the command rides back on a tab's next report", reply["commands"], [{"cmd": "focus", "tab": "1"}])
tab("1", "chatgpt", "idle", focused=True)
gpt.tick()
check("looking at the tab clears UNREAD", gpt.state, State.IDLE)
tab("1", "chatgpt", "idle", limited=True, focused=True)
gpt.tick()
check("limit banner: no purple face, the low battery instead", (gpt.shown_state, cw.face_low([gpt])),
      (State.IDLE, [True]))

# The extension covering claude.ai makes the UI-Automation backend stand down.
tab("9", "claude", "idle")
if claude_web is not None:
    claude_web.connected = True
    claude_web.tick()
    check("claude_web stands down for the extension", (claude_web.connected, claude_web.standing_down), (False, True))

# --- rim colours ----------------------------------------------------------------------
fs = cw.FaceSlots()
gpt.state = State.ANALYZING
check("accent: the busy app's colour", cw.accent_colors(slots, fs, [], State.ANALYZING), ["10A37F"])
gpt.state = State.IDLE
check("accent: nothing while idle", cw.accent_colors(slots, fs, [], State.IDLE), [])

# --- split faces: a third app's question takes a working app's side -----------------------
from types import SimpleNamespace as NS

a, b, c = NS(name="a", state=State.ANALYZING), NS(name="b", state=State.THINKING), NS(name="c", state=State.IDLE)
fs = cw.FaceSlots()
check("two busy: split", fs.update([a, b, c]), [State.ANALYZING, State.THINKING])
c.state = State.ANALYZING
check("a third working app waits", (fs.update([a, b, c]), fs.positions), ([State.ANALYZING, State.THINKING], ["a", "b"]))
c.state = State.QUESTION
check("its question takes the analyzing side", (fs.update([a, b, c]), fs.positions),
      ([State.QUESTION, State.THINKING], ["c", "b"]))
c.state = State.ANALYZING  # answered: it keeps the side, a waits
check("answered: keeps the side", fs.update([a, b, c]), [State.ANALYZING, State.THINKING])
b.state = State.IDLE  # b finishes and rests: a gets that side back
check("displaced app returns when a side frees", (fs.update([a, b, c]), fs.positions),
      ([State.ANALYZING, State.ANALYZING], ["c", "a"]))
a.state, c.state, b.state = State.QUESTION, State.QUESTION, State.QUESTION
check("two questions on screen: a third waits", (fs.update([a, b, c]), fs.positions),
      ([State.QUESTION, State.QUESTION], ["c", "a"]))
a.state, b.state, c.state = State.DONE, State.QUESTION, State.THINKING
check("thinking gives way when nothing's analyzing", (fs.update([a, b, c]), fs.positions),
      ([State.QUESTION, State.DONE], ["b", "a"]))

# --- Codex: questions asked while it keeps working (request_user_input_async) ---------
import datetime as dt
import os
import tempfile

import codex_backend as cb

home = tempfile.mkdtemp()
os.environ["CODEX_HOME"] = home
day = os.path.join(home, "sessions", "2026", "11", "13")
os.makedirs(day)


def stamp(ago=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ev(t, ago=0, **payload):
    return json.dumps({"timestamp": stamp(ago), "type": "event_msg", "payload": {"type": t, **payload}})


def item(t, ago=0, **payload):
    return json.dumps({"timestamp": stamp(ago), "type": "response_item", "payload": {"type": t, **payload}})


def user(text):
    return item("message", role="user", content=[{"type": "input_text", "text": text}])


def ask(call_id, ago=0):
    return item("function_call", ago, name="request_user_input_async", call_id=call_id,
                arguments='{"questions":[{"title":"What size magnet?"}]}')


def write(name, *lines):
    with open(os.path.join(day, name), "a", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))


write("root.jsonl", ev("task_started"), user("Add a magnet pocket"), item("reasoning"))
b = cb.CodexBackend()
b.try_connect()
b.poll()
check("codex: working", b._level(), "thinking")
write("root.jsonl", ask("call_q1"), item("function_call_output", call_id="call_q1", output='{"accepted":true}'),
      item("function_call", name="spawn_agent"))
r = b.poll()
check("codex: async question shows while it keeps working", (b._level(), r["question_started"]), ("question", True))
# A helper agent's log starts with a copy of the conversation, question included.
write("child.jsonl", user("Add a magnet pocket"), ask("call_q1"), ev("task_started"), item("reasoning"))
b.next_scan = 0
b.poll()
write("root.jsonl", ev("task_complete"))
b.poll()
check("codex: still asking after the turn ends", b._level(), "question")
write("root.jsonl", user('<send_user_message_question_reply>\n[{"questionItemId":"[\\"request_user_input_async\\",'
                         '\\"call_q1\\",0]","answer":"10 x 3 mm disc"}]'), ev("task_started"))
b.poll()
check("codex: answered in the parent's log -> the helper's copy closes too", b._level(), "thinking")
write("child.jsonl", ev("task_complete"))
write("root.jsonl", ask("call_q2"), ev("task_complete"))
b.poll()
check("codex: second question", b._level(), "question")
write("root.jsonl", user("Never mind, just make it 10 mm"), ev("task_started"))
b.poll()
check("codex: a typed prompt instead of an answer closes it", b._level(), "running")
write("root.jsonl", ev("task_complete"))
b.poll()

# Starting up: an open question is shown; one asked hours ago and never answered isn't.
write("root.jsonl", ask("call_old", ago=4 * 3600))
b2 = cb.CodexBackend()
b2.try_connect()
check("codex: old unanswered question let go", b2._level(), "idle")
write("root.jsonl", ask("call_q3"))
b3 = cb.CodexBackend()
b3.try_connect()
r = b3.poll()
check("codex: question already open at startup is shown", (b3._level(), r["question_started"]), ("question", True))

# --- Codex: running out shows as a turn ending on a usage-limit error -----------------
def done(ago=0, err=None, started_ago=60):
    payload = {"started_at": time.time() - started_ago}
    if err:
        payload["error"] = {"message": err, "codex_error_info": "usage_limit_exceeded"}
    return ev("task_complete", ago, **payload)


def rate(limit_id, used, resets_in=3600):
    win = {"used_percent": used, "window_minutes": 300, "resets_at": time.time() + resets_in} if used is not None else None
    return ev("token_count", rate_limits={"limit_id": limit_id, "primary": win, "secondary": None,
                                          "rate_limit_reached_type": None})


write("limit.jsonl", ev("task_started"), rate("codex", 97), rate("premium", None),
      done(err="You’ve hit your usage limit. Upgrade to Pro, purchase more credits or try again in 2h."))
write("helper.jsonl", ev("task_started"), item("reasoning"))
b4 = cb.CodexBackend()
b4.try_connect()
r = b4.poll()
check("codex: limit error -> out of tokens (the last reading said 97%)", r["limited"], True)
check("codex: ...until the time it gives", round((b4.limits["error"][1] - time.time()) / 3600, 1), 2.0)
write("helper.jsonl", done(started_ago=120))  # a helper finishing work it began before the limit
check("codex: a helper finishing its old task doesn't clear it", b4.poll()["limited"], True)
write("limit.jsonl", ev("task_started"), done(started_ago=-1))  # a new turn that went through
check("codex: a new turn going through clears it", b4.poll()["limited"], False)
write("limit.jsonl", rate("codex", 100), rate("premium", None))
check("codex: 100% isn't hidden by another limit's empty record", b4.poll()["limited"], True)

# --- Codex: usage from older logs; running low ----------------------------------------
# A limit hit five hours ago, in a log nobody's followed since, is known at start.
old_day = os.path.join(home, "sessions", "2026", "11", "12")
os.makedirs(old_day)
for name in os.listdir(day):
    os.remove(os.path.join(day, name))
old_log = os.path.join(old_day, "yesterday.jsonl")
with open(old_log, "w", encoding="utf-8") as f:
    f.write(ev("task_started", ago=5 * 3600) + "\n" + rate("codex", 60) + "\n" +
            done(ago=5 * 3600, started_ago=5 * 3600 + 30,
                 err="You've hit your usage limit. Upgrade to Pro, purchase more credits or try again in 7h.") + "\n")
os.utime(old_log, (time.time() - 5 * 3600, time.time() - 5 * 3600))
b5 = cb.CodexBackend()
b5.try_connect()
check("codex: an old log isn't followed", len(b5.sessions), 0)
r = b5.poll()
check("codex: out of usage, from the history -> low battery", (r["limited"], r["low"]), (True, True))
check("codex: ...until the time it gave", round((b5.limits["error"][1] - time.time()) / 3600, 1), 2.0)

# The history can arrive in any order: a turn that went through after the error wins.
lim = {}
cb._note_limit_error(lim, None, time.time() - 60, True, started_at=time.time() - 120)
cb._note_limit_error(lim, {"codex_error_info": "usage_limit_exceeded", "message": "try again in 3h"},
                     time.time() - 600, True)
check("codex: an error older than a turn that went through is history", cb.codex_limited(lim)[0], False)
cb._note_limit_error(lim, {"codex_error_info": "usage_limit_exceeded", "message": "no time here"},
                     time.time() - 30, True)
check("codex: a newer error counts", cb.codex_limited(lim)[0], True)

os.remove(old_log)
b6 = cb.CodexBackend()
write("low.jsonl", ev("task_started"), rate("codex", 92), item("reasoning"))
b6.try_connect()
r = b6.poll()
check("codex: 92% used -> low battery", (r["low"], r["limited"]), (True, False))
write("low.jsonl", rate("codex", 92, resets_in=-60))
check("codex: the window reset -> not low", b6.poll()["low"], False)

# Which faces MAICA puts the low battery on.
low = NS(low=True, limited=False)
out = NS(low=False, limited=True)
fine = NS(low=False, limited=False)
check("low battery: running low or used up, not otherwise", cw.face_low([low, out, fine]), [True, True, False])

# --- ChatGPT desktop stands aside while the window shows Codex ------------------------
import chatgpt_desktop_backend as cdb

with open(os.path.join(home, "session_index.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({"id": "a", "thread_name": "Update MAICA front shell mount"}) + "\n")
check("codex thread titles read", cdb.codex_thread_names(), {"Update MAICA front shell mount"})


class FakeChatGPT(cdb.ChatGPTDesktopBackend):
    codex = False
    stop = False

    def __init__(self):
        super().__init__()
        self.limit_watch.check = lambda *a, **k: False

    def _codex_showing(self, debug=False):
        return self.codex

    def _question_present(self):
        return False

    def _button_exists(self, title):
        return self.stop


g = FakeChatGPT()
g.stop = True
check("chatgpt: its own reply counts", g.poll()["prompt_sent"], True)
g.codex = True
r = g.poll()
check("chatgpt: switched to Codex mid-reply -> ends quietly", (bool(r.get("failed")), r["finished"], g.armed),
      (True, False, False))
check("chatgpt: Codex's Stop button isn't ChatGPT's", g.poll()["prompt_sent"], False)

# --- the board's events -----------------------------------------------------------------
class FakeLight(Light):
    def __init__(self):
        self.frames = []
    def show(self, state):
        pass
    def send_frame(self, text):
        self.frames.append(text)


light = FakeLight()
light.set_apps(["D97757", "10A37F"])
light.cheer(2)
light.send_config("br=80;ci=28DC5A")
check("frames", light.frames, ["{apps:D97757,10A37F}", "{cheer:2}", "{cfg:br=80;ci=28DC5A}"])
light.set_apps([])
check("clear accent", light.frames[-1], "{app:}")
light.set_low([True])
light.set_low([False, True])
light.set_low([])
check("low battery frames", light.frames[-3:], ["{low:1}", "{low:01}", "{low:}"])

print(f"\n{'All platform checks passed.' if not failures else f'{failures} FAILED'}")
event_server.stop()
raise SystemExit(1 if failures else 0)
