"""
claude_code_hook_installer.py (macOS)

Setup-time utility (NOT run by Claude Code itself -- that's
claude_code_status_hook.py's job) that registers our hook command into the
customer's ~/.claude/settings.json. Meant to be called once from the
product installer, and safely re-callable on every reinstall/upgrade.

Ported from the Windows version (Windows/claude_code_hook_installer.py) --
merge_hooks()/remove_hooks()/the atomic-write logic are all identical,
since settings.json lives under the user's home directory the same way on
both platforms and none of that logic is Windows-specific. The one real
difference: Windows registers a standalone ClaudeCodeHook.exe (see
Windows/ClaudeCodeHook.spec); there's no macOS app bundle yet, so this
registers a plain `python3 <script>.py` command instead -- the same
fallback form the Windows installer keeps around for a dev checkout
without the exe built. Update HOOK_SCRIPT_NAME/_is_ours()/
build_hook_command() together once a packaged macOS build exists (mirror
how the Windows version added HOOK_EXE_NAME alongside its own legacy
HOOK_SCRIPT_NAME marker so upgrades find and update prior entries in
place instead of duplicating them).

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

How "is this our entry" is decided: by checking whether
claude_code_status_hook.py appears in the command string, NOT by exact
string match against the full command -- that's what lets an upgrade that
moves the install path find and update its own prior entry in place, while
any of the customer's own unrelated hooks (which will never contain that
filename) are left completely untouched no matter what they look like.

Registers against every event claude_code_status_hook.py's own docstring
lists: SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse,
Notification, Stop. PreToolUse/PostToolUse require a "matcher" ("*" = every
tool); the others don't take one at all -- confirmed against a real
settings.json shape.

Also doubles as the uninstaller (`--uninstall`): strips exactly the entries
_is_ours() recognizes and nothing else, same non-destructive philosophy in
reverse.
"""

import json
import os
import sys

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
    # NOT os.path.dirname(__file__) -- if this script is ever frozen into a
    # packaged build (e.g. via PyInstaller, same as the Windows side),
    # __file__ resolves inside the bundle's internal layout, not the real
    # folder the built binary is sitting in on disk. sys.executable is the
    # actual running binary's path in that case. sys.frozen is the standard
    # PyInstaller-set flag for telling the two situations apart; __file__ is
    # correct (and sys.executable would just be some system python3) for a
    # plain `python3 claude_code_hook_installer.py` dev/test run, which is
    # the only case that exists today.
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_hook_script_path() -> str:
    # Co-located with this installer -- in the packaged product both files
    # ship in the same install directory.
    return os.path.join(_this_install_dir(), HOOK_SCRIPT_NAME)


def build_hook_command(hook_script_path: str, python_launcher: str = "python3") -> str:
    # Quoted path -- install directories containing spaces would otherwise
    # break the command when Claude Code invokes it.
    return f'{python_launcher} "{hook_script_path}"'


def _is_ours(command: str) -> bool:
    return HOOK_SCRIPT_NAME in command


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
                    # normcase (harmless no-op on POSIX, but keeps this
                    # identical to the Windows version) -- a hand-written or
                    # differently-cased existing entry must not be treated
                    # as "the install path moved" every single run.
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


def install(settings_path: str = None, hook_script_path: str = None, python_launcher: str = "python3") -> bool:
    """Reads, merges, and writes back settings.json. Returns True if the
    file was actually changed (False means it was already correctly
    registered -- a normal, expected outcome on a repeat run)."""
    settings_path = settings_path or default_settings_path()
    hook_script_path = hook_script_path or default_hook_script_path()
    hook_command = build_hook_command(hook_script_path, python_launcher)

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
    Returns True if the file was actually changed."""
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
        after, changed = merge_hooks(before, build_hook_command(default_hook_script_path()))
        print(f"Would change {settings_path}: {changed}")
        if changed:
            print(json.dumps(after, indent=2))
    else:
        changed = install()
        print(f"{'Updated' if changed else 'Already up to date:'} {settings_path}")
