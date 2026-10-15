# OR Usage Tracker

A lightweight Linux desktop application for tracking your OpenRouter API usage and free request limits.

Unofficial. Not affiliated with or endorsed by OpenRouter.

## Quick start

```bash
git clone https://github.com/k-bman/or-usage-tracker.git
cd or-usage-tracker
./install.sh
```

That creates a virtualenv in `./.venv`, installs the dependencies (~300 MB, mostly
Qt), adds an "OR Usage Tracker" entry to your application menu, links an
`ortracker` command into `~/.local/bin`. A first run takes a
couple of minutes, mostly downloading PySide6; later runs reuse the venv.

Then launch it — from the application menu, or:

```bash
ortracker
```

Open **Settings** (Ctrl+,) and paste your OpenRouter API key. Nothing else to
configure; the key goes to your system keyring and is never written to disk.

To remove it later: `./uninstall.sh --purge` (add `--purge` to also delete the
virtualenv, the config and, when you confirm the prompt, the stored API key).

## Features

- **Free-model quota tracking**: See how many of your daily free-model requests are used / remaining / limit
- **Daily usage summary**: Spending
- **System tray icon**: Live dot colored by quota state (green/amber/red) with the numbers in its tooltip
- **Close to tray**: Closing the window hides it and keeps background refresh running; quit from the tray menu or Ctrl+Q
- **Start hidden in tray**: Optional — launch straight into the tray instead of opening the window
- **Automatic refresh**: Configurable background refresh interval (default: 10 minutes)
- **Dark theme support**: Native dark theme
- **24-hour clock toggle**: Switch between 12h and 24h time formats
- **Keyboard shortcuts**: Ctrl+R to refresh, Ctrl+, for settings, Ctrl+Q to quit
- **Secure credential storage**: Uses the `keyring` library with native system keyring backends
- **Error resilience**: Shows last-known data when offline, clear error states
- **Cross-desktop**: Works on GNOME, KDE Plasma, Cinnamon, Hyprland/Sway, and other Linux desktops (Wayland and X11)

## Requirements

- Linux (GNOME, KDE Plasma, Cinnamon, Hyprland/Sway, or other desktops)
- Python 3.10+
- A working keyring daemon (GNOME Keyring, KWallet) for credential storage
- Qt's native libraries — already present on any desktop system; only a bare
  server or container needs them installed explicitly (see below)

Python dependencies (**PySide6-Essentials**, `keyring`, `requests`) are
installed by `install.sh` into a virtualenv, so nothing needs to be installed
system-wide.

## Installation

### System packages

pip can supply the Python bindings but *not* Qt's native libraries. On a
desktop system these are already present; on a minimal server or container,
install them first:

```bash
# Arch
sudo pacman -S --needed libglvnd libxkbcommon fontconfig
# Fedora
sudo dnf install mesa-libGL libxkbcommon fontconfig
# Ubuntu/Debian
sudo apt install libegl1 libgl1 libxkbcommon0 libfontconfig1
```

You do **not** need to install PySide6, keyring or requests yourself — the
installer puts them in the virtualenv. (Note that most distributions don't
package PySide6's Python bindings at all; see
[Why a virtualenv](#why-a-virtualenv).)

### Install

```bash
git clone https://github.com/k-bman/or-usage-tracker.git
cd or-usage-tracker
./install.sh
```

The installer creates `./.venv`, installs the app and its runtime
dependencies, verifies PySide6 / the Wayland plugin / your keyring backend,
installs a desktop entry in `~/.local/share/applications/`, and links
`ortracker` into `~/.local/bin`. It does not install the test extras and does
not run the test suite.

The virtualenv means your system Python is left untouched, and
`./uninstall.sh --purge` removes everything it added: the desktop entry, the
`~/.local/bin` launcher, `./.venv`, the editable-install metadata,
`~/.config/ortracker`, and — if you confirm the prompt — the API key in your
keyring. Without `--purge` it only removes the desktop entry and the launcher,
and prints what is left.

A **management key** is recommended over a regular API key — the usage
endpoints need account-level access, and management keys cannot make
inference calls, so they are safe to leave in place. Regular keys still work;
the app falls back to `/key` for basic key info but shows less detail.

## Arch / Hyprland (Omarchy)

The app is a Qt6 (PySide6) application and runs natively on Wayland, so it works on Hyprland as-is. A few notes specific to Arch + Hyprland:

### Why a virtualenv

Arch does **not** package the PySide6 Python bindings — `python-pyside6` is in
neither the `extra` repository nor the AUR (the `pyside6` package is the C++
Qt6 library, not the Python module). `install.sh` therefore creates `./.venv`
and installs everything from PyPI there. The installed desktop entry runs the
venv interpreter.

The PySide6 wheels are `abi3` builds (`cp310-abi3`), so one wheel works across
Python 3.10–3.15, including Arch's current Python.

### Why PySide6-Essentials

The app imports only `QtCore`, `QtGui` and `QtWidgets`, all of which ship in
the smaller **PySide6-Essentials** package. The full `PySide6` metapackage also
pulls QtWebEngine, Qt3D and QtQuick, none of which are used here:

| Package | Installed size |
|---------|----------------|
| `PySide6` (full) | 672 MB |
| `PySide6-Essentials` | **245 MB** |

The import name is identical (`PySide6`), so nothing in the code changes.

### Credentials on Hyprland

The app stores the API key through `keyring`, which needs a running credential
daemon:

- **Hyprland / Omarchy / GNOME-style**: `gnome-keyring-daemon --components=secrets`.
  Login is configured via `pam_gnome_keyring.so auto_start`, so the default
  keyring is unlocked at login and no password prompt appears.
- **KDE Plasma**: KWallet.

`install.sh` prints the detected keyring backend and warns if it is not a credential-backed store. On Arch that backend comes from the `python-keyring` and `python-secretstorage` packages.

If a key lookup ever hangs, your keyring is probably locked — log out and back in rather than reinstalling.

### Wayland notes

- Qt6 loads the Wayland platform plugin automatically. Confirm with
  `hyprctl clients | grep -i ortracker` after launching.
- If the app misbehaves under a specific compositor version, force XWayland for
  one run: `QT_QPA_PLATFORM=xcb ./run`.
- For plugin debugging: `QT_DEBUG_PLUGINS=1 ./run`.
- No `xdg-desktop-portal` is required — the app uses only Qt's built-in
  widgets and dialogs.

### System tray

The tray icon is a `StatusNotifierItem` (the protocol GTK/Qt apps use on
Wayland), which means **something on the bus has to host tray icons**. Your
Omarchy bar does exactly that — quickshell owns both
`org.kde.StatusNotifierWatcher` and a `org.kde.StatusNotifierHost-*`, and the
`omarchy.tray` bar widget draws the icons:

```bash
# Is an item registered for this app?
busctl --user get-property org.kde.StatusNotifierWatcher /StatusNotifierWatcher \
  org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
```

On KDE Plasma the panel provides the host. On a bare sway/wioolfg setup with
no tray host, the app detects this (`QSystemTrayIcon.isSystemTrayAvailable()`),
skips the icon, and behaves like a normal window: closing quits instead of
hiding. The same fallback runs if the host disappears while the app is
running — a 15 s liveness check restores normal closing and re-shows the
window, so the app can never end up hidden with no way to reach it.

Tray behavior:

- Left-click (or double-click) the icon to show and focus the window.
- The icon color mirrors the progress bar thresholds: green below 80%, amber
  at 80%+, red at 90%+, grey with no key configured or while loading, red on
  error.
- The tooltip carries the numbers (e.g. `750 free requests remaining
  (250 / 1,000) · $1.23 today`).
- Menu: **Show Dashboard**, **Refresh Now**, **Settings…**, **Quit**.

### Optional Hyprland integration

These are copy-paste snippets; the installer does not modify your Hyprland
configuration.

Start the app with your session (`~/.config/hypr/autostart.lua`):

```lua
o.launch_on_start("or-usage-tracker")
```

Open it as a small floating panel instead of letting Hyprland tile it
(`~/.config/hypr/looknfeel.lua` or `~/.config/hypr/hyprland.lua` — your
`looknfeel.lua` is loaded after Omarchy's defaults, so it wins):

```lua
-- OR Usage Tracker: float as a panel, never tiled
o.window("or-usage-tracker", {
  float = true,
  no_shadow = true,
  no_dim = true,
  no_blur = true,
})
```

Notes on this rule:

- The class `or-usage-tracker` comes from `QGuiApplication.setDesktopFileName()`
  in `ortracker/gui.py`. Without it the window class is the interpreter name
  (`python3`), and a class rule would float every Python app on your system.
- Hyprland centers new floating windows automatically, so no `center` effect is
  needed. The app's own `move()` call only has an effect under XWayland.
- `no_gaps` is not a Hyprland effect — gaps are global/workspace settings, so
  `no_dim`/`no_blur`/`no_shadow` are used to get a flat, gap-free-looking panel.
- The window opens at its natural 420×260 size. Add `persistent_size = true` to
  make Hyprland remember a size you resize it to, or `min_size`/`max_size` to
  clamp resizing.
- The Settings dialog uses the same app id, so this rule floats it as well.

After editing, reload Hyprland with `hyprctl reload` (or restart the session).
Check the result with:

```bash
hyprctl clients -j | jq '.[] | select(.class == "or-usage-tracker") | {class, floating, size}'
```

### Why Python/Qt rather than another language

Wayland support on Hyprland depends on the **toolkit**, not the language. Qt6
is the most predictable choice for plain Wayland compositors, and PySide6's
`abi3` wheel keeps the Python version independent. A rewrite in C++/Rust/Go
would not improve Hyprland compatibility — it would only change how the app is
packaged — while discarding a working UI and its test suite.

If you later want the app **inside the Omarchy bar**, that is a frontend swap
rather than a language swap: keep `ortracker/` as the backend, expose a JSON
mode, and render it with a small QML frontend (your bar is
[quickshell](https://quickshell.outfoxxed.me/), which is QtQuick-based).

## Usage

### Launching

Pick whichever fits:

| Command | When to use |
|---------|-------------|
| `ortracker` | Anywhere, after `./install.sh` (symlinked into `~/.local/bin`) |
| App menu | "OR Usage Tracker" from your desktop launcher |
| `./run` | From the project directory, or "Run as a Program" in a file manager |
| `./.venv/bin/python run.py` | If you prefer to be explicit about the interpreter |

`run.py` is a plain launcher: it uses whichever interpreter invoked it and
fails with an actionable message if that interpreter has no PySide6 (it also
shows the error as a desktop notification, since file managers discard
stderr). Use `ortracker` or `./run` so the project's virtualenv is picked up
automatically.

### First-time setup: Configuring credentials

1. Go to https://openrouter.ai/settings/management-keys
2. Create an **API Key**
   - Optionally set an expiration date on the key for security.
3. Copy the key
4. In the app, open **Settings** (gear icon or Ctrl+,)
5. Paste the key into the "API Key" field
6. Click **Save Key**, then **Test Connection** to verify

The key is stored using the `keyring` library, which automatically selects the
appropriate native credential store for your desktop:

- GNOME Keyring (GNOME, Cinnamon)
- KWallet (KDE Plasma)
- macOS Keychain (macOS)
- Windows Credential Manager (Windows)

It is never written to disk or application logs.

To change the key later: open Settings, enter the new key, and click Save.

### Settings

Configurable options (in Settings):

| Setting | Default | Description |
|---------|---------|-------------|
| API Key | (none) | Your OpenRouter API key |
| Refresh interval | 10 minutes | How often to auto-refresh usage data |
| Dark theme | On | Enable dark color scheme |
| 24-hour clock | Off | Use 24-hour time format for "Last Updated" |
| Start hidden in tray | Off | Start in the system tray instead of opening the window (ignored when no tray is available) |

Settings are saved automatically when you click **Apply**, and persist between sessions via a config file at `~/.config/ortracker/config.json`.

### Display

The main window shows:

- **Free Model Requests**: A progress bar with remaining count and used/total ratio (e.g., "750 remaining", "(250 / 1,000)")
- **Spending Today**: USD cost of today's usage
- **Updated**: Time of last successful fetch (HH:MM format)

The progress bar color indicates quota usage level:

- **Green**: Normal (below 80%)
- **Amber**: Warning (80%+)
- **Red**: Critical (90%+)

When a system tray is available, closing the window hides it instead of
quitting; the app keeps refreshing and can be reopened from the tray icon or
with Ctrl+Q to quit. Without a tray, closing quits as usual.

### Network behavior

- When the network is unavailable, the app keeps displaying the last-known data with an error indicator.
- Authentication errors prompt you to check your credentials.
- Rate limit errors are displayed with guidance.

## Architecture

```
or-usage-tracker/
├── ortracker/
│   ├── __init__.py          # Package marker
│   ├── __main__.py          # Module entry point (python -m ortracker)
│   ├── config.py            # Configuration (JSON in ~/.config/ortracker/)
│   ├── credentials.py       # Secure credential storage via keyring library
│   ├── api_client.py        # OpenRouter REST API client
│   ├── usage.py             # Data parsing, aggregation, quota calculations
│   ├── models.py            # Data classes (UsageSummary, DailyUsage, etc.)
│   ├── app_state.py         # State manager with background refresh (thread-safe)
│   └── gui.py               # PySide6/Qt6 UI (MainWindow, SettingsDialog, QuotaApplication)
│       ├── MainThreadDispatcher    # Thread-safe callback dispatch to Qt main thread
│       ├── TrayIcon                # QSystemTrayIcon (StatusNotifierItem) with menu
│       └── QuotaApplication        # QApplication subclass (owns tray, window, quit)
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # Forces the headless Qt platform for every run
│   ├── test_usage.py        # Usage calculation tests
│   ├── test_api_client.py   # API client tests
│   ├── test_state_config.py # Config, state and credential tests
│   └── test_gui.py          # PySide6 GUI and system tray tests (pytest-qt)
├── run                      # Convenience launcher (uses ./.venv)
├── run.py                   # Standalone launcher (uses the invoking interpreter)
├── install.sh               # Installer (.venv, desktop entry, launcher; no tests)
├── uninstall.sh             # Uninstaller script
└── or-usage-tracker.desktop # Desktop entry file
```

### Data flow

1. `app_state.py` calls the API client on a timer (background thread)
2. `api_client.py` calls OpenRouter endpoints with the API key:
   - `GET /api/v1/keys` — lists API keys with `free_model_daily_requests` (Management key only)
   - `GET /api/v1/key` — current key info (works with any valid API key; fallback)
   - `GET /api/v1/activity?date=<utc_today>` — per-model activity for today
   - `GET /api/v1/credits` — account credit balance
3. `usage.py` parses the responses and computes:
   - Free model quota (limit, used, remaining) from key data
   - Total requests/tokens/spending from activity data
4. Results stored in `UsageSummary` (see `models.py`)
5. `gui.py` observes state changes and updates the display

### API reference

The application uses these OpenRouter endpoints:

| Endpoint | Purpose | Auth Required |
|----------|---------|----------------|
| `GET /api/v1/keys` | Lists all API keys with `free_model_daily_requests.{limit, remaining, used}` | Management key |
| `GET /api/v1/key` | Current key info (fallback for regular API keys) | Any valid key |
| `GET /api/v1/activity?date=<date>` | Per-model activity for a UTC date (requests, tokens, cost) | Management key |
| `GET /api/v1/credits` | Account credit balance (total purchased, total used) | Management key |

Free-model requests are identified by the `:free` suffix in model slugs (e.g., `openai/gpt-4o:free`), as documented in OpenRouter's [Free Variant guide](https://openrouter.ai/docs/guides/routing/model-variants/free).

If `/keys` or `/activity` endpoints fail (requires Management key), the app falls back to `/key` which works with any valid API key.

## Running tests

```bash
# The installer brings in the runtime dependencies only; add the test extras:
./.venv/bin/python -m pip install -e ".[dev]"

./.venv/bin/python -m pytest tests/ -q
```

The tests always run headless: `tests/conftest.py` forces
`QT_QPA_PLATFORM=offscreen` before Qt loads, so the GUI tests never open windows
on your real desktop session (a desktop environment may export
`QT_QPA_PLATFORM=wayland;xcb`, which would otherwise make every widget a real
compositor window). Set `ORTRACKER_TEST_PLATFORM=wayland` to override that for
a single debugging run.

`install.sh` does not run the suite — its dependency checks are the
install-time verification, and the tests belong to development and CI.

### Continuous integration

`.github/workflows/tests.yml` runs the suite on Python 3.10–3.13 for every
push and pull request, plus a history-wide secret scan with
[gitleaks](https://github.com/gitleaks/gitleaks).

### Secret scanning locally

A `pre-commit` hook blocks commits that contain hardcoded secrets
(API keys, private keys, tokens):

```bash
sudo pacman -S gitleaks pre-commit   # or: pip install pre-commit
pre-commit install
```

The hook runs `gitleaks` from `PATH` (`repo: local` + `language: system` in
`.pre-commit-config.yaml`, so nothing is downloaded). It scans **staged
content only** and can be skipped with `git commit --no-verify` — the CI
secret scan covers that gap, and the case of a key landing in history.

The single deliberate exception lives in `.gitleaks.toml`: the fake
`sk-or-v1-TOPSECRET` fixture used by the error-redaction tests. It is
allowlisted by exact literal, never by prefix, so real OpenRouter keys are
still detected.

Tests cover:
- Parsing OpenRouter API responses (keys, activity, credits)
- Distinguishing free vs. paid models (`:free` suffix detection)
- Daily usage aggregation from per-model activity data
- Quota calculation and warning thresholds (80%, 90%, 100%+)
- Handling empty, malformed, and missing-field responses
- Timezone/date-boundary edge cases (UTC conversion)
- Rate limit header parsing
- API client error handling (401, 403, 429, network errors)
- PySide6 GUI rendering and state transitions (via pytest-qt)
- System tray icon: color per quota level, rendered pixmap pixels, menu contents and callbacks, close-to-tray vs. close-to-quit, launch visibility rules

Tests use mocked API responses — no real OpenRouter account or key is needed.

## Uninstallation

```bash
./uninstall.sh          # desktop entry + ~/.local/bin launcher only
./uninstall.sh --purge  # everything the installer added
```

`--purge` additionally removes `./.venv`, the `or_usage_tracker.egg-info`
metadata and `~/.config/ortracker`, and asks before deleting your stored API
key from the system keyring. Either way the script prints a summary of what it
removed and what it deliberately left alone.

The application directory itself is never deleted by the script. To remove the
source as well:
```bash
rm -rf /path/to/or-usage-tracker
```

If you ever remove the keyring entry by hand instead:
```bash
./.venv/bin/python -c "import keyring; keyring.delete_password('ortracker', 'default')"
```

## Security notes

- API keys are stored via the **keyring library**, which uses the system's native
  credential store — they are encrypted at rest and never written to config files
  or application logs.
- Error messages never include the API key value (it is redacted if present).
- The API key can be either a Management key or regular API key. Management keys
  cannot make inference calls, making them the safest option for monitoring.
- Consider setting an expiration date on your API key for additional security.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No module named PySide6` | run `./install.sh`, then launch with `ortracker` or `./run` |
| `libEGL.so.1: cannot open shared object file` | Qt's native libraries are missing; see [System packages](#system-packages) |
| Nothing happens when double-clicking `run.py` | the shebang uses the system Python; use `./run` ("Run as a Program") or the app menu instead |
| App opens as a black or blank window | try `QT_QPA_PLATFORM=xcb` |
| Keyring prompt on every launch | keyring is locked; log out and back in |
| `keyring` backend is `fail` | install `gnome-keyring` and ensure `pam_gnome_keyring.so auto_start` is in `/etc/pam.d/*` |
