#!/bin/bash
# build_mac.sh -- builds MAICA.app and a drag-to-Applications disk image on a Mac.
#
#   ./build_mac.sh                      into a fresh virtualenv (build_work/venv-mac)
#   MAICA_USE_CURRENT_PYTHON=1 ./build_mac.sh    with the python3 already on PATH (CI)
#
# Out: build_dist/mac/MAICA.app and build_dist/mac/MAICA-<version>-<arch>.dmg
#
# Firmware: the app can flash MAICA with firmware it carries. Set
# MAICA_FIRMWARE_DIR to a folder holding bootloader.bin, partitions.bin,
# boot_app0.bin and maica.bin (the CI firmware job makes one); without it,
# the sketch is built here if the Arduino IDE (or arduino-cli) is installed,
# else the app ships without firmware (its Flash window can still build the
# sketch on a computer that has the Arduino IDE).
#
# Signing: unsigned by default -- PyInstaller gives it an ad-hoc signature,
# and macOS asks once, the first time it's opened (System Settings > Privacy
# & Security > Open Anyway). With an Apple Developer ID:
#   MAICA_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   MAICA_NOTARY_PROFILE=<a profile saved with: xcrun notarytool store-credentials>
# signs it with the hardened runtime, notarizes the disk image and staples it.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
WORK="$HERE/build_work"
DIST="$HERE/build_dist/mac"
mkdir -p "$WORK" "$DIST"

VERSION="$(python3 -c 'import app_version; print(app_version.VERSION)')"
ARCH="$(uname -m)"
echo "== MAICA $VERSION for $ARCH"

# -- Python and the app's libraries -------------------------------------------
if [ "${MAICA_USE_CURRENT_PYTHON:-0}" = "1" ]; then
  PY=python3
else
  [ -d "$WORK/venv-mac" ] || python3 -m venv "$WORK/venv-mac"
  PY="$WORK/venv-mac/bin/python"
fi
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r requirements-mac.txt "pyinstaller>=6"

# -- What goes inside ----------------------------------------------------------
echo "== browser extension"
"$PY" ../extension/build.py

echo "== firmware"
if [ -z "${MAICA_FIRMWARE_DIR:-}" ]; then
  FW="$WORK/firmware"
  if "$PY" - "$FW" <<'EOF'
import os, shutil, sys
import firmware_update as fu
if not fu.can_build():
    print("No Arduino IDE / arduino-cli here: the app ships without firmware.")
    sys.exit(1)
parts = fu.build(round_screen=False)
if not parts:
    sys.exit(1)
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
for (addr, _bundled, _built), (_a, path) in zip(fu.PART_NAMES, parts):
    shutil.copy(path, os.path.join(out, _bundled))
print("Firmware built into", out)
EOF
  then
    export MAICA_FIRMWARE_DIR="$FW"
  fi
fi
[ -n "${MAICA_FIRMWARE_DIR:-}" ] && echo "firmware from $MAICA_FIRMWARE_DIR"

echo "== icon"
rm -rf "$WORK/MAICA.iconset"
"$PY" make_app_icon.py "$WORK/MAICA.iconset"
iconutil -c icns "$WORK/MAICA.iconset" -o "$WORK/MAICA.icns"

# -- The app ---------------------------------------------------------------------
echo "== MAICA.app"
"$PY" -m PyInstaller --noconfirm --clean --distpath "$DIST" --workpath "$WORK/pyinstaller-mac" \
  build_specs/MAICA_mac.spec
APP="$DIST/MAICA.app"

if [ -n "${MAICA_SIGN_IDENTITY:-}" ]; then
  echo "== signing as $MAICA_SIGN_IDENTITY"
  codesign --force --deep --options runtime --timestamp \
    --entitlements build_specs/MAICA_mac.entitlements --sign "$MAICA_SIGN_IDENTITY" "$APP"
else
  codesign --force --deep --sign - "$APP"  # ad-hoc: required to run on Apple Silicon
fi
codesign --verify --deep "$APP"

# Checks on the built app: everything inside loads, and Claude Code's hook
# mode runs (with HOME pointed at a scratch folder, so it touches nothing real).
"$APP/Contents/MacOS/MAICA" --self-test
rm -rf "$WORK/hook-home" && mkdir -p "$WORK/hook-home"
echo '{"hook_event_name":"SessionStart","session_id":"build-check"}' \
  | HOME="$WORK/hook-home" "$APP/Contents/MacOS/MAICA" --claude-hook
ls "$WORK/hook-home/.claude/maica_sessions/" | grep -q build-check
echo "Claude Code hook mode works"

# -- The disk image ----------------------------------------------------------------
echo "== disk image"
DMG="$DIST/MAICA-$VERSION-$ARCH.dmg"
STAGE="$WORK/dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
for try in 1 2 3; do  # hdiutil sometimes finds the disk "busy" for a moment
  hdiutil create -volname "MAICA" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null && break
  [ "$try" = 3 ] && exit 1
  sleep 5
done

if [ -n "${MAICA_SIGN_IDENTITY:-}" ] && [ -n "${MAICA_NOTARY_PROFILE:-}" ]; then
  echo "== notarizing"
  codesign --sign "$MAICA_SIGN_IDENTITY" --timestamp "$DMG"
  xcrun notarytool submit "$DMG" --keychain-profile "$MAICA_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi

echo "== done: $DMG"
