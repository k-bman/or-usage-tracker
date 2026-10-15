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

  --purge    Also remove the virtualenv (if there is one), the editable-install
             metadata and the app config, and offer to delete the API key from
             the system keyring.
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
        "$SCRIPT_DIR"/*)
            # Either mode's launcher (./run, or .venv/bin/ortracker) lives
            # inside this project directory, so one rule covers both.
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
# Done before the virtualenv is deleted, because it needs an interpreter that
# can import keyring: the venv's in venv mode, the system one in native mode.
keyring_python() {
    if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import keyring" 2>/dev/null; then
        echo "$VENV_PY"
    elif command -v python3 &>/dev/null && python3 -c "import keyring" 2>/dev/null; then
        command -v python3
    fi
}

delete_keyring_entry() {
    local py
    py="$(keyring_python)"
    if [ -z "$py" ]; then
        echo "  Cannot check the keyring: no interpreter with the keyring module."
        echo "  Remove it with: secret-tool delete service ortracker username default"
        return
    fi
    "$py" - <<'PY'
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
    fi

    if [ -d "$EGG_INFO_DIR" ]; then
        rm -rf "$EGG_INFO_DIR"
        removed "$EGG_INFO_DIR"
    fi

    if [ -d "$CONFIG_DIR" ]; then
        rm -rf "$CONFIG_DIR"
        removed "$CONFIG_DIR"
    fi

    if [ ! -d "$VENV_DIR" ]; then
        # Native mode: nothing was pip-installed, so there is nothing to
        # uninstall beyond the config above. The distribution packages are
        # shared with other applications, so we only ever offer the command.
        echo ""
        echo "This looks like a native (distribution package) install, so the"
        echo "system Python was never modified. To drop the shared packages too,"
        echo "if no other application needs them:"
        echo "  sudo pacman -Rns pyside6 python-keyring   # Arch"
        echo "  sudo dnf remove python3-pyside6 python3-keyring"
        echo "  sudo apt remove python3-pyside6.qtwidgets python3-keyring"
    fi
else
    echo ""
    echo "Run this again with --purge to also remove the virtualenv (if there"
    echo "is one), the editable-install metadata and ~/.config/ortracker."
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