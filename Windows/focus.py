"""
focus.py

Which app is in front, and bringing one to the front -- for "done, unread"
(a reply you haven't looked at yet) and tap-to-jump (double-tap MAICA to go
to the app that needs you). Windows uses the Win32 API through ctypes; the
Mac uses AppKit (pyobjc) and AppleScript. Every call is best-effort and
never raises: None means "can't tell".

A target is described by a dict, any of:
    {"title": regex for the window title (Windows),
     "process": executable name, e.g. "Claude.exe" (Windows),
     "mac_app": application name, e.g. "Claude" (macOS)}
"process" and "mac_app" may also be lists of names.
"""

import re
import subprocess
import sys


def foreground():
    """(window title, process name) of the frontmost window, or (None, None)."""
    try:
        if sys.platform == "win32":
            return _win_foreground()
        if sys.platform == "darwin":
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return None, str(app.localizedName()) if app else None
    except Exception:
        pass
    return None, None


def _names(value):
    return [value] if isinstance(value, str) else list(value or [])


def matches(target: dict, title, process) -> bool:
    if not target:
        return False
    if sys.platform == "darwin":
        return process is not None and process in _names(target.get("mac_app"))
    ok = False
    if target.get("process") and process:
        names = target["process"]  # one program name, or a list of them
        names = [names] if isinstance(names, str) else names
        ok = process.lower() in (n.lower() for n in names)
        if not ok:
            return False
    if target.get("title"):
        return bool(title) and re.search(target["title"], title, re.I) is not None
    return ok


def is_in_front(target: dict):
    """True / False, or None when it can't be told."""
    if not target:
        return None
    title, process = foreground()
    if title is None and process is None:
        return None
    return matches(target, title, process)


def bring_to_front(target: dict) -> bool:
    try:
        if sys.platform == "win32":
            return _win_bring(target)
        if sys.platform == "darwin" and target.get("mac_app"):
            names = _names(target["mac_app"])
            try:  # the one that's running, if any
                from AppKit import NSWorkspace
                running = {str(a.localizedName()) for a in NSWorkspace.sharedWorkspace().runningApplications()}
                names = [n for n in names if n in running] or names
            except Exception:
                pass
            name = names[0].replace('"', "")
            subprocess.run(["osascript", "-e", f'tell application "{name}" to activate'], timeout=5,
                           capture_output=True)
            return True
    except Exception:
        pass
    return False


# -- Windows ------------------------------------------------------------------

def _win_process_name(hwnd):
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    return None


def _win_title(hwnd):
    import ctypes
    n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _win_foreground():
    import ctypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    return _win_title(hwnd), _win_process_name(hwnd)


def _win_bring(target: dict) -> bool:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum(hwnd, _):
        if user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            title = _win_title(hwnd)
            if title and matches(target, title, _win_process_name(hwnd)):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(enum, 0)
    if not found:
        return False
    hwnd = found[0]
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the foreground process hand focus over; pressing and
    # releasing Alt first is the standard way to be allowed to.
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    return bool(user32.SetForegroundWindow(hwnd))
