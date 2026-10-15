"""Data models for OpenRouter usage data."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class FreeModelQuota:
    """Represents the free-model daily request quota."""
    limit: int
    used: int
    remaining: int
    source: str = "api"  # "api" or "config"

    @property
    def percentage(self) -> float:
        if self.limit == 0:
            return 0.0
        return (self.used / self.limit) * 100.0


@dataclass
class DailyUsage:
    """Aggregated daily usage statistics."""
    date: str                  # UTC date in YYYY-MM-DD format
    free_requests: int         # Requests to models with :free suffix
    total_requests: int        # All requests (free + paid)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    spending: float = 0.0     # USD (OpenRouter credits spent)
    byok_spending: float = 0.0


@dataclass
class UsageSummary:
    """Complete summary of daily OpenRouter usage."""
    date: str                              # UTC date YYYY-MM-DD
    free_quota: FreeModelQuota
    daily_usage: DailyUsage
    total_credits: Optional[float] = None   # Lifetime credits purchased
    total_usage: Optional[float] = None     # Lifetime credits used
    fetched_at: Optional[datetime] = None   # When data was retrieved (local time)
    error: Optional[str] = None


@dataclass
class ApiKeyInfo:
    """Information about an OpenRouter API key from the management API."""
    hash: str
    label: str
    name: str
    disabled: bool = False
    is_management_key: bool = False
    is_provisioning_key: bool = False
    is_free_tier: bool = False
    free_model_limit: int = 0
    free_model_remaining: int = 0
    free_model_used: int = 0
    usage_daily: float = 0.0
    usage_total: float = 0.0
    limit: Optional[float] = None
    limit_remaining: Optional[float] = None


@dataclass
class RateLimitInfo:
    """Rate limit information from HTTP response headers or API."""
    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset: Optional[datetime] = None
    retry_after: Optional[int] = None
