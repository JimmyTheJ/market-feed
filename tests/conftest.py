"""Shared test fixtures for the market pipeline test suite."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from src.models import (
    DailyPositionsSnapshot,
    EnrichedPosition,
    NormalizedArticle,
    PortfolioSummary,
    Position,
    PositionSummary,
    PositionsFile,
    ScoredArticle,
)


@pytest.fixture
def sample_positions_data():
    """Raw positions dict matching positions.yaml format."""
    return {
        "currencies": ["USD", "CAD", "BTC"],
        "positions": [
            {"ticker": "IBIT", "shares": 100, "currency": "USD"},
            {"ticker": "QQQ", "shares": 50, "currency": "USD"},
            {"ticker": "CPER", "shares": 75, "currency": "USD"},
            {"ticker": "UNG", "shares": 200, "currency": "USD"},
        ],
    }


@pytest.fixture
def sample_positions_file(sample_positions_data):
    """A validated PositionsFile."""
    return PositionsFile(**sample_positions_data)


@pytest.fixture
def sample_positions(sample_positions_file):
    """List of Position objects."""
    return sample_positions_file.positions


@pytest.fixture
def sample_metadata_registry():
    """A small local metadata registry."""
    return {
        "IBIT": {
            "instrument_type": "etf",
            "asset_class": "crypto",
            "sector": "digital_assets",
            "underlying": "bitcoin",
            "themes": ["bitcoin", "crypto_flows", "macro_liquidity", "risk_on"],
            "keywords": ["bitcoin", "btc", "spot bitcoin etf", "etf inflows", "blackrock"],
            "macro_sensitivities": ["real_yields", "usd_liquidity", "risk_appetite"],
            "related_terms": ["halving", "miners", "custody", "on-chain"],
            "risk_factors": ["regulatory_crackdown", "exchange_failure"],
            "region": "us",
            "currency": "usd",
        },
        "QQQ": {
            "instrument_type": "etf",
            "asset_class": "equities",
            "sector": "technology",
            "underlying": "nasdaq_100",
            "themes": ["mega_cap_tech", "rates", "ai", "growth"],
            "keywords": ["nasdaq", "qqq", "yields", "fed", "semiconductors", "ai capex"],
            "macro_sensitivities": ["duration", "policy_rates", "earnings_growth"],
            "related_terms": ["magnificent_7", "valuation", "guidance"],
            "region": "us",
            "currency": "usd",
        },
    }


@pytest.fixture
def sample_enriched_positions():
    """Pre-enriched positions for testing."""
    return [
        EnrichedPosition(
            ticker="IBIT",
            weight=0.35,
            instrument_type="etf",
            asset_class="crypto",
            sector="digital_assets",
            underlying="bitcoin",
            themes=["bitcoin", "crypto_flows", "macro_liquidity"],
            keywords=["bitcoin", "btc", "spot bitcoin etf", "etf inflows", "blackrock"],
            macro_sensitivities=["real_yields", "usd_liquidity", "risk_appetite"],
            related_terms=["halving", "miners", "custody"],
        ),
        EnrichedPosition(
            ticker="QQQ",
            weight=0.30,
            instrument_type="etf",
            asset_class="equities",
            sector="technology",
            underlying="nasdaq_100",
            themes=["mega_cap_tech", "rates", "ai", "growth"],
            keywords=["nasdaq", "qqq", "yields", "fed", "semiconductors"],
            macro_sensitivities=["duration", "policy_rates", "earnings_growth"],
            related_terms=["magnificent_7", "valuation"],
        ),
    ]


@pytest.fixture
def sample_articles():
    """Sample NormalizedArticle objects for testing."""
    return [
        NormalizedArticle(
            id="article-btc-1",
            source="coindesk",
            title="Bitcoin ETF inflows hit record as BlackRock IBIT leads",
            url="https://example.com/btc-etf",
            published_at=datetime.now(timezone.utc),
            content="Spot bitcoin ETF inflows reached new highs today with BlackRock's IBIT "
                    "capturing the largest share. Institutional demand continues to accelerate "
                    "as crypto regulation clarity improves.",
            tokens=["bitcoin", "etf", "inflows", "record", "blackrock", "ibit", "leads",
                    "spot", "reached", "highs", "institutional", "demand", "crypto",
                    "regulation", "clarity"],
            entities=["BlackRock", "Bitcoin"],
            category="crypto",
        ),
        NormalizedArticle(
            id="article-fed-1",
            source="marketwatch_top",
            title="Fed signals rate cuts as yields drop across the curve",
            url="https://example.com/fed-rates",
            published_at=datetime.now(timezone.utc),
            content="The Federal Reserve signaled potential rate cuts in upcoming meetings, "
                    "sending yields lower across the treasury curve. Nasdaq futures jumped "
                    "on the news as growth stocks rallied.",
            tokens=["fed", "signals", "rate", "cuts", "yields", "drop", "curve",
                    "federal", "reserve", "potential", "treasury", "nasdaq", "futures",
                    "jumped", "growth", "stocks", "rallied"],
            entities=["Federal Reserve", "Fed", "Nasdaq"],
            category="macro",
        ),
        NormalizedArticle(
            id="article-noise-1",
            source="techcrunch",
            title="New social media app launches with AI features",
            url="https://example.com/social-app",
            published_at=datetime.now(timezone.utc),
            content="A startup launched a new social media platform powered by AI. "
                    "The app targets Gen Z users with short-form video content.",
            tokens=["social", "media", "app", "launches", "features", "startup",
                    "platform", "powered", "targets", "gen", "users", "short",
                    "form", "video", "content"],
            entities=[],
            category="technology",
        ),
    ]


@pytest.fixture
def sample_scored_articles(sample_articles, sample_enriched_positions):
    """Pre-scored articles."""
    from src.scoring import score_article

    return [
        score_article(a, sample_enriched_positions, 8) for a in sample_articles
    ]


@pytest.fixture
def run_date():
    """A fixed test date."""
    return date(2026, 4, 3)


@pytest.fixture
def tmp_positions_file(tmp_path, sample_positions_data):
    """Write a temporary positions.yaml and return its path."""
    filepath = tmp_path / "positions.yaml"
    with open(filepath, "w") as f:
        yaml.dump(sample_positions_data, f)
    return filepath


@pytest.fixture
def tmp_metadata_file(tmp_path, sample_metadata_registry):
    """Write a temporary ticker_metadata.yaml and return its path."""
    filepath = tmp_path / "ticker_metadata.yaml"
    with open(filepath, "w") as f:
        yaml.dump(sample_metadata_registry, f)
    return filepath
