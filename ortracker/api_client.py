"""OpenRouter API client.

Handles all HTTP communication with the OpenRouter API.
Most endpoints require a Management API key; ``/key`` works with any valid
API key and is used as the fallback when ``/keys`` is refused.
"""

import json
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse

from .models import RateLimitInfo
from .usage import parse_rate_limit_headers

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
ALLOWED_HOSTS = ("openrouter.ai",)
REQUEST_TIMEOUT = 30  # seconds
MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB safety limit for response bodies
STREAM_CHUNK_BYTES = 64 * 1024  # read granularity when enforcing the limit
USER_AGENT = "ortracker/1.0 (desktop-monitor)"


def validate_base_url(url: str) -> str:
    """Return a sanitized HTTPS OpenRouter base URL.

    Falls back to :data:`DEFAULT_BASE_URL` for anything that is not HTTPS
    on an allowed host. This keeps the ``Authorization`` header from being
    sent to an attacker-controlled endpoint or in cleartext.
    """
    if not isinstance(url, str) or not url:
        return DEFAULT_BASE_URL
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return DEFAULT_BASE_URL
    if parsed.scheme != "https":
        return DEFAULT_BASE_URL
    if (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        return DEFAULT_BASE_URL
    return url.strip().rstrip("/")


class OpenRouterError(Exception):
    """Base exception for OpenRouter API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 rate_limit: Optional[RateLimitInfo] = None):
        super().__init__(message)
        self.status_code = status_code
        self.rate_limit = rate_limit


class AuthenticationError(OpenRouterError):
    """Raised when the API key is invalid, missing, or expired."""


class PermissionError(OpenRouterError):
    """Raised when the API key lacks permissions for the requested operation."""


class RateLimitError(OpenRouterError):
    """Raised when rate limited by the API."""

    def __init__(self, message: str, rate_limit: RateLimitInfo):
        super().__init__(message, status_code=429, rate_limit=rate_limit)


class NetworkError(OpenRouterError):
    """Raised when the network is unavailable or the server is unreachable."""


class OpenRouterClient:
    """Client for the OpenRouter REST API (management endpoints).

    Uses ``requests``. The stdlib ``urllib`` fallback was removed because
    its default redirect handler forwards the ``Authorization`` header to a
    different host on a 3xx, which would leak the API key.
    """

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        api_key = api_key.strip() if api_key else ""
        if not api_key:
            raise ValueError("API key must not be empty")
        self._api_key = api_key
        # Validate the host rather than trusting the config file: the
        # Authorization header is attached to whatever base URL is used, so
        # an unvalidated value would send the key to an arbitrary host (and
        # ``http://`` would put it on the wire in cleartext).
        self._base_url = validate_base_url(base_url)

    def _build_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": "https://openrouter.ai",
            "X-Title": "OR Usage Tracker",
        }
        if extra:
            headers.update(extra)
        return headers

    def _handle_error(self, resp, status_code: int, body: str) -> None:
        """Parse error response and raise the appropriate exception."""
        rate_limit = None
        if hasattr(resp, "headers"):
            rate_limit = parse_rate_limit_headers(dict(resp.headers) if hasattr(resp, 'headers') and resp.headers else {})

        # Try to parse error message from response body
        message = body or "Unknown error"
        try:
            err = json.loads(body)
            if isinstance(err, dict) and "error" in err:
                error_obj = err["error"]
                if isinstance(error_obj, dict):
                    message = error_obj.get("message", str(error_obj))
                else:
                    message = str(error_obj)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        if status_code == 401:
            raise AuthenticationError(
                f"Authentication failed (401): {message}",
                status_code=401,
                rate_limit=rate_limit,
            )
        elif status_code == 403:
            raise PermissionError(
                f"Permission denied (403): {message}",
                status_code=403,
                rate_limit=rate_limit,
            )
        elif status_code == 429:
            raise RateLimitError(f"Rate limited: {message}", rate_limit or RateLimitInfo())
        elif status_code >= 500:
            raise OpenRouterError(f"Server error ({status_code}): {message}", status_code=status_code)
        else:
            raise OpenRouterError(f"API error ({status_code}): {message}", status_code=status_code)

    def _make_request(self, method: str, path: str,
                      params: Optional[Dict[str, str]] = None,
                      body: Optional[Dict[str, Any]] = None,
                      ) -> Dict[str, Any]:
        """Execute an HTTP request and return the parsed JSON response."""
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        return self._make_request_requests(method, url, body)

    def _read_capped(self, resp) -> bytes:
        """Read a streamed response body, aborting past MAX_RESPONSE_BYTES.

        ``requests`` buffers the whole body before returning unless
        ``stream=True``, so the size has to be enforced while reading
        rather than after. A chunked response carries no ``content-length``
        and would otherwise be unbounded.
        """
        declared = resp.headers.get("content-length")
        if declared:
            try:
                if int(declared) > MAX_RESPONSE_BYTES:
                    raise NetworkError("Response too large from server.")
            except (ValueError, TypeError):
                pass

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=STREAM_CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise NetworkError("Response too large from server.")
            chunks.append(chunk)
        return b"".join(chunks)

    def _make_request_requests(self, method: str, url: str,
                               body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        import requests
        try:
            kwargs = {
                "headers": self._build_headers(),
                "timeout": REQUEST_TIMEOUT,
                "stream": True,
            }
            if body is not None:
                kwargs["json"] = body
            resp = requests.request(method, url, **kwargs)
            try:
                raw = self._read_capped(resp)

                if resp.status_code != 200:
                    self._handle_error(resp, resp.status_code, raw[:2000].decode("utf-8", "replace"))

                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    raise OpenRouterError(
                        f"Invalid JSON response from server: {raw[:200].decode('utf-8', 'replace')}"
                    )
            finally:
                resp.close()
        except requests.exceptions.ConnectionError:
            raise NetworkError("Network connection failed. Check your internet connection.")
        except requests.exceptions.Timeout:
            raise NetworkError("Request timed out.")
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Request failed: {e}")

    # --- Public API methods ---

    def list_keys(self) -> Dict[str, Any]:
        """List all API keys (``GET /api/v1/keys``).

        Requires a Management API key.
        """
        return self._make_request("GET", "/keys")

    def get_activity(self, date: str) -> Dict[str, Any]:
        """Get user activity for a specific UTC date (``GET /api/v1/activity``).

        Parameters
        ----------
        date
            UTC date in YYYY-MM-DD format.
        """
        return self._make_request("GET", "/activity", params={"date": date})

    def get_credits(self) -> Dict[str, Any]:
        """Get remaining account credits (``GET /api/v1/credits``).

        Requires a Management API key.
        """
        return self._make_request("GET", "/credits")

    def get_current_key(self) -> Dict[str, Any]:
        """Get the current API key info (``GET /key``).

        Works with any API key (regular or management).
        """
        return self._make_request("GET", "/key")
