"""
claude_light_tracker.py

Watches a stream of Claude's text output (e.g. from the Anthropic API's
streaming response, a log file, or stdin) and classifies each chunk into
one of three states:

    ANALYZING  -> blue light
    QUESTION   -> red light
    DONE       -> green light

A "light" is anything implementing the Light interface below. Two are
provided out of the box:

    ConsoleLight  - prints a colored block to the terminal (works everywhere)
    GPIOLight     - drives real LEDs on a Raspberry Pi (needs RPi.GPIO)

Usage
-----
1. Demo with fake data (no setup needed):
       python claude_light_tracker.py --demo

2. Pipe real text into it:
       python claude_light_tracker.py < claude_output.txt
       some_command_that_prints_claude_text | python claude_light_tracker.py

3. Use as a library while streaming from the Anthropic API:

       from claude_light_tracker import StateTracker, ConsoleLight

       tracker = StateTracker(ConsoleLight())
       with client.messages.stream(...) as stream:
           for text in stream.text_stream:
               tracker.feed(text)
       tracker.flush()
"""

import argparse
import re
import sys
import time
from dataclasses import dataclass
from enum import Enum


# --------------------------------------------------------------------------
# 1. States
# --------------------------------------------------------------------------

class State(Enum):
    ANALYZING = "analyzing"   # blue
    THINKING = "thinking"     # deep/extended reasoning phase
    QUESTION = "question"     # red
    DONE = "done"             # green
    IDLE = "idle"             # off / neutral


STATE_COLORS = {
    State.ANALYZING: "\033[44m\033[97m",  # white on blue
    State.THINKING: "\033[43m\033[30m",   # black on yellow
    State.QUESTION: "\033[41m\033[97m",   # white on red
    State.DONE: "\033[42m\033[30m",       # black on green
    State.IDLE: "\033[42m\033[30m",       # black on green -- idle == ready == green, not off
}
RESET = "\033[0m"


# --------------------------------------------------------------------------
# 2. Classifier — turns a chunk of text into a State
# --------------------------------------------------------------------------

ANALYZING_PATTERNS = [
    r"\blet me\b", r"\banaly[sz]", r"\bthinking\b", r"\bchecking\b",
    r"\blooking (into|at|through)\b", r"\bsearching\b", r"\bi'll (check|look|search|investigate)\b",
    r"\binvestigat", r"\blet's see\b", r"\bfirst,? i\b", r"\bgathering\b",
]

QUESTION_PATTERNS = [
    r"\?\s*$", r"\bwould you\b", r"\bcould you\b", r"\bdo you want\b",
    r"\bwhich (one|option)\b", r"\bcan you (clarify|confirm|tell me)\b",
    r"\bwhat would you\b", r"\bshould i\b",
]

DONE_PATTERNS = [
    r"\bdone\b", r"\bcompleted\b", r"\bfinished\b", r"\bhere you go\b",
    r"\bthat should do it\b", r"\ball set\b", r"\bhere('s| is) the\b.*\bready\b",
    r"\bi've (finished|completed|created|generated)\b", r"\bwrapped up\b",
]

_ANALYZING_RE = re.compile("|".join(ANALYZING_PATTERNS), re.IGNORECASE)
_QUESTION_RE = re.compile("|".join(QUESTION_PATTERNS), re.IGNORECASE)
_DONE_RE = re.compile("|".join(DONE_PATTERNS), re.IGNORECASE)


def classify(text: str) -> State:
    """Classify a single chunk/line of text into a State.

    Order matters: a line can technically match multiple patterns, so we
    check the most 'specific' signals first. Question mark endings are a
    strong, cheap signal, so DONE and ANALYZING are checked first since
    they're phrase-based and less likely to false-positive.
    """
    text = text.strip()
    if not text:
        return State.IDLE
    if _DONE_RE.search(text):
        return State.DONE
    if _ANALYZING_RE.search(text):
        return State.ANALYZING
    if _QUESTION_RE.search(text):
        return State.QUESTION
    return State.IDLE


# --------------------------------------------------------------------------
# 3. Light interface + implementations
# --------------------------------------------------------------------------

class Light:
    """Base class for anything that can display a State."""

    def show(self, state: State) -> None:
        raise NotImplementedError

    def off(self) -> None:
        """Default 'resting' state is IDLE (green/ready), not literally off."""
        self.show(State.IDLE)

    def heartbeat(self) -> None:
        """Optional periodic keepalive. Watcher loops call this every tick;
        most lights need nothing (default no-op), but BLELight re-sends the
        current state every few seconds so the firmware can tell a live
        connection from a ghost one (dead PC/killed process), and so any
        lost state byte self-heals within seconds."""
        pass


class ConsoleLight(Light):
    """Prints a colored block + label to the terminal. Zero setup required."""

    def show(self, state: State) -> None:
        color = STATE_COLORS[state]
        print(f"{color}  {state.value.upper():^11}  {RESET}")


class GPIOLight(Light):
    """
    Drives three real LEDs on a Raspberry Pi via RPi.GPIO.
    Wire an LED (with resistor) from each pin to ground.

    pip install RPi.GPIO   (on the Pi itself)
    """

    def __init__(self, blue_pin: int = 17, red_pin: int = 27, green_pin: int = 22):
        import RPi.GPIO as GPIO  # imported here so this file still loads on non-Pi machines
        self.GPIO = GPIO
        # IDLE reuses the green pin -- idle/ready and done both show green.
        # THINKING reuses the blue pin (this is a 3-LED setup with no
        # dedicated 4th color) -- if you want THINKING visually distinct on
        # real LEDs, wire a 4th LED and give it its own pin here.
        self.pins = {
            State.ANALYZING: blue_pin,
            State.THINKING: blue_pin,
            State.QUESTION: red_pin,
            State.DONE: green_pin,
            State.IDLE: green_pin,
        }

        GPIO.setmode(GPIO.BCM)
        for pin in {blue_pin, red_pin, green_pin}:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)

    def show(self, state: State) -> None:
        active_pin = self.pins[state]
        for s, pin in self.pins.items():
            self.GPIO.output(pin, self.GPIO.HIGH if pin == active_pin else self.GPIO.LOW)

    def cleanup(self) -> None:
        self.GPIO.cleanup()


class ArduinoLight(Light):
    """
    Drives LEDs on an Arduino running claude_light_tracker.ino over USB serial.
    Sends a single ASCII char per state change: 'A'=analyzing, 'T'=thinking,
    'Q'=question, 'D'=done, 'I'=idle. Protocol must match the .ino sketch.

    pip install pyserial
    """

    _COMMANDS = {
        State.ANALYZING: b"A",
        State.THINKING: b"T",
        State.QUESTION: b"Q",
        State.DONE: b"D",
        State.IDLE: b"D",  # idle/ready reuses the green command -- no sketch changes needed
    }

    def __init__(self, port: str, baud: int = 9600):
        import serial  # pyserial; imported here so this file still loads without it installed
        self.ser = serial.Serial(port, baud, timeout=1)
        # Arduino resets when the serial port opens; give the bootloader time
        # to finish before sending commands, or the first command gets dropped.
        time.sleep(2)

    def show(self, state: State) -> None:
        self.ser.write(self._COMMANDS[state])
        self.ser.flush()

    def close(self) -> None:
        self.ser.close()


class BLELight(Light):
    """
    Drives an ESP32-C3 running claude_matrix_esp32c3_ble.ino over Bluetooth
    Low Energy (BLE). Required for the C3 specifically -- it has no
    Bluetooth Classic/SPP radio, so it can't appear as a simple COM port
    the way ArduinoLight's targets do.

    bleak's API is async-only; this class runs a background asyncio event
    loop in its own thread and blocks synchronously on it, so the rest of
    this file (StateTracker, etc.) doesn't need to know or care that BLE is
    async under the hood.

    pip install bleak
    """

    _COMMANDS = {
        State.ANALYZING: b"A",
        State.THINKING: b"T",
        State.QUESTION: b"Q",
        State.DONE: b"D",
        State.IDLE: b"D",
    }

    # Nordic UART Service UUIDs -- must match the .ino sketch.
    RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

    def __init__(self, device_name: str = "ClaudeStatusMatrix", address: str = None, timeout: float = 15.0):
        import asyncio
        import threading
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            raise RuntimeError("BLE support needs: pip install bleak")

        self._asyncio = asyncio
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        async def _connect():
            target_address = address
            if target_address is None:
                print(f"Scanning for BLE device {device_name!r}...")
                device = await BleakScanner.find_device_by_name(device_name, timeout=timeout)
                if device is None:
                    raise RuntimeError(
                        f"Could not find a BLE device named {device_name!r}. "
                        "Is it powered on and advertising?"
                    )
                target_address = device.address
            client = BleakClient(target_address)
            await client.connect()
            return client

        try:
            future = self._asyncio.run_coroutine_threadsafe(_connect(), self._loop)
            self.client = future.result(timeout=timeout + 10)
        except Exception:
            # Connection failed -- shut this attempt's event loop/thread down
            # cleanly before re-raising, so a failed attempt doesn't leave a
            # zombie thread (and possibly a half-open BLE connection at the
            # OS level) behind to interfere with the next retry.
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            raise

        print(f"Connected to {device_name} ({self.client.address}).")

    def show(self, state: State) -> None:
        data = self._COMMANDS[state]

        async def _write():
            await self.client.write_gatt_char(self.RX_CHAR_UUID, data, response=True)

        future = self._asyncio.run_coroutine_threadsafe(_write(), self._loop)
        future.result(timeout=5)
        self._last_state = state
        self._last_send = time.monotonic()

    # Heartbeat interval: re-send the current state periodically as a
    # keepalive so the firmware can detect ghost connections AND so bleak
    # discovers silently-dead links (without a write attempt, bleak has no
    # way to know the connection died). 
    #
    # History: 3s flooded the ESP32's BLE stack and caused disconnects.
    # 45s was stable but left long blind spots where the dot lingered
    # silently. 15s is the proven sweet spot: fast enough that any dot
    # appearance self-clears within ~20s, slow enough to not stress the
    # BLE link. Tested stable on both Intel and MediaTek adapters.
    HEARTBEAT_INTERVAL = 15.0

    def heartbeat(self) -> None:
        now = time.monotonic()
        if now - getattr(self, "_last_send", 0.0) >= self.HEARTBEAT_INTERVAL:
            self.show(getattr(self, "_last_state", State.IDLE))

    def close(self) -> None:
        async def _disconnect():
            await self.client.disconnect()

        future = self._asyncio.run_coroutine_threadsafe(_disconnect(), self._loop)
        try:
            future.result(timeout=5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)


# --------------------------------------------------------------------------
# 4. StateTracker — feed it text, it drives the light
# --------------------------------------------------------------------------

@dataclass
class StateTracker:
    light: Light
    _current: State = State.IDLE
    _buffer: str = ""
    _armed: bool = False  # only reacts to text once a turn has started

    def start_turn(self) -> None:
        """Call this the instant a prompt is sent. Resets to IDLE and arms
        the tracker so the very next classified chunk lights up immediately."""
        self._buffer = ""
        self._current = State.IDLE
        self._armed = True
        self.light.off()

    def end_turn(self) -> None:
        """Call after a response is fully finished. Flushes any leftover
        buffered text, disarms the tracker, and returns the light to IDLE."""
        self.flush()
        self._armed = False
        self._current = State.IDLE
        self.light.off()

    @property
    def current_state(self) -> State:
        return self._current

    def set_state(self, state: State) -> None:
        """Force the light to a specific state directly, bypassing text
        classification. Useful for structural signals (e.g. 'a response has
        started streaming' or 'the stream just finished') that aren't tied
        to any particular wording."""
        self._current = state
        self.light.show(state)

    def feed(self, chunk: str) -> None:
        """Feed a chunk of streamed text. Buffers until a sentence/line boundary,
        then classifies and updates the light if the state changed.
        Ignored if start_turn() hasn't been called yet (tracker stays idle)."""
        if not self._armed:
            return
        self._buffer += chunk
        # flush on sentence-ish boundaries so we don't classify mid-word
        while True:
            match = re.search(r"[.!?\n]", self._buffer)
            if not match:
                break
            piece, self._buffer = self._buffer[: match.end()], self._buffer[match.end():]
            self._process(piece)

    def flush(self) -> None:
        if self._buffer.strip():
            self._process(self._buffer)
            self._buffer = ""


    def _process(self, piece: str) -> None:
        new_state = classify(piece)
        if new_state != State.IDLE and new_state != self._current:
            self._current = new_state
            self.light.show(new_state)


# --------------------------------------------------------------------------
# 5. Demo / CLI
# --------------------------------------------------------------------------

DEMO_LINES = [
    "Let me analyze the dataset you uploaded.",
    "I'm checking the correlation between these two columns.",
    "Would you like me to include a trendline on the chart?",
    "Great, generating the visualization now.",
    "I've completed the analysis and the chart is ready.",
]


def run_demo(light: Light) -> None:
    """One-shot demo: simulates a single prompt going through, idle -> lit -> idle."""
    tracker = StateTracker(light)
    print("[idle] waiting for a prompt...")
    tracker.light.off()
    time.sleep(1)

    print("\n>>> Prompt sent <<<\n")
    tracker.start_turn()  # <-- this is the trigger: light goes idle/off right as the prompt fires

    for line in DEMO_LINES:
        print(f"> {line}")
        tracker.feed(line + "\n")
        time.sleep(0.8)

    tracker.end_turn()
    print("\n[idle] response finished, waiting for next prompt...")


def run_interactive(light: Light) -> None:
    """Live loop: sits idle, waits for you to type a prompt, then 'streams' a
    canned response word-by-word so you can watch the light react in real time.
    Swap the canned response for a real Anthropic streaming call (see docstring
    at the top of this file) to drive it off actual Claude output."""
    tracker = StateTracker(light)
    print("Idle. Type a prompt and hit enter (Ctrl+C to quit).\n")
    tracker.light.off()

    canned_response = (
        "Let me analyze that for you. Checking the details now. "
        "Would you like me to go deeper on any part of it? "
        "Alright, I've completed the analysis and everything is ready."
    )

    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if not prompt.strip():
            continue

        # --- TRIGGER: the moment a prompt goes through, arm the tracker ---
        tracker.start_turn()

        # simulate streamed response, word by word
        for word in canned_response.split(" "):
            tracker.feed(word + " ")
            time.sleep(0.15)

        tracker.end_turn()
        print("[idle] waiting for next prompt...\n")


def main():
    parser = argparse.ArgumentParser(description="Track Claude-style text and drive a status light.")
    parser.add_argument("--demo", action="store_true", help="Run a single simulated prompt->response cycle.")
    parser.add_argument("--interactive", action="store_true", help="Idle until you type a prompt, then watch it react live.")
    parser.add_argument("--gpio", action="store_true", help="Use real Raspberry Pi GPIO LEDs instead of console output.")
    parser.add_argument("--port", type=str, default=None,
                         help="Use an Arduino over this serial port (e.g. /dev/ttyUSB0, COM3) instead of console output.")
    parser.add_argument("--baud", type=int, default=9600, help="Baud rate for --port (must match the .ino sketch).")
    parser.add_argument("--ble", action="store_true", help="Use an ESP32-C3 over Bluetooth LE instead of console output.")
    parser.add_argument("--ble-name", type=str, default="ClaudeStatusMatrix", help="BLE device name to scan for (--ble).")
    parser.add_argument("--ble-address", type=str, default=None, help="Skip scanning, connect directly to this BLE address.")
    args = parser.parse_args()

    if args.port:
        light: Light = ArduinoLight(args.port, args.baud)
    elif args.ble:
        light = BLELight(device_name=args.ble_name, address=args.ble_address)
    elif args.gpio:
        light = GPIOLight()
    else:
        light = ConsoleLight()

    if args.demo:
        run_demo(light)
        return

    if args.interactive:
        run_interactive(light)
        return

    tracker = StateTracker(light)
    print("Reading from stdin... (Ctrl+D / Ctrl+Z to end)", file=sys.stderr)
    print("Note: stdin mode treats the whole input as one turn.", file=sys.stderr)
    tracker.start_turn()
    for line in sys.stdin:
        tracker.feed(line)
    tracker.end_turn()



if __name__ == "__main__":
    main()
