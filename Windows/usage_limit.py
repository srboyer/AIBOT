"""
usage_limit.py

"Out of tokens" detection, shared by the backends. While an AI app has run
out of usage -- a Claude plan's 5-hour or weekly limit, a ChatGPT plan
limit, an API account out of credit -- MAICA shows its own face for it
(State.LIMIT), and goes back to normal once the limit resets.

Backends report it through poll()'s optional "limited" key (see
chat_backend.py). There are two ways to find out:

  - Claude Code says so exactly. Its StopFailure hook fires instead of Stop
    when an API error ends a turn, with the kind of error and the message it
    showed you -- for a plan limit, error "rate_limit" and a message like
    "You've hit your 5-hour limit · resets 3pm"; for an API key out of
    credit, "billing_error" and "Credit balance is too low" (both read out of
    Claude Code itself). is_limit_error() decides whether an error means
    "out of usage", and parse_reset() reads when it ends from the message.

  - The chat apps (Claude desktop / claude.ai, ChatGPT desktop / chatgpt.com)
    only show it on screen, as a banner by the message box. LimitBannerWatch
    looks for one every few seconds while the app is idle: it asks UI
    Automation for the few on-screen texts that contain "limit", "out of"
    and the like (a substring search done in one call, not a walk over every
    element), then checks each against LIMIT_BANNER_RE, which must match at
    the START of a short text -- a banner reads "You've hit your limit...",
    whereas a chat message merely mentioning limits rarely starts that way.
    The banner wording is the apps' and changes without notice; if a real
    banner isn't recognised, run the watcher with --debug: every candidate
    text it saw is printed as a "[limit]" line, ready to add here.
"""

import datetime as _dt
import re
import time

# Claude Code StopFailure error kinds that mean "out of usage" (see
# is_limit_error for how "rate_limit" is told apart from a plain throttle).
LIMIT_ERRORS = ("billing_error", "rate_limit")

# Claude Code's own rate_limit messages that are NOT your usage running out:
# the API throttling everyone for a moment.
NOT_A_LIMIT = ("not your usage limit", "high load", "temporarily limiting")

# Without a reset time to go by, a limit is assumed to last this long -- the
# length of Claude's shortest usage window. Starting a new turn finds out
# sooner either way: it either goes through (limit over) or hits it again.
FALLBACK_HOLD = _dt.timedelta(hours=5)

# How often the chat apps are searched for a limit banner while idle.
BANNER_SCAN_SECONDS = 10.0

# Texts longer than this are messages, not banners.
BANNER_MAX_CHARS = 200

_APOS = "['’]?"
LIMIT_BANNER_RE = re.compile(
    r"^\W{0,3}(?:"
    rf"you{_APOS}ve (?:hit|reached|used up) (?:your|the|our)\b.{{0,60}}?\b(?:limit|cap|messages|usage)\b"
    rf"|you (?:have )?(?:hit|reached) (?:your|the|our)\b.{{0,60}}?\b(?:limit|cap)\b"
    rf"|you{_APOS}re out of\b|you are out of\b|out of (?:free )?messages\b"
    r"|(?:claude (?:ai )?)?(?:usage|message|rate|5-hour|five-hour|weekly|daily|plan) limit reached\b"
    r"|limit reached\b"
    r"|credit balance is too low\b"
    r"|your (?:org|organization) is out of usage\b"
    r")",
    re.IGNORECASE,
)

# What the one-call substring search asks UI Automation for; LIMIT_BANNER_RE
# then has the final say on each hit.
BANNER_KEYWORDS = ("limit", "out of", "credit balance")


def is_limit_error(error, message="") -> bool:
    """Whether a Claude Code StopFailure means the account is out of usage.
    billing_error always does. rate_limit does unless the message is one of
    Claude Code's transient-throttle texts (NOT_A_LIMIT) -- Claude Code
    retries throttles itself, so a turn that still ends on one is rare."""
    if error not in LIMIT_ERRORS:
        return False
    text = (message or "").lower()
    if error == "rate_limit" and any(s in text for s in NOT_A_LIMIT):
        return False
    return True


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RESET_IN_RE = re.compile(
    r"\breset[s]?\s+in\s+(?:(?P<h>\d+)\s*(?:h|hr|hrs|hour|hours)\b)?\s*(?:(?P<m>\d+)\s*(?:m|min|mins|minute|minutes)\b)?",
    re.IGNORECASE)
_RESET_AT_RE = re.compile(
    r"\b(?:reset[s]?|until|after)\s+(?:at\s+|on\s+)?"
    r"(?:(?P<wd>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
    r"(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?:at\s+)?)?"
    r"(?P<hh>\d{1,2})(?::(?P<mm>\d{2}))?\s*(?P<ap>[ap]\.?\s?m\.?)?(?![\w:])",
    re.IGNORECASE)


def parse_reset(text, now=None):
    """When the limit in `text` ends, as a local datetime, or None if the
    text doesn't say. Understands Claude Code's "resets 3pm", "resets
    3:30pm", "resets Nov 14, 9am" (an optional "(Europe/London)" after it is
    ignored -- times are taken as this PC's local time), "resets in 2h 15m",
    and the chat apps' "until 3:00 PM" / "resets after 4:12 PM"."""
    if not text:
        return None
    now = now or _dt.datetime.now()

    m = _RESET_IN_RE.search(text)
    if m and (m.group("h") or m.group("m")):
        return now + _dt.timedelta(hours=int(m.group("h") or 0), minutes=int(m.group("m") or 0))

    for m in _RESET_AT_RE.finditer(text):
        hh, mm, ap = int(m.group("hh")), int(m.group("mm") or 0), m.group("ap")
        if not ap and m.group("mm") is None:
            continue  # a bare number ("resets 5") isn't a time
        if ap:
            if not 1 <= hh <= 12:
                continue
            hh = hh % 12 + (12 if ap.lower().startswith("p") else 0)
        if hh > 23 or mm > 59:
            continue
        if m.group("mon"):
            month = _MONTHS.index(m.group("mon").lower()[:3]) + 1
            try:
                when = now.replace(month=month, day=int(m.group("day")), hour=hh, minute=mm, second=0, microsecond=0)
            except ValueError:
                continue
            if when < now - _dt.timedelta(days=1):  # "Jan 2" seen on Dec 30: next year
                when = when.replace(year=when.year + 1)
            return when
        when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if m.group("wd"):
            ahead = (_WEEKDAYS.index(m.group("wd").lower()[:3]) - now.weekday()) % 7
            when += _dt.timedelta(days=ahead)
            if when <= now:
                when += _dt.timedelta(days=7)
        elif when <= now:
            when += _dt.timedelta(days=1)
        return when
    return None


def looks_like_limit_banner(text) -> bool:
    text = (text or "").strip()
    return 0 < len(text) <= BANNER_MAX_CHARS and bool(LIMIT_BANNER_RE.search(text))


def _candidate_texts(root):
    """Names of the elements under `root` (a pywinauto wrapper or window
    spec) that contain one of BANNER_KEYWORDS. One UI Automation call doing
    the substring match on its side, so its cost doesn't grow with a long
    conversation's thousands of elements (reading each name from here
    would)."""
    from pywinauto.uia_defines import IUIA

    wrapper = root.wrapper_object() if hasattr(root, "wrapper_object") else root
    element = wrapper.element_info.element
    iuia = IUIA().iuia
    UIA_NamePropertyId, TreeScope_Descendants = 30005, 4
    IGNORE_CASE_SUBSTRING = 1 | 2  # PropertyConditionFlags_IgnoreCase | _MatchSubstring (Windows 10 1809+)
    conds = [iuia.CreatePropertyConditionEx(UIA_NamePropertyId, k, IGNORE_CASE_SUBSTRING) for k in BANNER_KEYWORDS]
    cond = conds[0]
    for c in conds[1:]:
        cond = iuia.CreateOrCondition(cond, c)
    found = element.FindAll(TreeScope_Descendants, cond)
    names = []
    for i in range(min(found.Length, 60)):
        try:
            names.append(found.GetElement(i).CurrentName or "")
        except Exception:
            continue
    return names


class LimitBannerWatch:
    """Remembers whether a chat app is showing a usage-limit banner,
    searching for one at most every BANNER_SCAN_SECONDS -- and straight away
    when a turn ends -- while the app is idle. Mid-turn it keeps its last
    answer: the app is clearly answering, and a state that's showing work
    outranks LIMIT anyway."""

    def __init__(self, every: float = BANNER_SCAN_SECONDS):
        self.every = every
        self.limited = False
        self.next_scan = 0.0
        self.was_busy = False

    def check(self, get_root, busy: bool, debug: bool = False, label: str = "") -> bool:
        """get_root: called only when a search is due; returns what to search
        (a pywinauto wrapper or window spec), or None when the app's page
        can't be seen right now (a background browser tab, another mode of
        the app showing) -- then the last answer stands, since not finding a
        banner there means nothing."""
        now = time.monotonic()
        if self.was_busy and not busy:
            self.next_scan = 0.0  # a turn just ended: was it cut short by the limit?
        self.was_busy = busy
        if busy or now < self.next_scan:
            return self.limited
        self.next_scan = now + self.every
        try:
            root = get_root()
            if root is None:
                return self.limited
            texts = _candidate_texts(root)
        except Exception as e:
            if debug:
                print(f"[limit] {label}: banner search failed ({e})")
            return self.limited
        hit = next((t for t in texts if looks_like_limit_banner(t)), None)
        if debug and texts:
            for t in texts[:8]:
                if len(t) <= BANNER_MAX_CHARS:
                    print(f"[limit] {label}: {'MATCH ' if t is hit else ''}{t!r}")
        self.limited = hit is not None
        return self.limited
