# Chat Status Light Tracker — Project Summary

A physical status indicator (ESP32-C3 + MAX7219 8x8 LED dot-matrix display)
that shows in real time whether an AI chat app is idle, thinking, waiting
on you, or done — driven by watching the desktop app's UI directly, no API
key or app-side integration required. Originally built for Claude Desktop,
now generalized to also support the ChatGPT desktop app, with the
architecture designed so adding more chatbots later doesn't require
touching the hardware or core logic at all.

## Current status

Both Claude Desktop and ChatGPT Desktop backends are built and confirmed
working over BLE. No reflashing or code changes needed to switch between
them — just re-run the watcher with a different `--backend` flag.

## Hardware

- **ESP32-C3** + **MAX7219 8x8 LED dot-matrix** display, connected over
  **BLE** (the C3 is BLE-only, no Classic Bluetooth/SPP).
- Pins: DIN=GPIO4, CLK=GPIO5, CS=GPIO6. Library: MD_MAX72XX (not
  LedControl, which is AVR-only and won't compile for the C3).
- BLE device name: `ClaudeStatusMatrix`. Service UUID
  `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (Nordic UART Service), RX char
  `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`.
- Firmware file: `claude_matrix_esp32c3_ble.ino` — this is the one
  currently in use. Two earlier hardware paths also exist in the file set
  (Arduino Uno via USB serial, ESP32 classic via Bluetooth SPP) but are
  superseded.
- Protocol is a single ASCII byte per state: `A`=ANALYZING, `T`=THINKING,
  `Q`=QUESTION, `D`=DONE/IDLE. The firmware has zero knowledge of which
  chatbot sent the byte — this is what makes multi-app support possible
  without reflashing.
- Confirmed working on USB power bank power.

## Software architecture

The system is split into a generic core (hardware + state machine) and
swappable per-app "backend" modules (UI detection logic), connected by a
common interface. This split is what lets multiple chatbots share one
codebase.

```
chat_watcher.py              <- generic runner, drives everything below
    |
    +-- chat_backend.py       <- interface every backend implements
    +-- claude_desktop_backend.py   <- Claude Desktop detection logic
    +-- chatgpt_desktop_backend.py  <- ChatGPT Desktop detection logic
    |
    +-- claude_light_tracker.py     <- State enum, StateTracker, Light classes
                                        (ConsoleLight, ArduinoLight, BLELight, GPIOLight)
```

Run with: `py -3.11 chat_watcher.py --backend claude_desktop --ble` or
`--backend chatgpt_desktop --ble` (plus `--debug` for verbose tracing,
`--ble-name`/`--ble-address` to target a specific device, or `--port
COM5` / `--gpio` instead of `--ble`).

### chat_backend.py — the interface

Every backend implements:
- `connect()` — blocks/retries until the app window and needed UI elements
  are found.
- `poll(debug=False)` — called once a second, returns a dict of one-time
  event flags: `prompt_sent`, `question_started`, `answer_received`,
  `thinking_started`, `thinking_ended`, `finished`. A backend that doesn't
  support a concept (e.g. no extended-thinking mode) just always reports
  `False` for it.
- `check_alive()` — raises if the connection/window has gone stale (app
  was closed and reopened), so the outer loop in `chat_watcher.py` knows
  to reconnect from scratch.

### claude_desktop_backend.py — Claude Desktop detection

Uses pywinauto UI Automation on the "Claude" window.
- **Input box**: an `Edit` control. Detected by placeholder text ("Write a
  message...") rather than by position, since a naive `found_index=0`
  approach was found to grab the wrong control after a reconnect.
- **Prompt sent**: box had real text, then reverted to placeholder/empty.
- **Message list**: a "Chat messages" `Group` containing child groups
  titled "Message N of M". Scanned **in reverse** (newest first) with an
  early break — a forward scan caused real, growing lag in long
  conversations.
- **Thinking**: the latest message has a "Thought for Ns" button and no
  visible text yet.
- **Finished**: the latest message has a "Retry" button (only appears once
  that message is fully rendered).
- **Question/waiting-for-input**: the input box placeholder changes to
  include "reply directly" when Claude is waiting on an interactive
  choice. This holds the state until the hint clears, with a
  `question_message_n` guard so a stale "finished" check on the question
  message itself doesn't override it.
- **Liveness**: 10 consecutive failures reading the input box's text
  raises, triggering the outer reconnect loop.

### chatgpt_desktop_backend.py — ChatGPT Desktop detection

Uses pywinauto UI Automation on the "ChatGPT" window (must be in **Chat**
mode — the app also has a separate "Codex" agent mode with a completely
different, task-based UI that this does not target).
- **Detection signal**: ChatGPT swaps its composer's `Send` button for a
  `Stop` button the instant it starts generating, and swaps back the
  instant it finishes. Confirmed stable across a real ~45s generation with
  zero flicker — this single toggle is the entire detection mechanism.
- **No confirmed THINKING equivalent** — no observed structural signal for
  an extended-reasoning phase, so `thinking_started`/`thinking_ended`
  always report `False` for this backend.
- **No confirmed QUESTION equivalent** — if ChatGPT asks a clarifying
  question, there's no evidence it does so via any special UI state; it
  likely appears as ordinary response text, which just looks like a
  normal finished turn here. Not yet tested against a real clarifying-
  question scenario.
- **Notable UI quirk discovered along the way**: ChatGPT's input box is a
  rich-text `contenteditable` element, not a plain text field — its live
  typed content is *not* exposed through UI Automation the way Claude's
  input box is (a `Static` child stayed frozen at `'\n'` regardless of
  typed text). This is why detection had to pivot to the Send/Stop button
  toggle instead of watching the input box directly, unlike Claude.
- Individual response text is fragmented across dozens of separate
  `Static` elements (worse than Claude's), and rich content (generated
  images, map widgets, cards) injects large amounts of irrelevant noise
  into the UI tree — but since detection never reads response text or
  scans the message list for this backend, none of that noise matters.

### Diagnostic/inspection tools

- `inspect_window.py` — generic UI-tree dumper (works against any app by
  window title, not just Claude/ChatGPT). Recently patched: redirecting
  output to a file on Windows switches Python's stdout encoding to
  cp1252, which crashed on emoji/smart-quotes/em-dashes in real app UI
  text; now forces UTF-8 with `errors="replace"` so a dump can't silently
  die partway through.
- `test_send_button_state.py`, `test_send_stop_state.py`,
  `test_composer_buttons.py`, `test_composer_buttons_diff.py` — one-off
  diagnostics built while reverse-engineering ChatGPT's UI signals; the
  diff-only variant exists because large diagnostic output was silently
  failing to paste into chat as an attachment, while short diffs of
  what-changed pasted fine as plain text.

### Known gotcha: accessibility tree stays empty

Electron/Chromium desktop apps (both Claude Desktop and ChatGPT Desktop
hit this) sometimes expose only empty, unlabeled `Pane` elements to UI
Automation until a real screen reader has been detected running at least
once. Fix: enable Windows Narrator (`Win+Ctrl+Enter`), fully quit and
reopen the target app while Narrator is running, then Narrator can be
turned back off — the app keeps exposing full accessibility info after
that.

## Known issues / caveats

- Windows-only (pywinauto/UIA-based); Mac/Linux would need a full
  platform-specific accessibility API rewrite.
- BLE disconnect mid-session triggers the outer recovery loop and a
  ~5s reconnect/rescan.
- ChatGPT backend has no QUESTION state and no THINKING state (see above);
  Claude backend has both.
- Switching between backends currently means restarting the watcher with
  a different `--backend` flag — no automatic "detect which app is
  focused" switching yet (see roadmap).

## Roadmap / possible next steps

- [ ] Real-world hardening — run both backends through normal daily use to
      surface edge cases (mid-generation app switching, regenerating a
      response, network hiccups) that targeted testing hasn't hit yet.
- [ ] Auto-detect which app currently has focus and switch backends
      automatically, instead of a manual `--backend` flag.
- [ ] Additional states: an error/failure indicator; a distinct tool-use
      animation if either app exposes that structurally.
- [ ] Confirm whether ChatGPT has any structural signal for a clarifying
      question, once actually observed in the wild.
- [ ] OLED upgrade (SSD1306 128x64, ~$5, I2C) for actual facial
      expressions instead of 8x8 pixel art.
- [ ] Packaging — turn into a proper installer/executable instead of
      "run a Python script."
- [ ] Mac/Linux support (separate, larger effort).

## Full file list (in /mnt/user-data/outputs/)

**Active / current:**
- `chat_watcher.py` — generic runner (replaces the old
  `claude_desktop_watcher.py`)
- `chat_backend.py` — backend interface
- `claude_desktop_backend.py` — Claude Desktop detection logic
- `chatgpt_desktop_backend.py` — ChatGPT Desktop detection logic
- `claude_light_tracker.py` — State enum, StateTracker, Light classes
- `claude_matrix_esp32c3_ble.ino` — active firmware (ESP32-C3 + BLE)
- `inspect_window.py` — generic UI-tree inspector

**Diagnostics (kept for reference, not part of the running system):**
- `test_send_button_state.py`, `test_send_stop_state.py`,
  `test_composer_buttons.py`, `test_composer_buttons_diff.py`
- `test_ble_connection.py`, `test_arduino_serial.py`

**Superseded (earlier hardware/software iterations, kept for reference):**
- `claude_desktop_watcher.py` (pre-refactor, Claude-only monolithic
  watcher — logic now lives in `chat_watcher.py` +
  `claude_desktop_backend.py`)
- `claude_matrix_tracker2.ino` (Arduino Uno / USB serial version)
- `claude_matrix_esp32_bt.ino` (ESP32 classic Bluetooth SPP version — not
  compatible with the C3 board actually in use)
- `claude_live.py` (alternative: drives the light from real Anthropic API
  streaming instead of watching the desktop app)

**Setup:**
- `start_claude_watcher.bat` / `.vbs` — Windows Startup-folder launchers
  (currently point at the old watcher; would need updating to call
  `chat_watcher.py` with the desired `--backend` flag)
- `py -3.11 -m pip install pyserial pywinauto bleak` (add `anthropic` too
  if using `claude_live.py`)
