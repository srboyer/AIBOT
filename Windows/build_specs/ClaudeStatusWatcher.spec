# -*- mode: python ; coding: utf-8 -*-
#
# Builds the main BLE/watcher app (watcher_app_entry.py) into a standalone
# exe. Companion to ClaudeCodeHook.spec, which packages the separate Claude
# Code hook script -- these are two different products that ship together,
# not one build.
#
# hiddenimports below exist because chat_watcher.load_backend() imports
# backend modules dynamically via importlib.import_module() using strings
# built from the BACKENDS dict (see chat_watcher.py), not literal `import`
# statements -- PyInstaller's static analysis can't see those, so every
# entry in BACKENDS has to be listed here explicitly or the packaged exe
# fails at backend-selection time instead of at build time.

from PyInstaller.utils.hooks import collect_submodules

backend_modules = [
    'claude_desktop_chat_backend',
    'claude_desktop_backend',  # still imported by claude_web_backend
    'chatgpt_desktop_backend',
    'claude_web_backend',
    'chatgpt_web_backend',
    'claude_code_backend',
    'copilot_desktop_backend',
]

a = Analysis(
    ['../watcher_app_entry.py'],
    pathex=['..'],
    binaries=[],
    datas=[],
    hiddenimports=backend_modules + collect_submodules('pywinauto'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # comtypes.test is a large bundled unit-test suite pulled in only because
    # comtypes.gen (used by pywinauto's UIA backend) imports comtypes.test at
    # module scope in a couple of places -- none of it is reachable from our
    # actual code path, and hook-comtypes.client.py already supplies the
    # hiddenimports comtypes.client really needs at runtime.
    excludes=['comtypes.test'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClaudeStatusWatcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClaudeStatusWatcher',
)
