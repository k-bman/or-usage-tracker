"""Tests for AppState refresh guards and Config validation.

These cover the robustness fixes: the concurrent-refresh guard, the timer
generation check, and the per-key validation applied when loading config.
"""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ortracker.app_state import AppState
from ortracker.config import Config


def make_state(tmpdir):
    config = Config(path=Path(tmpdir) / "config.json")
    return AppState(config, dispatch=lambda cb: cb())


class TestRefreshGuard(unittest.TestCase):
    """A second refresh() while one is in flight must not spawn a thread."""

    def test_concurrent_refresh_spawns_one_thread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = make_state(tmpdir)
            started = []
            release = threading.Event()

            def slow_worker(_self):
                started.append(1)
                release.wait(2)

            with patch.object(AppState, "_fetch_usage", slow_worker), \
                    patch.object(AppState, "has_credentials", True), \
                    patch("ortracker.app_state.retrieve_api_key", return_value="k"):
                state.refresh()
                # let the first thread reach _fetch_usage
                for _ in range(100):
                    if started:
                        break
                    threading.Event().wait(0.01)

                # hammer refresh while the first is still running
                for _ in range(20):
                    state.refresh()

                self.assertEqual(len(started), 1)
                release.set()

    def test_refreshing_flag_cleared_after_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = make_state(tmpdir)
            with patch.object(AppState, "_fetch_usage", lambda self: None), \
                    patch.object(AppState, "has_credentials", True), \
                    patch("ortracker.app_state.retrieve_api_key", return_value="k"):
                state.refresh()
                for _ in range(200):
                    if not state._refreshing:
                        break
                    threading.Event().wait(0.01)
                self.assertFalse(state._refreshing)


class TestTimerGeneration(unittest.TestCase):
    """Restarting auto-refresh must not leave duplicate timer chains."""

    def test_restart_does_not_duplicate_timers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = make_state(tmpdir)
            state._config.refresh_interval_ms = 50

            with patch.object(type(state), "refresh"), \
                    patch.object(type(state), "has_credentials", True):
                state.start_auto_refresh()
                first_gen = state._timer_generation
                first_timer = state._refresh_timer
                state.start_auto_refresh()
                second_timer = state._refresh_timer

                self.assertGreater(state._timer_generation, first_gen)
                self.assertIsNot(first_timer, second_timer)

                # a stale tick from the previous generation must not reschedule
                state._on_timer_tick(first_gen)
                self.assertEqual(state._refresh_timer, second_timer)

                # the current generation's tick does reschedule
                current = state._timer_generation
                state._on_timer_tick(current)
                self.assertNotEqual(state._refresh_timer, second_timer)

            state._stop_auto_refresh()


class TestConfigValidation(unittest.TestCase):
    """Malformed config values must not propagate into runtime code."""

    def _write(self, payload):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "config.json"
        path.write_text(json.dumps(payload))
        return Config(path=path).load()

    def test_string_interval_rejected(self):
        cfg = self._write({"refresh_interval_ms": "abc"})
        self.assertEqual(cfg.refresh_interval_ms, 600000)

    def test_interval_clamped(self):
        cfg = self._write({"refresh_interval_ms": 10})
        self.assertEqual(cfg.refresh_interval_ms, 1000)
        cfg = self._write({"refresh_interval_ms": 10 ** 12})
        self.assertEqual(cfg.refresh_interval_ms, 24 * 60 * 60 * 1000)

    def test_bad_types_fall_back(self):
        cfg = self._write({
            "api_base_url": 123,
            "dark_theme": "yes",
            "use_24h_clock": 1,
            "start_hidden": "yes",
            "free_model_daily_limit": "x",
        })
        self.assertEqual(cfg.api_base_url, "https://openrouter.ai/api/v1")
        self.assertIs(cfg.dark_theme, True)
        self.assertIs(cfg.use_24h_clock, False)
        self.assertIs(cfg.start_hidden, False)
        self.assertEqual(cfg.free_model_daily_limit, 1000)

    def test_start_hidden_defaults_off(self):
        cfg = self._write({})
        self.assertIs(cfg.start_hidden, False)

    def test_start_hidden_round_trips(self):
        cfg = self._write({"start_hidden": True})
        self.assertIs(cfg.start_hidden, True)

        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "config.json"
        cfg = Config(path=path)
        cfg.start_hidden = True
        cfg.save()
        self.assertIs(Config(path=path).load().start_hidden, True)

        cfg.start_hidden = False
        cfg.save()
        self.assertIs(Config(path=path).load().start_hidden, False)

    def test_valid_values_are_kept(self):
        cfg = self._write({
            "refresh_interval_ms": 300000,
            "dark_theme": False,
            "use_24h_clock": True,
            "free_model_daily_limit": 50,
        })
        self.assertEqual(cfg.refresh_interval_ms, 300000)
        self.assertIs(cfg.dark_theme, False)
        self.assertIs(cfg.use_24h_clock, True)
        self.assertEqual(cfg.free_model_daily_limit, 50)

    def test_non_dict_root_ignored(self):
        cfg = self._write(["a", "b"])
        self.assertEqual(cfg.refresh_interval_ms, 600000)
        self.assertEqual(cfg.dark_theme, True)

    def test_invalid_json_ignored(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "config.json"
        path.write_text("{not json")
        cfg = Config(path=path).load()
        self.assertEqual(cfg.refresh_interval_ms, 600000)

    def test_missing_file_ok(self):
        cfg = Config(path=Path(tempfile.mkdtemp()) / "nope.json").load()
        self.assertEqual(cfg.refresh_interval_ms, 600000)


class TestErrorRedaction(unittest.TestCase):
    """A server that echoes the key must not put it in user-visible text."""

    SECRET = "sk-or-v1-TOPSECRET"

    def _state(self, tmpdir):
        return make_state(tmpdir)

    def test_auth_error_redacts_key(self):
        from ortracker.api_client import AuthenticationError
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._state(tmpdir)
            with patch("ortracker.app_state.retrieve_api_key", return_value=self.SECRET):
                state._handle_auth_error(
                    AuthenticationError(f"Authentication failed (401): {self.SECRET}"))
            self.assertNotIn(self.SECRET, state.status_message)
            self.assertIn("[REDACTED]", state.status_message)

    def test_network_error_redacts_key(self):
        from ortracker.api_client import NetworkError
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._state(tmpdir)
            with patch("ortracker.app_state.retrieve_api_key", return_value=self.SECRET):
                state._handle_network_error(NetworkError(f"failed: {self.SECRET}"))
            self.assertNotIn(self.SECRET, state.status_message)

    def test_network_error_redacts_key_with_stale_summary(self):
        """The stale-data branch sets status directly; it must redact too."""
        from ortracker.api_client import NetworkError
        from ortracker.models import DailyUsage, FreeModelQuota, UsageSummary
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._state(tmpdir)
            state._summary = UsageSummary(
                date="2024-01-01",
                free_quota=FreeModelQuota(limit=10, used=1, remaining=9),
                daily_usage=DailyUsage(
                    date="2024-01-01", free_requests=0, total_requests=0),
            )
            with patch("ortracker.app_state.retrieve_api_key", return_value=self.SECRET):
                state._handle_network_error(NetworkError(f"failed: {self.SECRET}"))
            self.assertNotIn(self.SECRET, state.status_message)
            self.assertIn("[REDACTED]", state.status_message)

    def test_redact_is_noop_without_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._state(tmpdir)
            with patch("ortracker.app_state.retrieve_api_key", return_value=None):
                self.assertEqual(state._redact("nothing secret"), "nothing secret")

    def test_redact_survives_keyring_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._state(tmpdir)
            with patch("ortracker.app_state.retrieve_api_key", side_effect=RuntimeError):
                self.assertEqual(state._redact("boom"), "boom")


class TestConfigSavePermissions(unittest.TestCase):
    """save() must write 0600 and must not follow a symlink."""

    def test_permissions_are_0600(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "config.json"
        Config(path=path).save()
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_existing_loose_permissions_are_tightened(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "config.json"
        path.write_text("{}")
        os.chmod(path, 0o666)
        Config(path=path).save()
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_does_not_follow_symlink(self):
        tmpdir = tempfile.mkdtemp()
        target = Path(tmpdir) / "target.json"
        target.write_text("{}")
        link = Path(tmpdir) / "config.json"
        link.symlink_to(target)
        Config(path=link).save()
        # the symlink is replaced, the target is left alone
        self.assertFalse(link.is_symlink())
        self.assertEqual(target.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()