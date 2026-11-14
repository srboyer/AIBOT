"""
chatgpt_web_backend.py

ChatBackend implementation for chatgpt.com open in a WEB BROWSER, via
pywinauto UI Automation.

Subclasses ChatGPTDesktopBackend and keeps its entire detection strategy:
the Send<->Stop composer button toggle, plus the "Custom answer for..."
Edit-control signal for interactive clarifying questions (see that file's
docstring). Only the browser-specific parts change:

1. WINDOW FINDING -- NOT by title. Claude's web tab titles always keep
   "- Claude" even after the conversation gets auto-titled (see
   claude_web_backend.py's TITLE_RE), but ChatGPT's tab title becomes JUST
   the conversation's own title once auto-titled ("Trip Planning
   Assistance", not "Trip Planning Assistance - ChatGPT") -- confirmed live
   against a real renamed conversation. A title regex requiring the word
   "ChatGPT" therefore only ever matches a brand-new, still-unnamed chat,
   and silently fails to (re)connect to literally every real conversation
   a few seconds later. Candidates are instead: any top-level window ending
   in a known browser name (BROWSER_SUFFIX, cheap title check, filters out
   non-browser windows before any tree walk), validated by requiring the
   browser's own address bar to show a chatgpt.com URL
   (CHATGPT_URL_EDIT_RE) -- reliable regardless of tab title/renaming, and
   incidentally a strictly better guard than the old title-content
   filtering ever was (a Google search results page merely mentioning
   "chatgpt" was never going to have chatgpt.com as ITS OWN address).

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

from chatgpt_desktop_backend import ChatGPTDesktopBackend, in_conversation
from usage_limit import LimitBannerWatch

BROWSER_SUFFIX = r"(Google Chrome|Microsoft.{0,2}Edge|Mozilla Firefox|Brave|Opera|Vivaldi)$"

# Matches the browser's address bar Edit control showing a chatgpt.com URL,
# with or without a "https://" / "www." prefix -- see module docstring for
# why this replaces title-based matching entirely.
CHATGPT_URL_EDIT_RE = r".*chatgpt\.com.*"


class ChatGPTWebBackend(ChatGPTDesktopBackend):
    name = "chatgpt_web"

    BROWSER_RE = re.compile(BROWSER_SUFFIX, re.IGNORECASE)

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
                if not self.BROWSER_RE.search(title):
                    continue
                spec = desktop.window(handle=w.handle)
                try:
                    has_chatgpt_url = spec.child_window(
                        control_type="Edit", title_re=CHATGPT_URL_EDIT_RE
                    ).exists(timeout=0.2, retry_interval=0.1)
                except Exception:
                    continue
                if has_chatgpt_url:
                    specs.append(spec)
        except Exception:
            pass
        return specs

    def try_connect(self) -> bool:
        """Single, non-blocking attempt (see chat_backend.py)."""
        for spec in self._candidate_window_specs():
            self.win = spec
            self.armed = False
            self.awaiting_answer = False
            self.limit_watch = LimitBannerWatch(skip_inside=in_conversation)
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

    def _page_visible(self) -> bool:
        # Only the active tab is exposed: when chatgpt.com isn't it, finding
        # no limit banner says nothing (see LimitBannerWatch.check).
        try:
            return self.win.child_window(
                control_type="Edit", title_re=CHATGPT_URL_EDIT_RE
            ).exists(timeout=0.1, retry_interval=0.1)
        except Exception:
            return False

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
