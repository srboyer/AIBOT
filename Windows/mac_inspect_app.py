#!/usr/bin/env python3
"""
mac_inspect_app.py -- macOS equivalent of inspect_window.py.

Dumps the accessibility (AXUIElement) tree of a running app so we can find
the elements/labels needed to build a Mac backend for the chat watcher.

SETUP (one time, on the Mac):
    1. Install pyobjc:            pip3 install pyobjc
    2. Grant accessibility permission: the FIRST run of this script will
       fail with a clear message; go to System Settings -> Privacy &
       Security -> Accessibility, and enable your terminal app (Terminal
       or iTerm). Then run again. (The permission belongs to whatever
       launches Python.)

USAGE:
    python3 mac_inspect_app.py                # dumps 'Claude' if running
    python3 mac_inspect_app.py ChatGPT        # dumps ChatGPT
    python3 mac_inspect_app.py Claude 25      # deeper tree (default depth 18)

Output goes to the console AND to mac_dump_<app>.txt next to this script.

NOTE: this script automatically sets the app's "AXManualAccessibility"
attribute -- the macOS equivalent of the Narrator trick. Chromium/Electron
apps don't populate their accessibility tree until an assistive client
announces itself; on Windows that required actually toggling Narrator, but
on macOS it can be requested programmatically, which is good news for
eventually making this customer-invisible.
"""

import sys

try:
    from AppKit import NSWorkspace
    import ApplicationServices as AS
except ImportError:
    print("pyobjc is not installed. Run:  pip3 install pyobjc")
    sys.exit(1)

# Attributes worth showing for each element, in display order.
INTERESTING_ATTRS = [
    "AXRole",
    "AXSubrole",
    "AXTitle",
    "AXDescription",
    "AXValue",
    "AXPlaceholderValue",
    "AXHelp",
]

MAX_TEXT = 90  # truncate long values so the dump stays readable


def ax_get(element, attr):
    """Read one AX attribute; returns None if absent/unreadable."""
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, attr, None)
    except Exception:
        return None
    if err != 0:
        return None
    return value


def describe(element):
    parts = []
    for attr in INTERESTING_ATTRS:
        value = ax_get(element, attr)
        if value is None:
            continue
        text = str(value)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "..."
        text = text.replace("\n", "\\n")
        if attr == "AXRole":
            parts.append(text)  # role leads the line, unlabeled
        else:
            parts.append(f"{attr[2:]}={text!r}")
    return "  ".join(parts) if parts else "(no readable attributes)"


def walk(element, out_lines, depth=0, max_depth=18):
    out_lines.append("  " * depth + describe(element))
    if depth >= max_depth:
        out_lines.append("  " * (depth + 1) + "[max depth reached]")
        return
    children = ax_get(element, "AXChildren")
    if not children:
        return
    for child in children:
        walk(child, out_lines, depth + 1, max_depth)


def find_app(name):
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if (app.localizedName() or "").lower() == name.lower():
            return app
    return None


def main():
    app_name = sys.argv[1] if len(sys.argv) > 1 else "Claude"
    max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 18

    if not AS.AXIsProcessTrusted():
        print("This process is NOT trusted for accessibility yet.")
        print("Fix: System Settings -> Privacy & Security -> Accessibility,")
        print("enable your terminal app (Terminal/iTerm), then rerun.")
        sys.exit(1)

    app = find_app(app_name)
    if app is None:
        running = sorted({a.localizedName() or "?" for a in
                          NSWorkspace.sharedWorkspace().runningApplications()
                          if a.activationPolicy() == 0})
        print(f"No running app named {app_name!r}. Regular apps currently running:")
        for name in running:
            print(f"  - {name}")
        sys.exit(1)

    pid = app.processIdentifier()
    print(f"Found {app_name!r} (pid {pid}). Dumping accessibility tree...")

    ax_app = AS.AXUIElementCreateApplication(pid)

    # The Electron accessibility wake-up, macOS edition: politely ask the
    # app to populate its tree. Errors are ignored -- native apps don't
    # have this attribute and don't need it.
    for attr in ("AXManualAccessibility", "AXEnhancedUserInterface"):
        try:
            AS.AXUIElementSetAttributeValue(ax_app, attr, True)
        except Exception:
            pass

    import time
    time.sleep(1.0)  # give the renderer a moment to build the tree

    out_lines = [f"=== Accessibility dump of {app_name!r} (pid {pid}) ==="]
    walk(ax_app, out_lines, max_depth=max_depth)

    dump_name = f"mac_dump_{app_name.lower()}.txt"
    with open(dump_name, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))

    print("\n".join(out_lines))
    print(f"\n[done] {len(out_lines)} lines. Also saved to {dump_name}")
    print("If the tree looks nearly empty (just a window and no content),")
    print("the accessibility wake-up may need a moment -- try rerunning once.")


if __name__ == "__main__":
    main()
