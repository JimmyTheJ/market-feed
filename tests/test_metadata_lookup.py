"""Tests for metadata_lookup module."""

import pytest

from src.metadata_lookup import (
    _fallback_enrich,
    enrich_all_positions,
    enrich_position,
    load_metadata_registry,
)
from src.models import EnrichedPosition, Position


class TestLoadMetadataRegistry:
    def test_load_existing_registry(self, tmp_metadata_file):
        registry = load_metadata_registry(tmp_metadata_file)
        assert "IBIT" in registry
        assert "QQQ" in registry
        assert registry["IBIT"]["asset_class"] == "crypto"

    def test_load_missing_registry_returns_empty(self, tmp_path):
        registry = load_metadata_registry(tmp_path / "missing.yaml")
        assert registry == {}


class TestEnrichPosition:
    def test_enrich_known_ticker(self, sample_metadata_registry):
        pos = Position(ticker="IBIT", weight=0.35)
        result = enrich_position(pos, sample_metadata_registry)

        assert isinstance(result, EnrichedPosition)
        assert result.ticker == "IBIT"
        assert result.weight == 0.35
        assert result.instrument_type == "etf"
        assert result.asset_class == "crypto"
        assert result.underlying == "bitcoin"
        assert "bitcoin" in result.themes
        assert "btc" in result.keywords
        assert "real_yields" in result.macro_sensitivities

    def test_enrich_unknown_ticker_uses_fallback(self, sample_metadata_registry):
        pos = Position(ticker="AAPL", weight=0.10)
        result = enrich_position(pos, sample_metadata_registry)

        assert result.ticker == "AAPL"
        assert result.weight == 0.10
        assert result.instrument_type == "equity"  # fallback default

    def test_weight_preserved(self, sample_metadata_registry):
        pos = Position(ticker="QQQ", weight=0.42)
        result = enrich_position(pos, sample_metadata_registry)
        assert result.weight == 0.42


class TestFallbackEnrich:
    def test_known_etf_fallback(self):
        pos = Position(ticker="DIA", weight=0.1)
        result = _fallback_enrich(pos)
        assert result.instrument_type == "etf"
        assert result.asset_class == "equities"
        assert result.underlying == "dow_jones"

    def test_unknown_ticker_gets_generic(self):
        pos = Position(ticker="ZZZZ", weight=0.05)
        result = _fallback_enrich(pos)
        assert result.instrument_type == "equity"
        assert result.asset_class == "equities"
        assert "zzzz" in result.keywords


class TestEnrichAllPositions:
    def test_enriches_all(self, tmp_metadata_file, sample_positions):
        results = enrich_all_positions(sample_positions, tmp_metadata_file)
        assert len(results) == len(sample_positions)

        # Known tickers get rich metadata
        ibit = next(r for r in results if r.ticker == "IBIT")
        assert ibit.asset_class == "crypto"

        # Unknown tickers get fallback
        cper = next(r for r in results if r.ticker == "CPER")
        assert cper.instrument_type in ("equity", "etf", "unknown")
