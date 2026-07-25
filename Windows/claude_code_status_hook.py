"""
claude_code_status_hook.py

The "detection" half of Claude Code support: unlike every other backend in
this project, Claude Code has no window or UI element to inspect -- it's a
CLI. So instead of UI automation, this script is registered directly as a
Claude Code hook command (see ~/.claude/settings.json) for SessionStart,
SessionEnd, UserPromptSubmit, PreToolUse, Notification, and Stop. Claude
Code invokes it -- feeding a JSON payload on stdin -- every time one of
those fires, and it just overwrites a small status file with the latest
event. claude_code_vscode_backend.py polls that file; that's the entire
"backend".

VS CODE DETECTION -- Claude Code doesn't say anywhere in the hook payload
itself whether this session is attached to VS Code, so that part comes from
the process environment instead, confirmed by inspecting a real running
session and cross-checking against the installed claude.exe:
    - CLAUDE_CODE_ENTRYPOINT=claude-vscode -- set when running as the
      native VS Code extension (the chat panel).
    - TERM_PROGRAM=vscode -- set by VS Code on every child process of its
      OWN integrated terminal, so this also catches `claude` typed directly
      into a VS Code terminal tab (a plain CLI session, not the extension).
Hook commands are spawned as children of the `claude` process, which
inherits whichever of these its own parent (VS Code) set -- so both are
visible here exactly as they would be to a `printenv` run manually in the
same window.

Registration (~/.claude/settings.json), one identical command for every
event listed above -- the payload's own "hook_event_name" field is what
tells this script which one just fired, so there's no need for a different
command per event:

    py -3.11 "<path to this file>"

SILENT BY DESIGN -- this must never print anything or exit non-zero: it
runs inline in Claude Code's own event path, and hook stdout/exit codes can
be interpreted specially (e.g. as a block decision) by the surrounding
hook machinery. Every failure mode here is swallowed -- a status light
losing sync is a cosmetic problem; interfering with the actual coding
session would not be.
"""

import json
import os
import sys
import time

from claude_code_status_shared import STATUS_FILE


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    is_vscode = (
        os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("claude-vscode")
        or os.environ.get("TERM_PROGRAM") == "vscode"
    )

    status = {
        "event": payload.get("hook_event_name", "Unknown"),
        "vscode": is_vscode,
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        # Notification-only fields -- absent/None for every other event.
        # notification_type is what lets the backend tell an actual
        # "I need you" prompt apart from the harmless "still waiting on
        # you" idle nudge (notification_type "idle_prompt") that fires
        # after ~60s of inactivity; see claude_code_vscode_backend.py.
        "notification_type": payload.get("notification_type"),
        "message": payload.get("message"),
        "written_at": time.time(),
    }

    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        # Write to a temp file + replace rather than writing STATUS_FILE
        # directly -- the backend polls this file's mtime/content on a
        # separate process at an arbitrary moment, and a partial write
        # (killed mid-write, disk hiccup) would otherwise hand it truncated
        # JSON instead of either the old or new state cleanly.
        tmp_path = STATUS_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(status, f)
        os.replace(tmp_path, STATUS_FILE)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
