# -*- mode: python ; coding: utf-8 -*-
#
# Packages claude_code_hook_installer.py -- the setup-time utility that
# registers ClaudeCodeHook.exe into the customer's ~/.claude/settings.json.
# console=True (unlike ClaudeCodeHook.spec and ClaudeStatusWatcher.spec,
# both windowed): this is a short-lived, one-shot step meant to be invoked
# from a product installer (or run manually for testing) and its stdout
# ("Updated ..." / "Already up to date" / --dry-run output) is the whole
# point, not something to hide.

a = Analysis(
    ['../claude_code_hook_installer.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClaudeCodeHookInstaller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
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
    name='ClaudeCodeHookInstaller',
)
