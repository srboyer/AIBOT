"""
claude_code_status_hook.py

The "detection" half of Claude Code support: unlike every other backend in
this project, Claude Code has no window or UI element to inspect -- it's a
CLI. So instead of UI automation, this script is registered directly as a
Claude Code hook command (see ~/.claude/settings.json) for SessionStart,
SessionEnd, UserPromptSubmit, PreToolUse, Notification, and Stop. Claude
Code invokes it -- feeding a JSON payload on stdin -- every time one of
those fires, and it just overwrites a small status file with the latest
event. claude_code_backend.py polls that file; that's the entire
"backend".

HOST LABEL, NOT A GATE -- Claude Code doesn't say anywhere in the hook
payload itself what it's running inside, so that comes from the process
environment instead. Hook commands are spawned as children of the `claude`
process, which inherits whichever of these its own parent set -- visible
here exactly as they'd be to a `printenv` run manually in the same window:
    - CLAUDE_CODE_ENTRYPOINT -- set by a native editor integration (e.g.
      `claude-vscode` for VS Code's chat panel, confirmed by inspecting a
      real running session and cross-checking against the installed
      claude.exe). Whatever a JetBrains/Cursor/Windsurf/etc. plugin sets
      here is captured as-is and used as the host label, without this
      project needing to know or hardcode that value in advance.
    - TERM_PROGRAM -- set by whatever terminal emulator is hosting a plain
      `claude` CLI session (VS Code's integrated terminal sets this to
      `vscode`; other terminals set their own name).
This is recorded purely as a LABEL for the backend's own printed/debug
output -- which host it's running in was never something the state
detection itself needed. Every session is tracked identically regardless
of host (this project is meant to work no matter which editor/terminal a
customer happens to use), so an unrecognized or absent value just falls
back to "unknown" rather than being dropped.

Registration (~/.claude/settings.json), one identical command for every
event listed above -- the payload's own "hook_event_name" field is what
tells this script which one just fired, so there's no need for a different
command per event. In the packaged product this file is built into
ClaudeCodeHook.exe (see ClaudeCodeHook.spec) and that exe path is what
gets registered directly, via claude_code_hook_installer.py:

    "<path to>\ClaudeCodeHook.exe"

(A dev checkout without the exe built can still register this .py file
directly with a "py -3.11 <path>" command -- claude_code_hook_installer.py
recognizes either form as its own entry.)

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

    host = os.environ.get("CLAUDE_CODE_ENTRYPOINT") or os.environ.get("TERM_PROGRAM") or "unknown"

    status = {
        "event": payload.get("hook_event_name", "Unknown"),
        "host": host,
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        # Notification-only fields -- absent/None for every other event.
        # notification_type is what lets the backend tell an actual
        # "I need you" prompt apart from the harmless "still waiting on
        # you" idle nudge (notification_type "idle_prompt") that fires
        # after ~60s of inactivity; see claude_code_backend.py.
        "notification_type": payload.get("notification_type"),
        "message": payload.get("message"),
        # Pre/PostToolUse only: which tool. Lets the backend see the
        # question tool (AskUserQuestion) finishing -- i.e. you answered.
        "tool_name": payload.get("tool_name"),
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

    _append_history(status, payload)


# A short rolling history of hook events next to STATUS_FILE, for diagnosing
# what Claude Code actually fires and in what order (the status file only
# ever holds the latest event). Event names, tool names and notification
# types only -- never prompts, tool inputs or outputs. Trimmed to the most
# recent half once it passes HISTORY_MAX_BYTES.
HISTORY_FILE = os.path.splitext(STATUS_FILE)[0] + "_history.jsonl"
HISTORY_MAX_BYTES = 64 * 1024


def _append_history(status, payload):
    try:
        line = json.dumps({
            "t": round(status["written_at"], 3),
            "event": status["event"],
            "tool": payload.get("tool_name"),
            "notification_type": status["notification_type"],
            "session": (status["session_id"] or "")[:8],
        })
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(HISTORY_FILE) > HISTORY_MAX_BYTES:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
