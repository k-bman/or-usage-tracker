"""Pytest configuration shared by the whole test suite.

The GUI tests must never touch the developer's real desktop session. A desktop
environment (Hyprland here) commonly exports QT_QPA_PLATFORM=wayland;xcb for its
own apps, so merely defaulting is not enough -- Qt would resolve to the native
platform and every test that builds a widget would create and destroy a real
compositor window, stealing focus mid-session. CI sets QT_QPA_PLATFORM=offscreen
in the workflow environment; this does the same for local runs, so the suite is
headless everywhere.

Debugging a test against real Qt platform plugins: set
ORTRACKER_TEST_PLATFORM=wayland (or xcb) for that one run.
"""

import os

os.environ["QT_QPA_PLATFORM"] = os.environ.get("ORTRACKER_TEST_PLATFORM", "offscreen")