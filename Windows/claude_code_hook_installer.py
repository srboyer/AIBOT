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

How "is this our entry" is decided: by checking whether
claude_code_status_hook.py appears in the command string, NOT by exact
string match against the full command -- that's what lets an upgrade that
moves the install path find and update its own prior entry in place,
while any of the customer's own unrelated hooks (which will never contain
that filename) are left completely untouched no matter what they look
like.

Registers against every event claude_code_status_hook.py's own docstring
lists: SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse,
Notification, Stop. PreToolUse/PostToolUse require a "matcher" ("*" = every
tool); the others don't take one at all -- confirmed against a real
settings.json shape.
"""

import json
import os

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


def default_hook_script_path() -> str:
    # Co-located with this installer -- in the packaged product both files
    # ship in the same install directory.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), HOOK_SCRIPT_NAME)


def build_hook_command(hook_script_path: str, python_launcher: str = "py -3.11") -> str:
    # Quoted path -- install directories containing spaces (e.g. "Program
    # Files") would otherwise break the command when Claude Code invokes it.
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


def install(settings_path: str = None, hook_script_path: str = None, python_launcher: str = "py -3.11") -> bool:
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

    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    # Temp file + replace, same reasoning as claude_code_status_hook.py's own
    # writes: Claude Code itself may read this file at an arbitrary moment,
    # and a partial write must never hand it truncated JSON.
    tmp_path = settings_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)
    os.replace(tmp_path, settings_path)
    return True


if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    settings_path = default_settings_path()

    if dry_run:
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
