"""Foreign-exchange rate service with local JSON caching.

Uses yfinance currency pairs (e.g. USDCAD=X) to fetch exchange rates.
Cached rates are valid for up to 24 hours.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = Path("data/cache/forex_cache.json")
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt forex cache, starting fresh")
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _is_fresh(entry: dict) -> bool:
    return (time.time() - entry.get("ts", 0)) < CACHE_MAX_AGE_SECONDS


def _pair_symbol(base: str, quote: str) -> str:
    """Build the yfinance ticker for a currency pair.

    Standard forex pairs use ``BASEQUOTE=X`` (e.g. ``USDCAD=X``).
    Crypto pairs use ``BASE-QUOTE`` (e.g. ``BTC-USD``).
    """
    base, quote = base.upper(), quote.upper()
    cryptos = {"BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "DOT", "AVAX", "LINK", "MATIC"}
    if base in cryptos:
        return f"{base}-{quote}"
    if quote in cryptos:
        # Inverse: we fetch CRYPTO-BASE then invert
        return f"{quote}-{base}"
    return f"{base}{quote}=X"


def get_rate(base: str, quote: str) -> float | None:
    """Get the exchange rate to convert 1 unit of *base* into *quote*.

    Returns None if the rate cannot be determined.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0

    cache_key = f"{base}_{quote}"
    cache = _load_cache()
    entry = cache.get(cache_key)
    if entry and _is_fresh(entry):
        return entry["rate"]

    rate = _fetch_rate(base, quote)
    if rate is not None:
        cache[cache_key] = {"rate": rate, "ts": time.time()}
        _save_cache(cache)

    return rate


def get_rates_to(display_currency: str, currencies: list[str]) -> dict[str, float]:
    """Return rates to convert each currency in *currencies* into *display_currency*.

    Returns {currency: rate} where ``value_display = value_native * rate``.
    Missing/failed pairs default to 1.0 with a warning.
    """
    result: dict[str, float] = {}
    for cur in currencies:
        cur = cur.upper()
        if cur == display_currency.upper():
            result[cur] = 1.0
            continue
        rate = get_rate(cur, display_currency)
        if rate is not None:
            result[cur] = rate
        else:
            logger.warning(
                "Could not get forex rate %s→%s, defaulting to 1.0", cur, display_currency
            )
            result[cur] = 1.0
    return result


def _fetch_rate(base: str, quote: str) -> float | None:
    """Fetch a single exchange rate from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance is not installed – cannot fetch forex rates")
        return None

    cryptos = {"BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "DOT", "AVAX", "LINK", "MATIC"}
    invert = False

    if quote in cryptos and base not in cryptos:
        # e.g. USD→BTC: fetch BTC-USD then invert
        symbol = f"{quote}-{base}"
        invert = True
    elif base in cryptos:
        symbol = f"{base}-{quote}"
    else:
        symbol = f"{base}{quote}=X"

    try:
        data = yf.download(symbol, period="5d", progress=False, auto_adjust=True)
        if data.empty:
            logger.warning("yfinance returned empty forex data for %s", symbol)
            return None
        close = data["Close"]
        # yfinance may return a DataFrame with MultiIndex columns even for a single ticker
        import pandas as pd

        if isinstance(close, pd.DataFrame):
            if symbol in close.columns:
                close = close[symbol].dropna()
            else:
                close = close.iloc[:, 0].dropna()
        else:
            close = close.dropna()
        if close.empty:
            return None
        rate = float(close.iloc[-1])
        if invert and rate != 0:
            rate = 1.0 / rate
        logger.info("Forex %s→%s = %.6f (via %s)", base, quote, rate, symbol)
        return rate
    except Exception as e:
        logger.error("Forex fetch failed for %s: %s", symbol, e)
        return None


def invalidate_cache(pair: str | None = None) -> None:
    """Remove a specific pair key (e.g. 'USD_CAD') or the entire cache."""
    if pair is None:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
        return
    cache = _load_cache()
    cache.pop(pair.upper(), None)
    _save_cache(cache)
