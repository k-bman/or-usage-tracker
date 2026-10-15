#!/bin/bash
#
# Installer for the OpenRouter usage monitor (ortracker).
#
# Two installation modes:
#
#   native  Use the system interpreter and the distribution's PySide6 /
#           keyring / requests packages. Nothing is downloaded, no virtualenv
#           is created, and OS upgrades leave the app working untouched.
#   venv    Create a project virtualenv at ./.venv and pip install the runtime
#           dependencies from PyPI. Use this when the distribution does not
#           package the Python bindings, or when you need a newer PySide6 than
#           your distribution ships.
#
# The mode is chosen automatically by probing the system interpreter. Pass
# --native or --venv to force one; --native fails loudly rather than silently
# doing something else.
#
# This script never runs pip or sudo as root. It prints the command for you.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
VENV_DIR="$APP_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
RUN_SCRIPT="$APP_DIR/run"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE_NAME="or-usage-tracker"
DESKTOP_FILE="$DESKTOP_DIR/$DESKTOP_FILE_NAME.desktop"

MODE=""
FORCED_MODE=""

usage() {
    cat << EOF
Usage: $0 [--native | --venv]

Install the OpenRouter usage monitor (ortracker).

  --native    Install against the system interpreter and the distribution's
              packages. No virtualenv, nothing downloaded. This is the default
              when the system interpreter can already import the dependencies.
  --venv      Create ./.venv and install the dependencies from PyPI into it.
              Use when the distribution has no PySide6 bindings, or when you
              need a newer PySide6 than it ships.
  -h, --help  Show this help.

Uninstall: ./uninstall.sh [--purge]
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --native) FORCED_MODE=native ;;
        --venv)   FORCED_MODE=venv ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

echo "=== OR Usage Tracker Installer ==="
echo "Application directory: $APP_DIR"

# --- Detect package manager ---
detect_pkg_manager() {
    if command -v pacman &>/dev/null; then
        echo "pacman"
    elif command -v dnf &>/dev/null; then
        echo "dnf"
    elif command -v apt-get &>/dev/null; then
        echo "apt"
    else
        echo "unknown"
    fi
}

# Prints the command to install the given packages with the given manager.
pkg_install_hint() {
    local pm="$1"
    shift
    case "$pm" in
        pacman) echo "sudo pacman -S --needed $*" ;;
        dnf)    echo "sudo dnf install $*" ;;
        apt)    echo "sudo apt install $*" ;;
        *)      echo "(no supported package manager found) $*" ;;
    esac
}

# Maps a dependency to its distribution package name.
pkg_name_for() {
    case "$1" in
        PySide6)
            case "$PKG_MANAGER" in
                pacman) echo "pyside6" ;;
                dnf)    echo "python3-pyside6" ;;
                apt)    echo "python3-pyside6.qtwidgets" ;;
                *)      echo "<distro package providing PySide6>" ;;
            esac
            ;;
        keyring)
            case "$PKG_MANAGER" in
                pacman) echo "python-keyring" ;;
                dnf)    echo "python3-keyring" ;;
                apt)    echo "python3-keyring" ;;
                *)      echo "<distro package providing keyring>" ;;
            esac
            ;;
        requests)
            case "$PKG_MANAGER" in
                pacman) echo "python-requests" ;;
                dnf)    echo "python3-requests" ;;
                apt)    echo "python3-requests" ;;
                *)      echo "<distro package providing requests>" ;;
            esac
            ;;
        qt6-wayland)
            case "$PKG_MANAGER" in
                pacman) echo "qt6-wayland" ;;
                dnf)    echo "qt6-qt6-wayland" ;;
                apt)    echo "qt6-wayland" ;;
                *)      echo "<distro package providing the Qt Wayland plugin>" ;;
            esac
            ;;
    esac
}

PKG_MANAGER="$(detect_pkg_manager)"
echo "Package manager: $PKG_MANAGER"

# --- Check Python 3 ---
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required but not installed."
    exit 1
fi
echo "Python 3: $(python3 --version) ($(command -v python3))"

# --- Choose the mode ---
# The probe is the source of truth, not a package-name table: it cannot go
# stale when a distribution renames or splits a package, and it answers the
# only question that matters -- can the system interpreter run the app.
system_python_has_deps() {
    python3 -c "import PySide6, keyring, requests" 2>/dev/null
}

if [ -n "$FORCED_MODE" ]; then
    MODE="$FORCED_MODE"
    if [ "$MODE" = native ] && ! system_python_has_deps; then
        echo ""
        echo "ERROR: --native was requested, but the system interpreter is missing"
        echo "       at least one of: PySide6, keyring, requests."
        echo ""
        echo "Install them with:"
        pkg_install_hint "$PKG_MANAGER" "$(pkg_name_for PySide6)" "$(pkg_name_for keyring)" "$(pkg_name_for requests)"
        echo ""
        echo "Or re-run without --native to install into a virtualenv instead:"
        echo "  $0 --venv"
        exit 1
    fi
elif system_python_has_deps; then
    MODE=native
else
    MODE=venv
fi

# --- Mode-specific setup ---
if [ "$MODE" = native ]; then
    PY="$(command -v python3)"
    echo ""
    echo "Mode: native (system interpreter, distribution packages)"
    echo "  Nothing will be downloaded and no virtualenv will be created."
    echo "  Interpreter: $PY"
else
    PY="$VENV_PY"
    echo ""
    echo "Mode: venv (project virtualenv, dependencies from PyPI)"
    if [ ! -x "$VENV_PY" ]; then
        echo "Creating virtualenv: $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    else
        echo "  Virtualenv already exists, reusing it."
    fi

    echo "Installing application dependencies..."
    "$VENV_PY" -m pip install --upgrade pip --quiet
    # Runtime dependencies only. The test extras (.[dev]) are for contributors
    # and CI, which install them explicitly; end users never run the suite.
    "$VENV_PY" -m pip install -e "$APP_DIR" --quiet
fi

# --- Verify dependencies ---
# These run against whichever interpreter the mode selected, so native mode
# gets exactly the same verification venv mode does.
echo ""
echo "Checking dependencies..."

MISSING=()

if "$PY" -c "import PySide6" 2>/dev/null; then
    echo "  PySide6: OK"
else
    MISSING+=("PySide6")
    echo "  PySide6: MISSING"
fi

if QT_VERSION="$("$PY" -c 'from PySide6.QtCore import qVersion; print(qVersion())' 2>/dev/null)"; then
    echo "  Qt runtime: OK (Qt $QT_VERSION)"
else
    MISSING+=("PySide6")
    echo "  Qt runtime: MISSING"
fi

# The Wayland platform plugin gives native rendering on Wayland compositors
# (Hyprland, Sway, ...). Without it the app falls back to XWayland, which works
# but is not preferred -- so this is a warning, not a fatal dependency.
if "$PY" -c "
import sys, pathlib, PySide6
plugins = pathlib.Path(PySide6.__file__).parent / 'Qt' / 'plugins' / 'platforms'
sys.exit(0 if (plugins / 'libqwayland.so').exists() else 1)
" 2>/dev/null; then
    echo "  Wayland platform plugin: OK"
else
    echo "  Wayland platform plugin: MISSING (app will fall back to XWayland)"
    echo "    Install it for native Wayland rendering:"
    echo "      $(pkg_install_hint "$PKG_MANAGER" "$(pkg_name_for qt6-wayland)")"
fi

if KEYRING_BACKEND="$("$PY" -c 'import keyring; b = keyring.get_keyring(); print(f"{b.__module__}.{type(b).__name__}")' 2>/dev/null)"; then
    echo "  keyring: OK (backend: $KEYRING_BACKEND)"
    case "$KEYRING_BACKEND" in
        keyring.backends.fail*)
            echo "    WARNING: no usable credential backend was found."
            echo "    On GNOME/Hyprland install gnome-keyring and ensure it runs with the"
            echo "    'secrets' component; on KDE Plasma install KWallet."
            ;;
    esac
else
    MISSING+=("keyring")
    echo "  keyring: MISSING"
fi

if "$PY" -c "import requests" 2>/dev/null; then
    echo "  requests: OK"
else
    MISSING+=("requests")
    echo "  requests: MISSING"
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "Missing dependencies: ${MISSING[*]}"
    echo ""
    if [ "$MODE" = native ]; then
        HINT_PKGS=()
        for dep in "${MISSING[@]}"; do
            HINT_PKGS+=("$(pkg_name_for "$dep")")
        done
        echo "Install them with:"
        echo "  $(pkg_install_hint "$PKG_MANAGER" "${HINT_PKGS[@]}")"
        echo ""
        echo "Or re-run to install into a virtualenv instead:"
        echo "  $0 --venv"
    else
        echo "Re-run this installer, or install manually with:"
        echo "  $VENV_PY -m pip install ${MISSING[*]}"
    fi
    echo ""
    exit 1
fi

# --- Install desktop entry ---
echo ""
echo "Installing desktop entry..."

mkdir -p "$DESKTOP_DIR"

# Generate the desktop file with the correct paths. Both modes launch through
# the project's ./run wrapper, which picks the right interpreter, so the entry
# does not need to know which mode it is in.
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=OR Usage Tracker
Comment=Track your OpenRouter API usage and free request limits
Exec=$RUN_SCRIPT
Icon=application-internet
Terminal=false
Type=Application
Categories=Network;Utility;
Keywords=OpenRouter;API;Usage;Tracker;
StartupNotify=true
StartupWMClass=$DESKTOP_FILE_NAME
EOF

chmod +x "$DESKTOP_DIR"/or-usage-tracker.desktop 2>/dev/null || true

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" &>/dev/null || true
fi

echo "  Installed desktop entry to: $DESKTOP_FILE"

# --- Install launcher on PATH ---
echo ""
echo "Installing launcher..."

BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/ortracker"

if [ -e "$LAUNCHER" ] && [ ! -L "$LAUNCHER" ]; then
    # Never clobber a real file the user may have put there on purpose.
    echo "  WARNING: $LAUNCHER exists and is not a symlink; leaving it alone."
    echo "           Use '$RUN_SCRIPT' to launch the app."
else
    mkdir -p "$BIN_DIR"
    ln -sfn "$RUN_SCRIPT" "$LAUNCHER"
    echo "  Linked $LAUNCHER -> $RUN_SCRIPT"
fi

# --- Installation complete ---
echo ""
echo "=== Installation complete! ==="
echo "  Mode: $MODE"
echo ""
echo "To launch:"
echo "  1. Find 'OR Usage Tracker' in your application menu, or"
echo "  2. Run: ortracker"
echo "     (from a file manager: ./run, or 'Run as a Program' on that file)"
echo ""
if [ "$MODE" = native ]; then
    echo "Native mode runs the app from this directory with your distribution's"
    echo "packages, so OS upgrades leave it working. To move it back into a"
    echo "virtualenv (or to get a newer PySide6 than your distro ships):"
    echo "  $0 --venv"
fi
echo ""
echo "Before first use, create an API key at:"
echo "  https://openrouter.ai/settings/management-keys"
echo "Then enter it in Settings (gear icon) in the app."