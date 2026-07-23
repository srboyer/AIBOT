"""
test_multi_watcher_sim.py

Offline simulation of the new multi-app watcher loop: registers two fake
scripted backends (no pywinauto needed), runs BackendSlot ticks manually,
and asserts the merged state the light would show at each step.

Run: python3 test_multi_watcher_sim.py
"""

import sys
import types

# --- Fake backend machinery ------------------------------------------------

EVENTS = ["prompt_sent", "question_started", "answer_received",
          "thinking_started", "thinking_ended", "finished"]


def empty_result(**overrides):
    r = {k: False for k in EVENTS}
    r.update(overrides)
    return r


class ScriptedBackend:
    """try_connect() succeeds according to a connect script; poll() replays
    a list of event dicts, then keeps returning all-False. check_alive()
    raises when told to, simulating the app dying."""

    connect_script = []   # list of bools consumed per try_connect call
    poll_script = []      # list of result dicts consumed per poll call

    def __init__(self):
        self._connects = list(self.connect_script)
        self._polls = list(self.poll_script)
        self.die_now = False

    def try_connect(self):
        if not self._connects:
            return True
        return self._connects.pop(0)

    def check_alive(self):
        if self.die_now:
            raise RuntimeError("simulated app close")

    def poll(self, debug=False):
        if self._polls:
            return self._polls.pop(0)
        return empty_result()


def register_fake(module_name, class_name, connect_script, poll_script):
    cls = type(class_name, (ScriptedBackend,), {
        "connect_script": connect_script,
        "poll_script": poll_script,
    })
    mod = types.ModuleType(module_name)
    setattr(mod, class_name, cls)
    sys.modules[module_name] = mod
    return cls


# --- Wire fakes into chat_watcher BEFORE importing it uses them ------------

register_fake("fake_claude", "FakeClaude",
              connect_script=[False, True],  # not open on first check, then opens
              poll_script=[
                  empty_result(),
                  empty_result(prompt_sent=True),
                  empty_result(thinking_started=True),
                  empty_result(thinking_ended=True),
                  empty_result(question_started=True),
                  empty_result(),                      # holding on question
                  empty_result(answer_received=True),
                  empty_result(finished=True),
              ])

register_fake("fake_gpt", "FakeGPT",
              connect_script=[True],
              poll_script=[
                  empty_result(),
                  empty_result(),
                  empty_result(),
                  empty_result(prompt_sent=True),      # starts generating while claude thinks
                  empty_result(),
                  empty_result(),
                  empty_result(),
                  empty_result(finished=True),
              ])

import chat_watcher
chat_watcher.BACKENDS = {
    "fake_claude": "fake_claude.FakeClaude",
    "fake_gpt": "fake_gpt.FakeGPT",
}
chat_watcher.RECONNECT_INTERVAL = 0.0   # retry instantly in simulation
chat_watcher.DONE_FLASH_SECONDS = 0.0   # DONE expires on the next tick

from chat_watcher import BackendSlot, STATE_PRIORITY
from claude_light_tracker import State


def merged(slots):
    return max((s.state for s in slots), key=lambda st: STATE_PRIORITY[st])


def step(slots, label, expect):
    for s in slots:
        s.tick()
    got = merged(slots)
    status = "PASS" if got is expect else "FAIL"
    detail = ", ".join(f"{s.name}={'(disc)' if not s.connected else s.state.value}" for s in slots)
    print(f"[{status}] {label}: merged={got.value}  ({detail})")
    assert got is expect, f"{label}: expected {expect}, got {got}"


slots = [BackendSlot("fake_claude"), BackendSlot("fake_gpt")]

# Tick 1: claude's first try_connect fails, gpt connects. Both idle.
step(slots, "t1  gpt connects, claude app 'not open'", State.IDLE)
# Tick 2: claude connects (2nd attempt); gpt polls idle. (claude's connect
# tick doesn't poll yet -- connection and first poll are separate ticks.)
step(slots, "t2  claude connects", State.IDLE)
# Tick 3: claude poll #1 idle, gpt poll idle
step(slots, "t3  both idle", State.IDLE)
# Tick 4: claude prompt_sent -> ANALYZING; gpt prompt_sent -> ANALYZING
step(slots, "t4  claude prompt sent", State.ANALYZING)
# Tick 5: claude thinking (THINKING beats gpt's ANALYZING)
step(slots, "t5  claude thinking while gpt generates", State.THINKING)
# Tick 6: claude thinking ended -> ANALYZING; gpt still ANALYZING
step(slots, "t6  claude back to analyzing", State.ANALYZING)
# Tick 7: claude question -> QUESTION outranks everything
step(slots, "t7  claude asks interactive question", State.QUESTION)
# Tick 8: claude holds question
step(slots, "t8  question holds", State.QUESTION)
# Tick 9: claude answered -> ANALYZING; gpt finished -> DONE.
#         ANALYZING must outrank the DONE flash.
step(slots, "t9  gpt DONE flash hidden by claude still working", State.ANALYZING)
# Tick 10: claude finished -> DONE (flash), gpt's DONE already expired
step(slots, "t10 claude finishes -> DONE flash", State.DONE)
# Tick 11: DONE expired -> everything idle
step(slots, "t11 flash over -> idle", State.IDLE)

# --- Crash recovery: kill claude mid-run, gpt unaffected -------------------
slots[0].backend.die_now = True
# Force a fresh poll event on gpt to prove it keeps working during the outage
slots[1].backend._polls = [empty_result(prompt_sent=True)]
step(slots, "t12 claude dies mid-run, gpt keeps tracking", State.ANALYZING)
assert not slots[0].connected, "claude slot should be disconnected after death"
# Fresh backend instance was created by _drop(); its connect_script replays
# ([False, True]) so it reconnects after two attempts -- meanwhile gpt idles.
slots[1].backend._polls = [empty_result(finished=True), empty_result()]
# t13: claude's fresh backend replays its connect script (attempt 1 fails);
# gpt finishes -> DONE flash shows even during the outage.
step(slots, "t13 gpt finishes during claude outage", State.DONE)
# t14: claude's attempt 2 succeeds; gpt's flash expires.
step(slots, "t14 claude reconnects, flash over", State.IDLE)
assert slots[0].connected, "claude slot should be reconnected"

print("\nAll simulation checks passed.")
