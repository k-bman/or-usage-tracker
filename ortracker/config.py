"""Configuration management for the OpenRouter usage monitor."""

import contextlib
import json
import os
import tempfile
from pathlib import Path

from .models import UsageSummary

DEFAULT_FREE_LIMIT = 1000
DEFAULT_REFRESH_INTERVAL_MS = 600000  # 10 minutes
MIN_REFRESH_INTERVAL_MS = 1000
MAX_REFRESH_INTERVAL_MS = 24 * 60 * 60 * 1000
DEFAULT_API_BASE_URL = "https://openrouter.ai/api/v1"
APP_NAME = "ortracker"
ORG_NAME = "or-usage-tracker"
DEFAULT_START_HIDDEN = False

CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    return None


def _coerce_int(value, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ivalue = int(value)
    if minimum is not None:
        ivalue = max(minimum, ivalue)
    if maximum is not None:
        ivalue = min(maximum, ivalue)
    return ivalue


def _coerce_str(value):
    return value if isinstance(value, str) and value else None


class Config:
    """Manages application configuration persisted as JSON in the XDG config dir."""

    FREE_LIMIT_KEY = "free_model_daily_limit"
    REFRESH_INTERVAL_KEY = "refresh_interval_ms"
    API_BASE_URL_KEY = "api_base_url"
    DARK_THEME_KEY = "dark_theme"
    USE_24H_KEY = "use_24h_clock"
    START_HIDDEN_KEY = "start_hidden"

    _VALIDATORS = {
        FREE_LIMIT_KEY: lambda v: _coerce_int(v, minimum=0),
        REFRESH_INTERVAL_KEY: lambda v: _coerce_int(
            v, minimum=MIN_REFRESH_INTERVAL_MS, maximum=MAX_REFRESH_INTERVAL_MS),
        API_BASE_URL_KEY: _coerce_str,
        DARK_THEME_KEY: _coerce_bool,
        USE_24H_KEY: _coerce_bool,
        START_HIDDEN_KEY: _coerce_bool,
    }

    def __init__(self, path: Path = CONFIG_FILE):
        self._path = path
        self._data = self._load_defaults()

    def _load_defaults(self):
        return {
            self.FREE_LIMIT_KEY: DEFAULT_FREE_LIMIT,
            self.REFRESH_INTERVAL_KEY: DEFAULT_REFRESH_INTERVAL_MS,
            self.API_BASE_URL_KEY: DEFAULT_API_BASE_URL,
            self.DARK_THEME_KEY: True,
            self.USE_24H_KEY: False,
            self.START_HIDDEN_KEY: DEFAULT_START_HIDDEN,
        }

    def load(self):
        """Load configuration from disk, falling back to defaults.

        Values are validated per key: a corrupt or hand-edited file must not
        be able to propagate a wrong type into the refresh timer or the API
        client, where it would raise on every use.
        """
        if not self._path.exists():
            return self
        try:
            with open(self._path, "r") as f:
                stored = json.load(f)
        except (json.JSONDecodeError, OSError):
            return self
        if not isinstance(stored, dict):
            return self
        for key, coerce in self._VALIDATORS.items():
            if key in stored:
                cleaned = coerce(stored[key])
                if cleaned is not None:
                    self._data[key] = cleaned
        return self

    def save(self):
        """Persist configuration to disk with restrictive permissions.

        Writes to a temp file and renames into place so a symlinked target is
        not followed, then chmods explicitly because the mode passed to
        ``os.open`` only applies when creating a new file.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".config-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @property
    def free_model_daily_limit(self) -> int:
        return self._data[self.FREE_LIMIT_KEY]

    @free_model_daily_limit.setter
    def free_model_daily_limit(self, value: int):
        self._data[self.FREE_LIMIT_KEY] = value

    @property
    def refresh_interval_ms(self) -> int:
        return self._data[self.REFRESH_INTERVAL_KEY]

    @refresh_interval_ms.setter
    def refresh_interval_ms(self, value: int):
        self._data[self.REFRESH_INTERVAL_KEY] = value

    @property
    def api_base_url(self) -> str:
        return self._data[self.API_BASE_URL_KEY]

    @api_base_url.setter
    def api_base_url(self, value: str):
        self._data[self.API_BASE_URL_KEY] = value

    @property
    def dark_theme(self) -> bool:
        return self._data[self.DARK_THEME_KEY]

    @dark_theme.setter
    def dark_theme(self, value: bool):
        self._data[self.DARK_THEME_KEY] = value

    @property
    def use_24h_clock(self) -> bool:
        return self._data[self.USE_24H_KEY]

    @use_24h_clock.setter
    def use_24h_clock(self, value: bool):
        self._data[self.USE_24H_KEY] = value

    @property
    def start_hidden(self) -> bool:
        """Whether to launch into the system tray instead of showing the window."""
        return self._data[self.START_HIDDEN_KEY]

    @start_hidden.setter
    def start_hidden(self, value: bool):
        self._data[self.START_HIDDEN_KEY] = value


def load_config() -> Config:
    return Config().load()
