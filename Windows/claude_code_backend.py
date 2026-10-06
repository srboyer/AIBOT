"""
claude_code_backend.py

ChatBackend implementation for Claude Code, the CLI/agent -- regardless of
which editor or terminal it's running inside. This project is meant to work
no matter which of those a given customer happens to use, so this backend
deliberately does not gate on host: VS Code, JetBrains, Cursor, Windsurf, a
bare terminal, or anything else that spawns `claude` are all tracked
identically. See claude_code_status_hook.py's docstring for how the host is
labeled (informational only, purely for printed/debug output) rather than
used as a filter.

Unlike every other backend here, this one does ZERO UI automation. Claude
Code is a CLI/agent, not a windowed app with buttons to inspect -- there's
nothing for pywinauto to grab onto (an integrated terminal is one opaque
scrolling text buffer, not structured elements). Instead, detection rides
on Claude Code's own hook system: claude_code_status_hook.py is registered
in ~/.claude/settings.json against SessionStart, SessionEnd,
UserPromptSubmit, PreToolUse, PostToolUse, Notification, and Stop, and
overwrites a small status file (claude_code_status_shared.STATUS_FILE)
every time one of those fires. This backend just polls that file -- no
window handle, no reconnect-by-UIA, none of the usual fragility.

SETUP REQUIRED -- this backend does nothing until the hooks are actually
registered. See claude_code_status_hook.py's docstring for the exact
~/.claude/settings.json shape. Until then, try_connect() simply never
succeeds (same as any other backend whose target app isn't running) --
chat_watcher.py handles that as normal "waiting for app", not an error.

STATE MAPPING -- Claude Code's hook events map onto this project's four
states as follows:
    UserPromptSubmit -> prompt_sent            (ANALYZING)
    PreToolUse        -> thinking_started       (THINKING -- "actively
                         working", the closest available proxy; see caveat
                         below)
    PostToolUse        -> thinking_ended         (back to ANALYZING -- the
                         tool call is over, whether it succeeded, errored,
                         or was rejected/cancelled)
    Notification      -> question_started       (QUESTION -- but only for
                         notification_type != "idle_prompt"; see caveat
                         below)
    Notification "idle_prompt" while a turn is in progress
                      -> finished               (DONE -- Claude Code is
                         waiting for your input, so the turn is over even
                         though no Stop fired; see INTERRUPTED/REJECTED
                         TURNS below)
    Stop               -> finished               (DONE)
    SessionStart/SessionEnd -> connection lifecycle only, no state of their
                         own (see check_alive via SessionEnd below)

CAVEAT -- no true THINKING signal: hooks only fire around tool calls, not
around plain-text reasoning between them. So THINKING here really means
"a tool call is in flight", not "extended thinking" in the Claude Desktop
sense. Pure reasoning/response-writing with no tool calls reports nothing
at all until the next hook fires, same as every other backend's honest
gaps (see chatgpt_desktop_backend.py's own THINKING caveat).

CAVEAT -- Notification is two very different events sharing one hook: its
payload's notification_type field is "idle_prompt" ("Claude is waiting for
your input") after ~60s of no activity -- NOT a real question, just a
nudge -- versus something like "agent_needs_input" for an actual
tool-permission prompt. These used to be conflated (every Notification ->
QUESTION), which was a real, observed bug: simply going idle for a minute
(e.g. reading a long response) turned the light red on its own with nothing
actually pending, and it stayed red into the start of the next prompt.
Fixed by excluding notification_type == "idle_prompt" specifically; any
OTHER value, including one this project hasn't seen yet, still maps to
QUESTION -- an unrecognized type is treated as "might genuinely need you",
not the reverse.

CAVEAT -- single session only: the status file holds exactly one session's
latest event, last-writer-wins. Multiple concurrent Claude Code sessions
(two editor panels, a panel plus a terminal session, or two different
hosts at once) will stomp on each other here, same limitation every other
backend in this project has with multiple windows of the same app.

QUESTION -> answer received: there's no dedicated "permission granted" hook
event, so (mirroring claude_desktop_backend.py's own awaiting_answer hold)
this holds in QUESTION until either the NEXT UserPromptSubmit or PreToolUse
shows real progress resumed (-> answer_received), OR a Stop shows the turn
ended without one (-> finished directly, skipping answer_received -- there
was no "resumed" moment to report). Both clear the hold; only
UserPromptSubmit/PreToolUse used to, which left a Stop arriving mid-hold
silently swallowed and the light stuck on QUESTION indefinitely (observed
live -- a Notification doesn't guarantee a matching UserPromptSubmit/
PreToolUse ever follows).

INTERRUPTED/REJECTED TURNS -- Claude Code has no hook event for "the user
hit Escape" or "the user rejected this tool call" specifically; when a turn
ends that way, NO hook fires at all telling us it's over (confirmed by
watching a real session's status file: the last recorded event stayed
"PreToolUse" indefinitely after a tool rejection -- no Stop ever followed).
Two mitigations, in order of how much they cover:
  1. PostToolUse very likely still fires for a rejected/errored/cancelled
     tool call (not just a successful one), so most interruptions-with-a-
     tool-in-flight self-heal back to ANALYZING immediately via the mapping
     above -- no timeout needed for that case.
  2. The one gap PostToolUse can't cover is interrupting Claude mid plain-
     text response with NO tool involved at all -- genuinely zero hook
     fires there. What DOES follow is Claude Code's own idle nudge: once
     the prompt input has sat idle for ~60s it fires Notification with
     notification_type "idle_prompt" ("Claude is waiting for your
     input"). That only happens once Claude is actually waiting on you,
     so poll() treats an idle_prompt arriving while busy as the turn's
     end (-> finished). Like the old timer it replaced, it deliberately
     does NOT apply while awaiting_answer (QUESTION): sitting on a
     permission prompt is normal, not finished.

  This used to be a blind 90s staleness timer -- "busy and no hook event
  for 90s -> assume finished". That was a real, observed bug: hooks also go
  silent during perfectly normal long work (a tool call running over 90s,
  e.g. a firmware compile, or a long response with no tool calls), so the
  light dropped to DONE mid-turn at ~1m40s while Claude was still working.
  idle_prompt can't misfire that way. A timer remains only as a last-resort
  safety net (STALE_AFTER_SECONDS, now 30 minutes) for a session that dies
  without firing anything at all -- far longer than any real silent stretch
  (Claude Code's own Bash tool caps at 10 minutes).
"""

import json
import os
import sys
import time

from chat_backend import ChatBackend
from claude_code_status_shared import STATUS_FILE

EMPTY_RESULT = {
    "prompt_sent": False,
    "question_started": False,
    "answer_received": False,
    "thinking_started": False,
    "thinking_ended": False,
    "finished": False,
}

# See "INTERRUPTED/REJECTED TURNS" above. Silent turns normally end via the
# idle_prompt notification; this is only the safety net for a session that
# dies without firing anything at all. It must be far longer than any real
# silent stretch -- at 90s it cut genuine long tool calls short.
STALE_AFTER_SECONDS = 30 * 60.0


class ClaudeCodeBackend(ChatBackend):
    name = "claude_code"

    def __init__(self):
        self.last_mtime = 0.0
        self.awaiting_answer = False
        # True from the moment a turn presumably starts (prompt_sent /
        # thinking_started / question_started) until we see it end
        # (finished, or the staleness fallback below fires). Purely local
        # bookkeeping -- mirrors, but is independent of, chat_watcher.py's
        # own State on the other side of the interface.
        self.busy = False
        self.last_event_at = time.monotonic()
        self.host = None  # informational only -- see module docstring

    # -- ChatBackend interface --------------------------------------------

    def try_connect(self) -> bool:
        status = self._read_status()
        if status is None:
            return False
        # A SessionEnd sitting as the latest event means the session that
        # wrote it has already closed -- don't "connect" to a corpse just
        # because its last status file write is still lying around.
        if status.get("event") == "SessionEnd":
            return False
        # Baseline to the file's CURRENT mtime, not 0 -- otherwise the very
        # next poll() would replay whatever event is already sitting in the
        # file (e.g. a stale "Stop" from a moment ago) as if it just
        # happened, same landmine the other backends avoid via
        # _reset_poll_state() on connect.
        self.last_mtime = self._mtime()
        self.awaiting_answer = False
        self.busy = False
        self.last_event_at = time.monotonic()
        self.host = status.get("host")
        return True

    def connect(self):
        attempt = 0
        while not self.try_connect():
            attempt += 1
            if attempt == 1 or attempt % 12 == 0:
                print("Waiting for Claude Code to be running somewhere "
                      "(hooks configured? see claude_code_status_hook.py's "
                      "docstring)...", file=sys.stderr)
            time.sleep(5)
        print(f"Watching Claude Code (host: {self.host}; hook-driven, no UI automation).")

    def check_alive(self):
        # No persistent window/handle here to go stale -- liveness is
        # entirely poll()'s job (it raises the moment it sees a SessionEnd
        # event), so there's nothing for this to check on its own.
        pass

    def poll(self, debug: bool = False) -> dict:
        result = dict(EMPTY_RESULT)

        mtime = self._mtime()
        if mtime <= self.last_mtime:
            # No new hook event this tick. Silent turns normally end via
            # idle_prompt (below); this is only the safety net for a
            # session that died without firing anything -- see
            # STALE_AFTER_SECONDS. Skipped while awaiting_answer: sitting on
            # a permission prompt is normal, not stuck.
            if (self.busy and not self.awaiting_answer
                    and (time.monotonic() - self.last_event_at) > STALE_AFTER_SECONDS):
                if debug:
                    print(f"[debug] claude_code: no event for "
                          f">{STALE_AFTER_SECONDS:.0f}s while busy -- assuming "
                          f"the session died -> DONE")
                result["finished"] = True
                self.busy = False
            return result

        self.last_mtime = mtime
        self.last_event_at = time.monotonic()

        status = self._read_status()
        if status is None:
            return result  # file vanished mid-read

        self.host = status.get("host")
        event = status.get("event")
        if debug:
            print(f"[debug] claude_code: event={event!r} "
                  f"host={self.host!r} "
                  f"session={status.get('session_id')!r} "
                  f"awaiting_answer={self.awaiting_answer}")

        if event == "SessionEnd":
            raise RuntimeError(
                "Claude Code session ended -- SessionEnd hook fired. "
                "Restarting watcher to look for the next session."
            )

        if self.awaiting_answer:
            # Hold entirely until real progress resumes, same as
            # claude_desktop_backend.py's own question-hold logic. Stop ALSO
            # clears the hold, straight to finished -- a Notification has no
            # guarantee a matching UserPromptSubmit/PreToolUse ever follows
            # (permission resolved some other way, or the turn just legitimately
            # ends on its own). Without this, a Stop arriving mid-hold was
            # silently swallowed by the `return result` below and the light
            # stuck on QUESTION forever, until whatever NEXT turn happened to
            # fire one of the other two events -- observed live.
            if event in ("UserPromptSubmit", "PreToolUse"):
                result["answer_received"] = True
                self.awaiting_answer = False
                self.busy = True
            elif event == "Stop":
                result["finished"] = True
                self.awaiting_answer = False
                self.busy = False
            return result

        if event == "UserPromptSubmit":
            result["prompt_sent"] = True
            self.busy = True
        elif event == "PreToolUse":
            result["thinking_started"] = True
            self.busy = True
        elif event == "PostToolUse":
            # The tool call is over -- successfully, in error, or rejected
            # by the user -- either way THINKING is over. This is what lets
            # a rejected/cancelled tool call self-heal back to ANALYZING
            # immediately instead of sitting on THINKING forever (see
            # "INTERRUPTED/REJECTED TURNS" above).
            result["thinking_ended"] = True
            self.busy = True
        elif event == "Notification":
            # notification_type "idle_prompt" ("Claude is waiting for your
            # input") fires just from sitting idle ~60s -- it is NOT a real
            # question, just a nudge that nothing new has happened. Treating
            # it as QUESTION was a real bug: going idle for a minute (e.g.
            # while reading a long response) turned the light red on its
            # own, and it stayed red into the next prompt since QUESTION
            # only clears on real progress. Every OTHER notification_type
            # (a genuine tool-permission prompt, or an unrecognized/future
            # type) still maps to QUESTION -- unknown defaults to "might
            # actually need you", not the reverse, so a renamed/new type
            # degrades to the old (safe) behavior rather than going silent.
            if status.get("notification_type") != "idle_prompt":
                result["question_started"] = True
                self.awaiting_answer = True
                self.busy = True
            elif self.busy:
                # idle_prompt mid-turn: Claude Code is waiting for input, so
                # the turn ended without a Stop (Escape mid-response, a
                # rejected tool call). This is the turn's real end signal --
                # see INTERRUPTED/REJECTED TURNS above.
                if debug:
                    print("[debug] claude_code: idle_prompt while busy -- "
                          "turn ended without a Stop -> DONE")
                result["finished"] = True
                self.busy = False
        elif event == "Stop":
            result["finished"] = True
            self.busy = False
        # SessionStart, or anything else future hook events might add:
        # connection/lifecycle only, no state change.

        return result

    # -- internal helpers ----------------------------------------------------

    def _read_status(self):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _mtime(self) -> float:
        try:
            return os.path.getmtime(STATUS_FILE)
        except Exception:
            return 0.0
