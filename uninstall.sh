#!/bin/bash
#
# Uninstaller for the OpenRouter usage monitor (ortracker).

set -e

DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$DESKTOP_DIR/or-usage-tracker.desktop"
LAUNCHER="$HOME/.local/bin/ortracker"

echo "=== OR Usage Tracker Uninstaller ==="

if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    echo "Removed desktop entry: $DESKTOP_FILE"
else
    echo "No desktop entry found."
fi

# Only remove the launcher if it is the symlink install.sh created.
if [ -L "$LAUNCHER" ]; then
    rm -f "$LAUNCHER"
    echo "Removed launcher: $LAUNCHER"
fi

echo ""
echo "NOTE: The application files remain in:"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "  $SCRIPT_DIR"
echo ""
echo "To remove the Python virtualenv created by the installer, run:"
echo "  rm -rf $SCRIPT_DIR/.venv"
echo ""
echo "To fully remove the application, delete that directory."
echo "Your stored API key in the system keyring (GNOME Keyring / KWallet) will persist."
echo "To remove it, run:"
echo "  $SCRIPT_DIR/.venv/bin/python -c \"import keyring; keyring.delete_password('ortracker', 'default')\""
