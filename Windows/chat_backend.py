"""
chat_backend.py

Common interface for detecting an AI chat app's state via UI automation.

Each supported chatbot (Claude Desktop, ChatGPT Desktop, etc.) gets its own
module implementing this interface (e.g. claude_desktop_backend.py). The
generic watcher (chat_watcher.py) only ever talks to this interface -- it
has zero app-specific knowledge. Adding support for a new chatbot means
writing one new backend module; nothing else in the project changes.

To add a new chatbot:
    1. Run inspect_window.py against it to find its window title and the
       UI elements that signal "prompt sent", "still generating", "waiting
       on user input", and "response finished".
    2. Create a new file (e.g. chatgpt_desktop_backend.py) with a class
       that implements every method below.
    3. Point chat_watcher.py at it with --backend <name>.
"""


class ChatBackend:
    """Base class -- subclass this for each supported chatbot app."""

    name = "generic"

    def connect(self):
        """Locate the app window and all needed UI elements. Should block,
        retrying patiently (e.g. every few seconds, printing a friendly
        wait message), until everything is found -- don't raise just
        because the app isn't open yet."""
        raise NotImplementedError

    def try_connect(self) -> bool:
        """Single, NON-blocking connection attempt: try once to locate the
        app window and all needed UI elements, and return True if fully
        connected or False if the app/UI isn't available right now. Must
        never block or raise just because the app isn't open -- the
        multi-app watcher calls this periodically for every registered
        backend, including ones whose app may never be launched, so a
        blocking wait here would stall tracking of the other apps."""
        raise NotImplementedError

    def poll(self, debug: bool = False) -> dict:
        """Called once per polling cycle (roughly once a second). Returns
        a dict describing what's true THIS tick:

            {
                "prompt_sent": bool,         # a new prompt just went out
                "waiting_for_answer": bool,  # waiting on an interactive choice
                "thinking": bool,            # extended/deep reasoning phase
                "new_text": str,             # newly-appeared response text since last poll
                "finished": bool,            # the current response just finished
            }

        Apps that don't support a given concept (e.g. no extended-thinking
        indicator) should just always report False/"" for that key rather
        than raising.
        """
        raise NotImplementedError

    def check_alive(self):
        """Raise an exception if the connection to the app has gone stale
        (e.g. it was fully closed and reopened), so the outer loop in
        chat_watcher.py knows to call connect() again from scratch instead
        of silently polling a dead handle forever."""
        raise NotImplementedError
