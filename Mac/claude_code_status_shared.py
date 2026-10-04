"""
claude_code_status_shared.py

Just the one constant that MUST agree between claude_code_status_hook.py
(writer, invoked by Claude Code's own hooks -- see that file's docstring)
and claude_code_backend.py (reader, polled by chat_watcher.py). Kept
in its own module rather than duplicated in both, since the two files run
as unrelated processes at unrelated times -- a copy-paste drift between two
inline copies would fail completely silently (the backend would just never
see any events, with no error anywhere).
"""

import os

STATUS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "claude_code_status.json")
