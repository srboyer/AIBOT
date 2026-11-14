"""
updater.py -- "Check for updates": asks GitHub for the newest release of
app_version.RELEASES_REPO and compares it with this app's version. It only
tells you and opens the release page; installing stays your click (an
installer you can see and that Windows / macOS can vet).
"""

import json
import urllib.request

from app_version import RELEASES_REPO, VERSION


def _parse(v: str):
    v = v.strip().lstrip("vV")
    out = []
    for part in v.split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def latest():
    """(version, page url) of the newest release, or None if it can't be told."""
    url = f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "maica-desktop"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        return data.get("tag_name", ""), data.get("html_url", "")
    except Exception:
        return None


def check():
    """(newer available?, message, url)."""
    found = latest()
    if not found or not found[0]:
        return False, "Couldn't check for updates (offline, or no releases published yet).", None
    tag, url = found
    if _parse(tag) > _parse(VERSION):
        return True, f"MAICA {tag} is available (you have {VERSION}).", url
    return False, f"You're up to date (MAICA {VERSION}).", url
