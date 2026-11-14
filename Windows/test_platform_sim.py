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
claude_web = cw.BackendSlot("claude_web")
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
check("limit banner -> LIMIT", gpt.shown_state, State.LIMIT)

# The extension covering claude.ai makes the UI-Automation backend stand down.
tab("9", "claude", "idle")
claude_web.connected = True
claude_web.tick()
check("claude_web stands down for the extension", (claude_web.connected, claude_web.standing_down), (False, True))

# --- rim colours ----------------------------------------------------------------------
fs = cw.FaceSlots()
check("accent: the limited app's colour", cw.accent_colors(slots, fs, [], gpt.shown_state), ["10A37F"])
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

print(f"\n{'All platform checks passed.' if not failures else f'{failures} FAILED'}")
event_server.stop()
raise SystemExit(1 if failures else 0)
