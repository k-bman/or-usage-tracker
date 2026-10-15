"""PySide6/Qt6 GUI for the OpenRouter usage monitor.

Provides a compact desktop window with:
- Header bar (title, refresh, settings)
- Free model quota display with progress bar
- Daily usage statistics
- Loading / error / no-credentials states
- Settings dialog (dark theme, 24h clock, refresh interval, API key)
"""

from datetime import datetime
from typing import Callable, Optional
import sys

from PySide6.QtCore import QObject, Qt, QRectF, QTimer, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .app_state import AppState, AppStatus
from .credentials import has_api_key, store_api_key, clear_api_key, retrieve_api_key
from .usage import format_currency, get_quota_warning_level
from .models import UsageSummary

# Base name of the desktop entry (installed by install.sh). Declaring it makes
# Qt use it as the Wayland app id, so compositors see the window class
# "or-usage-tracker" instead of the interpreter name ("python3"). Hyprland
# window rules can then target this app specifically.
DESKTOP_FILE_NAME = "or-usage-tracker"


def _apply_desktop_file_name() -> None:
    """Set the desktop entry name used as the window class/app id.

    Must be called before the QApplication instance is created.
    """
    QGuiApplication.setDesktopFileName(DESKTOP_FILE_NAME)


# --- System tray ---

# Dot colors mirror the progress bar thresholds in MainWindow._set_progress_color
TRAY_COLOR_NORMAL = "#59b258"
TRAY_COLOR_WARNING = "#e0a800"
TRAY_COLOR_CRITICAL = "#e02a2a"
TRAY_COLOR_IDLE = "#666666"

TRAY_ICON_SIZE = 22
TRAY_LIVENESS_CHECK_MS = 15000


def _tray_color_for_level(level: Optional[str]) -> str:
    """Map a quota warning level (see ``get_quota_warning_level``) to a color."""
    if level == "critical" or level == "exhausted":
        return TRAY_COLOR_CRITICAL
    if level == "warning":
        return TRAY_COLOR_WARNING
    return TRAY_COLOR_NORMAL


def _tray_pixmap(color: str, size: int = TRAY_ICON_SIZE) -> QPixmap:
    """Render a filled circle in ``color`` for use as a tray icon.

    Rendered at the device pixel ratio and marked as such, so it stays sharp on
    HiDPI displays without the host having to scale a logical-size pixmap.
    """
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.instance() else 1.0
    ratio = ratio if ratio and ratio > 0 else 1.0

    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        margin = size * 0.15
        painter.drawEllipse(
            QRectF(margin * ratio, margin * ratio, (size - 2 * margin) * ratio, (size - 2 * margin) * ratio)
        )
    finally:
        painter.end()

    return pixmap


def _tray_tooltip(status: AppStatus, summary: Optional[UsageSummary]) -> str:
    """Build the tray tooltip for the current state."""
    title = "OR Usage Tracker"

    if status == AppStatus.NO_CREDENTIALS:
        return f"{title} — no API key configured"

    if status == AppStatus.ERROR:
        return f"{title} — error"

    if status == AppStatus.LOADING or summary is None:
        return f"{title} — loading..."

    parts = [title]

    quota = summary.free_quota
    if quota is not None and quota.limit:
        parts.append(f"{quota.remaining:,} free requests remaining ({quota.used:,} / {quota.limit:,})")
    elif quota is not None:
        parts.append("No free-request quota reported")

    if summary.daily_usage and summary.daily_usage.spending:
        parts.append(f"{format_currency(summary.daily_usage.spending)} today")

    return " — ".join(parts)


def _tray_presentation(status: AppStatus, summary: Optional[UsageSummary]) -> tuple[str, str]:
    """Return ``(color, tooltip)`` for the tray icon."""
    if status == AppStatus.ERROR:
        return TRAY_COLOR_CRITICAL, _tray_tooltip(status, summary)
    if status in (AppStatus.NO_CREDENTIALS, AppStatus.LOADING) or summary is None:
        return TRAY_COLOR_IDLE, _tray_tooltip(status, summary)

    quota = summary.free_quota
    if quota is not None and quota.limit:
        level = get_quota_warning_level(quota.used, quota.limit)
        return _tray_color_for_level(level), _tray_tooltip(status, summary)

    return TRAY_COLOR_NORMAL, _tray_tooltip(status, summary)


def _should_show_window_on_launch(tray_available: bool, start_hidden: bool) -> bool:
    """Decide whether the main window is shown when the app starts.

    A missing tray always means the window is shown: otherwise the app would
    launch with no window and no way to be reached.
    """
    return not (tray_available and start_hidden)


class TrayIcon(QSystemTrayIcon):
    """System tray entry for the usage monitor.

    Actions are injected as callbacks so the icon can be exercised without a
    live application object.
    """

    def __init__(
        self,
        on_show,
        on_refresh,
        on_settings,
        on_quit,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._on_show = on_show
        self._on_refresh = on_refresh
        self._on_settings = on_settings
        self._on_quit = on_quit

        menu = QMenu()
        show_action = menu.addAction("Show Dashboard")
        refresh_action = menu.addAction("Refresh Now")
        settings_action = menu.addAction("Settings…")
        menu.addSeparator()
        quit_action = menu.addAction("Quit")

        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))

        show_action.triggered.connect(self._on_show)
        refresh_action.triggered.connect(self._on_refresh)
        settings_action.triggered.connect(self._on_settings)
        quit_action.triggered.connect(self._on_quit)

        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    @property
    def menu_actions(self) -> list[str]:
        """Text of the menu entries, separators excluded."""
        return [
            action.text()
            for action in self._menu.actions()
            if not action.isSeparator()
        ]

    def trigger(self, text: str) -> bool:
        """Trigger the menu entry with the given text (used by tests)."""
        for action in self._menu.actions():
            if action.text() == text:
                action.trigger()
                return True
        return False

    def set_status(self, color: str, tooltip: str) -> None:
        """Update the icon color and tooltip."""
        self.setIcon(QIcon(_tray_pixmap(color)))
        self.setToolTip(tooltip)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_show()


def _build_dark_palette() -> QPalette:
    palette = QPalette()
    base_color = QColor(18, 18, 18)
    text_color = QColor(228, 228, 228)
    palette.setColor(QPalette.ColorRole.Window, base_color)
    palette.setColor(QPalette.ColorRole.WindowText, text_color)
    palette.setColor(QPalette.ColorRole.Base, QColor(28, 28, 28))
    palette.setColor(QPalette.ColorRole.AlternateBase, base_color)
    palette.setColor(QPalette.ColorRole.ToolTipBase, text_color)
    palette.setColor(QPalette.ColorRole.ToolTipText, text_color)
    palette.setColor(QPalette.ColorRole.Text, text_color)
    palette.setColor(QPalette.ColorRole.Button, base_color)
    palette.setColor(QPalette.ColorRole.ButtonText, text_color)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(62, 128, 231))
    palette.setColor(QPalette.ColorRole.HighlightedText, text_color)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(150, 150, 150))
    return palette


def _apply_tooltip_style(app: QApplication):
    """Apply stylesheet to prevent tooltip text clipping (e.g., 'g' in 'Settings')."""
    app.setStyleSheet(
        app.styleSheet() + """
        QToolTip {
            padding: 4px 8px;
            border: 1px solid #555;
            border-radius: 3px;
        }
        """
    )


def _apply_dark_theme(enable: bool):
    """Apply or remove the dark color scheme via Qt's QApplication palette."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    if enable:
        app.setPalette(_build_dark_palette())
    else:
        app.setPalette(app.style().standardPalette())


class MainThreadDispatcher(QObject):
    """Thread-safe dispatcher that routes callbacks to the Qt main thread."""

    dispatch_signal = Signal(object)

    def __init__(self):
        super().__init__()
        self.dispatch_signal.connect(
            self._handle_dispatch, Qt.ConnectionType.QueuedConnection
        )

    @Slot(object)
    def _handle_dispatch(self, callback: Callable) -> None:
        callback()


_dispatcher: Optional[MainThreadDispatcher] = None


def _get_dispatcher() -> MainThreadDispatcher:
    global _dispatcher
    if _dispatcher is None:
        app = QApplication.instance()
        _dispatcher = MainThreadDispatcher()
        app._dispatcher = _dispatcher
    return _dispatcher


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, app: QApplication, state: AppState):
        super().__init__()
        self._app = app
        self._state = state
        self._state.add_listener(self._on_state_changed)
        self._stale_warning = None

        self.setWindowTitle("OR Usage Tracker")
        self.resize(420, 260)
        self.setMinimumSize(360, 260)

        # Ensure the window has proper content margins for GNOME Wayland CSD
        self._build_ui()
        self._setup_shortcuts()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.setStretch(1, 1)  # Stack gets expanding stretch

        # --- Header bar ---
        self._header_bar = QWidget()
        self._header_bar.setFixedHeight(40)
        header_layout = QHBoxLayout(self._header_bar)
        header_layout.setContentsMargins(10, 4, 10, 4)

        self._refresh_button = QPushButton()
        self._refresh_button.setIcon(QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_BrowserReload))
        self._refresh_button.setToolTip("Refresh now (Ctrl+R)")
        self._refresh_button.setFixedSize(32, 32)
        self._refresh_button.clicked.connect(self._on_manual_refresh)
        header_layout.addWidget(self._refresh_button)

        header_layout.addStretch()

        self._settings_button = QPushButton()
        self._settings_button.setIcon(QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self._settings_button.setToolTip("Settings")
        self._settings_button.setFixedSize(32, 32)
        self._settings_button.clicked.connect(self._on_open_settings)
        header_layout.addWidget(self._settings_button)

        main_layout.addWidget(self._header_bar)

        # --- Stack for view switching ---
        self._stack = QStackedWidget()
        main_layout.addWidget(self._stack)

        self._build_loading_view()
        self._build_error_view()
        self._build_no_credentials_view()
        self._build_content_view()

        self._switch_to_view("loading")

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts (Ctrl+R for refresh, Ctrl+, for settings,
        Ctrl+Q to quit)."""
        refresh_sc = QAction(self)
        refresh_sc.setShortcut(QKeySequence("Ctrl+R"))
        refresh_sc.triggered.connect(self._on_manual_refresh)
        self.addAction(refresh_sc)

        settings_sc = QAction(self)
        settings_sc.setShortcut(QKeySequence("Ctrl+,"))
        settings_sc.triggered.connect(self._on_open_settings)
        self.addAction(settings_sc)

        # With a tray icon, closing the window only hides it, so the app needs
        # an explicit keyboard exit.
        quit_sc = QAction(self)
        quit_sc.setShortcut(QKeySequence("Ctrl+Q"))
        quit_sc.triggered.connect(self._on_quit_requested)
        self.addAction(quit_sc)

    def _on_quit_requested(self):
        """Handle Ctrl+Q."""
        quit_app = getattr(self._app, "quit_app", None)
        if callable(quit_app):
            quit_app()
        else:
            QApplication.quit()

    def closeEvent(self, event):
        """Hide to the system tray when one is available, else close normally."""
        if getattr(self._app, "hide_on_close", False):
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)

    def _switch_to_view(self, name: str):
        view_map = {
            "loading": self._loading_view,
            "error": self._error_view,
            "no_credentials": self._no_credentials_view,
            "content": self._content,
        }
        if name in view_map:
            self._stack.setCurrentWidget(view_map[name])

    def _build_loading_view(self):
        self._loading_view = QWidget()
        layout = QVBoxLayout(self._loading_view)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(6)

        spinner = QProgressBar()
        spinner.setRange(0, 0)  # Indeterminate (spinning)
        spinner.setFixedSize(32, 32)
        spinner.setStyleSheet("QProgressBar {border: none; background: transparent; "
                              "color: transparent; }")
        spinner.setTextVisible(False)
        spinner.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(spinner, alignment=Qt.AlignHCenter)

        label = QLabel("Fetching usage data...")
        label.setStyleSheet("color: #666666;")
        label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(label, alignment=Qt.AlignHCenter)

        layout.addStretch()

        self._stack.addWidget(self._loading_view)

    def _build_error_view(self):
        self._error_view = QWidget()
        layout = QVBoxLayout(self._error_view)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(6)

        icon = QIcon.fromTheme("dialog-error")
        if icon.isNull():
            icon = QApplication.style().standardIcon(
                QStyle.StandardPixmap.SP_MessageBoxCritical)
        icon_label = QLabel()
        icon_pixmap = icon.pixmap(48, 48)
        icon_label.setPixmap(icon_pixmap)
        icon_label.setFixedSize(56, 56)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(icon_label, alignment=Qt.AlignHCenter)

        self._error_title = QLabel("Unable to load data")
        self._error_title.setStyleSheet("font-weight: bold;")
        self._error_title.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._error_title, alignment=Qt.AlignHCenter)

        self._error_msg = QLabel()
        self._error_msg.setWordWrap(True)
        self._error_msg.setAlignment(Qt.AlignCenter)
        self._error_msg.setStyleSheet("color: #666666;")
        self._error_msg.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(self._error_msg)

        self._retry_button = QPushButton("Retry")
        self._retry_button.clicked.connect(self._on_manual_refresh)
        self._retry_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._retry_button, alignment=Qt.AlignHCenter)

        layout.addStretch()

        self._stack.addWidget(self._error_view)

    def _build_no_credentials_view(self):
        self._no_credentials_view = QWidget()
        layout = QVBoxLayout(self._no_credentials_view)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(6)

        icon = QIcon.fromTheme("dialog-warning")
        if icon.isNull():
            icon = QApplication.style().standardIcon(
                QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_label = QLabel()
        icon_pixmap = icon.pixmap(48, 48)
        icon_label.setPixmap(icon_pixmap)
        icon_label.setFixedSize(56, 56)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(icon_label, alignment=Qt.AlignHCenter)

        label = QLabel("No API Key Configured")
        label.setStyleSheet("font-weight: bold;")
        label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(label, alignment=Qt.AlignHCenter)

        hint = QLabel("Configure your OpenRouter API key in Settings.")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #666666;")
        hint.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(hint)

        settings_btn = QPushButton("Open Settings")
        settings_btn.clicked.connect(self._on_open_settings)
        settings_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(settings_btn, alignment=Qt.AlignHCenter)

        layout.addStretch()

        self._stack.addWidget(self._no_credentials_view)

    def _build_content_view(self):
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(12, 2, 12, 2)
        content_layout.setSpacing(2)

        # --- Quota card ---
        self._quota_card = QWidget()
        self._quota_card.setObjectName("quota-card")
        card_layout = QVBoxLayout(self._quota_card)
        card_layout.setSpacing(2)

        self._quota_title = QLabel("Free Model Requests")
        self._quota_title.setStyleSheet("font-weight: bold;")
        card_layout.addWidget(self._quota_title)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(18)
        self._set_progress_color("normal", self._state.config.dark_theme)
        card_layout.addWidget(self._progress_bar)

        # Numbers row
        numbers_row = QHBoxLayout()
        numbers_row.setSpacing(8)

        self._quota_remaining_label = QLabel()
        self._quota_remaining_label.setStyleSheet("font-weight: bold; font-size: 1.1em;")
        numbers_row.addWidget(self._quota_remaining_label)

        numbers_row.addStretch()

        self._quota_used_label = QLabel()
        self._quota_used_label.setStyleSheet("color: #555555;")
        numbers_row.addWidget(self._quota_used_label)
        card_layout.addLayout(numbers_row)

        content_layout.addWidget(self._quota_card)

        # --- Footer ---
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 1, 12, 1)

        self._stat_spending_label = QLabel("Spending Today: $0.00")
        self._stat_spending_label.setStyleSheet("color: #555555;")
        footer.addWidget(self._stat_spending_label)

        footer.addStretch()

        self._last_updated_label = QLabel()
        self._last_updated_label.setStyleSheet("color: #555555;")
        footer.addWidget(self._last_updated_label)

        content_layout.addLayout(footer)

        self._stack.addWidget(self._content)

    def _set_progress_color(self, level: str, dark_theme: bool = False):
        """Set the progress bar color based on warning level."""
        if level == "critical":
            color = "#e02a2a"
        elif level == "warning":
            color = "#e0a800"
        else:
            color = "#59b258"
        # In dark theme, progress bar text should be black; in light theme, default
        text_color = "black" if dark_theme else "#555555"
        self._progress_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; }}"
            f"QProgressBar {{ border: none; color: {text_color}; }}"
        )

    # --- State change handlers ---

    def _on_state_changed(self):
        status = self._state.status
        summary = self._state.summary

        # Keep the tray icon in sync even while the window is hidden.
        update_tray = getattr(self._app, "_update_tray", None)
        if callable(update_tray):
            update_tray()

        if status == AppStatus.LOADING:
            self._clear_stale_indicator()
            self._switch_to_view("loading")
        elif status == AppStatus.READY:
            if summary is not None:
                self._show_usage(summary)
            self._switch_to_view("content")
        elif status == AppStatus.ERROR:
            if summary is not None:
                self._show_usage(summary)
                self._show_stale_indicator(self._state.status_message)
                self._switch_to_view("content")
            else:
                self._error_msg.setText(self._state.status_message)
                self._switch_to_view("error")
        elif status == AppStatus.NO_CREDENTIALS:
            self._switch_to_view("no_credentials")

    def _show_usage(self, summary: UsageSummary):
        """Update all content widgets with the latest usage data."""
        q = summary.free_quota

        remaining = q.remaining
        if remaining >= 0:
            remaining_str = f"{remaining:,} remaining"
        else:
            remaining_str = f"+{abs(remaining):,} over limit"
        self._quota_remaining_label.setText(remaining_str)

        self._quota_used_label.setText(f"({q.used:,} / {q.limit:,})")

        try:
            fraction = int(min(q.used / q.limit, 1.0) * 100) if q.limit > 0 else 0
        except (OverflowError, ZeroDivisionError, TypeError):
            fraction = 0
        self._progress_bar.setValue(fraction)
        level = get_quota_warning_level(q.used, q.limit)
        dark = self._state.config.dark_theme
        if level in ("exhausted", "critical"):
            self._set_progress_color("critical", dark)
        elif level == "warning":
            self._set_progress_color("warning", dark)
        else:
            self._set_progress_color("normal", dark)

        # Update label colors based on theme
        # Light theme: gray (#555555); Dark theme: follow palette (white)
        if not dark:
            self._quota_title.setStyleSheet("font-weight: bold; color: #555555;")
            self._quota_remaining_label.setStyleSheet("font-weight: bold; font-size: 1.1em; color: #555555;")
            self._quota_used_label.setStyleSheet("color: #555555;")
            self._stat_spending_label.setStyleSheet("color: #555555;")
            self._last_updated_label.setStyleSheet("color: #555555;")
        else:
            self._quota_title.setStyleSheet("font-weight: bold;")
            self._quota_remaining_label.setStyleSheet("font-weight: bold; font-size: 1.1em;")
            self._quota_used_label.setStyleSheet("")
            self._stat_spending_label.setStyleSheet("")
            self._last_updated_label.setStyleSheet("")

        u = summary.daily_usage
        self._stat_spending_label.setText(f"Spending Today: {format_currency(u.spending)}")

        updated = summary.fetched_at or datetime.now().astimezone()
        if self._state.config.use_24h_clock:
            time_fmt = '%H:%M'
        else:
            time_fmt = '%I:%M %p'
        self._last_updated_label.setText(f"Updated {updated.strftime(time_fmt)}")

    def _on_time_format_changed(self):
        """Refresh the last-updated label when clock format toggles."""
        summary = self._state._summary
        if summary is not None:
            self._show_usage(summary)

    def _show_stale_indicator(self, message: str):
        """Show a warning note below the last-updated label."""
        if self._stale_warning:
            self._clear_stale_indicator()
        self._stale_warning = QLabel(f"⚠ {message}")
        self._stale_warning.setStyleSheet("color: orange;")
        self._stale_warning.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Replace the updated label in the footer layout
        self._replace_widget(self._last_updated_label, self._stale_warning)

    def _clear_stale_indicator(self):
        """Remove the stale-data warning."""
        if self._stale_warning is not None:
            self._replace_widget(self._stale_warning, self._last_updated_label)
            self._stale_warning.deleteLater()
            self._stale_warning = None

    def _replace_widget(self, old_widget, new_widget):
        """Replace a widget in its parent's layout."""
        parent = old_widget.parentWidget()
        if parent is None:
            return
        layout = parent.layout()
        if layout is None:
            return
        pos = layout.indexOf(old_widget)
        if pos >= 0:
            layout.replaceWidget(old_widget, new_widget)

    # --- Button callbacks ---

    def _on_manual_refresh(self, *args):
        self._clear_stale_indicator()
        self._state.refresh()

    def _on_open_settings(self, *args):
        self.open_settings()

    def open_settings(self):
        """Open the settings dialog (used by the window and the tray menu)."""
        dialog = SettingsDialog(self, self._state)
        QTimer.singleShot(0, dialog._center_on_parent)
        dialog.exec()


class SettingsDialog(QDialog):
    """Settings/preferences dialog."""

    def __init__(self, parent: MainWindow, state: AppState):
        super().__init__(parent, Qt.WindowType.Dialog)
        self._state = state
        self._config = state.config
        self._main_window = parent

        self.setWindowTitle("Settings")
        self.resize(500, 420)
        self.setMinimumWidth(500)
        self.setMaximumWidth(500)
        self.setModal(True)

        self._build_ui()

    def _center_on_parent(self):
        """Align the dialog with its parent window.

        Horizontally the dialog is centered on the parent and clamped to
        the screen. Vertically it is aligned to the parent's top edge: the
        dialog is much taller than the window, so centering the two would
        place the dialog partly off-screen, and the screen clamp would then
        park it against the same edge as the parent -- making the title
        bars line up while the bodies are not centered.

        Uses ``frameGeometry`` for both windows so title bars and borders
        are accounted for on each side. Clamping uses the screen under the
        *parent* rather than ``self.screen()``: a dialog that has not been
        mapped yet can report a different screen than the one it appears on.
        """
        parent = self.parentWidget()
        if parent is None:
            return
        pgeo = parent.frameGeometry()
        dgeo = self.frameGeometry()
        x = pgeo.center().x() - dgeo.width() // 2
        y = pgeo.top()
        screen = QApplication.screenAt(pgeo.center()) or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = max(avail.left(), min(x, avail.right() - dgeo.width()))
            y = max(avail.top(), min(y, avail.bottom() - dgeo.height()))
        self.move(x, y)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        main_layout.addWidget(scroll)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(4)
        content_layout.setContentsMargins(0, 0, 12, 0)
        scroll.setWidget(content)

        creds_box = self._build_credentials_form()
        content_layout.addWidget(creds_box)

        refresh_box = self._build_refresh_form()
        content_layout.addWidget(refresh_box)

        appearance_box = self._build_appearance_form()
        content_layout.addWidget(appearance_box)

        actions = QDialogButtonBox()
        actions.setOrientation(Qt.Horizontal)
        actions.setStandardButtons(QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.Ok)
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        actions.rejected.connect(self._on_cancel)
        actions.accepted.connect(self._on_apply)
        main_layout.addWidget(actions)

        self._update_key_status()

    def _secondary_text_color(self) -> str:
        """Return the appropriate secondary text color for current theme."""
        return "" if self._config.dark_theme else "#555555"

    def _build_credentials_form(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        title = QLabel("OpenRouter Credentials")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self._hint_label = QLabel("API Key from openrouter.ai")
        self._hint_label.setStyleSheet(f"color: {self._secondary_text_color()};")
        layout.addWidget(self._hint_label)

        self._key_entry = QLineEdit()
        self._key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_entry.setPlaceholderText("sk-or-v1-...")
        layout.addWidget(self._key_entry)

        self._key_status_label = QLabel()
        layout.addWidget(self._key_status_label)

        key_button_box = QHBoxLayout()
        key_button_box.setSpacing(8)
        save_btn = QPushButton("Save Key")
        save_btn.clicked.connect(self._on_save_key)
        clear_btn = QPushButton("Clear Key")
        clear_btn.clicked.connect(self._on_clear_key)
        self._test_button = QPushButton("Test Connection")
        self._test_button.clicked.connect(self._on_test_connection)
        key_button_box.addWidget(save_btn)
        key_button_box.addWidget(clear_btn)
        key_button_box.addWidget(self._test_button)
        key_button_box.addStretch()
        layout.addLayout(key_button_box)

        return box

    def _build_refresh_form(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        title = QLabel("Refresh Settings")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(4)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 120)
        self._interval_spin.setSingleStep(1)
        self._interval_spin.setValue(self._config.refresh_interval_ms // 60000)
        self._interval_spin.setMaximumWidth(60)
        row.addWidget(self._interval_spin)

        self._interval_label = QLabel("minutes")
        self._interval_label.setStyleSheet(f"color: {self._secondary_text_color()};")
        row.addWidget(self._interval_label)

        layout.addLayout(row)
        return box

    def _build_appearance_form(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        title = QLabel("Appearance")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(12)
        self._dark_label = QLabel("Dark theme")
        self._dark_label.setStyleSheet(f"color: {self._secondary_text_color()};")
        row.addWidget(self._dark_label)
        row.addStretch()
        self._dark_switch = QCheckBox()
        self._dark_switch.setChecked(self._config.dark_theme)
        self._dark_switch.toggled.connect(self._on_theme_changed)
        row.addWidget(self._dark_switch)
        layout.addLayout(row)

        row24 = QHBoxLayout()
        row24.setSpacing(12)
        self._clock_label = QLabel("24-hour clock")
        self._clock_label.setStyleSheet(f"color: {self._secondary_text_color()};")
        row24.addWidget(self._clock_label)
        row24.addStretch()
        self._clock_switch = QCheckBox()
        self._clock_switch.setChecked(self._config.use_24h_clock)
        row24.addWidget(self._clock_switch)
        layout.addLayout(row24)

        row_tray = QHBoxLayout()
        row_tray.setSpacing(12)
        self._start_hidden_label = QLabel("Start hidden in tray")
        self._start_hidden_label.setToolTip(
            "When a system tray is available, start in the tray instead of\n"
            "opening the window. Without a tray the window always opens."
        )
        self._start_hidden_label.setStyleSheet(
            f"color: {self._secondary_text_color()};"
        )
        row_tray.addWidget(self._start_hidden_label)
        row_tray.addStretch()
        self._start_hidden_switch = QCheckBox()
        self._start_hidden_switch.setChecked(self._config.start_hidden)
        row_tray.addWidget(self._start_hidden_switch)
        layout.addLayout(row_tray)

        return box

    def _on_theme_changed(self, checked: bool):
        """Update label colors when dark theme is toggled."""
        self._config.dark_theme = checked
        color = self._secondary_text_color()
        # Update all labels that use secondary text color
        for label in (self._dark_label, self._clock_label, self._interval_label, self._hint_label, self._start_hidden_label):
            if label is not None:
                label.setStyleSheet(f"color: {color};")

    def _update_key_status(self):
        if has_api_key():
            self._key_status_label.setText("✓ Key stored in system keyring")
            self._key_status_label.setStyleSheet("color: green;")
        else:
            self._key_status_label.setText("No key stored. Enter key and click Save.")
            self._key_status_label.setStyleSheet(f"color: {self._secondary_text_color()};")

    def _on_save_key(self, *args):
        key = self._key_entry.text().strip()
        if not key:
            self._key_status_label.setText("Please enter an API key.")
            self._key_status_label.setStyleSheet("color: red;")
            return
        try:
            store_api_key(key)
            self._key_entry.clear()
            self._update_key_status()
            self._state.refresh()
        except Exception as e:
            self._key_status_label.setText(f"Failed: {e}")
            self._key_status_label.setStyleSheet("color: red;")

    def _on_clear_key(self, *args):
        clear_api_key()
        self._key_entry.clear()
        self._update_key_status()
        self._state.remove_api_key()

    def _on_test_connection(self, *args):
        from .api_client import (
            OpenRouterClient, OpenRouterError, AuthenticationError,
            PermissionError, NetworkError,
        )
        import threading

        # Debounce: prevent rapid repeated tests
        if hasattr(self, '_test_in_progress') and self._test_in_progress:
            return
        self._test_in_progress = True
        self._test_button.setEnabled(False)
        QTimer.singleShot(2000, lambda: setattr(self, '_test_in_progress', False))
        QTimer.singleShot(2000, lambda: self._test_button.setEnabled(True))

        api_key = self._key_entry.text().strip() or (retrieve_api_key() or "")
        if not api_key:
            self._key_status_label.setText("Enter an API key first.")
            self._key_status_label.setStyleSheet("color: red;")
            self._test_in_progress = False
            self._test_button.setEnabled(True)
            return
        self._key_status_label.setText("Testing connection...")

        def test_cb():
            result = None
            try:
                client = OpenRouterClient(api_key)
                client.get_current_key()
                result = (True, "")
            except (AuthenticationError, PermissionError) as e:
                msg = str(e)
                if api_key and api_key in msg:
                    msg = msg.replace(api_key, "[REDACTED]")
                detail = f"HTTP {e.status_code}: {msg}"
                if e.rate_limit and e.rate_limit.remaining is not None:
                    detail += f" (rate limit remaining: {e.rate_limit.remaining})"
                result = (False, detail)
            except OpenRouterError as e:
                msg = str(e)
                if api_key and api_key in msg:
                    msg = msg.replace(api_key, "[REDACTED]")
                detail = f"HTTP error: {msg}"
                if e.status_code is not None:
                    detail = f"HTTP {e.status_code}: {msg}"
                result = (False, detail)
            except Exception as e:
                msg = str(e)
                if api_key and api_key in msg:
                    msg = msg.replace(api_key, "[REDACTED]")
                result = (False, f"Network error: {msg}")

            # Dispatch result back to the main thread
            _get_dispatcher().dispatch_signal.emit(
                lambda: self._on_test_result(result[0], result[1])
            )

        threading.Thread(target=test_cb, daemon=True).start()

    def _on_test_result(self, success: bool, message: str):
        if success:
            self._key_status_label.setText("✓ Connection successful")
            self._key_status_label.setStyleSheet("color: green;")
        else:
            self._key_status_label.setText(f"✗ {message}")
            self._key_status_label.setStyleSheet("color: red;")

    def _on_apply(self, *args):
        interval_min = self._interval_spin.value()
        interval_ms = int(interval_min * 60000)

        self._config.refresh_interval_ms = interval_ms
        self._config.dark_theme = self._dark_switch.isChecked()
        self._config.use_24h_clock = self._clock_switch.isChecked()
        self._config.start_hidden = self._start_hidden_switch.isChecked()
        self._config.save()
        _apply_dark_theme(self._config.dark_theme)
        self._main_window._on_time_format_changed()

        key = self._key_entry.text().strip()
        if key:
            try:
                store_api_key(key)
                self._key_entry.clear()
                self._update_key_status()
            except Exception as e:
                self._key_status_label.setText(f"Failed to save key: {e}")
                self._key_status_label.setStyleSheet("color: red;")

        self._state.update_config_and_restart()
        self.accept()

    def _on_cancel(self, *args):
        self.reject()


class QuotaApplication(QApplication):
    """Main Qt6 application."""

    def __init__(self, argv=None):
        super().__init__(argv if argv is not None else [])
        self._state: Optional[AppState] = None
        self._window: Optional[MainWindow] = None
        self._tray: Optional[TrayIcon] = None
        self._tray_watch: Optional[QTimer] = None
        self.hide_on_close = False
        self._dispatcher: MainThreadDispatcher = _get_dispatcher()
        self.aboutToQuit.connect(self.shutdown)

    def _dispatch_to_main(self, callback: Callable):
        """Schedule a callable on the main thread."""
        self._dispatcher.dispatch_signal.emit(callback)

    def activate(self):
        from .config import load_config
        if self._state is None:
            self._state = AppState(load_config(), dispatch=self._dispatch_to_main)
        _apply_dark_theme(self._state.config.dark_theme)
        _apply_tooltip_style(self)
        self._create_tray()
        if self._window is None:
            self._window = MainWindow(self, self._state)

        show_window = _should_show_window_on_launch(
            tray_available=self._tray is not None,
            start_hidden=self._state.config.start_hidden,
        )
        if show_window:
            self.show_window()
        self._state.start_auto_refresh()
        self._update_tray()

    def _create_tray(self):
        """Create the tray icon if the desktop provides one."""
        if self._tray is not None or not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self._tray = TrayIcon(
            on_show=self.show_window,
            on_refresh=self._on_tray_refresh,
            on_settings=self._on_tray_settings,
            on_quit=self.quit_app,
            parent=self,
        )
        # Closing the window must not end the app while the tray icon lives.
        self.hide_on_close = True
        self.setQuitOnLastWindowClosed(False)
        # Set an icon before showing: Qt warns and skips showing otherwise.
        self._update_tray()
        self._tray.show()

        # If the tray host disappears while the window is hidden, the app would
        # be unreachable, so bring the window back.
        self._tray_watch = QTimer(self)
        self._tray_watch.setInterval(TRAY_LIVENESS_CHECK_MS)
        self._tray_watch.timeout.connect(self._check_tray_liveness)
        self._tray_watch.start()

    def _check_tray_liveness(self):
        if self._tray is None or QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.hide_on_close = False
        self.setQuitOnLastWindowClosed(True)
        if self._tray_watch is not None:
            self._tray_watch.stop()
            self._tray_watch = None
        self._tray.hide()
        self._tray = None
        self.show_window()

    def _on_tray_refresh(self):
        if self._state is not None:
            self._state.refresh()

    def _on_tray_settings(self):
        self.show_window()
        if self._window is not None:
            self._window.open_settings()

    def _update_tray(self):
        """Refresh the tray icon from the current application state."""
        if self._tray is None or self._state is None:
            return
        color, tooltip = _tray_presentation(self._state.status, self._state.summary)
        self._tray.set_status(color, tooltip)

    def show_window(self):
        """Show and focus the main window."""
        if self._window is None:
            return
        self._window.show()
        QTimer.singleShot(50, self._center_main_window)
        self._window.raise_()
        self._window.activateWindow()

    def quit_app(self):
        """Quit the application, stopping background refresh first."""
        self.shutdown()
        QApplication.quit()

    def _center_main_window(self):
        if not self._window:
            return
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            center = avail.center()
            self._window.move(
                center.x() - self._window.width() // 2,
                center.y() - self._window.height() // 2,
            )

    def shutdown(self):
        if self._state:
            self._state.shutdown()


def run():
    """Application entry point."""
    import sys
    _apply_desktop_file_name()
    app = QuotaApplication(sys.argv or [])
    app.activate()
    sys.exit(app.exec())
