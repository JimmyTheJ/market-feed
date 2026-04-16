"""Tests for digest_writer module."""

import pytest
from datetime import date

from src.digest_writer import generate_digest
from src.models import (
    DailyPositionsSnapshot,
    EnrichedPosition,
    PortfolioSummary,
    PositionSummary,
)


@pytest.fixture
def sample_snapshot(sample_enriched_positions, run_date):
    return DailyPositionsSnapshot(
        date=run_date,
        positions=sample_enriched_positions,
    )


@pytest.fixture
def sample_portfolio_summary(run_date):
    return PortfolioSummary(
        date=run_date,
        top_themes=["bitcoin", "rates", "ai", "growth", "macro_liquidity"],
        top_signals=[
            {
                "title": "Bitcoin ETF inflows surge",
                "source": "coindesk",
                "score": 45.2,
                "top_position": "IBIT",
            },
            {
                "title": "Fed signals rate cuts",
                "source": "marketwatch_top",
                "score": 32.1,
                "top_position": "QQQ",
            },
        ],
        position_summaries=[
            PositionSummary(
                ticker="IBIT",
                weight=0.35,
                underlying="bitcoin",
                net_bias="bullish",
                key_items=["Bitcoin ETF inflows surge", "Crypto regulation clarity improves"],
                interpretation="Strong institutional demand continues to drive IBIT higher.",
                risks=["Regulatory crackdown", "Correlation spike"],
                bullish_factors=["Record inflows", "Institutional adoption"],
                bearish_factors=["Macro uncertainty"],
            ),
            PositionSummary(
                ticker="QQQ",
                weight=0.30,
                underlying="nasdaq_100",
                net_bias="mixed",
                key_items=["Fed signals rate cuts", "AI capex narrative holds"],
                interpretation="Mixed signals as rate relief helps but valuations remain stretched.",
                risks=["Valuation compression", "Rate sensitivity"],
                bullish_factors=["Rate cuts", "AI investment"],
                bearish_factors=["High valuations"],
            ),
        ],
        contrarian_views=[
            "Consensus bullish positioning may be overextended."
        ],
        what_matters=[
            "Bitcoin ETF inflows surge",
            "Fed signals rate cuts",
        ],
        what_is_noise=[
            "New social media app launches",
        ],
    )


class TestGenerateDigest:
    def test_generates_valid_markdown(self, sample_portfolio_summary, sample_snapshot):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert isinstance(digest, str)
        assert len(digest) > 100

    def test_contains_date_header(self, sample_portfolio_summary, sample_snapshot, run_date):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert f"# Daily Digest: {run_date.isoformat()}" in digest

    def test_contains_position_sections(self, sample_portfolio_summary, sample_snapshot):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert "### IBIT" in digest
        assert "### QQQ" in digest

    def test_contains_weight_info(self, sample_portfolio_summary, sample_snapshot):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        # Position summaries still contain weight percentages
        assert "35%" in digest
        assert "30%" in digest

    def test_contains_net_bias(self, sample_portfolio_summary, sample_snapshot):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert "Bullish" in digest
        assert "Mixed" in digest

    def test_contains_all_sections(self, sample_portfolio_summary, sample_snapshot):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        expected_sections = [
            "## Portfolio Overview",
            "## Top Portfolio Signals",
            "## Position Analysis",
            "## Contrarian View",
            "## What Likely Matters Today",
            "## What Is Probably Noise",
        ]
        for section in expected_sections:
            assert section in digest, f"Missing section: {section}"

    def test_contains_signals(self, sample_portfolio_summary, sample_snapshot):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert "Bitcoin ETF inflows surge" in digest
        assert "Fed signals rate cuts" in digest

    def test_contains_contrarian_view(self, sample_portfolio_summary, sample_snapshot):
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert "consensus" in digest.lower()

    def test_contains_llm_indicator_extractive(self, sample_portfolio_summary, sample_snapshot):
        sample_portfolio_summary.llm_used = False
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert "Summary method:" in digest
        assert "Extractive" in digest

    def test_contains_llm_indicator_ai(self, sample_portfolio_summary, sample_snapshot):
        sample_portfolio_summary.llm_used = True
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert "Summary method:" in digest
        assert "AI-generated" in digest

    def test_contains_per_position_llm_tag(self, sample_portfolio_summary, sample_snapshot):
        sample_portfolio_summary.position_summaries[0].llm_used = True
        sample_portfolio_summary.position_summaries[1].llm_used = False
        digest = generate_digest(sample_portfolio_summary, sample_snapshot)
        assert "AI" in digest
        assert "Extractive" in digest

    def test_am_pm_label_in_header(self, sample_snapshot, run_date):
        summary = PortfolioSummary(
            date=run_date,
            run_label="AM",
            position_summaries=[],
        )
        digest = generate_digest(summary, sample_snapshot)
        assert f"# Daily Digest: {run_date.isoformat()} AM" in digest

    def test_no_label_in_header(self, sample_snapshot, run_date):
        summary = PortfolioSummary(
            date=run_date,
            run_label="",
            position_summaries=[],
        )
        digest = generate_digest(summary, sample_snapshot)
        header_line = digest.split("\n")[0]
        assert header_line == f"# Daily Digest: {run_date.isoformat()}"
