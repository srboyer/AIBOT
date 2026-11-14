"""
event_server.py

A small local HTTP server (127.0.0.1 only) that sources which can REPORT
their state -- the MAICA browser extension, Claude Code's hook -- push
events to, instead of the watcher finding out by polling. Two payoffs:

  - speed: a pushed event wakes the watcher loop at once (EventHub.wake),
    so the face changes within a few tens of milliseconds rather than on
    the next once-a-second poll;
  - reach: the browser extension sees every chat site's page directly, on
    Windows and Mac alike, with no screen-reader tricks.

Endpoints (JSON in and out):
  POST /event     {"source": "...", ...}      -- a source reports; the reply
                                                 carries any commands queued
                                                 for it (e.g. "focus this tab")
  GET  /rules                                  -- the site rules, so the
                                                 extension follows the desktop
                                                 app's copy (see rules.py)
  GET  /status                                 -- what the watcher shows now
                                                 (for the tray app and tests)

Safety: it only listens on 127.0.0.1, and POSTs must carry an "X-MAICA"
header. A web page can't add a custom header to a cross-site request without
the server approving a CORS preflight, and preflights are only approved for
browser-extension origins -- so ordinary websites can't feed it fake states.
Everything is best-effort: if the port is taken (another copy running), the
watcher carries on with polling alone.
"""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 47823
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://", "extension://")


class EventHub:
    """Thread-safe store of the latest report from each source, plus a wake
    event the watcher loop waits on between ticks."""

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.reports = {}      # (source, key) -> (monotonic time, payload)
        self.events = {}       # source -> list of pushed one-off events (payload dicts)
        self.commands = {}     # (source, key) -> list of commands to hand back to that client
        self.status = {}       # whatever the watcher publishes for /status
        self.rules_json = b"{}"

    def report(self, source, key, payload):
        with self.lock:
            self.reports[(source, key)] = (time.monotonic(), payload)
        self.wake.set()

    def push_event(self, source, payload):
        with self.lock:
            self.events.setdefault(source, []).append(payload)
        self.wake.set()

    def take_events(self, source):
        with self.lock:
            return self.events.pop(source, [])

    def current(self, source, max_age=None):
        """{key: payload} of a source's live reports, dropping ones older than
        max_age seconds."""
        now = time.monotonic()
        with self.lock:
            out = {}
            for (s, k), (t, p) in list(self.reports.items()):
                if s != source:
                    continue
                if max_age is not None and now - t > max_age:
                    del self.reports[(s, k)]
                    continue
                out[k] = p
            return out

    def last_seen(self, source):
        with self.lock:
            ts = [t for (s, _), (t, _) in self.reports.items() if s == source]
        return max(ts) if ts else None

    def queue_command(self, source, key, command):
        with self.lock:
            self.commands.setdefault((source, key), []).append(command)

    def take_commands(self, source, key):
        """Commands for this client, plus any addressed to the whole source
        (key "*") -- e.g. "focus tab 12" can ride back on any tab's report."""
        with self.lock:
            return self.commands.pop((source, key), []) + self.commands.pop((source, "*"), [])


hub = EventHub()


class _Handler(BaseHTTPRequestHandler):
    server_version = "MAICA/1"

    def log_message(self, *args):  # quiet: the watcher has its own log
        pass

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return origin is None or origin.startswith(EXTENSION_ORIGINS)

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and origin.startswith(EXTENSION_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MAICA")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _reply(self, code, obj):
        body = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self.headers.get("Origin", "").startswith(EXTENSION_ORIGINS):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            return self._reply(403, {"error": "origin"})
        if self.path == "/rules":
            return self._reply(200, hub.rules_json)
        if self.path == "/status":
            with hub.lock:
                return self._reply(200, dict(hub.status))
        return self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not self._origin_ok() or self.headers.get("X-MAICA") is None:
            return self._reply(403, {"error": "forbidden"})
        if self.path != "/event":
            return self._reply(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                return self._reply(413, {"error": "too large"})
            payload = json.loads(self.rfile.read(n) or b"{}")
            source = str(payload.get("source", ""))
            key = str(payload.get("key", ""))
        except Exception:
            return self._reply(400, {"error": "bad json"})
        if not source:
            return self._reply(400, {"error": "no source"})
        if payload.get("kind") == "event":
            hub.push_event(source, payload)
        else:
            hub.report(source, key, payload)
        return self._reply(200, {"ok": True, "commands": hub.take_commands(source, key)})


class _Server(ThreadingHTTPServer):
    # http.server sets SO_REUSEADDR, and on Windows that lets a second
    # program bind a port another one is already listening on -- so a second
    # MAICA app would "start" too and fight the first over the Bluetooth
    # port. Exclusive use makes the second bind fail, which is what start()
    # (and so the app's single-instance check) relies on. On Mac/Linux
    # SO_REUSEADDR only skips TIME_WAIT, which is what we want there.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


_server = None


def start(port: int = PORT) -> bool:
    """Start serving in a background thread. False if the port is taken."""
    global _server
    if _server is not None:
        return True
    try:
        _server = _Server(("127.0.0.1", port), _Handler)
    except OSError:
        return False
    threading.Thread(target=_server.serve_forever, name="maica-events", daemon=True).start()
    return True


def stop():
    global _server
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None


def post(payload: dict, port: int = PORT, timeout: float = 0.3) -> bool:
    """Push from another process (e.g. the Claude Code hook). Never raises."""
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/event", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "X-MAICA": "1"}, method="POST")
        urllib.request.urlopen(req, timeout=timeout).read()
        return True
    except Exception:
        return False
