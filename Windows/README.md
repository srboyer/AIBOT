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

Classic Bluetooth (SPP) is also built and confirmed working, on a separate
plain-ESP32 board — see "Classic Bluetooth (SPP) setup" below. Same
backends, same protocol; only the transport/board differs.

## Hardware

Two supported boards/transports, same MAX7219 display and same protocol
(single ASCII byte per state: `A`=ANALYZING, `T`=THINKING, `Q`=QUESTION,
`D`=DONE/IDLE — the firmware has zero knowledge of which chatbot sent the
byte, which is what makes multi-app support possible without reflashing):

- **ESP32-C3** + **MAX7219 8x8 LED dot-matrix**, connected over **BLE**
  (the C3 is BLE-only, no Classic Bluetooth/SPP radio).
  - Pins: DIN=GPIO4, CLK=GPIO5, CS=GPIO6. Library: MD_MAX72XX (not
    LedControl, which is AVR-only and won't compile for the C3).
  - BLE device name: `ClaudeStatusMatrix`. Service UUID
    `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (Nordic UART Service), RX char
    `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`.
  - Firmware file: `claude_matrix_esp32c3_ble.ino`.
  - Confirmed working on USB power bank power.
- **Plain ESP32** (e.g. NodeMCU-32S) + the same MAX7219 display, connected
  over **Classic Bluetooth (SPP)** — see "Classic Bluetooth (SPP) setup"
  below.
  - Pins: DIN=GPIO23, CLK=GPIO18, CS=GPIO21 (chosen to avoid the flash
    pins, strapping pins, and input-only pins on a full ESP32).
  - Firmware file: `claude_matrix_esp32_classicbt.ino`.
  - No BLE heartbeat/ghost-timeout machinery needed — SPP gives prompt,
    reliable connect/disconnect events, unlike BLE's flaky supervision
    timeouts.

An earlier Arduino Uno / USB-serial path also exists in the file set
(`claude_matrix_tracker2.ino`) but is superseded by both boards above.

## Classic Bluetooth (SPP) setup

Unlike BLE, a Classic Bluetooth SPP device needs OS-level pairing before
Windows will expose it as a COM port at all:

1. Flash `claude_matrix_esp32_classicbt.ino` to a plain ESP32 (Tools >
   Board must be a board with a Classic Bluetooth radio, e.g. "ESP32 Dev
   Module" — the ESP32-C3 cannot run this sketch).
2. Pair it: **Settings > Bluetooth & devices > Add device > Bluetooth**,
   select `ClaudeStatusMatrix`.
3. Run the watcher with `--classic-bt` (auto-detects the COM port) instead
   of `--ble`:
   ```
   py -3.11 chat_watcher.py --classic-bt
   ```

**Gotcha: Windows creates TWO "Standard Serial over Bluetooth link" COM
ports per pairing**, and only one of them actually connects to the device:
- The real outgoing port — its PnP instance ID embeds the paired device's
  actual Bluetooth MAC address.
- A generic incoming listener shared across *all* Bluetooth pairings on
  the machine — its instance ID MAC is the null address `000000000000`.
  Opening this one just hangs forever with no error, since it's waiting
  for an inbound connection that will never arrive.

The two are indistinguishable by friendly name or port number alone —
`--classic-bt` (via `find_classic_bt_port()` in `claude_light_tracker.py`)
resolves this automatically by shelling out to PowerShell's
`Get-PnpDevice` and matching MAC addresses, the same way this was
originally diagnosed by hand. If you'd rather skip auto-detection, the
manual equivalent is:
```
Get-PnpDevice | Where-Object FriendlyName -like "*Standard Serial over Bluetooth*" | Select FriendlyName, InstanceId
```
then pass the matching one directly: `--port COM<N>`.

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
    +-- claude_code_vscode_backend.py  <- Claude Code (VS Code) detection logic
    |       +-- claude_code_status_hook.py    <- writes status (Claude Code hook)
    |       +-- claude_code_status_shared.py  <- shared status-file path constant
    |
    +-- claude_light_tracker.py     <- State enum, StateTracker, Light classes
                                        (ConsoleLight, ArduinoLight, BLELight, GPIOLight)
```

Run with: `py -3.11 chat_watcher.py --backend claude_desktop --ble` or
`--backend chatgpt_desktop --ble` (plus `--debug` for verbose tracing,
`--ble-name`/`--ble-address` to target a specific device, or `--classic-bt`
/ `--port COM5` / `--gpio` instead of `--ble`).

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

### claude_code_vscode_backend.py — Claude Code (in VS Code) detection

Claude Code is a CLI, not a windowed app -- there's no button toggle or
input-box placeholder for UI Automation to watch (a VS Code terminal is one
opaque scrolling text buffer, not structured elements). So this backend does
**zero UI automation**. Instead it rides Claude Code's own hook system:

- `claude_code_status_hook.py` is registered in `~/.claude/settings.json`
  against `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`,
  `PostToolUse`, `Notification`, and `Stop`. Claude Code invokes it on every
  one of those events, feeding a JSON payload on stdin; it overwrites one
  small status file (`claude_code_status_shared.STATUS_FILE`,
  `~/.claude/claude_code_status.json`) each time.
- The backend just polls that file for a newer mtime -- no window handle,
  no reconnect-by-UIA.
- **"Is this VS Code?"** isn't in the hook payload itself, so the hook script
  reads it from the process environment instead (hook commands inherit
  whatever Claude Code's own parent process set): `CLAUDE_CODE_ENTRYPOINT=
  claude-vscode` for the native VS Code extension (chat panel), or
  `TERM_PROGRAM=vscode` for `claude` typed directly into a VS Code
  integrated terminal tab. Both were confirmed by inspecting a real running
  session's environment and cross-checking the literal strings baked into
  the installed `claude.exe`.
- **Setup required**: this backend does nothing until those hooks exist.
  Until then, `try_connect()` just never succeeds -- same as any other
  backend whose target app isn't open, not an error.
- **State mapping**: `UserPromptSubmit` -> ANALYZING, `PreToolUse` ->
  THINKING, `PostToolUse` -> back to ANALYZING, `Notification` -> QUESTION
  (except `notification_type: "idle_prompt"`, see below), `Stop` -> DONE.
  `SessionEnd` isn't a display state -- it makes `poll()` raise, so the
  outer loop treats it as "app closed" and goes back to waiting for the
  next session.
- **Notification is two different events sharing one hook**: its payload's
  `notification_type` field is `"idle_prompt"` ("Claude is waiting for your
  input") after ~60s of no activity -- not a real question, just a nudge --
  versus something like `"agent_needs_input"` for an actual tool-permission
  prompt. These were originally conflated (every `Notification` -> QUESTION),
  which was a real bug: just going idle for a minute turned the light red
  with nothing actually pending, and it stayed red into the start of the
  next prompt. Fixed by excluding `idle_prompt` specifically -- any other
  (including unrecognized/future) `notification_type` still maps to
  QUESTION, so the failure mode of an unknown type is "safe" (still shows
  red when it might matter) rather than silently swallowed.
- **No true THINKING signal**: hooks only fire around tool calls, so
  THINKING here really means "a tool call is in flight", not extended
  reasoning in the Claude Desktop sense -- plain reasoning/response text
  between tool calls reports nothing until the next hook fires.
- **No dedicated "answered" event**: like `claude_desktop_backend.py`'s own
  question-hold logic, QUESTION holds until the next `UserPromptSubmit` or
  `PreToolUse` shows real progress resumed.
- **Single session only**: the status file holds one session's latest
  event, last-writer-wins -- two concurrent Claude Code sessions in VS Code
  (two chat panels, or a panel plus a terminal session) will stomp on each
  other, the same limitation every other backend here has with multiple
  windows of the same app.
- **Interrupted/rejected turns**: Claude Code fires no hook at all for "the
  user hit Escape" or "the user rejected this tool call" -- confirmed live,
  by watching a real session's status file stay on `PreToolUse` forever
  after a tool rejection with no `Stop` ever following. `PostToolUse` covers
  most of this (a rejected/errored/cancelled tool call still very likely
  fires it, snapping the light back to ANALYZING immediately). The one gap
  it can't cover -- interrupting Claude mid plain-text response with no
  tool in flight, which leaves zero hook trace whatsoever -- falls back to
  a `STALE_AFTER_SECONDS` (90s) timeout: no event at all for that long while
  a turn is presumed busy synthesizes a `finished` event. Deliberately
  skipped while holding on QUESTION, since sitting on a permission prompt
  for a while is normal, not stuck.

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
- `claude_light_tracker.py` — State enum, StateTracker, Light classes,
  `find_classic_bt_port()`
- `claude_matrix_esp32c3_ble.ino` — active firmware (ESP32-C3 + BLE)
- `claude_matrix_esp32_classicbt.ino` — active firmware (plain ESP32 +
  Classic Bluetooth SPP)
- `inspect_window.py` — generic UI-tree inspector

**Diagnostics (kept for reference, not part of the running system):**
- `test_send_button_state.py`, `test_send_stop_state.py`,
  `test_composer_buttons.py`, `test_composer_buttons_diff.py`
- `test_ble_connection.py`, `test_arduino_serial.py`

**Superseded (earlier hardware/software iterations, kept for reference):**
- `claude_desktop_watcher.py` (pre-refactor, Claude-only monolithic
  watcher — logic now lives in `chat_watcher.py` +
  `claude_desktop_backend.py`)
- `claude_matrix_tracker2.ino` (Arduino Uno / USB serial version —
  superseded by both `claude_matrix_esp32c3_ble.ino` and
  `claude_matrix_esp32_classicbt.ino`)
- `claude_live.py` (alternative: drives the light from real Anthropic API
  streaming instead of watching the desktop app)

**Setup:**
- `start_claude_watcher.bat` / `.vbs` — Windows Startup-folder launchers,
  currently `chat_watcher.py --classic-bt`
- `py -3.11 -m pip install pyserial pywinauto bleak` (add `anthropic` too
  if using `claude_live.py`)
