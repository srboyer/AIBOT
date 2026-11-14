"""
chat_watcher.py

Generic watcher: drives the light/matrix tracker off of one or MORE chatbot
backends that implement the ChatBackend interface (see chat_backend.py).
This file has zero app-specific knowledge -- it just calls each backend's
poll() once a second (or the moment a source pushes an event, see
event_server.py) and translates the results into light state changes.

By default it watches ALL registered apps at once (--backend all): each app
gets its own little state machine, apps that aren't open are quietly
retried in the background, and the light shows the merged highest-priority
state across every app:

    QUESTION > THINKING > ANALYZING > DONE > UNREAD > LIMIT > IDLE

So if Claude is waiting on you (red) while ChatGPT is still generating
(blue), you see red -- "needs me" always outranks "working", and a DONE
flash never hides another app that's still busy. Closing or crashing one
app never interrupts tracking of the others.

UNREAD -- "done, unread": a reply that finished while its app wasn't the
one in front stays as a gentle done look until you switch to that app (or
for settings "unread_minutes"), instead of a 1.5 s flash you'd miss. Apps
whose focus can't be told just flash.

LIMIT -- out of tokens: while an app has used up its usage limit (see
usage_limit.py) and isn't doing anything else, it shows LIMIT, so MAICA
rests on its out-of-tokens face rather than its idle one; anything else
going on still shows over it.

A split-face display (the MAICA screen) goes further: while two apps are
busy at once its face splits in two, one per app, each showing its own
state (see FaceSlots). The merged state is still sent as before, so
single-light boards are unaffected. MAICA also gets (in "{...}" frames that
older firmware ignores): the colour of the app each face shows (its rim is
tinted with it), a celebration when a long task finishes, and the user's
face settings; and it talks back -- double-tap it to jump to the app that
needs you, hold push-to-talk to dictate into the app in front (voice.py).

Sources (BACKENDS plus everything generated from maica_rules.json):
  - desktop chat apps by UI Automation (Windows) -- claude_desktop, ...
  - chat sites by the MAICA browser extension -- web_chatgpt, web_claude, ...
    (where the extension covers a site, the UI-Automation web backend for it
    stands down)
  - coding agents by their own hooks/logs -- claude_code, codex
  - other desktop apps from the rules' "desktop" list -- lmstudio, ...

Setup:
    py -3.11 -m pip install pywinauto pyserial
    (plus bleak for --ble)

Usage:
    py -3.11 chat_watcher.py --classic-bt                   (Classic BT board, auto-detected COM port)
    py -3.11 chat_watcher.py --backend claude_desktop --classic-bt (single app, old behavior)
    py -3.11 chat_watcher.py --backend chatgpt_desktop --port COM5
    py -3.11 chat_watcher.py                                (console only)
    py -3.11 chat_watcher.py --list                         (every backend name)
"""

import argparse
import sys
import time

import event_server
import focus
import settings as settings_mod
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

POLL_INTERVAL = 1.0  # seconds between checks (pushed events wake it sooner)
DONE_FLASH_SECONDS = 1.5  # how long the green DONE flash holds before returning to idle
RECONNECT_INTERVAL = 5.0  # how often to re-try connecting to an app that isn't open
CHEER_AFTER = (45.0, 240.0)  # a turn at least this long (s) gets celebration level 1, 2

# Behaviour switches, from settings.json (see settings.py) at startup.
UNREAD_ENABLED = True
UNREAD_SECONDS = 20 * 60.0
CHEER_ENABLED = True
ACCENT_ENABLED = True
DOUBLE_TAP_JUMP = True

BACKENDS = {
    "claude_desktop": "claude_desktop_chat_backend.ClaudeDesktopChatBackend",
    "chatgpt_desktop": "chatgpt_desktop_backend.ChatGPTDesktopBackend",
    "claude_web": "claude_web_backend.ClaudeWebBackend",
    "chatgpt_web": "chatgpt_web_backend.ChatGPTWebBackend",
    "claude_code": "claude_code_backend.ClaudeCodeBackend",
    "copilot_desktop": "copilot_desktop_backend.CopilotDesktopBackend",
    "codex": "codex_backend.CodexBackend",
}
# UI Automation backends only exist on Windows; the Mac has its own.
WINDOWS_ONLY = {"claude_desktop", "chatgpt_desktop", "claude_web", "chatgpt_web", "copilot_desktop"}
MAC_BACKENDS = {
    "claude_desktop_mac": "mac_backends.ClaudeDesktopMacBackend",
    "chatgpt_desktop_mac": "mac_backends.ChatGPTDesktopMacBackend",
}
# The browser extension covering a site makes the older web backend redundant.
SUPERSEDED_BY_SITE = {"claude_web": "claude", "chatgpt_web": "chatgpt"}

# Which app each backend is (its rim colour, from maica_rules.json "apps"),
# and where its window is (for "done, unread" and double-tap-to-jump) --
# for backends that don't say themselves (app_id / focus_target attributes).
APP_IDS = {
    "claude_desktop": "claude", "claude_web": "claude", "claude_desktop_mac": "claude",
    "chatgpt_desktop": "chatgpt", "chatgpt_web": "chatgpt", "chatgpt_desktop_mac": "chatgpt",
    "copilot_desktop": "copilot",
}
_BROWSERS = r"(Google Chrome|Microsoft.{0,2}Edge|Mozilla Firefox|Brave|Opera|Vivaldi)$"
FOCUS_TARGETS = {
    "claude_desktop": {"title": "^Claude$", "process": "Claude.exe", "mac_app": "Claude"},
    "chatgpt_desktop": {"title": "^ChatGPT$", "mac_app": "ChatGPT"},
    "claude_web": {"title": "Claude.*" + _BROWSERS},
    "chatgpt_web": {"title": "ChatGPT.*" + _BROWSERS},
    "copilot_desktop": {"title": "^Copilot$"},
}

# When several apps are active at once, the light shows whichever state has
# the highest priority. "Waiting on you" always wins (that's the one you
# can act on), busy states beat the transient DONE flash, a reply you
# haven't read beats out of tokens, and IDLE only shows when nothing
# anywhere is happening.
STATE_PRIORITY = {
    State.QUESTION: 6,
    State.THINKING: 5,
    State.ANALYZING: 4,
    State.DONE: 3,
    State.UNREAD: 2,
    State.LIMIT: 1,
    State.IDLE: 0,
}


def backend_specs() -> dict:
    """Every backend name this platform can run -> (module, class, argument)."""
    specs = {}
    for name, path in BACKENDS.items():
        if sys.platform != "win32" and name in WINDOWS_ONLY:
            continue
        mod, cls = path.rsplit(".", 1)
        specs[name] = (mod, cls, None)
    if sys.platform == "darwin":
        for name, path in MAC_BACKENDS.items():
            mod, cls = path.rsplit(".", 1)
            specs[name] = (mod, cls, None)
    try:
        import extension_backend
        for name, site in extension_backend.backend_names().items():
            specs[name] = ("extension_backend", "BrowserSiteBackend", site)
    except Exception as e:
        print(f"[rules] no browser sites ({e})")
    if sys.platform in ("win32", "darwin"):
        try:
            import desktop_rules_backend
            for name, rid in desktop_rules_backend.backend_names().items():
                specs[name] = ("desktop_rules_backend", "DesktopRuleBackend", rid)
        except Exception as e:
            print(f"[rules] no desktop app rules ({e})")
    return specs


def load_backend(name: str):
    import importlib
    if name in BACKENDS:
        module_path, class_name = BACKENDS[name].rsplit(".", 1)
        return getattr(importlib.import_module(module_path), class_name)()
    specs = backend_specs()
    if name not in specs:
        print(f"Unknown backend {name!r}. Available: {', '.join(specs)}", file=sys.stderr)
        sys.exit(1)
    module_path, class_name, arg = specs[name]
    cls = getattr(importlib.import_module(module_path), class_name)
    return cls(arg) if arg is not None else cls()


def _site_active(site: str) -> bool:
    try:
        import extension_backend
        return extension_backend.site_active(site)
    except Exception:
        return False


def _app_color(app_id: str) -> str:
    try:
        import rules
        return rules.app_info(app_id).get("color", "A0A0A0")
    except Exception:
        return "A0A0A0"


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
        self.limited = False   # out of tokens, per the backend's "limited" report
        self.done_until = 0.0  # when the transient DONE flash expires
        self.next_connect_attempt = 0.0
        self.turn_started = None   # when the current turn began (for celebrations)
        self.cheer = 0             # celebration level waiting to be sent (run_multi clears it)
        self.unread_pending = False
        self.unread_since = 0.0
        self.standing_down = False

    @property
    def app_id(self) -> str:
        return getattr(self.backend, "app_id", None) or APP_IDS.get(self.name, self.name)

    @property
    def shown_state(self) -> State:
        """What this app contributes to the light: its turn's state, or
        LIMIT while it's out of tokens with nothing else going on. (LIMIT
        lives outside the turn state machine: it's a standing condition,
        not a step in a turn, and whatever turn is happening shows over it.)"""
        if self.limited and self.state is State.IDLE:
            return State.LIMIT
        return self.state

    def focus_target(self):
        return getattr(self.backend, "focus_target", None) or FOCUS_TARGETS.get(self.name)

    def is_focused(self):
        """True / False whether this app is the one in front; None = can't tell."""
        own = getattr(self.backend, "is_focused", None)
        if callable(own):
            try:
                return own()
            except Exception:
                return None
        return focus.is_in_front(self.focus_target())

    def bring_to_front(self) -> bool:
        own = getattr(self.backend, "focus", None)
        if callable(own):
            try:
                if own():
                    return True
            except Exception:
                pass
        target = self.focus_target()
        return bool(target) and focus.bring_to_front(target)

    def tick(self, debug: bool = False):
        """One polling cycle for this app. Never raises -- all failure
        modes downgrade to 'disconnected, retry later'."""
        now = time.monotonic()

        # The browser extension covers this site: the UI-Automation backend
        # for it stands down (both reporting would double-count it).
        site = SUPERSEDED_BY_SITE.get(self.name)
        if site and _site_active(site):
            if not self.standing_down:
                print(f"[{self.name}] The browser extension covers {site} now; standing down.")
                self.standing_down = True
            if self.connected:
                self._drop()
            return
        self.standing_down = False

        if not self.connected:
            if now < self.next_connect_attempt:
                return
            self.next_connect_attempt = now + RECONNECT_INTERVAL
            if not self.announced_waiting:
                print(f"[{self.name}] Waiting for app (will keep checking quietly)...")
                self.announced_waiting = True
            try:
                ok = self.backend.try_connect()
            except Exception:
                ok = False
            if ok:
                self.connected = True
                self.announced_waiting = False
                print(f"[{self.name}] Connected -- now watching.")
            return

        # Expire a leftover DONE flash from a previous tick BEFORE handling
        # new events, so a freshly-set DONE below is always guaranteed to
        # survive long enough to actually be displayed at least once.
        if self.state is State.DONE and now >= self.done_until:
            if self.unread_pending:
                self.state = State.UNREAD
                self.unread_since = now
                print(f"[{self.name}] Reply waiting to be read -> UNREAD")
            else:
                self.state = State.IDLE
            self.unread_pending = False
        if self.state is State.UNREAD and (now - self.unread_since > UNREAD_SECONDS or self.is_focused()):
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
            if self.state in (State.IDLE, State.DONE, State.UNREAD):
                self.turn_started = now
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
            # next tick returns this app to IDLE (or UNREAD, if you were
            # elsewhere) once it's had its moment.
            took = now - self.turn_started if self.turn_started is not None else 0.0
            self.cheer = 2 if took >= CHEER_AFTER[1] else 1 if took >= CHEER_AFTER[0] else 0
            self.unread_pending = UNREAD_ENABLED and self.is_focused() is False
            self.turn_started = None

        # Optional keys (older backends don't report them):
        #   failed  -- the turn ended on an error, with no reply: straight
        #              back to idle, no green "done" flash.
        #   limited -- True while the app is out of tokens, False once it
        #              isn't; absent or None = no news, keep what we had.
        if result.get("failed"):
            why = result["failed"] if isinstance(result["failed"], str) else "Turn ended on an error"
            print(f"[{self.name}] {why} -> IDLE")
            self.state = State.IDLE
            self.done_until = 0.0
            self.turn_started = None
            self.unread_pending = False
            self.cheer = 0

        limited = result.get("limited")
        if limited is not None and bool(limited) != self.limited:
            self.limited = bool(limited)
            print(f"[{self.name}] " + ("Out of tokens -> LIMIT" if self.limited else "Tokens available again"))

    def _drop(self):
        """Forget the stale connection and start over with a fresh backend
        instance, so no half-dead UI handles survive into the retry."""
        self.connected = False
        self.state = State.IDLE
        self.limited = False
        self.turn_started = None
        self.unread_pending = False
        self.next_connect_attempt = time.monotonic() + RECONNECT_INTERVAL
        try:
            self.backend = load_backend(self.name)
        except Exception:
            pass  # module vanished mid-run would be bizarre; keep old instance


class FaceSlots:
    """Which apps get their own face on a split-face display (the MAICA
    screen): when two apps are busy at once, each gets half the screen.

    There are two positions, left and right. An app that goes busy takes a
    free position and keeps it while it works and through its DONE flash,
    so a face never jumps sides; once it's resting (idle, or done-unread)
    its position frees up. With one face or none left, the screen goes back
    to a single face (and that face counts as the left one, so the next app
    to go busy splits off to the right). A third busy app waits for a free
    position -- except when it asks you something: a question takes the
    side of an app that's only working (analyzing before thinking), so it's
    never hidden behind two busy faces. The app it displaced is still
    tracked and gets a side back when one frees up. Two questions on screen
    already, or a face mid-DONE-flash, and it waits like any other.

    update() returns [left_state, right_state] while split, else []."""

    BUSY = (State.ANALYZING, State.THINKING, State.QUESTION)
    RESTING = (State.IDLE, State.UNREAD)
    BUMPABLE = (State.ANALYZING, State.THINKING)  # in the order they give way

    def __init__(self):
        self.positions = [None, None]

    def update(self, slots):
        by_name = {s.name: s for s in slots}
        self.positions = [n if n is not None and by_name[n].state not in self.RESTING else None
                          for n in self.positions]
        for s in slots:
            if s.state in self.BUSY and s.name not in self.positions and None in self.positions:
                self.positions[self.positions.index(None)] = s.name
        for s in slots:
            if s.state is State.QUESTION and s.name not in self.positions:
                side = self._side_to_give_up(by_name)
                if side is None:
                    break
                print(f"[faces] {s.name} has a question -> takes {self.positions[side]}'s side")
                self.positions[side] = s.name
        if None in self.positions:
            # One face or none: back to a single face, which counts as the left.
            self.positions = ([n for n in self.positions if n is not None] + [None, None])[:2]
            return []
        return [by_name[n].state for n in self.positions]

    def _side_to_give_up(self, by_name):
        """The side a question may take: one showing an app that's only
        working, analyzing first. None if both are asking or finishing."""
        for st in self.BUMPABLE:
            for i, n in enumerate(self.positions):
                if n is not None and by_name[n].state is st:
                    return i
        return None


def apply_settings(cfg: dict):
    global UNREAD_ENABLED, UNREAD_SECONDS, CHEER_ENABLED, ACCENT_ENABLED, DOUBLE_TAP_JUMP
    UNREAD_ENABLED = bool(cfg.get("unread", True))
    UNREAD_SECONDS = float(cfg.get("unread_minutes", 20)) * 60.0
    CHEER_ENABLED = bool(cfg.get("cheer", True))
    ACCENT_ENABLED = bool(cfg.get("accent", True))
    DOUBLE_TAP_JUMP = bool(cfg.get("double_tap_jump", True))


def jump_target(slots):
    """The app a double-tap should bring forward: one asking you something,
    else a reply you haven't read, else one that just finished, else one
    that's working, else one out of tokens."""
    order = (State.QUESTION, State.UNREAD, State.DONE, State.THINKING, State.ANALYZING, State.LIMIT)
    for st in order:
        for s in slots:
            if s.connected and s.shown_state is st:
                return s
    return None


def accent_colors(slots, face_slots, faces, merged):
    """The rim colour(s) for what MAICA is showing: one per face while split,
    else the app behind the merged state; none when it's just idle."""
    by_name = {s.name: s for s in slots}
    if faces:
        return [_app_color(by_name[n].app_id) for n in face_slots.positions]
    if merged is State.IDLE:
        return []
    owner = next((s for s in slots if s.connected and s.shown_state is merged), None)
    return [_app_color(owner.app_id)] if owner else []


def run_multi(backend_names, make_light, debug: bool = False, stop_event=None, status_cb=None):
    """make_light is a zero-arg factory (not a Light instance): this loop
    owns the light's lifecycle, so a BLE drop only costs the LIGHT a
    reconnect -- app tracking state (armed turns, held questions) survives,
    slots keep tracking while the light is away, and the heartbeat repaints
    the current state the moment it's back. Previously a BLE drop restarted
    everything, so a question arriving during the outage was seen by the
    fresh backend and silently discarded.

    stop_event (a threading.Event) ends the loop -- the tray app uses it;
    status_cb(dict) is called every tick with what's showing."""
    cfg = settings_mod.load()
    apply_settings(cfg)
    slots = [BackendSlot(name) for name in backend_names]
    face_slots = FaceSlots()
    light = None
    shown = None            # None forces a (re)send once a light exists
    shown_faces = None      # likewise for the split-face frame
    shown_apps = None       # ...and the rim colours
    next_light_attempt = 0.0
    light_connected_at = None  # for the link-duration autopsy on each drop
    voice = None

    print(f"Watching {len(slots)} apps at once: {', '.join(s.name for s in slots)}.")
    print("Apps that aren't open yet are checked for every few seconds automatically.\n")

    while not (stop_event is not None and stop_event.is_set()):
        now = time.monotonic()

        if light is None and now >= next_light_attempt:
            try:
                light = make_light()
                shown = None  # repaint whatever state we're in
                shown_faces = None
                shown_apps = None
                light_connected_at = time.monotonic()
                print(f"[light] {time.strftime('%H:%M:%S')} link up.")
                try:
                    light.send_config(settings_mod.face_config(cfg))
                except Exception as e:
                    print(f"[light] couldn't send settings ({e})")
            except Exception as e:
                print(f"[light] {time.strftime('%H:%M:%S')} connect failed ({e}); tracking continues, retrying in 5s.")
                next_light_attempt = time.monotonic() + 5.0

        if light is not None:
            try:
                light.heartbeat()
            except Exception as e:
                light = _drop_light(light, e, light_connected_at)
                next_light_attempt = time.monotonic() + 2.0

        # The Settings window saved: apply it, and send MAICA its part.
        for ev in event_server.hub.take_events("app"):
            if ev.get("cmd") == "reload":
                cfg = settings_mod.load()
                apply_settings(cfg)
                if voice is not None:
                    voice.cfg = cfg.get("voice", {})
                print("[settings] reloaded")
                if light is not None:
                    try:
                        light.send_config(settings_mod.face_config(cfg))
                    except Exception as e:
                        light = _drop_light(light, e, light_connected_at)

        for slot in slots:
            slot.tick(debug=debug)

        merged = max((s.shown_state for s in slots), key=lambda st: STATE_PRIORITY[st])
        faces = face_slots.update(slots)

        if debug:
            summary = ", ".join(
                f"{s.name}={'?' if not s.connected else s.shown_state.value}" for s in slots if s.connected
            )
            light_desc = "light: reconnecting" if light is None else "light: ok"
            split = f", faces {' | '.join(f.value for f in faces)}" if faces else ""
            print(f"[debug] {summary or 'no apps open'} -> showing {merged.value}{split} ({light_desc})")

        try:
            if light is not None and faces != shown_faces:
                if faces:
                    print("[faces] two faces: " + " | ".join(
                        f"{n} {st.value}" for n, st in zip(face_slots.positions, faces)))
                elif shown_faces:
                    print("[faces] back to one face")
                light.show_faces(faces)
                shown_faces = faces

            apps = accent_colors(slots, face_slots, faces, merged) if ACCENT_ENABLED else []
            if light is not None and apps != shown_apps:
                light.set_apps(apps)
                shown_apps = apps

            if light is not None and merged is not shown:
                light.show(merged)
                shown = merged

            for s in slots:
                if s.cheer:
                    if light is not None and CHEER_ENABLED:
                        print(f"[{s.name}] Long task done -> celebration {s.cheer}")
                        light.cheer(s.cheer)
                    s.cheer = 0

            for ev in (light.poll_events() if light is not None else []):
                voice = _board_event(ev, slots, light, cfg, voice)
        except Exception as e:
            light = _drop_light(light, e, light_connected_at)
            next_light_attempt = time.monotonic() + 2.0

        snapshot = {
            "time": time.time(),
            "light": light is not None,
            "showing": merged.value,
            "apps": [{"name": s.name, "app": s.app_id, "connected": s.connected, "state": s.shown_state.value}
                     for s in slots],
        }
        event_server.hub.status = snapshot
        if status_cb is not None:
            try:
                status_cb(snapshot)
            except Exception:
                pass

        # Sleep until the next poll -- or until a pushed event (Claude Code's
        # hook, the browser extension) wakes us at once.
        event_server.hub.wake.wait(POLL_INTERVAL)
        event_server.hub.wake.clear()

    if light is not None:
        try:
            light.close()
        except Exception:
            pass


def _board_event(ev, slots, light, cfg, voice):
    """Something MAICA sent: a double-tap (jump to the app that needs you),
    push-to-talk (dictate into the app in front), or hello on connect."""
    kind = ev[0]
    if kind == "dtap" and DOUBLE_TAP_JUMP:
        target = jump_target(slots)
        if target is None:
            print("[tap] double-tap: nothing needs you")
        else:
            ok = target.bring_to_front()
            print(f"[tap] double-tap -> {target.name}" + ("" if ok else " (couldn't bring it forward)"))
    elif kind in ("ptt", "audio"):
        if not cfg.get("voice", {}).get("enabled", True):
            return voice
        if voice is None:
            import voice as voice_mod
            voice = voice_mod.VoiceInput(cfg.get("voice", {}), notify=lambda ok: light.send_frame(
                "{heard:%d}" % (1 if ok else 0)))
        if kind == "ptt":
            (voice.start if ev[1] else voice.finish)()
        else:
            voice.feed(ev[1])
    elif kind == "hello":
        print(f"[light] MAICA says hello ({ev[1]})")
    return voice


def _drop_light(light, error, connected_at):
    """Best-effort teardown of a failed light; tracking continues without
    it. Prints how long the link survived -- the key diagnostic: durations
    clustering tightly at ~8s mean the firmware's ghost timer is cutting us
    off (heartbeats not arriving); scattered durations (5s, 30s, 2min...)
    mean the link is dying at the radio/OS level (power sag, interference,
    adapter power management)."""
    if light is None:
        return None
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
    limited = False      # out of tokens: LIMIT replaces the idle look between turns

    while True:
        backend.check_alive()

        result = backend.poll(debug=debug)
        rest_changed = False

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
            rest_changed = True

        if result.get("failed"):  # ended on an error: no DONE flash
            print("[event] Turn ended on an error -> IDLE")
            tracker.end_turn()
            rest_changed = True

        if result.get("limited") is not None and bool(result["limited"]) != limited:
            limited = bool(result["limited"])
            print("[event] " + ("Out of tokens -> LIMIT" if limited else "Tokens available again"))
            rest_changed = True

        if rest_changed and not tracker.in_turn:
            tracker.light.show(State.LIMIT if limited else State.IDLE)

        tracker.light.heartbeat()
        event_server.hub.wake.wait(POLL_INTERVAL)
        event_server.hub.wake.clear()


def make_light_factory(args):
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
    return make_light


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Watch chatbot apps and drive a status light/matrix off them directly.")
    parser.add_argument("--backend", type=str, default=None,
                        help="Which app(s) to watch: 'all' (default), one backend name, or a "
                             "comma-separated list (e.g. claude_desktop,web_chatgpt). --list shows them.")
    parser.add_argument("--list", action="store_true", help="List every backend name and exit.")
    parser.add_argument("--port", type=str, default=None, help="Serial port (e.g. COM5, /dev/cu.MAICA).")
    parser.add_argument("--baud", type=int, default=9600, help="Baud rate for --port.")
    parser.add_argument("--classic-bt", action="store_true",
                        help="Auto-detect the paired Classic Bluetooth board's serial port "
                             "(Windows: COM port; Mac: /dev/cu.<name>).")
    parser.add_argument("--classic-bt-name", type=str, default="ClaudeStatusMatrix",
                        help="Paired device name to look up for --classic-bt.")
    parser.add_argument("--gpio", action="store_true", help="Use Raspberry Pi GPIO LEDs instead of console output.")
    parser.add_argument("--ble", action="store_true", help="Use an ESP32-C3 over Bluetooth LE instead of console output.")
    parser.add_argument("--ble-name", type=str, default="ClaudeStatusMatrix", help="BLE device name to scan for (--ble).")
    parser.add_argument("--ble-address", type=str, default=None, help="Skip scanning, connect directly to this BLE address.")
    parser.add_argument("--debug", action="store_true", help="Print verbose tracing of what the watcher sees each poll.")
    return parser.parse_args(argv)


def resolve_backends(choice: str):
    specs = backend_specs()
    if choice in (None, "", "all"):
        return list(specs)
    names = [n.strip() for n in choice.split(",") if n.strip()]
    unknown = [n for n in names if n not in specs]
    if unknown or not names:
        print(f"Unknown backend(s): {', '.join(unknown) or '(none given)'}. "
              f"Available: {', '.join(specs)}", file=sys.stderr)
        sys.exit(1)
    return names


def main(argv=None):
    args = parse_args(argv)
    if args.list:
        for name, (mod, cls, arg) in backend_specs().items():
            print(f"{name:22s} {mod}.{cls}" + (f"({arg})" if arg else ""))
        return

    cfg = settings_mod.load()
    names = resolve_backends(args.backend or cfg.get("backends", "all"))

    if event_server.start():
        import rules
        event_server.hub.rules_json = rules.as_json_bytes()
        print(f"[events] listening on 127.0.0.1:{event_server.PORT} (browser extension, Claude Code hook)")
    else:
        print(f"[events] port {event_server.PORT} is taken (another copy running?); polling only")

    watching = ", ".join(names)
    print(f"Starting chat watcher (watching: {watching}). This will keep running and")
    print("auto-recover if an app closes/reopens, the device disconnects, or a")
    print("transient error occurs. Press Ctrl+C to stop.\n")

    make_light = make_light_factory(args)
    while True:
        light = None
        try:
            if len(names) > 1:
                # Multi mode owns the light lifecycle itself (survives BLE
                # drops without resetting tracking state).
                run_multi(names, make_light, debug=args.debug)
                return
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
