"""Tests for market_hours module."""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.market_hours import (
    _most_recent_checkpoint,
    get_schedule_for_ticker,
    get_tickers_needing_refresh,
    is_market_open,
    reload_config,
)

# ── Sample schedules ──────────────────────────────────────────────────

US_EQUITY = {
    "name": "US Equity",
    "open": "09:30",
    "close": "16:00",
    "timezone": "America/New_York",
    "days": [0, 1, 2, 3, 4],
}

CRYPTO = {
    "name": "Crypto 24/7",
    "open": "00:00",
    "close": "23:59",
    "timezone": "UTC",
    "days": [0, 1, 2, 3, 4, 5, 6],
}

SAMPLE_CONFIG = {
    "offsets": [1, 31],
    "schedules": {"us_equity": US_EQUITY, "crypto": CRYPTO},
    "default_schedule": "us_equity",
    "crypto_currencies": ["BTC", "ETH", "SOL"],
}


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Reset cached config between tests."""
    reload_config()
    yield
    reload_config()


# ── is_market_open ────────────────────────────────────────────────────

class TestIsMarketOpen:
    def test_open_during_hours(self):
        # Tuesday 10:00 AM ET
        now = datetime(2026, 4, 14, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(US_EQUITY, now) is True

    def test_closed_before_open(self):
        # Tuesday 9:00 AM ET
        now = datetime(2026, 4, 14, 9, 0, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(US_EQUITY, now) is False

    def test_closed_after_close(self):
        # Tuesday 4:30 PM ET
        now = datetime(2026, 4, 14, 16, 30, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(US_EQUITY, now) is False

    def test_closed_on_weekend(self):
        # Saturday 12:00 PM ET
        now = datetime(2026, 4, 18, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(US_EQUITY, now) is False

    def test_open_at_exact_open(self):
        now = datetime(2026, 4, 14, 9, 30, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(US_EQUITY, now) is True

    def test_closed_at_exact_close(self):
        # close is exclusive
        now = datetime(2026, 4, 14, 16, 0, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(US_EQUITY, now) is False

    def test_crypto_always_open_weekday(self):
        now = datetime(2026, 4, 14, 3, 0, tzinfo=ZoneInfo("UTC"))
        assert is_market_open(CRYPTO, now) is True

    def test_crypto_always_open_weekend(self):
        now = datetime(2026, 4, 18, 23, 30, tzinfo=ZoneInfo("UTC"))
        assert is_market_open(CRYPTO, now) is True

    def test_timezone_conversion(self):
        # 10:00 AM ET expressed as UTC (14:00 UTC)
        now_utc = datetime(2026, 4, 14, 14, 0, tzinfo=ZoneInfo("UTC"))
        assert is_market_open(US_EQUITY, now_utc) is True

    def test_timezone_outside_hours(self):
        # 8:00 AM ET expressed as UTC (12:00 UTC)
        now_utc = datetime(2026, 4, 14, 12, 0, tzinfo=ZoneInfo("UTC"))
        assert is_market_open(US_EQUITY, now_utc) is False


# ── get_schedule_for_ticker ────────────────────────────────────────────

class TestGetScheduleForTicker:
    def test_crypto_by_currency(self):
        assert get_schedule_for_ticker("IBIT", "BTC", SAMPLE_CONFIG) == "crypto"

    def test_crypto_by_ticker_pattern(self):
        assert get_schedule_for_ticker("BTC-USD", "USD", SAMPLE_CONFIG) == "crypto"
        assert get_schedule_for_ticker("ETH-CAD", "CAD", SAMPLE_CONFIG) == "crypto"

    def test_equity_default(self):
        assert get_schedule_for_ticker("AAPL", "USD", SAMPLE_CONFIG) == "us_equity"

    def test_tsx_ticker_default(self):
        assert get_schedule_for_ticker("NDA.V", "CAD", SAMPLE_CONFIG) == "us_equity"

    def test_non_crypto_currency(self):
        assert get_schedule_for_ticker("SPY", "CAD", SAMPLE_CONFIG) == "us_equity"


# ── _most_recent_checkpoint ───────────────────────────────────────────

class TestMostRecentCheckpoint:
    def test_after_second_offset(self):
        # 10:45 → most recent checkpoint is 10:31
        now = datetime(2026, 4, 14, 10, 45, tzinfo=ZoneInfo("America/New_York"))
        cp = _most_recent_checkpoint([1, 31], now)
        assert cp.hour == 10
        assert cp.minute == 31

    def test_between_offsets(self):
        # 10:15 → most recent checkpoint is 10:01
        now = datetime(2026, 4, 14, 10, 15, tzinfo=ZoneInfo("America/New_York"))
        cp = _most_recent_checkpoint([1, 31], now)
        assert cp.hour == 10
        assert cp.minute == 1

    def test_before_first_offset(self):
        # 10:00 → most recent checkpoint is 9:31
        now = datetime(2026, 4, 14, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        cp = _most_recent_checkpoint([1, 31], now)
        assert cp.hour == 9
        assert cp.minute == 31

    def test_at_exact_offset(self):
        # 10:31 → checkpoint is 10:31 (at or before)
        now = datetime(2026, 4, 14, 10, 31, tzinfo=ZoneInfo("America/New_York"))
        cp = _most_recent_checkpoint([1, 31], now)
        assert cp.hour == 10
        assert cp.minute == 31

    def test_single_offset(self):
        # Single offset at :00
        now = datetime(2026, 4, 14, 10, 45, tzinfo=ZoneInfo("America/New_York"))
        cp = _most_recent_checkpoint([0], now)
        assert cp.hour == 10
        assert cp.minute == 0


# ── get_tickers_needing_refresh ──────────────────────────────────────

class TestGetTickersNeedingRefresh:
    @patch("src.market_hours._get_config", return_value=SAMPLE_CONFIG)
    @patch("src.market_hours._load_price_cache")
    def test_stale_during_market_hours(self, mock_cache, mock_cfg):
        # Tuesday 10:45 AM ET → checkpoint at 10:31
        now = datetime(2026, 4, 14, 10, 45, tzinfo=ZoneInfo("America/New_York"))
        # Cached at 10:00 (before 10:31 checkpoint)
        old_ts = datetime(2026, 4, 14, 10, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
        mock_cache.return_value = {"AAPL": {"price": 200.0, "ts": old_ts}}

        result = get_tickers_needing_refresh([("AAPL", "USD")], now=now)
        assert "AAPL" in result

    @patch("src.market_hours._get_config", return_value=SAMPLE_CONFIG)
    @patch("src.market_hours._load_price_cache")
    def test_fresh_during_market_hours(self, mock_cache, mock_cfg):
        # Tuesday 10:45 AM ET → checkpoint at 10:31
        now = datetime(2026, 4, 14, 10, 45, tzinfo=ZoneInfo("America/New_York"))
        # Cached at 10:35 (after 10:31 checkpoint)
        fresh_ts = datetime(2026, 4, 14, 10, 35, tzinfo=ZoneInfo("America/New_York")).timestamp()
        mock_cache.return_value = {"AAPL": {"price": 200.0, "ts": fresh_ts}}

        result = get_tickers_needing_refresh([("AAPL", "USD")], now=now)
        assert result == []

    @patch("src.market_hours._get_config", return_value=SAMPLE_CONFIG)
    @patch("src.market_hours._load_price_cache")
    def test_outside_market_hours(self, mock_cache, mock_cfg):
        # Tuesday 8:00 AM ET — market not open
        now = datetime(2026, 4, 14, 8, 0, tzinfo=ZoneInfo("America/New_York"))
        mock_cache.return_value = {}

        result = get_tickers_needing_refresh([("AAPL", "USD")], now=now)
        assert result == []

    @patch("src.market_hours._get_config", return_value=SAMPLE_CONFIG)
    @patch("src.market_hours._load_price_cache")
    def test_crypto_refreshes_anytime(self, mock_cache, mock_cfg):
        # Saturday 3 AM UTC — equity closed, crypto open
        now = datetime(2026, 4, 18, 3, 15, tzinfo=ZoneInfo("UTC"))
        mock_cache.return_value = {}

        result = get_tickers_needing_refresh(
            [("AAPL", "USD"), ("BTC-USD", "USD")], now=now
        )
        assert "BTC-USD" in result
        assert "AAPL" not in result

    @patch("src.market_hours._get_config", return_value=SAMPLE_CONFIG)
    @patch("src.market_hours._load_price_cache")
    def test_no_cache_entry_triggers_refresh(self, mock_cache, mock_cfg):
        # Tuesday 10:45 AM ET, no cache at all
        now = datetime(2026, 4, 14, 10, 45, tzinfo=ZoneInfo("America/New_York"))
        mock_cache.return_value = {}

        result = get_tickers_needing_refresh([("TSLA", "USD")], now=now)
        assert "TSLA" in result

    @patch("src.market_hours._get_config", return_value=SAMPLE_CONFIG)
    @patch("src.market_hours._load_price_cache")
    def test_weekend_no_refresh(self, mock_cache, mock_cfg):
        # Saturday 12:00 PM ET
        now = datetime(2026, 4, 18, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        mock_cache.return_value = {}

        result = get_tickers_needing_refresh([("SPY", "USD")], now=now)
        assert result == []

    @patch("src.market_hours._get_config", return_value=SAMPLE_CONFIG)
    @patch("src.market_hours._load_price_cache")
    def test_mixed_portfolio(self, mock_cache, mock_cfg):
        # Tuesday 10:45 AM ET
        now = datetime(2026, 4, 14, 10, 45, tzinfo=ZoneInfo("America/New_York"))
        old_ts = datetime(2026, 4, 14, 9, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
        fresh_ts = datetime(2026, 4, 14, 10, 35, tzinfo=ZoneInfo("America/New_York")).timestamp()
        mock_cache.return_value = {
            "AAPL": {"price": 200.0, "ts": old_ts},
            "TSLA": {"price": 300.0, "ts": fresh_ts},
        }

        result = get_tickers_needing_refresh(
            [("AAPL", "USD"), ("TSLA", "USD")], now=now
        )
        assert "AAPL" in result
        assert "TSLA" not in result
