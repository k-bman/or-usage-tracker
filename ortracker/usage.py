"""Data processing: parsing API responses and calculating usage metrics.

This module contains all the non-GUI logic for:
- Parsing OpenRouter API responses (keys, activity, credits)
- Aggregating daily usage statistics
- Calculating free model quota and remaining requests
- Handling timezone/date-boundary cases correctly
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    ApiKeyInfo,
    DailyUsage,
    FreeModelQuota,
    UsageSummary,
    RateLimitInfo,
)

# OpenRouter free model URLs end with ":free" (e.g. "openai/gpt-4o:free").
# This is documented at https://openrouter.ai/docs/guides/routing/model-variants/free
_FREE_MODEL_SUFFIX = ":free"

# Regex to match free model slugs: any slug ending in ":free"
_FREE_MODEL_RE = re.compile(r":free$")


def is_free_model(model_slug: str) -> bool:
    """Determine whether a model slug refers to a free-model variant.

    Free models on OpenRouter use the ```:free`` catalog variant, appended to
    the model slug (e.g. ``openai/gpt-4o:free``). Per OpenRouter docs, the
    free version appears as its own entry with its own pricing.

    Some responses may use ``model_permaslug`` or a full name; we check both
    the primary slug and the permaslug fields.
    """
    if not model_slug:
        return False
    return model_slug.endswith(_FREE_MODEL_SUFFIX) or bool(_FREE_MODEL_RE.search(model_slug))


def utc_date_string(dt: Optional[datetime] = None) -> str:
    """Return the current UTC date as a YYYY-MM-DD string.

    If ``dt`` is provided, treats it as a naive local datetime (or aware)
    and converts to UTC first, then returns the UTC date string.
    This ensures consistency with OpenRouter's UTC-based date filters.
    """
    if dt is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def local_now_iso() -> str:
    """Return current local time as an ISO 8601 string for display."""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def parse_keys_response(data: Any) -> List[ApiKeyInfo]:
    """Parse the response from ``GET /api/v1/keys``.

    Returns a list of :class:`ApiKeyInfo`, one per API key in the account.
    Handles missing fields, malformed entries, and non-dict items gracefully.
    """
    if not isinstance(data, dict):
        return []
    keys = data.get("data", [])
    if not isinstance(keys, list):
        return []

    result = []
    for item in keys:
        if not isinstance(item, dict):
            continue
        info = _parse_single_key(item)
        if info is not None:
            result.append(info)
    return result


def _parse_single_key(item: Dict[str, Any]) -> Optional[ApiKeyInfo]:
    """Parse a single key object from the keys list response."""
    key_hash = item.get("hash")
    if not key_hash:
        # Older API versions might use 'name' as primary identifier; still try
        label = item.get("name") or item.get("label") or ""
        if not label:
            return None
        key_hash = label

    fdmr = item.get("free_model_daily_requests")
    if not isinstance(fdmr, dict):
        fdmr = {}

    return ApiKeyInfo(
        hash=key_hash,
        label=item.get("label", "") or item.get("name", ""),
        name=item.get("name", ""),
        disabled=bool(item.get("disabled", False)),
        is_management_key=bool(item.get("is_management_key", False)),
        is_provisioning_key=bool(item.get("is_provisioning_key", False)),
        is_free_tier=bool(item.get("is_free_tier", False)),
        free_model_limit=_safe_int(fdmr.get("limit")),
        free_model_remaining=_safe_int(fdmr.get("remaining")),
        free_model_used=_safe_int(fdmr.get("used")),
        usage_daily=_safe_float(item.get("usage_daily")),
        usage_total=_safe_float(item.get("usage")),
        limit=item.get("limit"),
        limit_remaining=item.get("limit_remaining"),
    )


def parse_activity_response(data: Any) -> List[Dict[str, Any]]:
    """Parse the response from ``GET /api/v1/activity``.

    Returns the list of activity items (each a dict, as returned by the API).
    Returns an empty list if the response is malformed or empty.
    """
    if not isinstance(data, dict):
        return []
    items = data.get("data", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def aggregate_daily_usage(
    activity_items: List[Dict[str, Any]],
    date_str: Optional[str] = None,
) -> DailyUsage:
    """Aggregate activity items into a :class:`DailyUsage` summary.

    Parameters
    ----------
    activity_items
        List of activity dicts as returned by ``GET /api/v1/activity`` or
        parsed from the analytics query endpoint.
    date_str
        UTC date string (YYYY-MM-DD). If provided, filters items to that date.
        If ``None``, no date filtering is applied (all items aggregated).
    """
    if not activity_items:
        return DailyUsage(
            date=date_str or utc_date_string(),
            free_requests=0,
            total_requests=0,
        )

    free_requests = 0
    total_requests = 0
    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    cached_tokens = 0
    spending = 0.0
    byok_spending = 0.0

    for item in activity_items:
        item_date = item.get("date", "")
        if date_str and item_date != date_str:
            continue

        model = item.get("model", "") or item.get("model_permaslug", "")
        is_free = is_free_model(model)

        reqs = _safe_int(item.get("requests", 0))
        total_requests += reqs
        if is_free:
            free_requests += reqs

        prompt_tokens += _safe_int(item.get("prompt_tokens", 0))
        completion_tokens += _safe_int(item.get("completion_tokens", 0))
        reasoning_tokens += _safe_int(item.get("reasoning_tokens", 0))
        cached_tokens += _safe_int(item.get("cached_tokens", 0))
        spending += _safe_float(item.get("usage", 0.0))
        byok_spending += _safe_float(item.get("byok_usage_inference", 0.0))

    total_tokens = prompt_tokens + completion_tokens

    return DailyUsage(
        date=date_str or utc_date_string(),
        free_requests=free_requests,
        total_requests=total_requests,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        spending=spending,
        byok_spending=byok_spending,
    )


def parse_analytics_query_response(data: Any) -> List[Dict[str, Any]]:
    """Parse the response from ``POST /api/v1/analytics/query``.

    The analytics query returns rows in ``data.data.data`` (nested structure).
    Returns a flat list of row dicts. Returns empty list on malformed input.
    """
    if not isinstance(data, dict):
        return []

    inner = data.get("data", {})
    if not isinstance(inner, dict):
        rows = data.get("data", [])
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        return []

    rows = inner.get("data", [])
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def parse_analytics_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an analytics query row to a common dict format.

    Analytics query rows use ``date__day`` or ``date`` as the date field,
    ``request_count`` instead of ``requests``, and may have cost as ``cost``
    instead of ``usage``. This function normalizes these to the activity format.
    """
    normalized = dict(row)
    # Normalize date field
    if "date__day" in normalized and "date" not in normalized:
        normalized["date"] = normalized["date__day"]
    # Normalize request_count to requests
    if "request_count" in normalized and "requests" not in normalized:
        normalized["requests"] = normalized["request_count"]
    # Normalize cost to usage if usage not present
    if "cost" in normalized and "usage" not in normalized:
        normalized["usage"] = normalized["cost"]
    # Normalize token fields
    if "total_tokens" not in normalized:
        prompt = normalized.get("prompt_tokens")
        completion = normalized.get("completion_tokens")
        if prompt is not None and completion is not None:
            try:
                normalized["total_tokens"] = int(prompt) + int(completion)
            except (ValueError, TypeError):
                pass
    return normalized


def parse_credits_response(data: Any) -> Dict[str, float]:
    """Parse the response from ``GET /api/v1/credits``.

    Returns a dict with ``total_credits`` and ``total_usage`` (lifetime).
    Returns zeros if the response is malformed.
    """
    if not isinstance(data, dict):
        return {"total_credits": 0.0, "total_usage": 0.0}
    credit_data = data.get("data", {})
    if not isinstance(credit_data, dict):
        return {"total_credits": 0.0, "total_usage": 0.0}
    return {
        "total_credits": _safe_float(credit_data.get("total_credits", 0.0)),
        "total_usage": _safe_float(credit_data.get("total_usage", 0.0)),
    }


def build_usage_summary(
    keys: List[ApiKeyInfo],
    activity_items: List[Dict[str, Any]],
    credits_data: Optional[Dict[str, float]] = None,
    configured_free_limit: int = 1000,
    date_str: Optional[str] = None,
    fetched_at: Optional[datetime] = None,
    error: Optional[str] = None,
) -> UsageSummary:
    """Build a complete :class:`UsageSummary` from raw API data.

    This function ties together all the parsed data:
    - Free model quota from key info (authoritative, from API)
    - Daily usage aggregation from activity data
    - Optional credits data
    - Configurable free model limit as fallback
    """
    utc_today = date_str or utc_date_string()

    daily_usage = aggregate_daily_usage(activity_items, utc_today)

    # Determine free model quota from API keys
    free_limit = configured_free_limit
    free_used = daily_usage.free_requests
    free_remaining = max(0, free_limit - free_used)
    quota_source = "config"

    regular_keys = [k for k in keys if not k.is_management_key and not k.is_provisioning_key]
    if regular_keys:
        # Sum across all regular API keys
        total_free_limit = sum(k.free_model_limit for k in regular_keys)
        total_free_used = sum(k.free_model_used for k in regular_keys)
        total_free_remaining = sum(k.free_model_remaining for k in regular_keys)

        if total_free_limit > 0:
            free_limit = total_free_limit
            free_used = total_free_used
            free_remaining = total_free_remaining
            quota_source = "api"

        # When activity data is unavailable (e.g., regular API key can't access /activity),
        # use key-level data as fallback for free_requests and spending
        if not activity_items:
            daily_usage = DailyUsage(
                date=daily_usage.date,
                free_requests=total_free_used,
                total_requests=total_free_used,
                spending=sum(k.usage_daily for k in regular_keys),
            )

    free_quota = FreeModelQuota(
        limit=free_limit,
        used=free_used,
        remaining=free_remaining,
        source=quota_source,
    )

    total_credits = None
    total_usage = None
    if credits_data:
        total_credits = credits_data.get("total_credits")
        total_usage = credits_data.get("total_usage")

    return UsageSummary(
        date=utc_today,
        free_quota=free_quota,
        daily_usage=daily_usage,
        total_credits=total_credits,
        total_usage=total_usage,
        fetched_at=fetched_at or datetime.now().astimezone(),
        error=error,
    )


def get_quota_warning_level(used: int, limit: int) -> Optional[str]:
    """Return a warning level string if the free model quota is exhausted.

    Returns ``"warning"`` at 80%, ``"critical"`` at 90%, and ``"exhausted"``
    at 100% (or beyond). Returns ``None`` if below 80%.
    """
    if limit == 0:
        return None
    pct = (used / limit) * 100
    if used >= limit:
        return "exhausted"
    if pct >= 90:
        return "critical"
    if pct >= 80:
        return "warning"
    return None


def format_currency(amount: float) -> str:
    """Format a USD amount for display."""
    return f"${amount:.2f}"


def parse_rate_limit_headers(headers: Dict[str, str]) -> RateLimitInfo:
    """Parse rate limit information from HTTP response headers.

    OpenRouter (and many APIs) return rate limit info via headers like
    ``X-RateLimit-Limit``, ``X-RateLimit-Remaining``, ``X-RateLimit-Reset``,
    and ``Retry-After``.
    """
    info = RateLimitInfo()
    limit = headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit")
    if limit:
        parsed = _try_int(limit)
        if parsed is not None:
            info.limit = parsed
    remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
    if remaining:
        parsed = _try_int(remaining)
        if parsed is not None:
            info.remaining = parsed
    reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
    if reset:
        try:
            reset_ts = float(reset)
            info.reset = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after:
        parsed = _try_int(retry_after)
        if parsed is not None:
            info.retry_after = parsed
    return info


# --- helpers ---

def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int, returning default on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float, returning default on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError, OverflowError):
        return default


def _try_int(value: Any) -> Optional[int]:
    """Safely convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
