"""
firmware_update.py

Updates MAICA's firmware over USB from the desktop app -- no Arduino IDE.
The release build (build_release.ps1 / build_mac.sh) puts the compiled
firmware next to the app as firmware/maica.merged.bin: bootloader, partition
table and app in one image, written at address 0 with esptool (the same
tool the Arduino IDE uses). MAICA's USB-serial chip and auto-download
circuit put it into its bootloader by themselves, so it's: plug in, pick
the port, wait half a minute.

    py -3.11 firmware_update.py --list
    py -3.11 firmware_update.py --port COM7 [--bin path/to/maica.merged.bin]

Over Bluetooth it can't be done: Classic Bluetooth serial is far too slow
and the board can't rewrite itself from it safely.
"""

import argparse
import contextlib
import io
import os
import sys

from app_paths import bundled

# USB-serial chips an ESP32 board shows up as: Silicon Labs CP210x (MAICA's
# CP2102N), WCH CH34x, FTDI, Espressif's own USB.
USB_SERIAL_VIDS = {0x10C4: "Silicon Labs CP210x", 0x1A86: "WCH CH34x", 0x0403: "FTDI", 0x303A: "Espressif"}
BAUD = 921600


def default_image() -> str:
    return bundled(os.path.join("firmware", "maica.merged.bin"))


def board_ports():
    """[(device, description)] of USB-serial ports that could be MAICA,
    MAICA's own chip (CP210x) first."""
    from serial.tools import list_ports
    found = []
    for p in list_ports.comports():
        if p.vid in USB_SERIAL_VIDS:
            found.append((p.device, f"{p.device} - {USB_SERIAL_VIDS[p.vid]}" + (f" ({p.description})" if p.description else "")))
    found.sort(key=lambda d: (0 if "CP210x" in d[1] else 1, d[0]))
    return found


def esptool_args(port: str, image: str):
    return ["--chip", "esp32", "--port", port, "--baud", str(BAUD), "write-flash", "0x0", image]


def flash(port: str, image: str = None, log=print) -> bool:
    """Write the firmware image to the board on `port`. Returns success;
    esptool's progress goes to log(line)."""
    image = image or default_image()
    if not os.path.exists(image):
        log(f"No firmware image at {image}")
        return False
    import esptool

    class _Lines(io.TextIOBase):
        def __init__(self):
            self.part = ""

        def write(self, s):
            self.part += s
            while "\n" in self.part or "\r" in self.part:
                i = min(x for x in (self.part.find("\n"), self.part.find("\r")) if x >= 0)
                line, self.part = self.part[:i].strip(), self.part[i + 1:]
                if line:
                    log(line)
            return len(s)

    out = _Lines()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            esptool.main(esptool_args(port, image))
        log("Done -- MAICA restarts with the new firmware.")
        return True
    except SystemExit as e:
        ok = e.code in (0, None)
        log("Done." if ok else f"esptool stopped (exit {e.code})")
        return ok
    except Exception as e:
        log(f"Failed: {e}")
        log("Check the cable is a data cable (not charge-only) and nothing else has the port open.")
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Update MAICA's firmware over USB.")
    ap.add_argument("--list", action="store_true", help="list USB serial ports that could be MAICA")
    ap.add_argument("--port", help="the port MAICA is on (e.g. COM7, /dev/cu.usbserial-0001)")
    ap.add_argument("--bin", help=f"firmware image (default: {default_image()})")
    ap.add_argument("--dry-run", action="store_true", help="show what would run, don't flash")
    a = ap.parse_args(argv)
    if a.list or not a.port:
        ports = board_ports()
        print("\n".join(d for _, d in ports) or "No USB serial ports that look like MAICA. Plugged in with a data cable?")
        return 0
    if a.dry_run:
        print("esptool " + " ".join(esptool_args(a.port, a.bin or default_image())))
        return 0
    return 0 if flash(a.port, a.bin) else 1


if __name__ == "__main__":
    sys.exit(main())
