"""
mac_ax.py -- small helpers over macOS Accessibility (pyobjc), the Mac's
counterpart of Windows UI Automation: find a running app, walk its element
tree, find a button by its label, read short texts.

Needs: pip install pyobjc-framework-ApplicationServices pyobjc-framework-Cocoa
and MAICA allowed under System Settings > Privacy & Security > Accessibility
(the setup guide opens it). Without that permission every lookup comes back
empty rather than raising.

Electron apps (the Claude desktop app) only build their accessibility tree
once something asks: app_element() sets "AXManualAccessibility" on them,
which is Chromium's switch for exactly that.

UNTESTED on a real Mac so far -- written against Apple's AX API; check with
--debug on first use.
"""

import re
import time


def _ax():
    import ApplicationServices as AS
    return AS


def is_trusted() -> bool:
    try:
        return bool(_ax().AXIsProcessTrusted())
    except Exception:
        return False


def running_app(name: str):
    from AppKit import NSWorkspace
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.localizedName() == name:
            return app
    return None


def app_running(name: str) -> bool:
    return running_app(name) is not None


def app_element(name: str):
    app = running_app(name)
    if app is None:
        return None
    AS = _ax()
    el = AS.AXUIElementCreateApplication(app.processIdentifier())
    try:
        AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
    except Exception:
        pass
    return el


def attr(el, name):
    try:
        err, value = _ax().AXUIElementCopyAttributeValue(el, name, None)
        return value if err == 0 else None
    except Exception:
        return None


def label(el) -> str:
    for a in ("AXTitle", "AXDescription", "AXHelp", "AXValue"):
        v = attr(el, a)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def walk(root, max_nodes=4000, max_seconds=0.25):
    """Breadth-first over the element tree, bounded in size and time so a
    huge conversation can't stall the watcher."""
    if root is None:
        return
    queue, seen, t0 = [root], 0, time.monotonic()
    while queue and seen < max_nodes and time.monotonic() - t0 < max_seconds:
        el = queue.pop(0)
        seen += 1
        yield el
        kids = attr(el, "AXChildren")
        if kids:
            queue.extend(kids)


def find_button(root, pattern, scope=None):
    """The first button whose label matches `pattern` (a compiled regex),
    searching `scope` (a remembered container) first if given."""
    for base in ([scope] if scope is not None else []) + [root]:
        for el in walk(base):
            if attr(el, "AXRole") == "AXButton" and pattern.search(label(el)):
                return el
    return None


def short_texts(root, keywords=("limit", "out of", "credit balance"), max_len=200):
    """Short static texts containing one of the keywords (for limit banners)."""
    out = []
    for el in walk(root, max_nodes=6000):
        if attr(el, "AXRole") in ("AXStaticText", "AXText"):
            t = label(el)
            if t and len(t) <= max_len and any(k in t.lower() for k in keywords):
                out.append(t)
    return out
