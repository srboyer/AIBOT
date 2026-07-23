"""
inspect_claude_desktop.py (v2)

Diagnostic tool -- NOT the final tracker. This dumps the accessibility tree
of the Claude Desktop window so we can identify:
  1. The button that toggles between "Send" and "Stop generating"
     (this is how we'll detect analyzing vs idle)
  2. The element containing Claude's response text
     (this is how we'll read the text for question-mark detection)

Setup:
    py -3.11 -m pip install pywinauto

Usage:
    1. Open Claude Desktop and make sure it's visible.
    2. Run:  py -3.11 inspect_claude_desktop.py
       This lists real application windows (terminal/console windows are
       excluded automatically so they don't get mistaken for the app).
    3. Find the Claude Desktop entry, copy its EXACT title text.
    4. Re-run with that exact title in quotes:
           py -3.11 inspect_claude_desktop.py "exact title text"
    5. Copy/paste the full output back so we can identify the right elements.
"""

import sys
import io

# Redirecting output to a file on Windows (py inspect_window.py ... > dump.txt)
# switches stdout's encoding from the console's to cp1252, which can't
# represent characters like smart quotes, em dashes, or other symbols that
# show up in real app UI text -- and print() then crashes with a
# UnicodeEncodeError instead of writing the file. Force UTF-8 with a safe
# fallback so a dump can never silently die partway through.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from pywinauto import Desktop
except ImportError:
    print("pywinauto isn't installed. Run: py -3.11 -m pip install pywinauto")
    sys.exit(1)

# Window classes that are consoles/terminals, not real apps -- filtered out
# of the listing so a terminal's own title (which may echo the command you
# typed, including this script's path) doesn't get mistaken for the target.
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}


def list_all_windows():
    print("Top-level application windows currently visible:\n")
    for w in Desktop(backend="uia").windows():
        try:
            title = w.window_text()
            cls = w.element_info.class_name
        except Exception:
            continue
        if not title.strip():
            continue
        if cls in CONSOLE_CLASSES:
            continue  # skip terminal/console windows
        print(f"  - {title!r}   (class: {cls})")

    print('\nFind the Claude Desktop entry above, copy its EXACT title, then run:')
    print('    py -3.11 inspect_claude_desktop.py "exact title text"')


def dump_window(exact_title, depth=30):
    # Singular .window() returns a WindowSpecification, which has
    # print_control_identifiers(); plural .windows() does not.
    spec = Desktop(backend="uia").window(title=exact_title)
    try:
        resolved_title = spec.window_text()
    except Exception:
        print(f"Couldn't find a window titled exactly {exact_title!r}.")
        print("Run with no arguments first to see the exact available titles.")
        return

    print(f"Found window: {resolved_title!r}\n")
    print(f"Full control tree, depth={depth} (this is what we need to identify the right elements):\n")
    spec.print_control_identifiers(depth=depth)


if __name__ == "__main__":
    if len(sys.argv) > 2:
        dump_window(sys.argv[1], depth=int(sys.argv[2]))
    elif len(sys.argv) > 1:
        dump_window(sys.argv[1])
    else:
        list_all_windows()
