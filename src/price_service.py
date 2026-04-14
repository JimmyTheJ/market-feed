"""Ticker price fetching with local JSON caching.

Uses yfinance for stock/ETF/crypto prices. Cached prices are valid for up to
24 hours to avoid excessive API calls while keeping data reasonably fresh.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = Path("data/cache/price_cache.json")
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours


def _load_cache() -> dict:
    """Load the price cache from disk."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt price cache, starting fresh")
    return {}


def _save_cache(cache: dict) -> None:
    """Persist the price cache to disk."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _is_fresh(entry: dict) -> bool:
    """Check if a cache entry is still within the max age window."""
    return (time.time() - entry.get("ts", 0)) < CACHE_MAX_AGE_SECONDS


def get_prices(tickers: list[str]) -> dict[str, float | None]:
    """Fetch current prices for a list of tickers.

    Returns a dict mapping ticker -> price (or None if unavailable).
    Uses a local JSON cache; only fetches from yfinance for stale/missing entries.
    """
    if not tickers:
        return {}

    cache = _load_cache()
    result: dict[str, float | None] = {}
    stale: list[str] = []

    for t in tickers:
        key = t.upper()
        entry = cache.get(key)
        if entry and _is_fresh(entry):
            result[key] = entry["price"]
        else:
            stale.append(key)

    if stale:
        fetched = _fetch_from_yfinance(stale)
        now = time.time()
        for ticker, price in fetched.items():
            result[ticker] = price
            if price is not None:
                cache[ticker] = {"price": price, "ts": now}
        _save_cache(cache)

    return result


def _fetch_from_yfinance(tickers: list[str]) -> dict[str, float | None]:
    """Fetch prices from yfinance for the given tickers."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance is not installed – cannot fetch prices")
        return {t: None for t in tickers}

    prices: dict[str, float | None] = {}
    try:
        data = yf.download(
            tickers,
            period="5d",
            progress=False,
            auto_adjust=True,
            threads=True,
        )

        if data.empty:
            logger.warning("yfinance returned empty data for %s", tickers)
            return {t: None for t in tickers}

        close = data["Close"]
        if len(tickers) == 1:
            # Single ticker returns a Series, not a DataFrame
            last_valid = close.dropna()
            price = float(last_valid.iloc[-1]) if not last_valid.empty else None
            prices[tickers[0]] = price
        else:
            for t in tickers:
                if t in close.columns:
                    col = close[t].dropna()
                    prices[t] = float(col.iloc[-1]) if not col.empty else None
                else:
                    prices[t] = None

    except Exception as e:
        logger.error("yfinance download failed: %s", e)
        for t in tickers:
            prices.setdefault(t, None)

    for t in tickers:
        if prices.get(t) is not None:
            logger.info("Fetched price for %s: %.4f", t, prices[t])
        else:
            logger.warning("Could not fetch price for %s", t)

    return prices


def invalidate_cache(ticker: str | None = None) -> None:
    """Remove a specific ticker or the entire cache."""
    if ticker is None:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
        return
    cache = _load_cache()
    cache.pop(ticker.upper(), None)
    _save_cache(cache)


def get_option_price(
    ticker: str,
    expiration: str,
    option_type: str,
    strike: float,
) -> float | None:
    """Fetch the last traded premium for a specific option contract.

    Args:
        ticker: Underlying symbol (e.g. "AAPL").
        expiration: Expiry date as "YYYY-MM-DD".
        option_type: "CALL" or "PUT".
        strike: Strike price.

    Returns:
        Last price (premium) or None if unavailable.
    """
    cache_key = f"{ticker.upper()}_{option_type}_{strike}_{expiration}"
    cache = _load_cache()
    entry = cache.get(cache_key)
    if entry and _is_fresh(entry) and entry.get("price") is not None:
        return entry["price"]

    try:
        import yfinance as yf

        t = yf.Ticker(ticker.upper())
        chain = t.option_chain(expiration)
        df = chain.calls if option_type.upper() == "CALL" else chain.puts

        # Find the matching strike row
        row = df[df["strike"] == strike]
        if row.empty:
            # Try closest strike
            closest_idx = (df["strike"] - strike).abs().idxmin()
            row = df.loc[[closest_idx]]
            if abs(row.iloc[0]["strike"] - strike) > 1.0:
                logger.warning(
                    "No matching strike %.2f for %s %s exp %s",
                    strike, ticker, option_type, expiration,
                )
                return None

        premium = float(row.iloc[0]["lastPrice"])
        cache[cache_key] = {"price": premium, "ts": time.time()}
        _save_cache(cache)
        logger.info(
            "Fetched option premium for %s %s %.0f %s: %.4f",
            ticker, option_type, strike, expiration, premium,
        )
        return premium

    except Exception as e:
        logger.warning("Option price lookup failed for %s: %s", cache_key, e)
        return None
