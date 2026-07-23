"""
chatgpt_web_backend.py

ChatBackend implementation for chatgpt.com open in a WEB BROWSER, via
pywinauto UI Automation.

Subclasses ChatGPTDesktopBackend and keeps its entire detection strategy:
the Send<->Stop composer button toggle. Only the browser-specific parts
change:

1. WINDOW FINDING -- regex-match any top-level window whose title mentions
   ChatGPT AND ends with a known browser name (so the desktop app's exact
   "ChatGPT" title can never cross-match). Since this backend reads no
   message tree, there's less structure available to validate a candidate
   window with -- a "search" title filter plus the ChatGPT-before-browser
   ordering is the guard. If it ever latches onto the wrong window, run
   with --debug and check which title it connected to.

2. STOP-BUTTON LABEL -- the web version may not title the button exactly
   "Stop" (e.g. "Stop streaming"). Detection therefore matches any Button
   whose title STARTS with the word, via STOP_TITLE_PATTERN below. If
   generation isn't being detected, run inspect_window.py against the
   browser mid-generation and adjust that pattern to the real label.

3. BACKGROUNDED TABS -- browsers only expose the active tab to UIA. For
   this backend that failure mode is benign-by-construction: an absent
   Stop button just reads as "not generating", and check_alive only cares
   about the window (not tab contents), so tabbing away can't cause
   reconnect-thrashing. Worst case, a generation that finishes entirely
   while the tab is backgrounded is missed -- the state machine simply
   catches up on the next real transition.

ACCESSIBILITY: same requirement as claude_web_backend.py -- Chromium
browsers need accessibility enabled once (Narrator trick, or launch
Chrome with --force-renderer-accessibility) before page content appears
in the UIA tree at all.
"""

import re
import sys
import time

from pywinauto import Desktop

from chatgpt_desktop_backend import ChatGPTDesktopBackend

BROWSER_SUFFIX = r"(Google Chrome|Microsoft.{0,2}Edge|Mozilla Firefox|Brave|Opera|Vivaldi)$"


class ChatGPTWebBackend(ChatGPTDesktopBackend):
    name = "chatgpt_web"

    TITLE_RE = re.compile(r"ChatGPT.*" + BROWSER_SUFFIX, re.IGNORECASE)

    # Matches "Stop", "Stop streaming", "Stop generating", ... but not
    # words merely containing "stop".
    STOP_TITLE_PATTERN = r"Stop\b.*"

    # -- window finding ----------------------------------------------------

    def _candidate_window_specs(self):
        specs = []
        try:
            desktop = Desktop(backend="uia")
            for w in desktop.windows():
                try:
                    title = w.window_text()
                except Exception:
                    continue
                if self.TITLE_RE.search(title) and "search" not in title.lower():
                    specs.append(desktop.window(handle=w.handle))
        except Exception:
            pass
        return specs

    def try_connect(self) -> bool:
        """Single, non-blocking attempt (see chat_backend.py)."""
        for spec in self._candidate_window_specs():
            self.win = spec
            self.armed = False
            return True
        return False

    def connect(self):
        attempt = 0
        while not self.try_connect():
            attempt += 1
            if attempt == 1 or attempt % 12 == 0:
                print("Waiting for a chatgpt.com browser tab (is browser "
                      "accessibility enabled? see this file's docstring)...",
                      file=sys.stderr)
            time.sleep(5)
        print("Watching chatgpt.com in the browser. Type a prompt directly in the tab.")

    # -- browser-flexible button matching ----------------------------------

    def _button_exists(self, title):
        # Same instant-return .exists() approach as the desktop backend
        # (never .wrapper_object() -- its hidden ~5s absent-element retry
        # loop is what caused the multi-app lag), but matching by prefix
        # regex since the web label may be longer than the desktop's.
        try:
            pattern = self.STOP_TITLE_PATTERN if title == "Stop" else re.escape(title)
            return self.win.child_window(title_re=pattern, control_type="Button").exists(timeout=0.1, retry_interval=0.1)
        except Exception:
            return False
