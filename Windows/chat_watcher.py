"""
chat_watcher.py

Generic watcher: drives the light/matrix tracker off of one or MORE chatbot
backends that implement the ChatBackend interface (see chat_backend.py).
This file has zero app-specific knowledge -- it just calls each backend's
poll() once a second and translates the results into light state changes.

By default it now watches ALL registered apps at once (--backend all):
each app gets its own little state machine, apps that aren't open are
quietly retried in the background, and the light shows the merged
highest-priority state across every app:

    QUESTION > THINKING > ANALYZING > DONE > IDLE

So if Claude is waiting on you (red) while ChatGPT is still generating
(blue), you see red -- "needs me" always outranks "working", and a DONE
flash never hides another app that's still busy. Closing or crashing one
app never interrupts tracking of the others.

To watch a different chatbot, write a new backend module and register it
in BACKENDS below -- nothing else changes, including the firmware.

Setup:
    py -3.11 -m pip install pywinauto
    (plus pyserial / bleak, depending on which --port/--classic-bt/--ble/--gpio you use)

Usage:
    py -3.11 chat_watcher.py --ble                          (watch everything)
    py -3.11 chat_watcher.py --classic-bt                   (Classic BT SPP board, auto-detected COM port)
    py -3.11 chat_watcher.py --backend claude_desktop --ble (single app, old behavior)
    py -3.11 chat_watcher.py --backend chatgpt_desktop --port COM5
    py -3.11 chat_watcher.py                                (console only)
"""

import argparse
import sys
import time

from claude_light_tracker import (
    ArduinoLight,
    BLELight,
    ConsoleLight,
    GPIOLight,
    Light,
    State,
    StateTracker,
    find_classic_bt_port,
)

POLL_INTERVAL = 1.0  # seconds between checks; raise this if it feels sluggish
DONE_FLASH_SECONDS = 1.5  # how long the green DONE flash holds before returning to idle
RECONNECT_INTERVAL = 5.0  # how often to re-try connecting to an app that isn't open

BACKENDS = {
    "claude_desktop": "claude_desktop_backend.ClaudeDesktopBackend",
    "chatgpt_desktop": "chatgpt_desktop_backend.ChatGPTDesktopBackend",
    "claude_web": "claude_web_backend.ClaudeWebBackend",
    "chatgpt_web": "chatgpt_web_backend.ChatGPTWebBackend",
    "claude_code": "claude_code_backend.ClaudeCodeBackend",
    "copilot_desktop": "copilot_desktop_backend.CopilotDesktopBackend",
}

# When several apps are active at once, the light shows whichever state has
# the highest priority. "Waiting on you" always wins (that's the one you
# can act on), busy states beat the transient DONE flash, and IDLE only
# shows when nothing anywhere is happening.
STATE_PRIORITY = {
    State.QUESTION: 4,
    State.THINKING: 3,
    State.ANALYZING: 2,
    State.DONE: 1,
    State.IDLE: 0,
}


def load_backend(name: str):
    if name not in BACKENDS:
        available = ", ".join(BACKENDS)
        print(f"Unknown backend {name!r}. Available: {available}", file=sys.stderr)
        sys.exit(1)

    module_path, class_name = BACKENDS[name].rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    backend_class = getattr(module, class_name)
    return backend_class()


# --------------------------------------------------------------------------
# Multi-app mode (the default)
# --------------------------------------------------------------------------

class BackendSlot:
    """One watched app: its backend instance, connection status, and its own
    little state machine. Slots recover independently -- one app closing or
    erroring never disturbs the others."""

    def __init__(self, name: str):
        self.name = name
        self.backend = load_backend(name)
        self.connected = False
        self.announced_waiting = False
        self.state = State.IDLE
        self.done_until = 0.0  # when the transient DONE flash expires
        self.next_connect_attempt = 0.0

    def tick(self, debug: bool = False):
        """One polling cycle for this app. Never raises -- all failure
        modes downgrade to 'disconnected, retry later'."""
        now = time.monotonic()

        if not self.connected:
            if now < self.next_connect_attempt:
                return
            self.next_connect_attempt = now + RECONNECT_INTERVAL
            if not self.announced_waiting:
                print(f"[{self.name}] Waiting for app (will keep checking quietly)...")
                self.announced_waiting = True
            if self.backend.try_connect():
                self.connected = True
                self.announced_waiting = False
                print(f"[{self.name}] Connected -- now watching.")
            return

        # Expire a leftover DONE flash from a previous tick BEFORE handling
        # new events, so a freshly-set DONE below is always guaranteed to
        # survive long enough to actually be displayed at least once.
        if self.state is State.DONE and now >= self.done_until:
            self.state = State.IDLE

        try:
            self.backend.check_alive()
            result = self.backend.poll(debug=debug)
        except Exception as e:
            print(f"[{self.name}] Lost the app ({e}); will keep retrying in the background.")
            self._drop()
            return

        if result["prompt_sent"]:
            print(f"[{self.name}] Prompt detected -> ANALYZING")
            self.state = State.ANALYZING

        if result["question_started"]:
            print(f"[{self.name}] Interactive question detected -> QUESTION (holding until answered)")
            self.state = State.QUESTION

        if result["answer_received"]:
            print(f"[{self.name}] Answer received -> ANALYZING")
            self.state = State.ANALYZING

        if result["thinking_started"]:
            print(f"[{self.name}] Extended thinking detected -> THINKING")
            self.state = State.THINKING

        if result["thinking_ended"]:
            print(f"[{self.name}] Thinking complete, response streaming -> ANALYZING")
            self.state = State.ANALYZING

        if result["finished"]:
            print(f"[{self.name}] Response finished -> DONE")
            self.state = State.DONE
            self.done_until = now + DONE_FLASH_SECONDS
            # The flash is transient: the expiry check at the top of the
            # next tick returns this app to IDLE once it's had its moment,
            # at which point it stops having any say in the merged state.

    def _drop(self):
        """Forget the stale connection and start over with a fresh backend
        instance, so no half-dead UI handles survive into the retry."""
        self.connected = False
        self.state = State.IDLE
        self.next_connect_attempt = time.monotonic() + RECONNECT_INTERVAL
        try:
            self.backend = load_backend(self.name)
        except Exception:
            pass  # module vanished mid-run would be bizarre; keep old instance


def run_multi(backend_names, make_light, debug: bool = False):
    """make_light is a zero-arg factory (not a Light instance): this loop
    owns the light's lifecycle, so a BLE drop only costs the LIGHT a
    reconnect -- app tracking state (armed turns, held questions) survives,
    slots keep tracking while the light is away, and the heartbeat repaints
    the current state the moment it's back. Previously a BLE drop restarted
    everything, so a question arriving during the outage was seen by the
    fresh backend and silently discarded."""
    slots = [BackendSlot(name) for name in backend_names]
    light = None
    shown = None            # None forces a (re)send once a light exists
    next_light_attempt = 0.0
    light_connected_at = None  # for the link-duration autopsy on each drop

    print(f"Watching {len(slots)} apps at once: {', '.join(s.name for s in slots)}.")
    print("Apps that aren't open yet are checked for every few seconds automatically.\n")

    while True:
        now = time.monotonic()

        if light is None and now >= next_light_attempt:
            try:
                light = make_light()
                shown = None  # repaint whatever state we're in
                light_connected_at = time.monotonic()
                print(f"[light] {time.strftime('%H:%M:%S')} link up.")
            except Exception as e:
                print(f"[light] {time.strftime('%H:%M:%S')} connect failed ({e}); tracking continues, retrying in 5s.")
                next_light_attempt = time.monotonic() + 5.0

        if light is not None:
            try:
                light.heartbeat()
            except Exception as e:
                light = _drop_light(light, e, light_connected_at)
                next_light_attempt = time.monotonic() + 2.0

        for slot in slots:
            slot.tick(debug=debug)

        merged = max((s.state for s in slots), key=lambda st: STATE_PRIORITY[st])

        if debug:
            summary = ", ".join(
                f"{s.name}={'?' if not s.connected else s.state.value}" for s in slots
            )
            light_desc = "light: reconnecting" if light is None else "light: ok"
            print(f"[debug] {summary} -> showing {merged.value} ({light_desc})")

        if light is not None and merged is not shown:
            try:
                light.show(merged)
                shown = merged
            except Exception as e:
                light = _drop_light(light, e, light_connected_at)
                next_light_attempt = time.monotonic() + 2.0

        time.sleep(POLL_INTERVAL)


def _drop_light(light, error, connected_at):
    """Best-effort teardown of a failed light; tracking continues without
    it. Prints how long the link survived -- the key diagnostic: durations
    clustering tightly at ~8s mean the firmware's ghost timer is cutting us
    off (heartbeats not arriving); scattered durations (5s, 30s, 2min...)
    mean the link is dying at the radio/OS level (power sag, interference,
    adapter power management)."""
    lasted = "" if connected_at is None else f" after {time.monotonic() - connected_at:.1f}s"
    print(f"[light] {time.strftime('%H:%M:%S')} lost the light ({error}){lasted}; app tracking continues, reconnecting...")
    try:
        light.close()
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Single-app mode (unchanged original behavior, kept for --backend <name>)
# --------------------------------------------------------------------------

def run(backend, light: Light, debug: bool = False):
    backend.connect()
    tracker = StateTracker(light)
    tracker.light.off()  # shows the idle/green resting state

    while True:
        backend.check_alive()

        result = backend.poll(debug=debug)

        if result["prompt_sent"]:
            print("[event] Prompt detected -> ANALYZING")
            tracker.start_turn()
            tracker.set_state(State.ANALYZING)

        if result["question_started"]:
            print("[event] Interactive question detected -> QUESTION (holding until answered)")
            tracker.set_state(State.QUESTION)

        if result["answer_received"]:
            print("[event] Answer received -> ANALYZING")
            tracker.set_state(State.ANALYZING)

        if result["thinking_started"]:
            print("[event] Extended thinking detected -> THINKING")
            tracker.set_state(State.THINKING)

        if result["thinking_ended"]:
            print("[event] Thinking complete, response streaming -> ANALYZING")
            tracker.set_state(State.ANALYZING)

        if result["finished"]:
            print("[event] Response finished -> DONE")
            tracker.set_state(State.DONE)
            time.sleep(DONE_FLASH_SECONDS)
            tracker.end_turn()

        tracker.light.heartbeat()
        time.sleep(POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="Watch chatbot apps and drive a status light/matrix off them directly.")
    parser.add_argument("--backend", type=str, default="all",
                         help="Which app(s) to watch: 'all' (default), one backend name, or a "
                              "comma-separated list (e.g. claude_desktop,claude_web). "
                              f"Available: {', '.join(BACKENDS)}")
    parser.add_argument("--port", type=str, default=None, help="Arduino serial port (e.g. COM5).")
    parser.add_argument("--baud", type=int, default=9600, help="Baud rate for --port.")
    parser.add_argument("--classic-bt", action="store_true",
                         help="Auto-detect a paired Classic Bluetooth SPP device's COM port "
                              "(e.g. an ESP32 running claude_matrix_esp32_classicbt.ino) instead of "
                              "specifying --port by hand.")
    parser.add_argument("--classic-bt-name", type=str, default="ClaudeStatusMatrix",
                         help="Paired device name to look up for --classic-bt.")
    parser.add_argument("--gpio", action="store_true", help="Use Raspberry Pi GPIO LEDs instead of console output.")
    parser.add_argument("--ble", action="store_true", help="Use an ESP32-C3 over Bluetooth LE instead of console output.")
    parser.add_argument("--ble-name", type=str, default="ClaudeStatusMatrix", help="BLE device name to scan for (--ble).")
    parser.add_argument("--ble-address", type=str, default=None, help="Skip scanning, connect directly to this BLE address.")
    parser.add_argument("--debug", action="store_true", help="Print verbose tracing of what the watcher sees each poll.")
    args = parser.parse_args()

    if args.backend == "all":
        names = list(BACKENDS.keys())
    else:
        names = [n.strip() for n in args.backend.split(",") if n.strip()]
        unknown = [n for n in names if n not in BACKENDS]
        if unknown or not names:
            print(f"Unknown backend(s): {', '.join(unknown) or '(none given)'}. "
                  f"Available: {', '.join(BACKENDS)}", file=sys.stderr)
            sys.exit(1)

    watching = ", ".join(names)
    print(f"Starting chat watcher (watching: {watching}). This will keep running and")
    print("auto-recover if an app closes/reopens, the device disconnects, or a")
    print("transient error occurs. Press Ctrl+C to stop.\n")

    while True:
        light = None
        try:
            def make_light():
                if args.port:
                    return ArduinoLight(args.port, args.baud)
                elif args.classic_bt:
                    port = find_classic_bt_port(args.classic_bt_name)
                    print(f"[light] Found {args.classic_bt_name!r} on {port}.")
                    return ArduinoLight(port, args.baud)
                elif args.ble:
                    return BLELight(device_name=args.ble_name, address=args.ble_address)
                elif args.gpio:
                    return GPIOLight()
                else:
                    return ConsoleLight()

            if len(names) > 1:
                # Multi mode owns the light lifecycle itself (survives BLE
                # drops without resetting tracking state).
                run_multi(names, make_light, debug=args.debug)
            else:
                light = make_light()
                run(load_backend(names[0]), light, debug=args.debug)
        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except Exception as e:
            print(f"[recover] Unexpected error, retrying in 5s: {e}", file=sys.stderr)
            if light is not None and hasattr(light, "close"):
                try:
                    light.close()
                except Exception as close_error:
                    print(f"[recover] (cleanup during retry also hit an error, continuing anyway: {close_error})", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
