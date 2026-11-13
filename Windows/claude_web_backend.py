"""
claude_web_backend.py

ChatBackend implementation for claude.ai open in a WEB BROWSER (Chrome,
Edge, Firefox, Brave...), via pywinauto UI Automation.

Subclasses ClaudeDesktopBackend and inherits all of its detection logic
unchanged (input-box placeholder watching, reverse message scan, thinking
button, Retry-button finished check, question-hold). The desktop app is
Electron wrapping essentially the same frontend as claude.ai, so the UI
tree is expected to match closely. Only the browser-specific parts are
overridden:

1. WINDOW FINDING -- a browser window isn't titled exactly "Claude"; it's
   something like "Planning a trip - Claude - Google Chrome" and the title
   changes as you switch tabs. We regex-match any top-level window whose
   title mentions Claude AND ends with a known browser name (so the
   desktop app's exact "Claude" title can never cross-match), then
   validate each candidate by requiring the real claude.ai UI elements to
   resolve inside it -- which automatically rejects lookalikes such as a
   web search results tab that merely mentions Claude.

2. BACKGROUNDED TABS -- browsers generally only expose the ACTIVE tab's
   content to UI Automation. Tabbing away makes our elements unreachable,
   which the desktop backend would misread as "app closed" and trigger
   reconnect-thrashing. Here, element loss while the window still exists
   is treated as "tab is backgrounded: wait quietly", and elements are
   re-acquired in place when the tab comes back (they get recreated, so
   the old handles are stale).

IMPORTANT -- accessibility must be enabled in the browser first, same
root cause as the Electron/Narrator gotcha (Electron IS Chromium):
Chromium browsers don't render page content into the accessibility tree
until a screen reader has been detected. Either use the Narrator trick
(Win+Ctrl+Enter, reopen the browser, turn Narrator off), or launch Chrome
with the --force-renderer-accessibility flag to make it unconditional.

OUT OF TOKENS (wording unverified): while nothing is running and the tab is
showing, it's searched every few seconds for a usage-limit banner -- see
usage_limit.LimitBannerWatch, and its --debug "[limit]" lines if a real
banner isn't recognised.

UNVERIFIED LABELS -- the web placeholder text may differ from the desktop
app's ("Write a message..."). PLACEHOLDER_HINTS below accepts several
plausible variants; if connection fails with the tab open and accessible,
run inspect_window.py against the browser window and add whatever
placeholder the real input box reports.
"""

import re
import sys
import time

from pywinauto import Desktop

from claude_desktop_backend import ClaudeDesktopBackend, QUESTION_PLACEHOLDER_HINT
from usage_limit import LimitBannerWatch

# Title must END with a recognized browser name -- this is what guarantees
# we can never grab the desktop app (titled exactly "Claude") by mistake.
BROWSER_SUFFIX = r"(Google Chrome|Microsoft.{0,2}Edge|Mozilla Firefox|Brave|Opera|Vivaldi)$"

EMPTY_RESULT = {
    "prompt_sent": False,
    "question_started": False,
    "answer_received": False,
    "thinking_started": False,
    "thinking_ended": False,
    "finished": False,
}


class ClaudeWebBackend(ClaudeDesktopBackend):
    name = "claude_web"

    TITLE_RE = re.compile(r"Claude.*" + BROWSER_SUFFIX, re.IGNORECASE)

    # The web input box's placeholder may not match the desktop app's.
    # Any of these substrings is accepted; extend after inspecting.
    PLACEHOLDER_HINTS = ("Write a message", "Reply to Claude", "How can I help")

    # How many consecutive failed input reads before we try re-acquiring
    # the elements in place (tab likely switched away and back).
    REACQUIRE_AFTER_FAILURES = 3

    def __init__(self):
        super().__init__()
        self.limit_watch = LimitBannerWatch()

    # -- window finding ----------------------------------------------------

    def _candidate_window_specs(self):
        """Every top-level window whose title looks like a browser showing
        Claude. Resolved by window handle so the spec stays pinned to the
        same window even as its title changes with tab switches."""
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
        """Single, non-blocking attempt. A candidate window only counts if
        the actual claude.ai elements resolve inside it -- a news article
        or bookmark folder mentioning Claude fails this and is skipped."""
        for spec in self._candidate_window_specs():
            try:
                input_box = self._get_input_box(spec)
                chat_group = self._get_chat_messages_group(spec)
                # .exists() not .window_text(): forcing resolution blocks
                # ~5s when this browser window matched the title regex but
                # isn't showing an actual conversation -- repeated every
                # retry cycle, that starves the BLE heartbeat (see
                # claude_desktop_backend.try_connect).
                if not chat_group.exists(timeout=0.2, retry_interval=0.1):
                    continue
            except Exception:
                continue
            self.win = spec
            self.input_box = input_box
            self.chat_group = chat_group
            self._reset_poll_state()
            self.limit_watch = LimitBannerWatch()
            return True
        return False

    def connect(self):
        attempt = 0
        while not self.try_connect():
            attempt += 1
            if attempt == 1 or attempt % 12 == 0:
                print("Waiting for a claude.ai browser tab (is browser "
                      "accessibility enabled? see this file's docstring)...",
                      file=sys.stderr)
            time.sleep(5)
        print("Watching claude.ai in the browser. Type a prompt directly in the tab.")

    def _get_input_box(self, win):
        # Same approach as the desktop backend, but accepting the wider
        # set of possible web placeholders.
        try:
            candidates = win.descendants(control_type="Edit")
        except Exception:
            candidates = []

        for c in candidates:
            try:
                text = c.window_text()
            except Exception:
                continue
            lowered = text.lower()
            if any(h.lower() in lowered for h in self.PLACEHOLDER_HINTS) \
                    or QUESTION_PLACEHOLDER_HINT in lowered:
                return c

        raise RuntimeError(
            "No Edit control in this window shows a recognized claude.ai "
            "placeholder. If the tab is open and focused, run "
            "inspect_window.py against the browser and add the real "
            "placeholder text to PLACEHOLDER_HINTS."
        )

    # -- backgrounded-tab tolerance ----------------------------------------

    def poll(self, debug: bool = False) -> dict:
        # Elements going unreachable is EXPECTED in a browser (tab switched
        # away); they're also recreated when the tab returns, leaving our
        # old handles permanently stale. So: after a few consecutive
        # failures, try re-resolving the elements in place...
        if self.consecutive_input_failures >= self.REACQUIRE_AFTER_FAILURES:
            if self._reacquire_elements():
                self.consecutive_input_failures = 0
                if debug:
                    print("[debug] re-acquired claude.ai elements (tab returned)")

        try:
            result = super().poll(debug=debug)
            # Out of tokens: see usage_limit.LimitBannerWatch. Only searched
            # while the tab's own elements answer -- a backgrounded tab shows
            # nothing, which says nothing about a banner.
            result["limited"] = self.limit_watch.check(
                lambda: self.win if self.consecutive_input_failures == 0 else None,
                busy=self.armed or self.awaiting_answer, debug=debug, label=self.name)
            return result
        except RuntimeError:
            # ...and if the parent's liveness limit still trips, only
            # treat it as fatal when the WINDOW itself is gone (raise ->
            # slot reconnects); otherwise the tab is just backgrounded --
            # report nothing and keep waiting quietly. Checked via
            # .exists() so a truly-gone window costs a fast check here,
            # not a ~5s spec-resolution stall.
            if not self.win.exists(timeout=0.5, retry_interval=0.2):
                raise RuntimeError("browser window is gone")
            self.consecutive_input_failures = 0
            if debug:
                print("[debug] claude.ai tab appears backgrounded; waiting quietly")
            return dict(EMPTY_RESULT)

    def _reacquire_elements(self) -> bool:
        try:
            input_box = self._get_input_box(self.win)
            chat_group = self._get_chat_messages_group(self.win)
            # .exists() not forced resolution: while the tab is backgrounded
            # this reacquire attempt runs EVERY poll, and a ~5s resolution
            # stall per poll would starve the BLE heartbeat continuously.
            if not chat_group.exists(timeout=0.2, retry_interval=0.1):
                return False
        except Exception:
            return False
        self.input_box = input_box
        self.chat_group = chat_group
        return True
