#!/bin/bash
#
# Uninstaller for the OpenRouter usage monitor (ortracker).
#
# Removes the desktop entry and the ~/.local/bin launcher. With --purge it
# also removes the virtualenv, the editable-install metadata and the app
# config, and offers to delete the API key from the system keyring.
#
# See usage() below, or run ./uninstall.sh --help.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
EGG_INFO_DIR="$SCRIPT_DIR/or_usage_tracker.egg-info"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$DESKTOP_DIR/or-usage-tracker.desktop"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/ortracker"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/ortracker"

PURGE=0
REMOVED=()
KEPT=()

usage() {
    cat << EOF
Usage: $0 [--purge]

Uninstall the OpenRouter usage monitor (ortracker).

  --purge    Also remove the virtualenv, the editable-install metadata and
             the app config, and offer to delete the API key from the system
             keyring.
  -h, --help Show this help.

Without --purge only the desktop entry and the ~/.local/bin launcher are
removed. The application directory itself is never deleted, and neither is a
real file at ~/.local/bin/ortracker that you may have put there yourself.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "ERROR: unknown option: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

removed() { REMOVED+=("$1"); }
kept()    { KEPT+=("$1"); }

echo "=== OR Usage Tracker Uninstaller ==="
echo "Application directory: $SCRIPT_DIR"

# --- Desktop entry ---
echo ""
echo "Desktop entry..."
if [ -f "$DESKTOP_FILE" ] || [ -L "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    removed "$DESKTOP_FILE"
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$DESKTOP_DIR" &>/dev/null || true
    fi
else
    echo "  Not found: $DESKTOP_FILE"
fi

# --- Launcher ---
echo ""
echo "Launcher..."
if [ ! -e "$LAUNCHER" ] && [ ! -L "$LAUNCHER" ]; then
    echo "  Not found: $LAUNCHER"
elif [ ! -L "$LAUNCHER" ]; then
    # install.sh never clobbers a real file here, so neither do we.
    echo "  $LAUNCHER is a real file, not a symlink. Leaving it alone."
    kept "$LAUNCHER"
else
    target="$(readlink -f "$LAUNCHER" 2>/dev/null || true)"
    case "$target" in
        "$VENV_DIR"/*)
            rm -f "$LAUNCHER"
            removed "$LAUNCHER"
            ;;
        "")
            echo "  $LAUNCHER is a dangling symlink. Removing it."
            rm -f "$LAUNCHER"
            removed "$LAUNCHER"
            ;;
        *)
            echo "  $LAUNCHER points elsewhere ($target); not created by this installer."
            echo "  Leaving it alone."
            kept "$LAUNCHER"
            ;;
    esac
fi

# --- Keyring ---
# Done before the virtualenv is deleted, because it needs the venv's keyring.
delete_keyring_entry() {
    if [ ! -x "$VENV_PY" ]; then
        echo "  Cannot check the keyring: $VENV_PY is gone."
        echo "  Remove it with: secret-tool delete service ortracker username default"
        return
    fi
    "$VENV_PY" - <<'PY'
import keyring

try:
    keyring.delete_password("ortracker", "default")
    print("  Deleted the ortracker/default secret from the keyring.")
except Exception as exc:  # nothing stored, or no backend available
    print(f"  Nothing removed from the keyring ({type(exc).__name__}: {exc}).")
PY
}

ask_about_keyring() {
    echo ""
    echo "Your OpenRouter API key is stored in the system keyring"
    echo "(GNOME Keyring / KWallet) under service 'ortracker', account 'default'."
    printf "Delete it now? [y/N] "
    read -r reply
    case "$reply" in
        [yY]|[yY][eE][sS]) delete_keyring_entry ;;
        *) echo "  Left the API key in the keyring." ;;
    esac
}

# --- Purge ---
if [ "$PURGE" -eq 1 ]; then
    ask_about_keyring

    echo ""
    echo "Removing application data..."

    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        removed "$VENV_DIR"
    else
        echo "  Not found: $VENV_DIR"
    fi

    if [ -d "$EGG_INFO_DIR" ]; then
        rm -rf "$EGG_INFO_DIR"
        removed "$EGG_INFO_DIR"
    else
        echo "  Not found: $EGG_INFO_DIR"
    fi

    if [ -d "$CONFIG_DIR" ]; then
        rm -rf "$CONFIG_DIR"
        removed "$CONFIG_DIR"
    else
        echo "  Not found: $CONFIG_DIR"
    fi
else
    echo ""
    echo "Run this again with --purge to also remove the virtualenv, the"
    echo "editable-install metadata and ~/.config/ortracker."
fi

# --- Summary ---
echo ""
echo "=== Summary ==="
if [ ${#REMOVED[@]} -gt 0 ]; then
    echo "Removed:"
    printf '  %s\n' "${REMOVED[@]}"
else
    echo "Removed: nothing (the app does not appear to be installed)."
fi
if [ ${#KEPT[@]} -gt 0 ]; then
    echo ""
    echo "Kept on purpose:"
    printf '  %s\n' "${KEPT[@]}"
fi

echo ""
echo "The application directory itself is untouched:"
echo "  $SCRIPT_DIR"
echo "Delete it by hand if you no longer want the source."