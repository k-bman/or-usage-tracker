#!/bin/bash
#
# Installer for the OpenRouter usage monitor (ortracker).
#
# This script:
#   1. Detects the system package manager and reports missing system packages
#   2. Creates a project-local virtualenv and installs the app + test deps into it
#   3. Verifies dependencies (including the Qt Wayland platform plugin)
#   4. Installs the desktop entry to ~/.local/share/applications/
#   5. Runs the test suite
#
# The app runs from a virtualenv on purpose: several distributions (Arch among
# them) do not package the PySide6 Python bindings, so they come from PyPI.
#
# Usage:  ./install.sh
# Uninstall: ./uninstall.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
VENV_DIR="$APP_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE_NAME="or-usage-tracker"
DESKTOP_FILE="$DESKTOP_DIR/$DESKTOP_FILE_NAME.desktop"

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

# Prints the command to install the given packages with the detected manager.
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

PKG_MANAGER="$(detect_pkg_manager)"
echo "Package manager: $PKG_MANAGER"

# --- Check Python 3 ---
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required but not installed."
    exit 1
fi
echo "Python 3: $(python3 --version) ($(command -v python3))"

# --- System packages (informational) ---
# Python bindings are intentionally absent from this list: most distributions
# do not package them, and the ones that do may lag behind PyPI. The
# virtualenv below installs PySide6-Essentials from PyPI.
#
# What system packages *do* matter are Qt's native libraries, which pip cannot
# provide. On a minimal system PySide6 fails at import with e.g.
# "libEGL.so.1: cannot open shared object file". Desktop systems already have
# them; see README.md for per-distro names.
echo ""
echo "Recommended system packages (Qt native libraries):"
case "$PKG_MANAGER" in
    pacman)
        echo "  libglvnd libxkbcommon fontconfig"
        ;;
    dnf)
        echo "  mesa-libGL libxkbcommon fontconfig"
        ;;
    apt)
        echo "  libegl1 libgl1 libxkbcommon0 libfontconfig1"
        ;;
    *)
        echo "  libEGL, libGL, libxkbcommon, fontconfig"
        ;;
esac

# --- Create virtualenv ---
echo ""
echo "Creating virtualenv: $VENV_DIR"
if [ ! -x "$VENV_PY" ]; then
    python3 -m venv "$VENV_DIR"
else
    echo "  Virtualenv already exists, reusing it."
fi

echo "Installing application and test dependencies..."
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -e "$APP_DIR[dev]" --quiet

# --- Verify dependencies (inside the virtualenv) ---
echo ""
echo "Checking dependencies..."

MISSING_PKGS=()

if ! "$VENV_PY" -c "import PySide6" 2>/dev/null; then
    MISSING_PKGS+=("PySide6-Essentials (pip install PySide6-Essentials)")
    echo "  PySide6-Essentials: MISSING"
else
    echo "  PySide6-Essentials: OK"
fi

if QT_VERSION="$("$VENV_PY" -c 'from PySide6.QtCore import qVersion; print(qVersion())' 2>/dev/null)"; then
    echo "  Qt runtime: OK (Qt $QT_VERSION)"
else
    MISSING_PKGS+=("PySide6 Qt6 runtime")
    echo "  Qt runtime: MISSING"
fi

# Wayland platform plugin: required for native rendering on Wayland compositors
# (Hyprland, Sway, ...). XWayland works as a fallback but is not preferred.
if "$VENV_PY" -c "
import sys, pathlib, PySide6
plugins = pathlib.Path(PySide6.__file__).parent / 'Qt' / 'plugins' / 'platforms'
sys.exit(0 if (plugins / 'libqwayland.so').exists() else 1)
" 2>/dev/null; then
    echo "  Wayland platform plugin: OK"
else
    MISSING_PKGS+=("qt6-wayland (system package providing the Qt Wayland platform plugin)")
    echo "  Wayland platform plugin: MISSING (app will fall back to XWayland)"
fi

if KEYRING_BACKEND="$("$VENV_PY" -c 'import keyring; b = keyring.get_keyring(); print(f"{b.__module__}.{type(b).__name__}")' 2>/dev/null)"; then
    echo "  keyring: OK (backend: $KEYRING_BACKEND)"
    case "$KEYRING_BACKEND" in
        keyring.backends.fail*)
            echo "    WARNING: no usable credential backend was found."
            echo "    On GNOME/Hyprland install gnome-keyring and ensure it runs with the"
            echo "    'secrets' component; on KDE Plasma install KWallet."
            ;;
    esac
else
    MISSING_PKGS+=("keyring (pip install keyring)")
    echo "  keyring: MISSING"
fi

if "$VENV_PY" -c "import requests" 2>/dev/null; then
    echo "  requests: OK"
else
    MISSING_PKGS+=("requests (pip install requests)")
    echo "  requests: MISSING"
fi

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo ""
    echo "Missing dependencies:"
    printf '  %s\n' "${MISSING_PKGS[@]}"
    echo ""
    echo "Re-run this installer, or install manually with:"
    pkg_install_hint "$PKG_MANAGER" "${MISSING_PKGS[@]}"
    echo ""
    exit 1
fi

# --- Install desktop entry ---
echo ""
echo "Installing desktop entry..."

mkdir -p "$DESKTOP_DIR"

# Generate the desktop file with the correct paths.
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=OR Usage Tracker
Comment=Track your OpenRouter API usage and free request limits
Exec=$VENV_DIR/bin/ortracker
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

if [ ! -x "$VENV_DIR/bin/ortracker" ]; then
    echo "  WARNING: $VENV_DIR/bin/ortracker is missing; skipping launcher."
elif [ -e "$LAUNCHER" ] && [ ! -L "$LAUNCHER" ]; then
    # Never clobber a real file the user may have put there on purpose.
    echo "  WARNING: $LAUNCHER exists and is not a symlink; leaving it alone."
    echo "           Use '$VENV_DIR/bin/ortracker' or './run' to launch the app."
else
    mkdir -p "$BIN_DIR"
    ln -sfn "$VENV_DIR/bin/ortracker" "$LAUNCHER"
    echo "  Linked $LAUNCHER -> $VENV_DIR/bin/ortracker"
fi

# --- Verify tests pass ---
echo ""
echo "Running tests..."
"$VENV_PY" -m pytest "$APP_DIR/tests/" -v

echo ""
echo "=== Installation complete! ==="
echo ""
echo "To launch:"
echo "  1. Find 'OR Usage Tracker' in your application menu, or"
echo "  2. Run: ortracker"
echo "     (from a file manager: ./run, or 'Run as a Program' on that file)"
echo ""
echo "Before first use, create an API key at:"
echo "  https://openrouter.ai/settings/management-keys"
echo "Then enter it in Settings (gear icon) in the app."