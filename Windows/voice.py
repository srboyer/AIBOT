"""
voice.py

Push-to-talk (rev-2 MAICA board): hold the button, speak, let go -- what you
said is typed into the app in front (the chat box you're in).

The board streams the microphone while the button is held (16 kHz, 16-bit
mono; see the sketch's pttUpdate). VoiceInput collects it, then on release
transcribes it ON THIS PC with faster-whisper (no audio leaves the machine;
the model -- "base.en" by default, ~150 MB -- downloads once on first use)
and pastes the text into the app in front: clipboard + Ctrl+V / Cmd+V, so
any characters work, with your previous clipboard text put back afterwards.
MAICA nods if it worked and shakes its head if not.

    pip install faster-whisper        (optional: without it, push-to-talk
                                       just shakes its head and says why in
                                       the log)
"""

import struct
import sys
import threading
import time

RATE = 16000
MAX_SECONDS = 35

_model = None
_model_name = None
_model_lock = threading.Lock()


class VoiceInput:
    def __init__(self, cfg: dict, notify=None):
        self.cfg = cfg or {}
        self.notify = notify or (lambda ok: None)
        self.buf = bytearray()
        self.active = False

    def start(self):
        self.buf = bytearray()
        self.active = True
        print("[voice] listening...")

    def feed(self, data: bytes):
        if self.active and len(self.buf) < RATE * 2 * MAX_SECONDS:
            self.buf += data

    def finish(self):
        if not self.active:
            return
        self.active = False
        pcm = bytes(self.buf)
        print(f"[voice] got {len(pcm) / (RATE * 2):.1f}s of audio")
        threading.Thread(target=self._process, args=(pcm,), name="maica-voice", daemon=True).start()

    def _process(self, pcm: bytes):
        ok = False
        try:
            if len(pcm) < RATE * 2 * 0.3:
                print("[voice] too short")
            else:
                text = transcribe(pcm, self.cfg.get("model", "base.en"))
                if text:
                    print(f"[voice] heard: {text!r}")
                    type_text(text, enter=bool(self.cfg.get("auto_send", False)))
                    ok = True
                else:
                    print("[voice] nothing recognised")
        except Exception as e:
            print(f"[voice] failed: {e}")
        try:
            self.notify(ok)
        except Exception:
            pass


def normalise(samples):
    """PDM mics come in quiet: scale so the loudest part peaks near 0.9."""
    peak = max((abs(s) for s in samples), default=0)
    k = 0.9 * 32768 / peak if peak > 300 else 1.0
    return [s * k / 32768.0 for s in samples]


def transcribe(pcm: bytes, model_name: str = "base.en"):
    global _model, _model_name
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[voice] push-to-talk needs faster-whisper: pip install faster-whisper")
        return None
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[: len(pcm) // 2 * 2])
    try:
        import numpy as np
        audio = np.asarray(normalise(samples), dtype=np.float32)
    except ImportError:
        audio = normalise(samples)
    with _model_lock:
        if _model is None or _model_name != model_name:
            print(f"[voice] loading the {model_name} speech model (first time downloads it)...")
            _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            _model_name = model_name
        segments, _info = _model.transcribe(audio, language="en" if model_name.endswith(".en") else None,
                                            vad_filter=True, beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()


# -- typing into the app in front ------------------------------------------------

def type_text(text: str, enter: bool = False):
    if sys.platform == "win32":
        _win_paste(text, enter)
    elif sys.platform == "darwin":
        _mac_paste(text, enter)
    else:
        raise RuntimeError("typing isn't supported on this platform")


def _win_clipboard_get():
    import ctypes
    CF_UNICODETEXT = 13
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = ctypes.c_void_p
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def _win_clipboard_set(text: str):
    import ctypes
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = text.encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):  # another app may hold the clipboard for a moment
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("clipboard busy")
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        u.CloseClipboard()


def _win_keys(*combo):
    import ctypes
    KEYUP = 0x0002
    for vk in combo:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(combo):
        ctypes.windll.user32.keybd_event(vk, 0, KEYUP, 0)


def _win_paste(text: str, enter: bool):
    VK_CONTROL, VK_V, VK_RETURN = 0x11, 0x56, 0x0D
    old = _win_clipboard_get()
    _win_clipboard_set(text)
    time.sleep(0.05)
    _win_keys(VK_CONTROL, VK_V)
    if enter:
        time.sleep(0.1)
        _win_keys(VK_RETURN)
    time.sleep(0.4)  # let the app read it before the clipboard goes back
    if old is not None:
        _win_clipboard_set(old)


def _mac_paste(text: str, enter: bool):
    import subprocess
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    script = 'tell application "System Events" to keystroke "v" using command down'
    if enter:
        script += '\ndelay 0.1\ntell application "System Events" to key code 36'
    subprocess.run(["osascript", "-e", script], capture_output=True)  # needs the Accessibility permission
    time.sleep(0.4)
    subprocess.run(["pbcopy"], input=old)
