#!/usr/bin/env python3
"""Launcher script for the OpenRouter usage monitor.

This script is intentionally dumb: it runs the app with whichever interpreter
invoked it. If PySide6 is missing it fails with an actionable message instead
of quietly switching interpreters.

Use one of these instead, all of which pick the right interpreter for you:

    ortracker                (symlinked into ~/.local/bin by install.sh)
    ./run                    (wrapper around the project virtualenv)
    ./install.sh             (initial setup)
"""

import os
import shutil
import subprocess
import sys

_here = os.path.dirname(os.path.abspath(__file__))


def _notify(message: str) -> None:
    """Show a desktop notification.

    GUI file managers ("Run as a Program" in Nautilus) discard stdout/stderr,
    so fatal errors would otherwise be completely invisible. Only used when
    stderr is not a terminal, so terminal users just read the message.
    """
    try:
        if sys.stderr.isatty():
            return
        if shutil.which("zenity"):
            subprocess.run(
                [
                    "zenity",
                    "--error",
                    "--no-markup",
                    "--title=OR Usage Tracker",
                    f"--text={message}",
                ],
                check=False,
                timeout=60,
            )
        elif shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "-u", "critical", "OR Usage Tracker", message],
                check=False,
                timeout=60,
            )
    except Exception:
        # Never let the error reporter itself break the launch.
        pass


try:
    import PySide6  # noqa: F401
except ImportError:
    message = (
        f"PySide6 is not installed for {sys.executable}.\n\n"
        "Run the app with the project's virtualenv instead:\n"
        "  ortracker\n"
        "  (or: ./run, from the project directory)\n\n"
        "If that is not available yet, install the dependencies first:\n"
        "  ./install.sh"
    )
    print(message, file=sys.stderr)
    _notify(message)
    sys.exit(1)

# Ensure the package directory is in the path
if _here not in sys.path:
    sys.path.insert(0, _here)

from ortracker.gui import run

if __name__ == "__main__":
    run()