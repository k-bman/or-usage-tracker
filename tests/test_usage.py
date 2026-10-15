"""Unit tests for usage calculation and API response parsing logic."""

import unittest
from datetime import datetime, timezone, timedelta

from ortracker.usage import (
    is_free_model,
    utc_date_string,
    local_now_iso,
    parse_keys_response,
    parse_activity_response,
    aggregate_daily_usage,
    parse_analytics_query_response,
    parse_analytics_row,
    parse_credits_response,
    build_usage_summary,
    get_quota_warning_level,
    format_currency,
    parse_rate_limit_headers,
)
from ortracker.models import (
    ApiKeyInfo,
    DailyUsage,
    FreeModelQuota,
    UsageSummary,
)


class TestIsFreeModel(unittest.TestCase):
    """Tests for distinguishing free vs paid model slugs."""

    def test_free_suffix(self):
        self.assertTrue(is_free_model("openai/gpt-4o:free"))

    def test_paid_model(self):
        self.assertFalse(is_free_model("openai/gpt-4o"))
        self.assertFalse(is_free_model("anthropic/claude-3-5-sonnet"))

    def test_empty_string(self):
        self.assertFalse(is_free_model(""))

    def test_none(self):
        self.assertFalse(is_free_model(None))

    def test_free_with_long_name(self):
        self.assertTrue(is_free_model("meta-llama/llama-3.2-3b-instruct:free"))

    def test_free_variant_in_middle(self):
        # Should only match at the end
        self.assertFalse(is_free_model("openai/gpt-4o:free-v2"))
        self.assertFalse(is_free_model("openai/gpt-4o:not-free"))

    def test_regular_model_with_free_substring(self):
        self.assertFalse(is_free_model("openai/gpt-free-model"))
        self.assertFalse(is_free_model("free-model-variant"))


class TestUtcDateString(unittest.TestCase):
    """Tests for UTC date calculation (timezone boundary handling)."""

    def test_current_date(self):
        result = utc_date_string()
        self.assertEqual(len(result), 10)
        self.assertEqual(result[4], "-")
        self.assertEqual(result[7], "-")

    def test_naive_local_datetime(self):
        # A naive datetime should be treated as UTC
        dt = datetime(2025, 1, 15, 12, 0, 0)
        self.assertEqual(utc_date_string(dt), "2025-01-15")

    def test_aware_datetime_converted_to_utc(self):
        # Noon in UTC+5 -> midnight in UTC (previous day boundary)
        dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        # 2025-01-15 12:00 UTC+5 = 2025-01-15 07:00 UTC -> same date
        self.assertEqual(utc_date_string(dt), "2025-01-15")

    def test_timezone_crossing_past_midnight(self):
        # 1 AM in UTC-5 = 6 AM UTC, same date
        # But 11 PM in UTC+5 = 16:00 UTC previous day? No:
        # 2025-01-16 23:00 UTC+14 = 2025-01-16 09:00 UTC -> same date
        # Let's test a case that crosses:
        # 2025-01-16 02:00 UTC+5 = 2025-01-15 21:00 UTC -> different date!
        dt = datetime(2025, 1, 16, 2, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        self.assertEqual(utc_date_string(dt), "2025-01-15")

    def test_utc_midnight(self):
        dt = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(utc_date_string(dt), "2025-01-15")

    def test_just_before_utc_midnight(self):
        dt = datetime(2025, 1, 14, 23, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(utc_date_string(dt), "2025-01-14")


class TestParseKeysResponse(unittest.TestCase):
    """Tests for parsing GET /api/v1/keys response."""

    def test_valid_response(self):
        data = {
            "data": [
                {
                    "hash": "abc123",
                    "label": "My API Key",
                    "name": "My API Key",
                    "disabled": False,
                    "is_management_key": False,
                    "is_provisioning_key": False,
                    "is_free_tier": False,
                    "free_model_daily_requests": {
                        "limit": 1000,
                        "remaining": 653,
                        "used": 347,
                    },
                    "usage_daily": 0.50,
                    "usage": 25.75,
                    "limit": 100,
                    "limit_remaining": 74.5,
                },
                {
                    "hash": "def456",
                    "label": "Management Key",
                    "name": "Management Key",
                    "disabled": False,
                    "is_management_key": True,
                    "is_provisioning_key": False,
                    "free_model_daily_requests": {
                        "limit": 0,
                        "remaining": 0,
                        "used": 0,
                    },
                },
            ]
        }
        keys = parse_keys_response(data)
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0].hash, "abc123")
        self.assertEqual(keys[0].free_model_limit, 1000)
        self.assertEqual(keys[0].free_model_used, 347)
        self.assertEqual(keys[0].free_model_remaining, 653)
        self.assertFalse(keys[0].is_management_key)
        self.assertTrue(keys[1].is_management_key)

    def test_empty_response(self):
        self.assertEqual(parse_keys_response({"data": []}), [])

    def test_malformed_response(self):
        self.assertEqual(parse_keys_response({}), [])
        self.assertEqual(parse_keys_response({"data": "not a list"}), [])
        self.assertEqual(parse_keys_response(None), [])
        self.assertEqual(parse_keys_response("string"), [])

    def test_item_with_missing_fields(self):
        data = {"data": [{"hash": "only_hash"}]}
        keys = parse_keys_response(data)
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].hash, "only_hash")
        self.assertEqual(keys[0].free_model_limit, 0)
        self.assertFalse(keys[0].is_management_key)

    def test_item_not_a_dict(self):
        data = {"data": [42, "string", None, {"hash": "ok"}]}
        keys = parse_keys_response(data)
        self.assertEqual(len(keys), 1)

    def test_missing_hash_uses_name(self):
        data = {"data": [{"name": "fallback-name", "label": "label"}]}
        keys = parse_keys_response(data)
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].hash, "fallback-name")

    def test_free_model_daily_requests_missing(self):
        data = {"data": [{"hash": "abc", "free_model_daily_requests": None}]}
        keys = parse_keys_response(data)
        self.assertEqual(keys[0].free_model_limit, 0)

    def test_free_tier_fifty_limit(self):
        data = {"data": [{
            "hash": "abc",
            "is_free_tier": True,
            "free_model_daily_requests": {"limit": 50, "remaining": 48, "used": 2},
        }]}
        keys = parse_keys_response(data)
        self.assertTrue(keys[0].is_free_tier)
        self.assertEqual(keys[0].free_model_limit, 50)


class TestParseActivityResponse(unittest.TestCase):
    """Tests for parsing GET /api/v1/activity response."""

    def test_valid_response(self):
        data = {
            "data": [
                {
                    "date": "2025-01-15",
                    "model": "openai/gpt-4o:free",
                    "requests": 100,
                    "usage": 0.0,
                    "prompt_tokens": 5000,
                    "completion_tokens": 3000,
                    "reasoning_tokens": 0,
                    "cached_tokens": 500,
                    "byok_usage_inference": 0.0,
                },
                {
                    "date": "2025-01-15",
                    "model": "openai/gpt-4o",
                    "requests": 50,
                    "usage": 0.75,
                    "prompt_tokens": 20000,
                    "completion_tokens": 10000,
                    "reasoning_tokens": 1000,
                    "cached_tokens": 200,
                    "byok_usage_inference": 0.0,
                },
            ]
        }
        items = parse_activity_response(data)
        self.assertEqual(len(items), 2)

    def test_empty_response(self):
        self.assertEqual(parse_activity_response({"data": []}), [])

    def test_malformed_response(self):
        self.assertEqual(parse_activity_response({}), [])
        self.assertEqual(parse_activity_response(None), [])
        self.assertEqual(parse_activity_response({"data": "not_a_list"}), [])

    def test_non_dict_items_filtered(self):
        data = {"data": [42, None, "string", {"date": "2025-01-15"}]}
        items = parse_activity_response(data)
        self.assertEqual(len(items), 1)


class TestAggregateDailyUsage(unittest.TestCase):
    """Tests for daily usage aggregation."""

    def test_aggregate_mixed_free_paid(self):
        items = [
            {
                "date": "2025-01-15",
                "model": "openai/gpt-4o:free",
                "requests": 300,
                "usage": 0.0,
                "prompt_tokens": 10000,
                "completion_tokens": 5000,
                "reasoning_tokens": 100,
                "cached_tokens": 500,
                "byok_usage_inference": 0.0,
            },
            {
                "date": "2025-01-15",
                "model": "anthropic/claude-3-5-sonnet",
                "requests": 100,
                "usage": 0.50,
                "prompt_tokens": 20000,
                "completion_tokens": 10000,
                "reasoning_tokens": 500,
                "cached_tokens": 300,
                "byok_usage_inference": 0.0,
            },
            {
                "date": "2025-01-15",
                "model": "meta-llama/llama-3.2-3b-instruct:free",
                "requests": 50,
                "usage": 0.0,
                "prompt_tokens": 5000,
                "completion_tokens": 3000,
                "reasoning_tokens": 50,
                "cached_tokens": 100,
                "byok_usage_inference": 0.0,
            },
        ]
        usage = aggregate_daily_usage(items, "2025-01-15")
        self.assertEqual(usage.date, "2025-01-15")
        self.assertEqual(usage.free_requests, 350)  # 300 + 50
        self.assertEqual(usage.total_requests, 450)  # 300 + 100 + 50
        self.assertEqual(usage.prompt_tokens, 35000)
        self.assertEqual(usage.completion_tokens, 18000)
        self.assertEqual(usage.total_tokens, 53000)
        self.assertEqual(usage.spending, 0.50)
        self.assertEqual(usage.reasoning_tokens, 650)

    def test_empty_items(self):
        usage = aggregate_daily_usage([], "2025-01-15")
        self.assertEqual(usage.free_requests, 0)
        self.assertEqual(usage.total_requests, 0)
        self.assertEqual(usage.spending, 0.0)

    def test_date_filtering(self):
        items = [
            {
                "date": "2025-01-14",
                "model": "openai/gpt-4o:free",
                "requests": 100,
                "usage": 0.0,
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "byok_usage_inference": 0.0,
            },
            {
                "date": "2025-01-15",
                "model": "openai/gpt-4o:free",
                "requests": 200,
                "usage": 0.0,
                "prompt_tokens": 2000,
                "completion_tokens": 1000,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "byok_usage_inference": 0.0,
            },
        ]
        # Only today's items
        usage = aggregate_daily_usage(items, "2025-01-15")
        self.assertEqual(usage.free_requests, 200)

    def test_all_free_models(self):
        items = [
            {"date": "2025-01-15", "model": "openai/gpt-4o:free", "requests": 100, "usage": 0.0, "prompt_tokens": 1000, "completion_tokens": 500, "reasoning_tokens": 0, "cached_tokens": 0, "byok_usage_inference": 0.0},
            {"date": "2025-01-15", "model": "gemma/gemma-2-9b-it:free", "requests": 200, "usage": 0.0, "prompt_tokens": 2000, "completion_tokens": 1000, "reasoning_tokens": 0, "cached_tokens": 0, "byok_usage_inference": 0.0},
        ]
        usage = aggregate_daily_usage(items, "2025-01-15")
        self.assertEqual(usage.free_requests, 300)
        self.assertEqual(usage.total_requests, 300)

    def test_all_paid_models(self):
        items = [
            {"date": "2025-01-15", "model": "openai/gpt-4o", "requests": 100, "usage": 1.0, "prompt_tokens": 1000, "completion_tokens": 500, "reasoning_tokens": 0, "cached_tokens": 0, "byok_usage_inference": 0.0},
        ]
        usage = aggregate_daily_usage(items, "2025-01-15")
        self.assertEqual(usage.free_requests, 0)
        self.assertEqual(usage.total_requests, 100)
        self.assertEqual(usage.spending, 1.0)

    def test_missing_fields_handled_gracefully(self):
        items = [
            # has no date, won't match the filter
            {"model": "openai/gpt-4o:free", "requests": 10},
            # date matches but requests is a string -> parsed as 0
            {"date": "2025-01-15", "model": "openai/gpt-4o", "requests": "not_a_number",
             "usage": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
             "reasoning_tokens": 0, "cached_tokens": 0, "byok_usage_inference": 0.0},
        ]
        usage = aggregate_daily_usage(items, "2025-01-15")
        # First item has no date -> excluded. Second item has bad requests -> 0.
        self.assertEqual(usage.total_requests, 0)
        self.assertEqual(usage.free_requests, 0)

    def test_byok_spending(self):
        items = [
            {"date": "2025-01-15", "model": "openai/gpt-4o", "requests": 10, "usage": 0.5, "byok_usage_inference": 0.1, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0},
        ]
        usage = aggregate_daily_usage(items, "2025-01-15")
        self.assertEqual(usage.spending, 0.5)
        self.assertEqual(usage.byok_spending, 0.1)


class TestParseAnalyticsQueryResponse(unittest.TestCase):
    """Tests for parsing POST /api/v1/analytics/query response."""

    def test_valid_response(self):
        data = {
            "data": {
                "data": [
                    {"date__day": "2025-01-15", "request_count": 500, "cost": 0.5},
                ],
                "metadata": {"query_time_ms": 42, "row_count": 1, "truncated": False},
            }
        }
        rows = parse_analytics_query_response(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_count"], 500)

    def test_flat_data_response(self):
        data = {"data": [{"date": "2025-01-15", "request_count": 100}]}
        rows = parse_analytics_query_response(data)
        self.assertEqual(len(rows), 1)

    def test_malformed_response(self):
        self.assertEqual(parse_analytics_query_response({}), [])
        self.assertEqual(parse_analytics_query_response(None), [])
        self.assertEqual(parse_analytics_query_response({"data": "not_dict"}), [])

    def test_flat_with_data_as_string(self):
        # If data.data is not a list, try data directly
        data = {"data": "not_a_dict"}
        rows = parse_analytics_query_response(data)
        self.assertEqual(rows, [])


class TestParseAnalyticsRow(unittest.TestCase):
    """Tests for normalizing analytics query rows."""

    def test_normalize_date_field(self):
        row = {"date__day": "2025-01-15", "request_count": 100}
        normalized = parse_analytics_row(row)
        self.assertEqual(normalized["date"], "2025-01-15")
        self.assertEqual(normalized["requests"], 100)

    def test_normalize_cost_field(self):
        row = {"date": "2025-01-15", "cost": 1.5}
        normalized = parse_analytics_row(row)
        self.assertEqual(normalized["usage"], 1.5)

    def test_no_normalization_needed(self):
        row = {"date": "2025-01-15", "requests": 100, "usage": 0.5}
        normalized = parse_analytics_row(row)
        self.assertEqual(normalized["requests"], 100)
        self.assertEqual(normalized["usage"], 0.5)

    def test_total_tokens_calculated(self):
        """When total_tokens is missing, compute from prompt + completion."""
        row = {"date": "2025-01-15", "prompt_tokens": 100, "completion_tokens": 50}
        normalized = parse_analytics_row(row)
        self.assertEqual(normalized["total_tokens"], 150)

    def test_total_tokens_not_overwritten(self):
        """Existing total_tokens should not be overwritten."""
        row = {"date": "2025-01-15", "total_tokens": 200, "prompt_tokens": 100, "completion_tokens": 50}
        normalized = parse_analytics_row(row)
        self.assertEqual(normalized["total_tokens"], 200)

    def test_total_tokens_missing_partial_data(self):
        """If prompt or completion tokens missing, total_tokens stays unset."""
        row = {"date": "2025-01-15", "prompt_tokens": 100}
        normalized = parse_analytics_row(row)
        self.assertNotIn("total_tokens", normalized)

    def test_single_key_wrapped_from_key_endpoint(self):
        """parse_keys_response should handle /key response wrapped in a list."""
        # Simulate the fallback: wrap /key response data in a list
        response = {
            "data": {
                "hash": "keyhash123",
                "name": "My API Key",
                "is_management_key": False,
                "free_model_daily_requests": {
                    "limit": 1000,
                    "remaining": 850,
                    "used": 150,
                },
            }
        }
        # After wrapping in fallback logic: {"data": [key_data]}
        wrapped = {"data": [response["data"]]}
        keys = parse_keys_response(wrapped)
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].free_model_limit, 1000)
        self.assertEqual(keys[0].free_model_remaining, 850)
        self.assertEqual(keys[0].free_model_used, 150)
        self.assertFalse(keys[0].is_management_key)


class TestParseCreditsResponse(unittest.TestCase):
    """Tests for parsing GET /api/v1/credits response."""

    def test_valid_response(self):
        data = {"data": {"total_credits": 100.5, "total_usage": 25.75}}
        result = parse_credits_response(data)
        self.assertEqual(result["total_credits"], 100.5)
        self.assertEqual(result["total_usage"], 25.75)

    def test_empty_response(self):
        result = parse_credits_response({})
        self.assertEqual(result["total_credits"], 0.0)

    def test_malformed_response(self):
        self.assertEqual(parse_credits_response(None)["total_credits"], 0.0)
        self.assertEqual(parse_credits_response("string")["total_credits"], 0.0)
        self.assertEqual(parse_credits_response({"data": "not_a_dict"})["total_usage"], 0.0)

    def test_missing_fields(self):
        result = parse_credits_response({"data": {}})
        self.assertEqual(result["total_credits"], 0.0)
        self.assertEqual(result["total_usage"], 0.0)


class TestBuildUsageSummary(unittest.TestCase):
    """Tests for building the complete usage summary."""

    def test_with_single_regular_key(self):
        keys = parse_keys_response({
            "data": [{
                "hash": "abc123",
                "label": "My Key",
                "name": "My Key",
                "is_management_key": False,
                "is_free_tier": False,
                "free_model_daily_requests": {"limit": 1000, "remaining": 653, "used": 347},
                "usage_daily": 0.50,
                "usage": 25.75,
            }]
        })
        activity = parse_activity_response({
            "data": [{
                "date": "2025-01-15",
                "model": "openai/gpt-4o:free",
                "requests": 347,
                "usage": 0.50,
                "prompt_tokens": 10000,
                "completion_tokens": 5000,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "byok_usage_inference": 0.0,
            }]
        })
        summary = build_usage_summary(
            keys=keys,
            activity_items=activity,
            credits_data={"total_credits": 100.0, "total_usage": 25.75},
            configured_free_limit=1000,
            date_str="2025-01-15",
        )
        self.assertEqual(summary.free_quota.limit, 1000)
        self.assertEqual(summary.free_quota.used, 347)
        self.assertEqual(summary.free_quota.remaining, 653)
        self.assertEqual(summary.free_quota.source, "api")
        self.assertEqual(summary.daily_usage.total_requests, 347)
        self.assertEqual(summary.daily_usage.free_requests, 347)
        self.assertEqual(summary.total_credits, 100.0)

    def test_with_management_key_only(self):
        """If only management keys exist, fall back to activity-based counting."""
        keys = parse_keys_response({
            "data": [{
                "hash": "mgmt",
                "label": "Mgmt Key",
                "name": "Mgmt Key",
                "is_management_key": True,
                "free_model_daily_requests": {"limit": 0, "remaining": 0, "used": 0},
            }]
        })
        activity = [{
            "date": "2025-01-15",
            "model": "openai/gpt-4o:free",
            "requests": 100,
            "usage": 0.0,
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "byok_usage_inference": 0.0,
        }]
        summary = build_usage_summary(
            keys=keys,
            activity_items=activity,
            configured_free_limit=1000,
            date_str="2025-01-15",
        )
        # No regular keys with limit > 0, so falls back to config
        self.assertEqual(summary.free_quota.used, 100)
        self.assertEqual(summary.free_quota.limit, 1000)
        self.assertEqual(summary.free_quota.remaining, 900)
        self.assertEqual(summary.free_quota.source, "config")

    def test_empty_keys_uses_activity(self):
        activity = [{
            "date": "2025-01-15",
            "model": "gemma/gemma-2-9b-it:free",
            "requests": 50,
            "usage": 0.0,
            "prompt_tokens": 500,
            "completion_tokens": 300,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "byok_usage_inference": 0.0,
        }]
        summary = build_usage_summary(
            keys=[],
            activity_items=activity,
            configured_free_limit=1000,
            date_str="2025-01-15",
        )
        self.assertEqual(summary.free_quota.used, 50)
        self.assertEqual(summary.free_quota.limit, 1000)

    def test_with_error(self):
        summary = build_usage_summary(
            keys=[],
            activity_items=[],
            configured_free_limit=1000,
            date_str="2025-01-15",
            error="Network error",
        )
        self.assertIsNotNone(summary.error)
        self.assertEqual(summary.error, "Network error")

    def test_no_activity_uses_key_data(self):
        """When activity items are empty, use key data for daily usage stats."""
        keys = parse_keys_response({
            "data": [{
                "hash": "abc123",
                "label": "My Key",
                "name": "My Key",
                "is_management_key": False,
                "is_free_tier": False,
                "free_model_daily_requests": {"limit": 1000, "remaining": 653, "used": 347},
                "usage_daily": 0.50,
                "usage": 25.75,
            }]
        })
        summary = build_usage_summary(
            keys=keys,
            activity_items=[],  # No activity data (regular API key)
            configured_free_limit=1000,
            date_str="2025-01-15",
        )
        # Quota from key data
        self.assertEqual(summary.free_quota.used, 347)
        self.assertEqual(summary.free_quota.limit, 1000)
        self.assertEqual(summary.free_quota.source, "api")
        # Daily usage from key data (fallback)
        self.assertEqual(summary.daily_usage.free_requests, 347)
        self.assertEqual(summary.daily_usage.total_requests, 347)
        self.assertAlmostEqual(summary.daily_usage.spending, 0.50)


class TestQuotaWarningLevel(unittest.TestCase):
    """Tests for quota warning thresholds."""

    def test_below_80(self):
        self.assertIsNone(get_quota_warning_level(700, 1000))
        self.assertIsNone(get_quota_warning_level(799, 1000))

    def test_at_80(self):
        self.assertEqual(get_quota_warning_level(800, 1000), "warning")
        self.assertEqual(get_quota_warning_level(850, 1000), "warning")

    def test_at_90(self):
        self.assertEqual(get_quota_warning_level(900, 1000), "critical")
        self.assertEqual(get_quota_warning_level(950, 1000), "critical")

    def test_at_100(self):
        self.assertEqual(get_quota_warning_level(1000, 1000), "exhausted")
        self.assertEqual(get_quota_warning_level(1100, 1000), "exhausted")

    def test_zero_limit(self):
        self.assertIsNone(get_quota_warning_level(0, 0))

    def test_fifty_limit(self):
        # 80% of 50 = 40, 90% = 45
        self.assertIsNone(get_quota_warning_level(39, 50))
        self.assertEqual(get_quota_warning_level(40, 50), "warning")
        self.assertEqual(get_quota_warning_level(45, 50), "critical")
        self.assertEqual(get_quota_warning_level(50, 50), "exhausted")


class TestFormatCurrency(unittest.TestCase):
    """Tests for currency formatting."""

    def test_basic(self):
        self.assertEqual(format_currency(0.0), "$0.00")
        self.assertEqual(format_currency(1.5), "$1.50")
        self.assertEqual(format_currency(25.75), "$25.75")
        self.assertEqual(format_currency(0.001), "$0.00")


class TestParseRateLimitHeaders(unittest.TestCase):
    """Tests for parsing rate limit HTTP headers."""

    def test_parsed_headers(self):
        headers = {
            "X-RateLimit-Limit": "1000",
            "X-RateLimit-Remaining": "500",
            "X-RateLimit-Reset": "1700000000",
            "Retry-After": "60",
        }
        rl = parse_rate_limit_headers(headers)
        self.assertEqual(rl.limit, 1000)
        self.assertEqual(rl.remaining, 500)
        self.assertIsNotNone(rl.reset)
        self.assertEqual(rl.retry_after, 60)

    def test_lowercase_headers(self):
        headers = {
            "x-ratelimit-limit": "1000",
            "x-ratelimit-remaining": "500",
        }
        rl = parse_rate_limit_headers(headers)
        self.assertEqual(rl.limit, 1000)
        self.assertEqual(rl.remaining, 500)

    def test_empty_headers(self):
        rl = parse_rate_limit_headers({})
        self.assertIsNone(rl.limit)
        self.assertIsNone(rl.remaining)

    def test_invalid_values(self):
        rl = parse_rate_limit_headers({
            "X-RateLimit-Limit": "not_a_number",
            "X-RateLimit-Remaining": "",
        })
        self.assertIsNone(rl.limit)
        self.assertIsNone(rl.remaining)


class TestEdgeCases(unittest.TestCase):
    """Edge case tests for robustness."""

    def test_model_with_free_in_name_but_not_suffix(self):
        """Model that contains 'free' but not as a suffix should not be counted."""
        items = [{
            "date": "2025-01-15",
            "model": "openai/gpt-4o-free",  # has "free" but no :free suffix
            "requests": 100,
            "usage": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "byok_usage_inference": 0.0,
        }]
        usage = aggregate_daily_usage(items, "2025-01-15")
        self.assertEqual(usage.free_requests, 0)
        self.assertEqual(usage.total_requests, 100)

    def test_no_date_filter(self):
        """When date_str is None, all items should be aggregated."""
        items = [
            {"model": "openai/gpt-4o:free", "requests": 100, "usage": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0, "byok_usage_inference": 0.0, "date": "2025-01-14"},
            {"model": "openai/gpt-4o:free", "requests": 50, "usage": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0, "byok_usage_inference": 0.0, "date": "2025-01-15"},
        ]
        usage = aggregate_daily_usage(items, None)
        self.assertEqual(usage.free_requests, 150)
        self.assertEqual(usage.total_requests, 150)

    def test_large_numbers(self):
        items = [{
            "date": "2025-01-15",
            "model": "openai/gpt-4o:free",
            "requests": 999999999,
            "usage": 9999.99,
            "prompt_tokens": 999999999,
            "completion_tokens": 999999999,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "byok_usage_inference": 0.0,
        }]
        usage = aggregate_daily_usage(items, "2025-01-15")
        self.assertEqual(usage.total_requests, 999999999)
        self.assertEqual(usage.spending, 9999.99)


class TestMalformedKeyData(unittest.TestCase):
    """Server-supplied key data must never raise while parsing."""

    def _parse_one(self, item):
        parsed = parse_keys_response({"data": [item]})
        self.assertEqual(len(parsed), 1)
        return parsed[0]

    def test_non_dict_free_model_daily_requests(self):
        info = self._parse_one({"hash": "h", "free_model_daily_requests": "nope"})
        self.assertEqual(info.free_model_limit, 0)
        self.assertEqual(info.free_model_used, 0)

    def test_non_numeric_limit(self):
        info = self._parse_one(
            {"hash": "h", "free_model_daily_requests": {"limit": "abc"}})
        self.assertEqual(info.free_model_limit, 0)

    def test_nested_dict_limit(self):
        info = self._parse_one(
            {"hash": "h", "free_model_daily_requests": {"limit": {"a": 1}}})
        self.assertEqual(info.free_model_limit, 0)

    def test_bad_usage_daily(self):
        info = self._parse_one({"hash": "h", "usage_daily": "free"})
        self.assertEqual(info.usage_daily, 0.0)

    def test_huge_int_usage_does_not_raise(self):
        """OverflowError from float(10**500) must be contained."""
        info = self._parse_one({"hash": "h", "usage_daily": 10 ** 500})
        self.assertEqual(info.usage_daily, 0.0)

    def test_null_free_model_daily_requests(self):
        info = self._parse_one({"hash": "h", "free_model_daily_requests": None})
        self.assertEqual(info.free_model_limit, 0)


class TestSafeParsers(unittest.TestCase):
    """_safe_int/_safe_float must not raise on hostile input."""

    def test_safe_int_hostile(self):
        from ortracker.usage import _safe_int
        for value in (None, {}, [], "abc", object()):
            self.assertEqual(_safe_int(value), 0)
        # bool is an int subclass; True -> 1 is correct, not a failure
        self.assertEqual(_safe_int(True), 1)

    def test_safe_float_hostile(self):
        from ortracker.usage import _safe_float
        for value in (None, {}, [], "abc", object(), 10 ** 500):
            self.assertEqual(_safe_float(value), 0.0)

    def test_rate_limit_reset_overflow_contained(self):
        """fromtimestamp('1e30') raises OverflowError, not ValueError."""
        info = parse_rate_limit_headers({"X-RateLimit-Reset": "1e30"})
        self.assertIsNone(info.reset)


if __name__ == "__main__":
    unittest.main()
