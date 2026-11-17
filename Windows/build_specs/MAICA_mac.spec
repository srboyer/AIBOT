# -*- mode: python ; coding: utf-8 -*-
#
# MAICA.app -- the desktop app for a Mac (maica_app.py). Built by
# build_mac.sh, which first prepares what goes inside:
#   build_work/MAICA.icns        the icon (make_app_icon.py + iconutil)
#   ../extension/dist/           the browser extension (extension/build.py)
#   $MAICA_FIRMWARE_DIR          the four firmware parts, if available
#                                (bootloader.bin, partitions.bin, boot_app0.bin, maica.bin)
#
# The app is also Claude Code's hook ("MAICA --claude-hook", see
# maica_app.py), so there's no separate hook executable on a Mac.
#
# Backends are imported by name at run time (chat_watcher.load_backend), so
# PyInstaller can't see them: every app module is listed in hiddenimports.

import glob
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = os.path.abspath(os.path.join(SPECPATH, ".."))  # the Windows/ folder: the app's source
ROOT = os.path.abspath(os.path.join(HERE, ".."))

version = {}
exec(open(os.path.join(HERE, "app_version.py"), encoding="utf-8").read(), version)
VERSION = version["VERSION"]

# Every module of the app except the Windows-only ones, tests and tools.
WINDOWS_ONLY = {"claude_desktop_backend", "claude_desktop_chat_backend", "chatgpt_desktop_backend",
                "claude_web_backend", "chatgpt_web_backend", "copilot_desktop_backend", "inspect_window",
                "watcher_app_entry"}
TOOLS = {"make_face_font", "make_app_icon", "mac_inspect_app"}
modules = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(HERE, "*.py")))
app_modules = [m for m in modules if not m.startswith("test_") and m not in WINDOWS_ONLY | TOOLS]

datas = [(os.path.join(HERE, "maica_rules.json"), ".")]
datas += collect_data_files("esptool")  # its flasher stubs
ext = os.path.join(ROOT, "extension", "dist")
if os.path.isdir(ext):
    datas.append((ext, "extension/dist"))
fw = os.environ.get("MAICA_FIRMWARE_DIR")
if fw and all(os.path.exists(os.path.join(fw, n)) for n in ("bootloader.bin", "partitions.bin",
                                                          "boot_app0.bin", "maica.bin")):
    datas.append((fw, "firmware"))

icon = os.path.join(HERE, "build_work", "MAICA.icns")

a = Analysis(
    [os.path.join(HERE, "maica_app.py")],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=app_modules + collect_submodules("esptool") + ["pystray._darwin", "PyObjCTools.AppHelper"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Windows-only libraries, and push-to-talk's speech model (large; added
    # when the microphone board ships).
    excludes=["pywinauto", "comtypes", "win32api", "win32con", "faster_whisper", "ctranslate2", "onnxruntime",
              "torch"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MAICA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=os.environ.get("MAICA_SIGN_IDENTITY") or None,
    entitlements_file=os.path.join(SPECPATH, "MAICA_mac.entitlements") if os.environ.get("MAICA_SIGN_IDENTITY") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="MAICA")
app = BUNDLE(
    coll,
    name="MAICA.app",
    icon=icon if os.path.exists(icon) else None,
    bundle_identifier="com.maica.desktop",
    version=VERSION,
    info_plist={
        "CFBundleName": "MAICA",
        "CFBundleDisplayName": "MAICA",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSUIElement": True,                  # a menu-bar app: no Dock icon
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSBluetoothAlwaysUsageDescription": "MAICA talks to your MAICA desk robot over Bluetooth.",
        "NSAppleEventsUsageDescription": "MAICA brings the AI app that needs you to the front, and types what "
                                         "you dictate with push-to-talk.",
    },
)
