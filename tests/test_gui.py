"""GUI tests for the Qt6-based PySide6 interface.

Uses pytest-qt to create and interact with widgets in an offscreen
Qt environment so tests are deterministic and headless.
"""

import pytest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ortracker.app_state import AppState
from ortracker.config import Config
from ortracker.gui import MainWindow, SettingsDialog, QuotaApplication, _apply_dark_theme
from ortracker.models import FreeModelQuota, DailyUsage, UsageSummary


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def app(config):
    """Create a QApplication for testing (Qt must have one instance)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    _apply_dark_theme(config.dark_theme)
    yield app


@pytest.fixture
def state(config, app):
    """Create an AppState connected to the Qt event loop for GUI tests."""
    # Use a synchronous dispatch for test purposes
    app_instance = AppState(config, dispatch=lambda cb: cb())
    yield app_instance


@pytest.fixture
def main_window(app, state):
    """Create a MainWindow for testing."""
    win = MainWindow(app, state)
    win.show()
    yield win


@pytest.fixture
def settings_dialog(main_window, state):
    """Create a SettingsDialog for testing."""
    dialog = SettingsDialog(main_window, state)
    yield dialog


class TestMainWindowCreation:
    """Verify MainWindow initializes correctly."""

    def test_window_title(self, main_window):
        assert main_window.windowTitle() == "OR Usage Tracker"

    def test_window_size(self, main_window):
        assert main_window.width() == 420
        assert main_window.height() == 260

    def test_window_has_minimum_size(self, main_window):
        assert main_window.minimumWidth() >= 360
        assert main_window.minimumHeight() >= 260

    def test_tallest_view_fits_at_minimum_size(self, main_window):
        """No view may need more vertical space than the window allows."""
        main_window.resize(
            main_window.minimumWidth(), main_window.minimumHeight())
        for view in (
            main_window._loading_view,
            main_window._error_view,
            main_window._no_credentials_view,
            main_window._content,
        ):
            view.layout().activate()
            assert view.layout().sizeHint().height() <= main_window._stack.height(), (
                f"{type(view).__name__} needs "
                f"{view.layout().sizeHint().height()}px but stack is only "
                f"{main_window._stack.height()}px"
            )

    def test_stack_has_four_pages(self, main_window):
        """MainWindow should have loading, error, no_credentials, content views."""
        assert main_window._stack.count() == 4

    def test_initial_state_is_loading(self, main_window, state):
        """On startup with no listener notification yet, window shows loading."""
        assert main_window._stack.currentWidget() == main_window._loading_view


class TestContentDisplay:
    """Verify usage data renders correctly in the content view."""

    def _make_summary(self):
        return UsageSummary(
            date="2024-01-15",
            free_quota=FreeModelQuota(limit=1000, used=250, remaining=750, source="api"),
            daily_usage=DailyUsage(
                date="2024-01-15",
                free_requests=16,
                total_requests=16,
                spending=0.00,
            ),
            fetched_at=None,
        )

    def test_show_usage_updates_labels(self, main_window):
        summary = self._make_summary()
        main_window._show_usage(summary)
        assert main_window._quota_remaining_label.text() == "750 remaining"
        assert main_window._quota_used_label.text() == "(250 / 1,000)"

    def test_show_usage_progress_bar(self, main_window):
        summary = self._make_summary()
        main_window._show_usage(summary)
        expected_fraction = int((250 / 1000) * 100)
        assert main_window._progress_bar.value() == expected_fraction

    def test_show_usage_spending_label(self, main_window):
        from ortracker.usage import format_currency
        summary = self._make_summary()
        main_window._show_usage(summary)
        assert main_window._stat_spending_label.text() == f"Spending Today: {format_currency(0.00)}"

    def test_show_usage_over_limit(self, main_window):
        summary = UsageSummary(
            date="2024-01-15",
            free_quota=FreeModelQuota(limit=100, used=120, remaining=-20, source="api"),
            daily_usage=DailyUsage(
                date="2024-01-15",
                free_requests=120,
                total_requests=120,
                spending=0.00,
            ),
            fetched_at=None,
        )
        main_window._show_usage(summary)
        assert main_window._quota_remaining_label.text() == "+20 over limit"

    def test_time_format_12h(self, main_window, state):
        """12-hour clock should display AM/PM format."""
        state._config._data["use_24h_clock"] = False
        summary = self._make_summary()
        summary.fetched_at = None  # triggers datetime.now().astimezone()
        main_window._show_usage(summary)
        text = main_window._last_updated_label.text()
        assert "Updated " in text
        assert "AM" in text or "PM" in text  # 12h format includes AM/PM

    def test_time_format_24h(self, main_window, state):
        """24-hour clock should display HH:MM format."""
        state._config._data["use_24h_clock"] = True
        summary = self._make_summary()
        summary.fetched_at = None
        main_window._show_usage(summary)
        text = main_window._last_updated_label.text()
        assert "Updated " in text
        # 24h format has no AM/PM
        assert "AM" not in text and "PM" not in text


class TestSettingsDialog:
    """Verify SettingsDialog initializes and behaves correctly."""

    def test_dialog_title(self, settings_dialog):
        assert settings_dialog.windowTitle() == "Settings"

    def test_dialog_modal(self, settings_dialog):
        assert settings_dialog.isModal()

    def test_dialog_aligns_tops_with_parent(self, main_window, state):
        """Dialog top aligns with parent; centers align horizontally.

        Vertical centering is impossible here: the dialog is much taller
        than the main window, so centering it would push it off-screen
        and the clamp would park both windows against the same edge.
        """
        avail = QApplication.primaryScreen().availableGeometry()
        pframe = main_window.frameGeometry()
        main_window.move(
            avail.center().x() - pframe.width() // 2,
            avail.center().y() - pframe.height() // 2,
        )
        dialog = SettingsDialog(main_window, state)
        dialog.show()
        dialog._center_on_parent()
        parent_geo = main_window.frameGeometry()
        dialog_geo = dialog.frameGeometry()
        assert dialog_geo.top() == parent_geo.top()
        assert abs(dialog_geo.center().x() - parent_geo.center().x()) <= 1

    @pytest.mark.parametrize("corner", [
        "top_left", "top_right", "bottom_left", "bottom_right"])
    def test_dialog_stays_on_screen_near_edge(self, main_window, state, corner):
        """Clamping keeps the dialog inside the parent's screen.

        The expected screen is the one under the parent, which is what
        the implementation clamps against; using primaryScreen() here
        would mask a multi-monitor mismatch.
        """
        avail = QApplication.primaryScreen().availableGeometry()
        pframe = main_window.frameGeometry()

        if "left" in corner:
            x = avail.left()
        else:
            x = avail.right() - pframe.width()
        if "top" in corner:
            y = avail.top()
        else:
            y = avail.bottom() - pframe.height()
        main_window.move(x, y)

        parent_screen = QApplication.screenAt(main_window.frameGeometry().center())
        if parent_screen is None:
            pytest.skip("main window is not on any screen")

        dialog = SettingsDialog(main_window, state)
        dialog.show()
        dialog._center_on_parent()

        pavail = parent_screen.availableGeometry()
        frame = dialog.frameGeometry()
        assert frame.left() >= pavail.left()
        assert frame.top() >= pavail.top()
        assert frame.right() <= pavail.right()
        assert frame.bottom() <= pavail.bottom()

    def test_has_dark_switch(self, settings_dialog):
        assert hasattr(settings_dialog, "_dark_switch")
        assert isinstance(settings_dialog._dark_switch, type(settings_dialog._dark_switch))

    def test_has_clock_switch(self, settings_dialog):
        assert hasattr(settings_dialog, "_clock_switch")

    def test_has_interval_spin(self, settings_dialog):
        assert hasattr(settings_dialog, "_interval_spin")

    def test_interval_spin_default_10(self, settings_dialog):
        assert settings_dialog._interval_spin.value() == 10

    def test_interval_spin_range(self, settings_dialog):
        assert settings_dialog._interval_spin.minimum() == 1
        assert settings_dialog._interval_spin.maximum() == 120

    def test_has_key_entry(self, settings_dialog):
        assert hasattr(settings_dialog, "_key_entry")

    def test_dark_switch_state_matches_config(self, settings_dialog, state):
        settings_dialog._config._data["dark_theme"] = True
        settings_dialog._dark_switch.setChecked(True)
        assert settings_dialog._dark_switch.isChecked() is True

    def test_clock_switch_state_matches_config(self, settings_dialog, state):
        settings_dialog._config._data["use_24h_clock"] = True
        settings_dialog._clock_switch.setChecked(True)
        assert settings_dialog._clock_switch.isChecked() is True


class TestQuotaWarningColors:
    """Verify progress bar color changes based on quota usage level."""

    def _make_summary(self, used, limit):
        return UsageSummary(
            date="2024-01-15",
            free_quota=FreeModelQuota(limit=limit, used=used, remaining=limit - used),
            daily_usage=DailyUsage(
                date="2024-01-15",
                free_requests=used,
                total_requests=used,
                spending=0.00,
            ),
            fetched_at=None,
        )

    def test_progress_color_warning(self, main_window):
        """85% usage should be warning (yellow)."""
        summary = self._make_summary(used=850, limit=1000)
        main_window._show_usage(summary)
        stylesheet = main_window._progress_bar.styleSheet()
        assert "#e0a800" in stylesheet

    def test_progress_color_critical(self, main_window):
        """95% usage should be critical (red)."""
        summary = self._make_summary(used=950, limit=1000)
        main_window._show_usage(summary)
        stylesheet = main_window._progress_bar.styleSheet()
        assert "#e02a2a" in stylesheet

    def test_progress_color_normal(self, main_window):
        """50% usage should be normal (green)."""
        summary = self._make_summary(used=500, limit=1000)
        main_window._show_usage(summary)
        stylesheet = main_window._progress_bar.styleSheet()
        assert "#59b258" in stylesheet


class TestStateTransitions:
    """Verify MainWindow responds correctly to AppState changes."""

    def test_loading_state_shows_spinner(self, main_window, state):
        from ortracker.app_state import AppStatus
        state._set_status(AppStatus.LOADING, "Loading...")
        assert main_window._stack.currentWidget() == main_window._loading_view

    def test_no_credentials_state(self, main_window, state):
        from ortracker.app_state import AppStatus
        state._set_status(AppStatus.NO_CREDENTIALS, "No key")
        assert main_window._stack.currentWidget() == main_window._no_credentials_view


class TestDesktopFileName:
    """The desktop entry name doubles as the Wayland app id / window class."""

    def test_desktop_file_name_is_set(self):
        from ortracker.gui import _apply_desktop_file_name
        from PySide6.QtGui import QGuiApplication

        _apply_desktop_file_name()

        assert QGuiApplication.desktopFileName() == "or-usage-tracker"

    def test_desktop_file_name_has_no_extension(self):
        from ortracker.gui import DESKTOP_FILE_NAME

        assert DESKTOP_FILE_NAME == "or-usage-tracker"
        assert not DESKTOP_FILE_NAME.endswith(".desktop")


def _summary(used=250, limit=1000, spending=0.0):
    return UsageSummary(
        date="2024-01-15",
        free_quota=FreeModelQuota(limit=limit, used=used, remaining=limit - used),
        daily_usage=DailyUsage(
            date="2024-01-15",
            free_requests=used,
            total_requests=used,
            spending=spending,
        ),
        fetched_at=None,
    )


def _center_color(pixmap):
    image = pixmap.toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2)


class TestTrayIconAppearance:
    """The tray icon is a dot colored by quota state."""

    def test_pixmap_has_requested_size(self, app):
        from ortracker.gui import _tray_pixmap, TRAY_ICON_SIZE

        pixmap = _tray_pixmap("#59b258")
        assert not pixmap.isNull()
        # Logical size is what the host lays out; device pixels may be more.
        assert pixmap.width() / pixmap.devicePixelRatio() == TRAY_ICON_SIZE

    @pytest.mark.parametrize(
        "color",
        ["#59b258", "#e0a800", "#e02a2a", "#666666"],
    )
    def test_pixmap_uses_requested_color(self, app, color):
        from ortracker.gui import _tray_pixmap

        pixmap = _tray_pixmap(color)
        center = _center_color(pixmap)
        expected = QColor(color)
        assert abs(center.red() - expected.red()) <= 2
        assert abs(center.green() - expected.green()) <= 2
        assert abs(center.blue() - expected.blue()) <= 2

    def test_colors_differ_per_level(self, app):
        from ortracker.gui import (
            _tray_pixmap,
            TRAY_COLOR_CRITICAL,
            TRAY_COLOR_IDLE,
            TRAY_COLOR_NORMAL,
            TRAY_COLOR_WARNING,
        )

        colors = {
            _center_color(_tray_pixmap(c)).name()
            for c in (
                TRAY_COLOR_NORMAL,
                TRAY_COLOR_WARNING,
                TRAY_COLOR_CRITICAL,
                TRAY_COLOR_IDLE,
            )
        }
        assert len(colors) == 4


class TestTrayPresentation:
    """Mapping of app state to tray color and tooltip."""

    def test_normal_usage_is_green(self, app):
        from ortracker.app_state import AppStatus
        from ortracker.gui import _tray_presentation, TRAY_COLOR_NORMAL

        color, tooltip = _tray_presentation(AppStatus.READY, _summary(used=250))
        assert color == TRAY_COLOR_NORMAL
        assert "750 free requests remaining" in tooltip
        assert "(250 / 1,000)" in tooltip

    def test_warning_usage_is_amber(self, app):
        from ortracker.app_state import AppStatus
        from ortracker.gui import _tray_presentation, TRAY_COLOR_WARNING

        color, _ = _tray_presentation(AppStatus.READY, _summary(used=850))
        assert color == TRAY_COLOR_WARNING

    def test_critical_usage_is_red(self, app):
        from ortracker.app_state import AppStatus
        from ortracker.gui import _tray_presentation, TRAY_COLOR_CRITICAL

        color, _ = _tray_presentation(AppStatus.READY, _summary(used=950))
        assert color == TRAY_COLOR_CRITICAL

    def test_exhausted_quota_is_red(self, app):
        from ortracker.app_state import AppStatus
        from ortracker.gui import _tray_presentation, TRAY_COLOR_CRITICAL

        color, _ = _tray_presentation(AppStatus.READY, _summary(used=1000))
        assert color == TRAY_COLOR_CRITICAL

    def test_no_credentials_is_idle(self, app):
        from ortracker.app_state import AppStatus
        from ortracker.gui import _tray_presentation, TRAY_COLOR_IDLE

        color, tooltip = _tray_presentation(AppStatus.NO_CREDENTIALS, None)
        assert color == TRAY_COLOR_IDLE
        assert "no API key" in tooltip

    def test_error_is_critical(self, app):
        from ortracker.app_state import AppStatus
        from ortracker.gui import _tray_presentation, TRAY_COLOR_CRITICAL

        color, tooltip = _tray_presentation(AppStatus.ERROR, None)
        assert color == TRAY_COLOR_CRITICAL
        assert "error" in tooltip.lower()

    def test_spending_included_in_tooltip(self, app):
        from ortracker.app_state import AppStatus
        from ortracker.gui import _tray_tooltip

        tooltip = _tray_tooltip(AppStatus.READY, _summary(spending=1.25))
        assert "$1.25 today" in tooltip


class TestTrayMenu:
    """Tray menu contents and callbacks."""

    @pytest.fixture
    def callbacks(self):
        return {
            "on_show": Mock(),
            "on_refresh": Mock(),
            "on_settings": Mock(),
            "on_quit": Mock(),
        }

    @pytest.fixture
    def tray(self, app, callbacks):
        from ortracker.gui import TrayIcon

        icon = TrayIcon(**callbacks)
        yield icon
        icon.hide()

    def test_menu_entries(self, tray):
        assert tray.menu_actions == [
            "Show Dashboard",
            "Refresh Now",
            "Settings…",
            "Quit",
        ]

    def test_show_action(self, tray, callbacks):
        assert tray.trigger("Show Dashboard") is True
        callbacks["on_show"].assert_called_once()

    def test_refresh_action(self, tray, callbacks):
        assert tray.trigger("Refresh Now") is True
        callbacks["on_refresh"].assert_called_once()

    def test_settings_action(self, tray, callbacks):
        assert tray.trigger("Settings…") is True
        callbacks["on_settings"].assert_called_once()

    def test_quit_action(self, tray, callbacks):
        assert tray.trigger("Quit") is True
        callbacks["on_quit"].assert_called_once()

    def test_unknown_action_returns_false(self, tray):
        assert tray.trigger("Nonexistent") is False

    def test_click_shows_window(self, tray, callbacks):
        from PySide6.QtWidgets import QSystemTrayIcon

        tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
        callbacks["on_show"].assert_called_once()

    def test_context_click_does_not_show(self, tray, callbacks):
        from PySide6.QtWidgets import QSystemTrayIcon

        tray._on_activated(QSystemTrayIcon.ActivationReason.Context)
        callbacks["on_show"].assert_not_called()

    def test_set_status_sets_icon_and_tooltip(self, tray):
        from ortracker.gui import TRAY_COLOR_WARNING

        tray.set_status(TRAY_COLOR_WARNING, "hello")
        assert tray.toolTip() == "hello"
        assert not tray.icon().isNull()


class TestCloseToTray:
    """Closing the window hides it when a tray icon exists."""

    def test_close_hides_when_tray_available(self, app, state):
        app.hide_on_close = True
        try:
            win = MainWindow(app, state)
            win.show()
            win.close()
            assert not win.isVisible()
        finally:
            app.hide_on_close = False

    def test_close_closes_when_no_tray(self, app, state):
        win = MainWindow(app, state)
        win.show()
        win.close()
        assert not win.isVisible()

    def test_ctrl_q_uses_app_quit_app(self, app, state, monkeypatch):
        win = MainWindow(app, state)
        calls = []
        monkeypatch.setattr(app, "quit_app", lambda: calls.append(True), raising=False)
        win._on_quit_requested()
        assert calls == [True]

    def test_ctrl_q_falls_back_to_quit(self, app, state):
        win = MainWindow(app, state)
        assert getattr(app, "quit_app", None) is None
        win._on_quit_requested()


class TestLaunchVisibility:
    """Window visibility at startup depends on tray availability."""

    def test_tray_and_start_hidden_hides_window(self):
        from ortracker.gui import _should_show_window_on_launch

        assert _should_show_window_on_launch(True, True) is False

    def test_tray_but_default_setting_shows_window(self):
        from ortracker.gui import _should_show_window_on_launch

        assert _should_show_window_on_launch(True, False) is True

    def test_no_tray_always_shows_window(self):
        from ortracker.gui import _should_show_window_on_launch

        assert _should_show_window_on_launch(False, True) is True
        assert _should_show_window_on_launch(False, False) is True
