"""
claude_code_hook_installer.py

Setup-time utility (NOT run by Claude Code itself -- that's
claude_code_status_hook.py's job) that registers our hook command into the
customer's ~/.claude/settings.json. Meant to be called once from the
product installer, and safely re-callable on every reinstall/upgrade.

Why this needs to be careful, not just "write the file":
- settings.json is the customer's OWN Claude Code config -- it may already
  have unrelated hooks, permissions, a theme, etc. This must only ever ADD
  our own hook entries, never touch anything else in the file.
- Re-running (reinstall, upgrade, or the installer just being idempotent by
  design) must not pile up duplicate hook entries every time.
- If the app gets reinstalled to a different path, the OLD registered
  command (pointing at the old path) would otherwise sit there silently
  broken forever -- see claude_code_status_hook.py's "SILENT BY DESIGN"
  note, failures here never surface to the customer on their own.

How "is this our entry" is decided: by checking whether ClaudeCodeHook.exe
(or, for a customer upgrading from before the hook was packaged as an exe,
claude_code_status_hook.py) appears in the command string, NOT by exact
string match against the full command -- that's what lets an upgrade that
moves the install path, or that moves a customer off the old "py -3.11
<script>.py" invocation onto the packaged exe, find and update its own
prior entry in place, while any of the customer's own unrelated hooks
(which will never contain either name) are left completely untouched no
matter what they look like.

Registers against every event claude_code_status_hook.py's own docstring
lists: SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse,
Notification, Stop. PreToolUse/PostToolUse require a "matcher" ("*" = every
tool); the others don't take one at all -- confirmed against a real
settings.json shape.

Also doubles as the uninstaller (`--uninstall`): strips exactly the entries
_is_ours() recognizes and nothing else, same non-destructive philosophy in
reverse. See the ClaudeStatusLight.iss [UninstallRun] entry -- that's what
actually invokes this.
"""

import json
import os
import sys

HOOK_EXE_NAME = "ClaudeCodeHook.exe"
# Legacy marker: what the registered command looked like before the hook
# script got packaged as an exe (see ClaudeCodeHook.spec). Kept only so
# _is_ours() still recognizes -- and upgrades in place -- a hook entry
# registered by an older version of this installer.
HOOK_SCRIPT_NAME = "claude_code_status_hook.py"

# (event name, matcher or None) -- order matches claude_code_status_hook.py's
# own docstring list.
HOOK_EVENTS = [
    ("SessionStart", None),
    ("SessionEnd", None),
    ("UserPromptSubmit", None),
    ("PreToolUse", "*"),
    ("PostToolUse", "*"),
    ("Notification", None),
    ("Stop", None),
]


def default_settings_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _this_install_dir() -> str:
    # NOT os.path.dirname(__file__) -- once this script is itself frozen by
    # PyInstaller (see ClaudeCodeHookInstaller.spec), __file__ resolves
    # inside the bundle's internal layout, not the real folder the exe is
    # sitting in on disk. sys.executable is the actual running exe's path
    # in that case. sys.frozen is the standard PyInstaller-set flag for
    # telling the two situations apart; __file__ is correct (and
    # sys.executable would just be some system python.exe) for a plain
    # `py -3.11 claude_code_hook_installer.py` dev/test run.
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_hook_exe_path() -> str:
    # Co-located with this installer -- in the packaged product both files
    # ship in the same install directory.
    return os.path.join(_this_install_dir(), HOOK_EXE_NAME)


def build_hook_command(hook_exe_path: str) -> str:
    # Just the quoted exe path -- no "py -3.11" launcher needed since
    # ClaudeCodeHook.exe is a standalone PyInstaller build (see
    # ClaudeCodeHook.spec). Quoting matters because install directories
    # containing spaces (e.g. "Program Files") would otherwise break the
    # command when Claude Code invokes it.
    return f'"{hook_exe_path}"'


def _is_ours(command: str) -> bool:
    return HOOK_EXE_NAME in command or HOOK_SCRIPT_NAME in command


def merge_hooks(settings: dict, hook_command: str) -> tuple[dict, bool]:
    """Returns (updated_settings, changed). Never mutates the input dict --
    callers that want to diff before/after can still compare against their
    original. Safe to call repeatedly with the same hook_command: the
    second call always reports changed=False."""
    settings = json.loads(json.dumps(settings))  # deep copy, no extra dependency
    hooks = settings.setdefault("hooks", {})
    changed = False

    for event, matcher in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])

        # Look for an entry we previously registered (see module docstring
        # for why this matches by filename, not exact command string).
        found_ours = False
        for group in groups:
            for entry in group.get("hooks", []):
                if entry.get("type") == "command" and _is_ours(entry.get("command", "")):
                    found_ours = True
                    # normcase, not a plain string compare -- Windows paths are
                    # case-insensitive and os.path.abspath() normalizes the
                    # drive letter to uppercase, so a hand-written or
                    # differently-cased existing entry must not be treated as
                    # "the install path moved" every single run.
                    if os.path.normcase(entry.get("command", "")) != os.path.normcase(hook_command):
                        entry["command"] = hook_command  # install path actually moved -- update in place
                        changed = True

        if not found_ours:
            new_group = {"hooks": [{"type": "command", "command": hook_command}]}
            if matcher is not None:
                new_group["matcher"] = matcher
            groups.append(new_group)
            changed = True

    return settings, changed


def remove_hooks(settings: dict) -> tuple[dict, bool]:
    """The uninstall-time counterpart to merge_hooks(): strips out every
    entry _is_ours() recognizes and nothing else. Returns (updated_settings,
    changed); never mutates the input. A group that held only our entry is
    dropped entirely rather than left behind as an empty {"hooks": []} --
    same for an event key whose groups all end up empty -- so a clean
    uninstall leaves settings.json looking exactly like we were never
    installed, not littered with empty scaffolding. Safe to call on a
    settings dict that never had our hooks in it: changed comes back False."""
    settings = json.loads(json.dumps(settings))  # deep copy, same reasoning as merge_hooks
    hooks = settings.get("hooks")
    if hooks is None:
        return settings, False
    changed = False

    for event, _matcher in HOOK_EVENTS:
        groups = hooks.get(event)
        if not groups:
            continue

        kept_groups = []
        for group in groups:
            kept_entries = [
                entry for entry in group.get("hooks", [])
                if not (entry.get("type") == "command" and _is_ours(entry.get("command", "")))
            ]
            if len(kept_entries) != len(group.get("hooks", [])):
                changed = True
            if kept_entries:
                kept_groups.append({**group, "hooks": kept_entries})
            # else: group held only our entry -- drop it entirely

        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]

    if not hooks:
        del settings["hooks"]

    return settings, changed


def _write_settings(settings_path: str, updated: dict) -> None:
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    # Temp file + replace, same reasoning as claude_code_status_hook.py's own
    # writes: Claude Code itself may read this file at an arbitrary moment,
    # and a partial write must never hand it truncated JSON.
    tmp_path = settings_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)
    os.replace(tmp_path, settings_path)


def install(settings_path: str = None, hook_exe_path: str = None) -> bool:
    """Reads, merges, and writes back settings.json. Returns True if the
    file was actually changed (False means it was already correctly
    registered -- a normal, expected outcome on a repeat run)."""
    settings_path = settings_path or default_settings_path()
    hook_exe_path = hook_exe_path or default_hook_exe_path()
    hook_command = build_hook_command(hook_exe_path)

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}

    updated, changed = merge_hooks(settings, hook_command)
    if not changed:
        return False

    _write_settings(settings_path, updated)
    return True


def uninstall(settings_path: str = None) -> bool:
    """Reads, strips our hook entries, and writes back settings.json.
    Returns True if the file was actually changed. Called from
    [UninstallRun] (see the .iss installer script) -- runs while
    ClaudeCodeHook.exe still exists on disk (UninstallRun fires before
    Setup deletes files), which is what makes this the right moment to do
    it rather than leaving stale, permanently-broken hook entries behind
    for a customer who uninstalls."""
    settings_path = settings_path or default_settings_path()

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except FileNotFoundError:
        return False

    updated, changed = remove_hooks(settings)
    if not changed:
        return False

    _write_settings(settings_path, updated)
    return True


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    do_uninstall = "--uninstall" in sys.argv
    settings_path = default_settings_path()

    if do_uninstall:
        if dry_run:
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    before = json.load(f)
            except FileNotFoundError:
                before = {}
            after, changed = remove_hooks(before)
            print(f"Would change {settings_path}: {changed}")
            if changed:
                print(json.dumps(after, indent=2))
        else:
            changed = uninstall()
            print(f"{'Removed hooks from' if changed else 'No hooks found in'} {settings_path}")
    elif dry_run:
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                before = json.load(f)
        except FileNotFoundError:
            before = {}
        after, changed = merge_hooks(before, build_hook_command(default_hook_exe_path()))
        print(f"Would change {settings_path}: {changed}")
        if changed:
            print(json.dumps(after, indent=2))
    else:
        changed = install()
        print(f"{'Updated' if changed else 'Already up to date:'} {settings_path}")
