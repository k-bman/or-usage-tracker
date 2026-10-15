"""Unit tests for the API client error handling and response parsing.

These tests mock the HTTP layer and do NOT make real network calls.
They verify that HTTP responses are correctly translated into the
appropriate exception types and parsed results.
"""

import json
import os
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ortracker.api_client import (
    OpenRouterClient,
    OpenRouterError,
    AuthenticationError,
    PermissionError,
    RateLimitError,
    NetworkError,
    MAX_RESPONSE_BYTES,
)
from ortracker.usage import parse_rate_limit_headers
from ortracker.models import RateLimitInfo
from ortracker.config import Config


class TestClientInitialization(unittest.TestCase):
    """Tests for client construction."""

    def test_valid_key(self):
        client = OpenRouterClient("test-key")
        self.assertEqual(client._api_key, "test-key")

    def test_whitespace_key_stripped(self):
        client = OpenRouterClient("  test-key  ")
        self.assertEqual(client._api_key, "test-key")

    def test_whitespace_only_key_raises(self):
        with self.assertRaises(ValueError):
            OpenRouterClient("   ")

    def test_empty_key_raises(self):
        with self.assertRaises(ValueError):
            OpenRouterClient("")

    def test_base_url_trailing_slash_stripped(self):
        client = OpenRouterClient("test-key", "https://openrouter.ai/api/v1/")
        self.assertEqual(client._base_url, "https://openrouter.ai/api/v1")


class TestErrorParsing(unittest.TestCase):
    """Tests for HTTP error response parsing."""

    def setUp(self):
        self.client = OpenRouterClient("test-key")

    def test_401_raises_authentication_error(self):
        with patch.object(self.client, "_make_request_requests") as mock:
            mock.side_effect = AuthenticationError("auth failed", status_code=401)
            with self.assertRaises(AuthenticationError):
                self.client.list_keys()

    def test_403_raises_permission_error(self):
        with self.assertRaises(PermissionError) as ctx:
            self.client._handle_error(
                MagicMock(headers={}),
                403,
                json.dumps({"error": {"code": 403, "message": "Only management keys can perform this operation"}})
            )
        self.assertIn("Only management keys", str(ctx.exception))

    def test_400_raises_openrouter_error(self):
        with self.assertRaises(OpenRouterError):
            self.client._handle_error(
                MagicMock(headers={}),
                400,
                json.dumps({"error": {"code": 400, "message": "Invalid request parameters"}})
            )

    def test_500_raises_openrouter_error(self):
        with self.assertRaises(OpenRouterError) as ctx:
            self.client._handle_error(
                MagicMock(headers={}),
                500,
                "Internal Server Error"
            )
        self.assertEqual(ctx.exception.status_code, 500)

    def test_429_raises_rate_limit_error(self):
        headers = {"x-ratelimit-remaining": "0", "Retry-After": "60"}
        with self.assertRaises(RateLimitError) as ctx:
            self.client._handle_error(
                MagicMock(headers=headers),
                429,
                json.dumps({"error": {"code": 429, "message": "Too many requests"}})
            )
        self.assertIsNotNone(ctx.exception.rate_limit)

    def test_error_message_extracted_from_json(self):
        body = json.dumps({"error": {"code": 400, "message": "Custom error message"}})
        try:
            self.client._handle_error(MagicMock(headers={}), 400, body)
        except OpenRouterError as e:
            self.assertIn("Custom error message", str(e))

    def test_error_unparseable_body(self):
        try:
            self.client._handle_error(MagicMock(headers={}), 500, "not json")
        except OpenRouterError as e:
            self.assertIn("500", str(e))

    def test_error_string_format_in_body(self):
        """API may return error as a string instead of dict."""
        body = json.dumps({"error": "Invalid API key provided"})
        try:
            self.client._handle_error(MagicMock(headers={}), 401, body)
        except AuthenticationError as e:
            self.assertIn("Invalid API key provided", str(e))

    def test_401_error_includes_api_message(self):
        """401 error should include the actual API response message."""
        with self.assertRaises(AuthenticationError) as ctx:
            self.client._handle_error(
                MagicMock(headers={}),
                401,
                json.dumps({"error": {"message": "Invalid API key"}})
            )
        self.assertIn("Invalid API key", str(ctx.exception))

    def test_headers_use_referer_not_http_referer(self):
        """HTTP header should be 'Referer', not 'HTTP-Referer'."""
        headers = self.client._build_headers()
        self.assertIn("Referer", headers)
        self.assertNotIn("HTTP-Referer", headers)


class TestRateLimitHeaderParsing(unittest.TestCase):
    """Re-test rate limit parsing (integration with api_client)."""

    def test_various_header_casing(self):
        headers = {
            "X-RateLimit-Limit": "500",
            "X-RateLimit-Remaining": "250",
            "x-ratelimit-reset": "1700000000",
        }
        rl = parse_rate_limit_headers(headers)
        self.assertEqual(rl.limit, 500)
        self.assertEqual(rl.remaining, 250)
        self.assertIsNotNone(rl.reset)

    def test_empty_headers(self):
        rl = parse_rate_limit_headers({})
        self.assertIsNone(rl.limit)


class TestUrlBuilding(unittest.TestCase):
    """Tests for URL construction."""

    def test_query_params_added(self):
        client = OpenRouterClient("test-key")
        # Test that query params are properly encoded
        from urllib.parse import urlencode
        url = f"{client._base_url}/activity"
        params = {"date": "2025-01-15"}
        full_url = f"{url}?{urlencode(params)}"
        self.assertIn("date=2025-01-15", full_url)
        self.assertIn("/activity", full_url)


class TestMockedApiCalls(unittest.TestCase):
    """Tests using mocked HTTP to verify end-to-end client behavior."""

    def _make_client(self):
        return OpenRouterClient("test-key")

    @patch("ortracker.api_client.OpenRouterClient._make_request")
    def test_list_keys_success(self, mock_request):
        mock_request.return_value = {"data": [{"hash": "abc", "label": "key"}]}
        client = self._make_client()
        result = client.list_keys()
        self.assertIn("data", result)
        self.assertEqual(result["data"][0]["hash"], "abc")

    @patch("ortracker.api_client.OpenRouterClient._make_request")
    def test_get_activity_success(self, mock_request):
        mock_request.return_value = {"data": []}
        client = self._make_client()
        result = client.get_activity("2025-01-15")
        mock_request.assert_called_once_with(
            "GET", "/activity", params={"date": "2025-01-15"}
        )

    @patch("ortracker.api_client.OpenRouterClient._make_request")
    def test_get_credits_success(self, mock_request):
        mock_request.return_value = {"data": {"total_credits": 50.0, "total_usage": 10.0}}
        client = self._make_client()
        result = client.get_credits()
        self.assertEqual(result["data"]["total_credits"], 50.0)

    @patch("ortracker.api_client.OpenRouterClient._make_request")
    def test_get_current_key_success(self, mock_request):
        mock_request.return_value = {"data": {"free_model_daily_requests": {"limit": 1000}}}
        client = self._make_client()
        result = client.get_current_key()
        self.assertIn("free_model_daily_requests", result["data"])

    @patch("ortracker.api_client.OpenRouterClient._make_request")
    def test_list_keys_raises_network_error(self, mock_request):
        from ortracker.api_client import OpenRouterClient, NetworkError
        mock_request.side_effect = NetworkError("Network connection failed")
        client = OpenRouterClient("test-key")
        with self.assertRaises(NetworkError):
            client.list_keys()

    @patch("ortracker.api_client.OpenRouterClient._make_request")
    def test_list_keys_raises_auth_error(self, mock_request):
        from ortracker.api_client import OpenRouterClient, AuthenticationError
        mock_request.side_effect = AuthenticationError("bad key", status_code=401)
        client = OpenRouterClient("test-key")
        with self.assertRaises(AuthenticationError):
            client.list_keys()


class TestNoUrllibFallback(unittest.TestCase):
    """The urllib path was removed; it forwarded Authorization cross-host."""

    def test_urllib_method_removed(self):
        self.assertFalse(hasattr(OpenRouterClient, "_make_request_urllib"))

    def test_no_requests_detection_flag(self):
        client = OpenRouterClient("test-key")
        self.assertFalse(hasattr(client, "_use_requests"))


class TestSecurity(unittest.TestCase):
    """Tests for security-related behaviors."""

    def test_config_file_permissions_secure(self):
        """Config file must be written with 0600 permissions."""
        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / "test_config.json"
        config = Config(path=config_path)
        config.save()

        mode = stat.S_IMODE(os.stat(config_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_max_response_bytes_constant_exists(self):
        """Ensure MAX_RESPONSE_BYTES is defined and reasonable."""
        self.assertIsInstance(MAX_RESPONSE_BYTES, int)
        self.assertGreater(MAX_RESPONSE_BYTES, 0)
        self.assertLessEqual(MAX_RESPONSE_BYTES, 10 * 1024 * 1024)

    def test_base_url_rejects_non_https(self):
        """http:// must not be used; the key would go out in cleartext."""
        client = OpenRouterClient("k", "http://openrouter.ai/api/v1")
        self.assertEqual(client._base_url, "https://openrouter.ai/api/v1")

    def test_base_url_rejects_foreign_host(self):
        """A foreign host must not receive the Authorization header."""
        client = OpenRouterClient("k", "https://evil.example/api/v1")
        self.assertEqual(client._base_url, "https://openrouter.ai/api/v1")

    def test_base_url_accepts_openrouter_https(self):
        client = OpenRouterClient("k", "https://openrouter.ai/api/v1")
        self.assertEqual(client._base_url, "https://openrouter.ai/api/v1")

    def test_base_url_rejects_traversal_host(self):
        client = OpenRouterClient("k", "https://openrouter.ai.evil.test/api")
        self.assertEqual(client._base_url, "https://openrouter.ai/api/v1")

    def test_base_url_rejects_non_string(self):
        client = OpenRouterClient("k", 12345)
        self.assertEqual(client._base_url, "https://openrouter.ai/api/v1")

    def test_oversized_chunked_body_rejected(self):
        """A chunked body with no content-length must still be capped."""
        client = OpenRouterClient("test-key")

        def fake_iter(chunk_size):
            for _ in range(3):
                yield b"x" * (4 * 1024 * 1024)

        resp = MagicMock()
        resp.headers = {}
        resp.iter_content = fake_iter
        with self.assertRaises(NetworkError):
            client._read_capped(resp)

    def test_declared_oversize_rejected(self):
        client = OpenRouterClient("test-key")
        resp = MagicMock()
        resp.headers = {"content-length": str(50 * 1024 * 1024)}
        resp.iter_content = lambda chunk_size: iter([b"small"])
        with self.assertRaises(NetworkError):
            client._read_capped(resp)

    def test_body_within_limit_accepted(self):
        client = OpenRouterClient("test-key")
        resp = MagicMock()
        resp.headers = {"content-length": "10"}
        resp.iter_content = lambda chunk_size: iter([b'{"ok":', b"true}"])
        self.assertEqual(client._read_capped(resp), b'{"ok":true}')


if __name__ == "__main__":
    unittest.main()
