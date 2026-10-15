"""Application state management with background refresh.

This module ties together the API client, credential storage, and usage
processing. It runs the API calls in a background thread and notifies
registered callbacks when the state changes, so the GUI can update without
blocking.

The module is GUI-agnostic: it accepts an optional ``dispatch`` callable
that is responsible for scheduling listener callbacks on the GUI main
thread.  This keeps ``AppState`` testable and reusable behind any toolkit
(GTK, Qt, asyncio, etc.).
"""

import threading
from datetime import datetime
from enum import Enum
from typing import Callable, List, Optional

from .api_client import (
    OpenRouterClient,
    OpenRouterError,
    AuthenticationError,
    PermissionError,
    RateLimitError,
    NetworkError,
)
from .config import Config
from .credentials import retrieve_api_key, store_api_key, clear_api_key, has_api_key
from .usage import (
    parse_keys_response,
    parse_activity_response,
    parse_credits_response,
    build_usage_summary,
    utc_date_string,
)
from .models import UsageSummary


def _default_dispatch(callback: Callable) -> None:
    """Default dispatch: call synchronously (used in tests / headless mode)."""
    callback()


class AppStatus(Enum):
    """Application loading/ready/error states."""
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    NO_CREDENTIALS = "no_credentials"


class AppState:
    """Manages application state and background refresh.

    Holds the current :class:`UsageSummary` (or error), coordinates API
    calls in a background thread, and notifies listeners via callbacks.

    Parameters
    ----------
    config
        Application configuration object.
    dispatch
        Callable that schedules *callback* to run on the GUI main thread.
        If omitted, callbacks are invoked synchronously (suitable for
        tests or environments without a GUI event loop).
    """

    def __init__(
        self,
        config: Config,
        dispatch: Optional[Callable[[Callable], None]] = None,
    ):
        self._config = config
        self._dispatch = dispatch or _default_dispatch
        self._summary: Optional[UsageSummary] = None
        self._status: AppStatus = AppStatus.NO_CREDENTIALS
        self._status_message: str = ""
        self._last_error: Optional[str] = None
        self._listeners: List[Callable] = []
        self._refresh_timer: Optional[threading.Timer] = None
        self._refresh_lock = threading.Lock()
        self._refreshing = False
        self._timer_generation = 0

    # --- Properties ---

    @property
    def status(self) -> AppStatus:
        return self._status

    @property
    def status_message(self) -> str:
        return self._status_message

    @property
    def summary(self) -> Optional[UsageSummary]:
        return self._summary

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def has_credentials(self) -> bool:
        return has_api_key()

    @property
    def config(self) -> Config:
        return self._config

    # --- Listener management ---

    def add_listener(self, callback: Callable):
        """Register a callback to be called when state changes."""
        self._listeners.append(callback)

    def _notify_listeners(self):
        """Notify all listeners that the state has changed (via dispatch)."""
        for cb in list(self._listeners):
            self._dispatch(cb)

    # --- Credential management ---

    def save_api_key(self, api_key: str) -> bool:
        """Store the API key securely."""
        try:
            store_api_key(api_key)
            self._set_status(AppStatus.LOADING, "Fetching usage data...")
            self.refresh()
            return True
        except Exception as e:
            self._set_status(AppStatus.ERROR, f"Failed to save API key: {e}")
            return False

    def remove_api_key(self) -> None:
        """Remove the stored API key."""
        clear_api_key()
        self._summary = None
        self._set_status(AppStatus.NO_CREDENTIALS, "No API key configured")
        self._last_error = None

    # --- Refresh ---

    def refresh(self):
        """Trigger a background refresh of usage data."""
        if not self.has_credentials:
            self._set_status(AppStatus.NO_CREDENTIALS, "No API key configured")
            self._notify_listeners()
            return

        if self._refreshing:
            return  # Already refreshing

        self._refreshing = True
        self._set_status(AppStatus.LOADING, "Fetching usage data...")
        self._notify_listeners()

        thread = threading.Thread(target=self._do_refresh, daemon=True)
        thread.start()

    def _do_refresh(self):
        """Background worker that fetches data from the API."""
        try:
            self._fetch_usage()
        finally:
            self._refreshing = False

    def _fetch_usage(self):
        api_key = retrieve_api_key()
        if not api_key:
            self._dispatch(lambda: self._set_status(
                AppStatus.NO_CREDENTIALS, "No API key configured"))
            return

        try:
            client = OpenRouterClient(api_key, self._config.api_base_url)

            utc_today = utc_date_string()

            # Fetch keys (for free model daily requests)
            # Try /keys (Management key), fall back to /key (any API key)
            keys = []
            try:
                keys_response = client.list_keys()
                keys = parse_keys_response(keys_response)
            except (PermissionError, AuthenticationError):
                # /keys failed (needs Management key, or 401 for regular key)
                # Fall back to /key which works with any valid API key
                try:
                    key_response = client.get_current_key()
                    key_data = key_response.get("data", {})
                    if isinstance(key_data, dict):
                        # Wrap in list format to match /keys response structure
                        keys = parse_keys_response({"data": [key_data]})
                except (AuthenticationError, PermissionError) as e:
                    # /key also failed — key is truly invalid
                    self._dispatch(lambda: self._handle_auth_error(e))
                    return
                except NetworkError as e:
                    self._dispatch(lambda: self._handle_network_error(e))
                    return
            except NetworkError as e:
                self._dispatch(lambda: self._handle_network_error(e))
                return

            # Fetch activity for today
            activity_items = []
            try:
                activity_response = client.get_activity(utc_today)
                activity_items = parse_activity_response(activity_response)
            except RateLimitError as e:
                self._dispatch(lambda: self._handle_rate_limit_error(e))
                return
            except OpenRouterError:
                # Activity endpoint may require Management key, have no data for today,
                # or other non-fatal API error; key data alone is sufficient
                activity_items = []
            except NetworkError as e:
                self._dispatch(lambda: self._handle_network_error(e))
                return

            # Fetch credits (best effort)
            credits_data = None
            try:
                credits_response = client.get_credits()
                credits_data = parse_credits_response(credits_response)
            except Exception:
                # Credits failure is non-fatal
                pass

            summary = build_usage_summary(
                keys=keys,
                activity_items=activity_items,
                credits_data=credits_data,
                configured_free_limit=self._config.free_model_daily_limit,
                date_str=utc_today,
                fetched_at=datetime.now().astimezone(),
            )

            self._dispatch(lambda: self._set_ready(summary))

        except Exception as e:
            error_msg = str(e)
            # Don't expose the API key in error messages
            if api_key and api_key in error_msg:
                error_msg = error_msg.replace(api_key, "[REDACTED]")
            self._dispatch(lambda: self._set_error(self._redact(error_msg)))

    def _set_ready(self, summary: UsageSummary):
        with self._refresh_lock:
            self._summary = summary
            self._status = AppStatus.READY
            self._status_message = ""
            self._last_error = None
        self._notify_listeners()

    def _set_error(self, message: str):
        with self._refresh_lock:
            self._status = AppStatus.ERROR
            self._status_message = message
            self._last_error = message
        self._notify_listeners()

    def _set_status(self, status: AppStatus, message: str):
        with self._refresh_lock:
            self._status = status
            self._status_message = message
        self._notify_listeners()

    def _redact(self, message: str) -> str:
        """Remove the API key from a message before it reaches the GUI.

        Server error bodies are echoed into user-visible text, so a server
        that reflects the key would otherwise render it on screen.
        """
        try:
            key = retrieve_api_key()
        except Exception:
            key = None
        if not key:
            return message
        return message.replace(key, "[REDACTED]")

    def _handle_auth_error(self, error: OpenRouterError):
        """Handle authentication errors - the key may be invalid."""
        message = str(error)
        # Check for expired key message
        if "expired" in message.lower():
            msg = "API key has expired. Please update your credentials in Settings."
        elif "invalid" in message.lower():
            msg = "API key is invalid. Please check your credentials in Settings."
        else:
            msg = f"Authentication error: {message}"
        self._set_error(self._redact(msg))

    def _handle_network_error(self, error: NetworkError):
        """Handle network errors - keep showing previous data if available."""
        if self._summary is not None:
            # Keep showing last known data but mark as stale
            msg = f"Network error: {error}. Showing last known data. Auto-refresh will retry."
            msg = self._redact(msg)
            with self._refresh_lock:
                self._status = AppStatus.ERROR
                self._status_message = msg
                self._last_error = msg
            self._notify_listeners()
        else:
            self._set_error(self._redact(str(error)))

    def _handle_rate_limit_error(self, error: RateLimitError):
        """Handle rate limit errors."""
        rl = error.rate_limit
        if rl.retry_after:
            msg = f"Rate limited. Retrying in {rl.retry_after} seconds."
        else:
            msg = "Rate limited by OpenRouter. Will retry on next refresh."
        self._set_error(msg)

    # --- Scheduled refresh ---

    def start_auto_refresh(self):
        """Start periodic background refresh based on config interval."""
        self._stop_auto_refresh()
        self.refresh()
        self._schedule_timer()

    def _schedule_timer(self):
        """Schedule the next refresh tick using a simple threading.Timer."""
        interval_ms = self._config.refresh_interval_ms
        generation = self._timer_generation
        self._refresh_timer = threading.Timer(
            interval_ms / 1000.0,
            self._on_timer_tick,
            args=(generation,),
        )
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _on_timer_tick(self, generation: int):
        """Timer callback - triggers a refresh and reschedules itself.

        Only reschedules if this tick still belongs to the current timer
        chain. Without the generation check a tick that had already fired
        would reschedule itself alongside the chain that replaced it,
        producing duplicate chains and a refresh rate that drifts upward.
        """
        if generation != self._timer_generation:
            return
        self.refresh()
        if generation == self._timer_generation:
            self._schedule_timer()

    def _stop_auto_refresh(self):
        """Stop the periodic refresh timer."""
        self._timer_generation += 1
        if self._refresh_timer is not None:
            self._refresh_timer.cancel()
            self._refresh_timer = None

    def update_config_and_restart(self):
        """Called when config changes - saves config and restarts auto-refresh."""
        self._config.save()
        self.start_auto_refresh()

    def shutdown(self):
        """Clean up resources."""
        self._stop_auto_refresh()
