# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A physical status light (ESP32-C3 + MAX7219 8x8 LED dot-matrix display over
BLE) that shows whether an AI chat app (Claude Desktop/Web, ChatGPT
Desktop/Web) is idle, analyzing, thinking, waiting on you, done, or out of tokens — driven
by watching the app's UI directly via Windows UI Automation (pywinauto), no
API key or app-side integration required.

All code lives under `Windows/` (Windows-only currently; `mac_inspect_app.py`
is prep work for an eventual Mac port, not a working backend).

Read `Windows/README.md` for the full hardware write-up, protocol details,
and known gotchas (accessibility-tree-stays-empty, BLE heartbeat tuning
history, etc.) — treat its "Software architecture" walkthrough as accurate
for the individual backends, but note it predates the current default
multi-app watching mode and the `*_web_backend.py` files described below.

## Setup / running

```
py -3.11 -m pip install pywinauto bleak pyserial
```

```
py -3.11 chat_watcher.py --ble                     # watch ALL registered apps at once (default)
py -3.11 chat_watcher.py --backend claude_desktop --ble
py -3.11 chat_watcher.py --backend claude_desktop,claude_web --ble
py -3.11 chat_watcher.py --port COM5                # Arduino instead of BLE
py -3.11 chat_watcher.py --gpio                     # Raspberry Pi GPIO LEDs
py -3.11 chat_watcher.py                            # console-only, no hardware
py -3.11 chat_watcher.py --debug                    # verbose per-poll tracing
```

`--backend` accepts `all` (default), a single name, or a comma-separated
list. Registered names: `claude_desktop`, `chatgpt_desktop`, `claude_web`,
`chatgpt_web`.

No test runner/lint config exists in this repo. The test files run
directly and need no apps or hardware:

```
python3 test_multi_watcher_sim.py
py -3.11 test_usage_limit.py        # out-of-tokens detection + Claude Code StopFailure
```

Diagnostics for reverse-engineering a UI when a backend needs updating:

```
py -3.11 inspect_window.py                    # list windows
py -3.11 inspect_window.py "exact window title"  # dump its control tree
```

## Architecture

```
chat_watcher.py               <- generic runner; owns the light lifecycle and merge logic
    |
    +-- chat_backend.py                <- ChatBackend interface every backend implements
    +-- claude_desktop_backend.py      <- Claude Desktop (pywinauto UIA)
    +-- chatgpt_desktop_backend.py     <- ChatGPT Desktop (pywinauto UIA)
    +-- claude_web_backend.py          <- subclasses claude_desktop_backend, browser tab variant
    +-- chatgpt_web_backend.py         <- subclasses chatgpt_desktop_backend, browser tab variant
    |
    +-- claude_light_tracker.py        <- State enum, StateTracker, Light implementations
```

### The `ChatBackend` interface (`chat_backend.py`)

Every backend implements:
- `connect()` — blocking, retries patiently until the app is found. Used in single-backend mode.
- `try_connect() -> bool` — single non-blocking attempt; never raises just because the app isn't open. This is what multi-app mode polls for every registered backend, including apps that may never launch.
- `poll(debug=False) -> dict` — called ~once/sec, returns one-tick event flags: `prompt_sent`, `question_started`, `answer_received`, `thinking_started`, `thinking_ended`, `finished`. A backend with no concept of a given flag (e.g. no extended-thinking mode) always reports `False` for it. Two optional keys: `failed` (the turn ended on an error — idle with no DONE flash) and `limited` (True while the app is out of tokens, False once it isn't, None = no news; see `usage_limit.py`).
- `check_alive()` — raises if the connection has gone stale (app closed/reopened), telling the outer loop to reconnect from scratch.

Adding a new chatbot = one new module implementing this interface + a
`BACKENDS` entry in `chat_watcher.py`. Nothing else changes, including the
firmware (it only knows single ASCII state bytes, not which app sent them).

### Multi-app watching (`chat_watcher.py`, default mode)

`run_multi()` tracks every registered app independently via a `BackendSlot`
per app (own connection state + own little state machine; one app
crashing/closing never disturbs the others) and shows the
**highest-priority** state across all of them each tick:

```
QUESTION > THINKING > ANALYZING > DONE > LIMIT > IDLE
```

("Needs you" always outranks "working"; a transient DONE flash never hides
another app still busy. LIMIT — out of tokens — is not part of a slot's turn
state machine: `BackendSlot.shown_state` shows it in place of IDLE while the
backend reports `limited`. Claude Code learns it from its StopFailure hook;
the chat apps from a usage-limit banner, found by `usage_limit.LimitBannerWatch`
with a one-call UIA substring search — its wording is unverified, and
`--debug` prints what it saw as `[limit]` lines.) The light's own lifecycle is owned by this loop
independently of backend tracking, so a BLE drop only costs the *light* a
reconnect — armed turns / held questions in the backends survive the outage
and the light repaints the current state the moment it reconnects.

Single-backend mode (`run()`, used when `--backend` names exactly one app)
is the older, simpler code path: one backend, one `StateTracker`, no merge
logic.

### Desktop vs. web backend pairs

`claude_web_backend.py` and `chatgpt_web_backend.py` **subclass** their
desktop counterparts and reuse all detection logic unchanged. They only
override:
1. **Window finding** — desktop apps have an exact window title (`"Claude"`,
   `"ChatGPT"`); browser tabs match a regex requiring the title to mention
   the app AND end with a known browser name, so the desktop app's exact
   title can never cross-match, and candidate windows are further validated
   by requiring real app UI elements to actually resolve inside them.
2. **Backgrounded-tab tolerance** — browsers only expose the *active* tab to
   UI Automation. Losing elements when a tab is backgrounded must NOT be
   treated as "app closed" (that would thrash reconnects); it's treated as
   "wait quietly," with elements re-acquired in place once the tab returns
   (their old handles go permanently stale).

Both browser and Electron desktop apps require accessibility to have been
enabled at least once (Windows Narrator trick, or `--force-renderer-accessibility`
for Chrome) before their UI tree is populated at all — see
`Windows/README.md`'s "Known gotcha" section.

### Detection strategies per app (why, not just what)

- **Claude Desktop**: input-box placeholder text signals prompt-sent/
  question-waiting; the message list is scanned **in reverse** (newest
  first, early break) since a forward scan caused real lag in long
  conversations; "thinking" = latest message has a "Thought for Ns" button
  and no visible text yet; "finished" = latest message has a "Retry" button
  (only appears once fully rendered).
- **ChatGPT Desktop**: single signal — the composer's Send button swaps to
  Stop the instant generation starts and swaps back the instant it ends.
  No confirmed THINKING or QUESTION equivalent for this app.

### Performance-sensitive UI Automation patterns (don't undo these)

These show up repeatedly across the backends and are load-bearing for
multi-app mode's responsiveness, not incidental style:
- Use `.exists(timeout=..., retry_interval=...)` with a small timeout to
  check for an absent element, never `.wrapper_object()` or a forced
  `.window_text()` on a spec that might not resolve — pywinauto's default
  find-timeout is ~5s, and a handful of those stacking in one loop
  iteration is enough to starve the BLE heartbeat and make the firmware
  declare a ghost connection and drop.
- Expensive subtree walks (e.g. reading all message text) are gated to run
  only when actually needed (e.g. only while a "thinking" button is present
  and no text has been seen yet), not on every poll.

### `claude_light_tracker.py`

Defines `State` (ANALYZING/THINKING/QUESTION/DONE/IDLE/LIMIT), the `Light`
interface, and its implementations. LIMIT (out of tokens) goes out as the two
bytes `DL` (`STATE_COMMANDS`): firmware that knows `L` ends on it, older boards
act on the `D` and go idle instead of staying stuck.
- `ConsoleLight` — colored terminal block, zero setup.
- `ArduinoLight` — USB serial, single ASCII byte per state (superseded hardware path).
- `BLELight` — the active path; Nordic UART Service over BLE to the ESP32-C3. Runs bleak's async API on a background asyncio loop/thread since the rest of the file is sync. `heartbeat()` re-sends the current state every 15s (tuned value — see in-file history comment on why 3s and 45s both failed) so the firmware can detect a dead PC/killed process and so bleak itself notices a silently-dropped link.
- `GPIOLight` — Raspberry Pi LEDs (3-LED setup; THINKING reuses the blue pin).

Also contains a standalone text-classifier path (`classify()`, `StateTracker.feed()`)
for driving the light from streamed API text instead of UI watching — unused
by the current UI-watching backends but kept as a library entry point (see
the file's own docstring for the Anthropic-streaming usage pattern).

### Firmware (`claude_matrix_esp32c3_ble.ino`)

Single ASCII byte per state on the RX characteristic: `A`=ANALYZING,
`T`=THINKING, `Q`=QUESTION, `D`=DONE/IDLE. The firmware has zero knowledge
of which chatbot sent the byte. Current version implements a heartbeat/ghost-
timeout protocol matched to `BLELight`'s send interval — if you change one
side's timing, update the other (see in-file version history for why: a 3s
heartbeat flooded the BLE stack and caused real disconnects).

The MAICA board's firmware (`maica_status_classicbt/`, Classic BT) also takes
`L` (out of tokens: its own face in `maica_face.h`) and the split-face frames
`<..>`. `maica_face.h` is plain C++: it compiles on a PC (g++) for previews and
for checking the partial redraws against full renders.
