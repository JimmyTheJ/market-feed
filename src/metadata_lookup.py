"""Metadata lookup for ticker enrichment.

Three-tier strategy:
1. Local metadata registry (data/metadata/ticker_metadata.yaml)
2. External API lookup (future - stubbed)
3. Rule-based fallback inference
"""

import logging
from pathlib import Path

import yaml

from .models import EnrichedPosition, Position

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = "data/metadata/ticker_metadata.yaml"


def load_metadata_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> dict:
    """Load the local metadata registry."""
    path = Path(path)
    if not path.exists():
        logger.warning(f"Metadata registry not found: {path}")
        return {}

    with open(path) as f:
        data = yaml.safe_load(f)

    return data or {}


def enrich_position(position: Position, registry: dict) -> EnrichedPosition:
    """Enrich a single position using the registry and fallback rules."""
    ticker = position.ticker.upper()

    # Tier 1: Local registry lookup
    metadata = registry.get(ticker, {})

    if metadata:
        logger.info(f"Found local metadata for {ticker}")
        safe_fields = {
            k: v for k, v in metadata.items() if k not in ("ticker", "weight")
        }
        return EnrichedPosition(ticker=ticker, weight=position.weight, **safe_fields)

    # Tier 2: External API (stubbed for future implementation)
    logger.info(f"No local metadata for {ticker}, using fallback rules")

    # Tier 3: Rule-based fallback
    return _fallback_enrich(position)


def _fallback_enrich(position: Position) -> EnrichedPosition:
    """Apply rule-based fallback enrichment for unknown tickers."""
    ticker = position.ticker.upper()

    enriched = EnrichedPosition(ticker=ticker, weight=position.weight)

    # Common ETF fallback patterns
    known_etfs = {
        "VOO": ("etf", "equities", "broad_market", "sp500"),
        "VTI": ("etf", "equities", "broad_market", "total_us_market"),
        "IWM": ("etf", "equities", "small_cap", "russell_2000"),
        "DIA": ("etf", "equities", "broad_market", "dow_jones"),
        "ARKK": ("etf", "equities", "innovation", "disruptive_tech"),
        "XLF": ("etf", "equities", "financials", "financial_select"),
        "XLK": ("etf", "equities", "technology", "tech_select"),
        "VEU": ("etf", "equities", "international", "ftse_all_world_ex_us"),
        "BND": ("etf", "fixed_income", "bonds", "total_bond_market"),
        "HYG": ("etf", "fixed_income", "high_yield", "high_yield_corporate"),
        "BITO": ("etf", "crypto", "digital_assets", "bitcoin_futures"),
    }

    if ticker in known_etfs:
        itype, aclass, sector, underlying = known_etfs[ticker]
        enriched.instrument_type = itype
        enriched.asset_class = aclass
        enriched.sector = sector
        enriched.underlying = underlying
        enriched.themes = [underlying.replace("_", " "), sector.replace("_", " ")]
        enriched.keywords = [ticker.lower(), underlying.replace("_", " ")]
    else:
        # Generic fallback: assume equity
        enriched.instrument_type = "equity"
        enriched.asset_class = "equities"
        enriched.themes = [ticker.lower()]
        enriched.keywords = [ticker.lower()]

    return enriched


def enrich_all_positions(
    positions: list[Position],
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> list[EnrichedPosition]:
    """Enrich all positions."""
    registry = load_metadata_registry(registry_path)
    return [enrich_position(pos, registry) for pos in positions]
