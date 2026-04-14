"""Market hours awareness and smart price refresh logic.

Determines which tickers need a price refresh based on configurable
market-hour schedules and minute-offset refresh checkpoints.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.config_loader import load_yaml_config
from src.price_service import _load_cache as _load_price_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_cached_config: dict | None = None


def _get_config(profile: str | None = None) -> dict:
    """Load the price_refresh config from settings.yaml (cached in-process)."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    settings = load_yaml_config("settings.yaml", merge_with_defaults=True, profile=profile)
    cfg = settings.get("price_refresh", {})

    # Normalise schedule day lists to ints (0=Mon .. 6=Sun)
    for sched in cfg.get("schedules", {}).values():
        sched["days"] = [int(d) for d in sched.get("days", [])]

    _cached_config = cfg
    return cfg


def reload_config() -> None:
    """Force config reload on next access (useful after settings change)."""
    global _cached_config
    _cached_config = None


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------


def _parse_time(t: str) -> tuple[int, int]:
    """Parse 'HH:MM' to (hour, minute)."""
    parts = t.split(":")
    return int(parts[0]), int(parts[1])


def is_market_open(schedule: dict, now: datetime | None = None) -> bool:
    """Check whether *now* falls within the given schedule's trading hours.

    ``schedule`` is a dict with keys: open, close, timezone, days.
    """
    tz = ZoneInfo(schedule["timezone"])
    if now is None:
        now = datetime.now(tz)
    else:
        now = now.astimezone(tz)

    if now.weekday() not in schedule["days"]:
        return False

    open_h, open_m = _parse_time(schedule["open"])
    close_h, close_m = _parse_time(schedule["close"])

    open_minutes = open_h * 60 + open_m
    close_minutes = close_h * 60 + close_m
    now_minutes = now.hour * 60 + now.minute

    # Handle 00:00-23:59 (effectively 24h)
    if close_minutes <= open_minutes:
        return True

    return open_minutes <= now_minutes < close_minutes


# ---------------------------------------------------------------------------
# Ticker → schedule mapping
# ---------------------------------------------------------------------------


def get_schedule_for_ticker(
    ticker: str,
    currency: str,
    config: dict | None = None,
) -> str:
    """Return the schedule id that applies to a given ticker/currency pair.

    Crypto currencies (by currency code or by ticker pattern like ``BTC-USD``)
    are mapped to the ``crypto`` schedule.  Everything else uses the
    ``default_schedule`` from config.
    """
    if config is None:
        config = _get_config()

    crypto_currencies = {c.upper() for c in config.get("crypto_currencies", [])}

    # Currency itself is crypto
    if currency.upper() in crypto_currencies:
        return "crypto"

    # Ticker looks like a crypto pair (e.g. BTC-USD, ETH-CAD)
    if "-" in ticker:
        base = ticker.split("-")[0].upper()
        if base in crypto_currencies:
            return "crypto"

    return config.get("default_schedule", "us_equity")


# ---------------------------------------------------------------------------
# Refresh-checkpoint logic
# ---------------------------------------------------------------------------


def _most_recent_checkpoint(offsets: list[int], now: datetime) -> datetime:
    """Return the most recent refresh checkpoint at or before *now*.

    Checkpoints are defined by minute offsets within each hour.
    For example, offsets=[1, 31] generates checkpoints at HH:01 and HH:31.
    """
    offsets = sorted(offsets)
    # Check current hour's offsets (descending) then previous hour
    for hour_delta in (0, 1):
        ref_time = now - timedelta(hours=hour_delta)
        for offset in reversed(offsets):
            candidate = ref_time.replace(minute=offset, second=0, microsecond=0)
            if hour_delta == 1:
                # For previous hour, set to that hour
                candidate = candidate.replace(hour=ref_time.hour)
            if candidate <= now:
                return candidate

    # Fallback: shouldn't reach here with valid offsets
    return now - timedelta(minutes=30)


def get_tickers_needing_refresh(
    tickers_with_currency: list[tuple[str, str]],
    profile: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Determine which tickers need a price refresh right now.

    A ticker needs refresh if:
    1. Its market schedule is currently open.
    2. Its cached price timestamp is older than the most recent refresh
       checkpoint (based on the configured minute offsets).

    Args:
        tickers_with_currency: List of (ticker, currency) pairs.
        profile: Optional profile name for config resolution.
        now: Override current time (for testing).

    Returns:
        List of ticker symbols that should be re-fetched.
    """
    config = _get_config(profile)
    schedules = config.get("schedules", {})
    offsets = config.get("offsets", [1, 31])

    if not offsets or not schedules:
        return []

    price_cache = _load_price_cache()
    need_refresh: list[str] = []

    for ticker, currency in tickers_with_currency:
        ticker_upper = ticker.upper()
        sched_id = get_schedule_for_ticker(ticker_upper, currency, config)
        sched = schedules.get(sched_id)
        if sched is None:
            continue

        tz = ZoneInfo(sched["timezone"])
        local_now = (now or datetime.now(tz)).astimezone(tz)

        if not is_market_open(sched, local_now):
            continue

        # Find the most recent checkpoint in this schedule's timezone
        checkpoint = _most_recent_checkpoint(offsets, local_now)

        # Check if cached price is older than checkpoint
        entry = price_cache.get(ticker_upper, {})
        cached_ts = entry.get("ts", 0)
        if cached_ts < checkpoint.timestamp():
            need_refresh.append(ticker_upper)

    if need_refresh:
        logger.info(
            "Smart refresh: %d ticker(s) need update: %s",
            len(need_refresh),
            ", ".join(need_refresh),
        )

    return need_refresh
