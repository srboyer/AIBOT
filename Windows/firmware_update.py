"""
firmware_update.py

Flashes MAICA's firmware over USB from the desktop app -- no Arduino IDE.

Two sources:
  - bundled: the release build puts the compiled firmware next to the app
    as firmware/bootloader.bin, partitions.bin, boot_app0.bin and maica.bin
    (build_release.ps1 / build_mac.sh copy them from the sketch's build).
  - from the sketch: running from a source checkout with the Arduino IDE
    (or arduino-cli) installed, the sketch in maica_status_classicbt/ is
    built fresh first -- the developer loop: edit, press Flash.

Exactly the four parts the Arduino IDE writes, each at its own address:
the bootloader (0x1000), the partition table (0x8000), boot_app0 (0xe000)
and the app (0x10000). NOT one merged image at 0x0: that covers the NVS
partition (0x9000) too, and wiping NVS forgets MAICA's Bluetooth pairing
and its saved settings.

Getting into download mode: esptool resets the board through the USB chip's
DTR/RTS lines with GPIO0 held low. Where that doesn't work (the 09-16 board
reboots but never enters download mode -- "Wrong boot mode detected"), you
hold BOOT, tap RESET, let go of BOOT, and it's flashed without the reset
dance (--before no-reset). The window walks you through it.

    py -3.11 firmware_update.py --list
    py -3.11 firmware_update.py --port COM6 --build [--round]     build the sketch, then flash
    py -3.11 firmware_update.py --port COM6 [--no-reset]          the bundled firmware

Over Bluetooth it can't be done: Classic Bluetooth serial is far too slow
and the board can't rewrite itself from it safely.
"""

import argparse
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys

from app_paths import bundled, config_dir

# USB-serial chips an ESP32 board shows up as: Silicon Labs CP210x (MAICA's
# CP2102N), WCH CH34x, FTDI, Espressif's own USB.
USB_SERIAL_VIDS = {0x10C4: "Silicon Labs CP210x", 0x1A86: "WCH CH34x", 0x0403: "FTDI", 0x303A: "Espressif"}
MAICA_VID = 0x10C4
BAUD = 921600

HERE = os.path.dirname(os.path.abspath(__file__))
SKETCH_DIR = os.path.join(HERE, "maica_status_classicbt")
FQBN = ("esp32:esp32:esp32wrover:UploadSpeed=921600,FlashFreq=80,FlashMode=qio,"
        "PartitionScheme=default,DebugLevel=none,EraseFlash=none")
PART_NAMES = (  # (address, bundled file name, build-folder file name)
    (0x1000, "bootloader.bin", "maica_status_classicbt.ino.bootloader.bin"),
    (0x8000, "partitions.bin", "maica_status_classicbt.ino.partitions.bin"),
    (0xE000, "boot_app0.bin", "boot_app0.bin"),
    (0x10000, "maica.bin", "maica_status_classicbt.ino.bin"),
)


# -- where the firmware comes from -------------------------------------------------

def bundled_parts():
    """[(address, path)] of the firmware shipped with the app, or None."""
    parts = [(addr, bundled(os.path.join("firmware", name))) for addr, name, _ in PART_NAMES]
    return parts if all(os.path.exists(p) for _, p in parts) else None


def find_arduino_cli():
    """The arduino-cli the Arduino IDE ships with (or one on PATH), or None."""
    candidates = [shutil.which("arduino-cli")]
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA") and
                     os.path.join(os.environ["LOCALAPPDATA"], "Programs")):
            if base:
                candidates.append(os.path.join(base, "Arduino IDE", "resources", "app", "lib", "backend",
                                               "resources", "arduino-cli.exe"))
    elif sys.platform == "darwin":
        candidates.append("/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli")
    return next((c for c in candidates if c and os.path.exists(c)), None)


def can_build() -> bool:
    return os.path.isdir(SKETCH_DIR) and find_arduino_cli() is not None


def sketch_screen_round() -> bool:
    """What the sketch itself is set to (its #define SCREEN_ROUND)."""
    try:
        with open(os.path.join(SKETCH_DIR, "maica_status_classicbt.ino"), encoding="utf-8") as f:
            m = re.search(r"^\s*#define\s+SCREEN_ROUND\s+(\d)", f.read(), re.M)
        return bool(m and m.group(1) == "1")
    except OSError:
        return False


def build_dir() -> str:
    return os.path.join(config_dir(), "firmware_build")


def build(round_screen=None, log=print):
    """Compile the sketch (arduino-cli, the Arduino IDE's own libraries and
    boards). round_screen True/False overrides the sketch's SCREEN_ROUND,
    None keeps it. Returns [(address, path)] or None."""
    cli = find_arduino_cli()
    if cli is None:
        log("Couldn't find the Arduino IDE (or arduino-cli) to build the sketch.")
        return None
    out = build_dir()
    cmd = [cli, "compile", "--fqbn", FQBN, "--build-path", out]
    ide_config = os.path.join(os.path.expanduser("~"), ".arduinoIDE", "arduino-cli.yaml")
    if os.path.exists(ide_config):
        cmd += ["--config-file", ide_config]  # the IDE's sketchbook libraries and installed boards
    if round_screen is not None:
        cmd += ["--build-property", f"compiler.cpp.extra_flags=-DSCREEN_ROUND={1 if round_screen else 0}"]
    cmd.append(SKETCH_DIR)
    log(f"Building the sketch for the {'round' if (sketch_screen_round() if round_screen is None else round_screen) else 'square'} "
        "screen (the first build takes a couple of minutes)...")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", **kwargs)
    except OSError as e:
        log(f"Couldn't run arduino-cli: {e}")
        return None
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        if line:
            tail = (tail + [line])[-25:]
            if line.startswith(("Sketch uses", "Global variables")) or "error" in line.lower():
                log(line)
    if p.wait() != 0:
        log("The build failed:")
        for line in tail:
            log("  " + line)
        return None
    parts = [(addr, os.path.join(out, name)) for addr, _, name in PART_NAMES]
    missing = [p for _, p in parts if not os.path.exists(p)]
    if missing:
        log("Built, but these are missing: " + ", ".join(os.path.basename(m) for m in missing))
        return None
    return parts


# -- the board --------------------------------------------------------------------

def board_ports():
    """[(device, description)] of USB-serial ports that could be MAICA,
    MAICA's own chip (CP210x) first."""
    from serial.tools import list_ports
    found = []
    for p in list_ports.comports():
        if p.vid in USB_SERIAL_VIDS:
            found.append((p.device, f"{p.device} - {USB_SERIAL_VIDS[p.vid]}" + (f" ({p.description})" if p.description else ""),
                          p.vid == MAICA_VID))
    found.sort(key=lambda d: (not d[2], d[0]))
    return [(dev, desc) for dev, desc, _ in found]


def esptool_args(port: str, parts, reset: bool = True):
    args = ["--chip", "esp32", "--port", port, "--baud", str(BAUD),
            "--before", "default-reset" if reset else "no-reset", "--after", "hard-reset",
            "write-flash", "-z", "--flash-mode", "keep", "--flash-freq", "keep", "--flash-size", "keep"]
    for addr, path in parts:
        args += [hex(addr), path]
    return args


def flash(port: str, parts, reset: bool = True, log=print) -> str:
    """Write the firmware parts to the board on `port`. reset=False: the
    board is already in download mode (BOOT + RESET). Returns "ok",
    "needs-boot" (it didn't enter download mode by itself: press the
    buttons and try with reset=False), "busy" (something else has the
    port) or "failed". esptool's progress goes to log(line)."""
    import esptool

    seen = []

    class _Lines(io.TextIOBase):
        def __init__(self):
            self.part = ""

        def write(self, s):
            self.part += s
            while "\n" in self.part or "\r" in self.part:
                i = min(x for x in (self.part.find("\n"), self.part.find("\r")) if x >= 0)
                line, self.part = self.part[:i].strip(), self.part[i + 1:]
                if line:
                    seen.append(line)
                    if not line.startswith("Writing at") or line.endswith("100.0%"):
                        log(line)
            return len(s)

    out = _Lines()
    ok = False
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            esptool.main(esptool_args(port, parts, reset))
        ok = True
    except SystemExit as e:
        ok = e.code in (0, None)
    except Exception as e:
        seen.append(str(e))
        log(f"Failed: {e}")
    if ok:
        log("Done -- MAICA restarts with the new firmware, and the app reconnects to it by itself.")
        return "ok"
    text = " ".join(seen).lower()
    if "access is denied" in text or "port is busy" in text or "permissionerror" in text:
        return "busy"
    if "wrong boot mode" in text or "failed to connect" in text or "no serial data received" in text:
        return "needs-boot"
    return "failed"


BOOT_STEPS = ("MAICA didn't switch into download mode by itself. Hold its BOOT button, tap RESET, "
              "let go of BOOT -- the screen stays dark -- then flash again.")
BUSY_STEPS = ("Something else has MAICA's USB port open -- usually the Arduino IDE's Serial Monitor. "
              "Close it and flash again.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flash MAICA's firmware over USB.")
    ap.add_argument("--list", action="store_true", help="list USB serial ports that could be MAICA")
    ap.add_argument("--port", help="the port MAICA is on (e.g. COM6, /dev/cu.usbserial-0001)")
    ap.add_argument("--build", action="store_true", help="build the sketch first instead of the bundled firmware")
    screen = ap.add_mutually_exclusive_group()
    screen.add_argument("--round", action="store_true", help="with --build: for the round screen")
    screen.add_argument("--square", action="store_true", help="with --build: for the square screen")
    ap.add_argument("--no-reset", action="store_true", help="MAICA is already in download mode (BOOT + RESET)")
    ap.add_argument("--dry-run", action="store_true", help="show what would run, don't flash")
    a = ap.parse_args(argv)
    if a.list or not a.port:
        ports = board_ports()
        print("\n".join(d for _, d in ports) or "No USB serial ports that look like MAICA. Plugged in with a data cable?")
        return 0
    if a.build:
        parts = build(True if a.round else False if a.square else None)
    else:
        parts = bundled_parts()
        if parts is None:
            print("No bundled firmware next to the app -- use --build to build the sketch.")
    if not parts:
        return 1
    if a.dry_run:
        print("esptool " + " ".join(esptool_args(a.port, parts, not a.no_reset)))
        return 0
    result = flash(a.port, parts, reset=not a.no_reset)
    if result == "needs-boot":
        print(BOOT_STEPS + " (add --no-reset)")
    elif result == "busy":
        print(BUSY_STEPS)
    return 0 if result == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
