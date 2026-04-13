"""Integration tests for the complete pipeline."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.main import run_pipeline


def _mock_fetch_all_sources(sources, timeout=30.0):
    """Return mock articles instead of making HTTP requests."""
    return [
        {
            "title": "Bitcoin ETF inflows hit all-time high as institutional demand surges",
            "url": "https://example.com/btc-inflows",
            "published_at": None,
            "content": "BlackRock's IBIT led the charge with record spot bitcoin ETF inflows. "
                       "Institutional crypto adoption accelerated sharply this week.",
            "source_name": "coindesk",
            "category": "crypto",
            "priority": 9,
        },
        {
            "title": "Fed holds rates steady, signals potential cuts in September",
            "url": "https://example.com/fed-rates",
            "published_at": None,
            "content": "The Federal Reserve kept rates unchanged but the dot plot shifted, "
                       "signaling two rate cuts by year end. Nasdaq and S&P 500 rallied. "
                       "QQQ and growth stocks led the move higher on falling yields.",
            "source_name": "marketwatch_top",
            "category": "macro",
            "priority": 9,
        },
        {
            "title": "Gold prices surge past $2,500 on geopolitical tensions",
            "url": "https://example.com/gold-surge",
            "published_at": None,
            "content": "Gold bullion broke through $2,500 per troy ounce as safe haven demand "
                       "intensified. Central bank buying and dedollarization trends support "
                       "the precious metals rally. GLD and SLV saw strong inflows.",
            "source_name": "kitco_news",
            "category": "commodities",
            "priority": 9,
        },
        {
            "title": "Tesla Cybertruck deliveries exceed expectations in Q2",
            "url": "https://example.com/tsla-q2",
            "published_at": None,
            "content": "Tesla reported strong Cybertruck delivery numbers for Q2 2026. "
                       "TSLA shares rose 4% on the news. Elon Musk indicated FSD "
                       "regulatory approval is imminent in several markets.",
            "source_name": "electrek",
            "category": "ev_tesla",
            "priority": 8,
        },
        {
            "title": "Natural gas storage report shows surprise draw",
            "url": "https://example.com/natgas-storage",
            "published_at": None,
            "content": "The EIA weekly storage report showed an unexpected draw in natural gas "
                       "inventories. UNG surged 6% as traders priced in tighter supply. "
                       "LNG export capacity expansion continues to support Henry Hub prices.",
            "source_name": "oilprice",
            "category": "energy",
            "priority": 8,
        },
        {
            "title": "European markets hit record highs as ECB signals easing",
            "url": "https://example.com/europe-highs",
            "published_at": None,
            "content": "The STOXX 600 and DAX hit all-time highs after ECB signaled further "
                       "rate cuts. EFA and international developed markets rallied. "
                       "The euro weakened against the dollar on the dovish pivot.",
            "source_name": "bbc_business",
            "category": "international",
            "priority": 7,
        },
        {
            "title": "Treasury yields plunge as bond market rallies",
            "url": "https://example.com/tlt-rally",
            "published_at": None,
            "content": "Long-term treasury yields dropped sharply, boosting TLT prices. "
                       "The 20-year bond yield fell 15 basis points. Flight to safety "
                       "and falling inflation expectations drove the move.",
            "source_name": "marketwatch_bonds",
            "category": "fixed_income",
            "priority": 8,
        },
        {
            "title": "Commodity index rises on broad supply concerns",
            "url": "https://example.com/dbc-supply",
            "published_at": None,
            "content": "DBC tracking fund rose as commodities rallied broadly. Oil, copper, "
                       "and agricultural commodities all gained. China demand recovery "
                       "signals and supply disruptions contributed to the move.",
            "source_name": "marketwatch_top",
            "category": "macro",
            "priority": 9,
        },
        {
            "title": "New social media startup raises $50M Series A",
            "url": "https://example.com/social-startup",
            "published_at": None,
            "content": "A social media startup focused on Gen Z raised a Series A round.",
            "source_name": "techcrunch",
            "category": "technology",
            "priority": 7,
        },
    ]


class TestPipelineIntegration:
    @patch("src.main.get_rates_to", return_value={"USD": 1.0})
    @patch("src.main.get_prices", return_value={
        "IBIT": 50.0, "QQQ": 400.0, "TSLA": 200.0, "GLD": 180.0,
        "TLT": 90.0, "UNG": 10.0, "SPY": 450.0, "EFA": 70.0,
        "XLE": 85.0, "SLV": 25.0, "DBC": 20.0, "ETHA": 30.0,
    })
    @patch("src.main.fetch_all_sources", side_effect=_mock_fetch_all_sources)
    def test_full_pipeline_run(self, mock_fetch, mock_prices, mock_forex, tmp_path):
        """Test the complete pipeline end-to-end with mock data."""
        # Set up config paths within tmp
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        data_dir = tmp_path / "data" / "metadata"
        data_dir.mkdir(parents=True)
        output_dir = tmp_path / "output"

        # Write positions
        positions_data = {
            "currencies": ["USD", "CAD", "BTC"],
            "positions": [
                {"ticker": "IBIT", "shares": 100, "currency": "USD"},
                {"ticker": "QQQ", "shares": 50, "currency": "USD"},
                {"ticker": "TSLA", "shares": 25, "currency": "USD"},
                {"ticker": "GLD", "shares": 30, "currency": "USD"},
                {"ticker": "TLT", "shares": 50, "currency": "USD"},
                {"ticker": "UNG", "shares": 200, "currency": "USD"},
                {"ticker": "SPY", "shares": 20, "currency": "USD"},
                {"ticker": "EFA", "shares": 60, "currency": "USD"},
                {"ticker": "XLE", "shares": 40, "currency": "USD"},
                {"ticker": "SLV", "shares": 100, "currency": "USD"},
                {"ticker": "DBC", "shares": 75, "currency": "USD"},
                {"ticker": "ETHA", "shares": 50, "currency": "USD"},
            ]
        }
        with open(config_dir / "positions.yaml", "w") as f:
            yaml.dump(positions_data, f)

        # Write sources
        sources_data = {
            "rss": [
                {"name": "coindesk", "url": "https://mock.feed/crypto", "category": "crypto", "priority": 9},
                {"name": "marketwatch_top", "url": "https://mock.feed/market", "category": "macro", "priority": 9},
            ]
        }
        with open(config_dir / "sources.yaml", "w") as f:
            yaml.dump(sources_data, f)

        # Write metadata registry
        metadata = {
            "IBIT": {
                "instrument_type": "etf",
                "asset_class": "crypto",
                "sector": "digital_assets",
                "underlying": "bitcoin",
                "themes": ["bitcoin", "crypto_flows", "macro_liquidity"],
                "keywords": ["bitcoin", "btc", "spot bitcoin etf", "etf inflows", "blackrock", "ibit"],
                "macro_sensitivities": ["real_yields", "usd_liquidity"],
                "related_terms": ["halving", "miners"],
            },
            "QQQ": {
                "instrument_type": "etf",
                "asset_class": "equities",
                "sector": "technology",
                "underlying": "nasdaq_100",
                "themes": ["mega_cap_tech", "rates", "ai", "growth"],
                "keywords": ["nasdaq", "qqq", "yields", "fed", "semiconductors"],
                "macro_sensitivities": ["duration", "policy_rates"],
                "related_terms": ["magnificent_7", "valuation"],
            },
            "GLD": {
                "instrument_type": "etf",
                "asset_class": "commodities",
                "sector": "precious_metals",
                "underlying": "gold",
                "themes": ["gold", "safe_haven", "inflation_hedge"],
                "keywords": ["gold", "gld", "bullion", "precious metals", "safe haven"],
                "macro_sensitivities": ["real_yields", "dollar_strength"],
                "related_terms": ["troy ounce", "comex"],
            },
            "TSLA": {
                "instrument_type": "equity",
                "asset_class": "equities",
                "sector": "consumer_discretionary",
                "underlying": "tesla",
                "themes": ["ev", "autonomous_driving", "energy_storage"],
                "keywords": ["tesla", "tsla", "elon musk", "cybertruck", "fsd"],
                "macro_sensitivities": ["consumer_spending", "interest_rates"],
                "related_terms": ["gigafactory", "supercharger"],
            },
            "TLT": {
                "instrument_type": "etf",
                "asset_class": "fixed_income",
                "sector": "treasury",
                "underlying": "long_term_treasury",
                "themes": ["bonds", "yields", "fed_policy"],
                "keywords": ["treasury", "tlt", "bonds", "yields", "20 year"],
                "macro_sensitivities": ["inflation", "fed_funds_rate"],
                "related_terms": ["yield curve", "duration risk"],
            },
            "UNG": {
                "instrument_type": "etf",
                "asset_class": "commodities",
                "sector": "energy_commodities",
                "underlying": "natural_gas",
                "themes": ["natural_gas", "energy", "weather"],
                "keywords": ["natural gas", "ung", "henry hub", "gas storage", "lng"],
                "macro_sensitivities": ["weather_patterns", "storage_levels"],
                "related_terms": ["eia storage report", "freeport lng"],
            },
            "SPY": {
                "instrument_type": "etf",
                "asset_class": "equities",
                "sector": "broad_market",
                "underlying": "sp500",
                "themes": ["us_equities", "large_cap", "earnings"],
                "keywords": ["s&p 500", "spy", "sp500", "large cap"],
                "macro_sensitivities": ["earnings_growth", "interest_rates"],
                "related_terms": ["magnificent 7", "market breadth"],
            },
            "EFA": {
                "instrument_type": "etf",
                "asset_class": "equities",
                "sector": "international",
                "underlying": "msci_eafe",
                "themes": ["international", "europe", "japan"],
                "keywords": ["international stocks", "efa", "eafe", "europe"],
                "macro_sensitivities": ["global_growth", "currency_moves"],
                "related_terms": ["euro", "yen", "dax", "nikkei"],
            },
            "XLE": {
                "instrument_type": "etf",
                "asset_class": "equities",
                "sector": "energy",
                "underlying": "energy_select_sector",
                "themes": ["oil", "natural_gas", "energy_transition"],
                "keywords": ["energy", "oil", "crude", "natural gas", "xle"],
                "macro_sensitivities": ["oil_prices", "geopolitics"],
                "related_terms": ["upstream", "downstream", "shale"],
            },
            "SLV": {
                "instrument_type": "etf",
                "asset_class": "commodities",
                "sector": "precious_metals",
                "underlying": "silver",
                "themes": ["silver", "industrial_metals", "green_energy"],
                "keywords": ["silver", "slv", "silver price", "precious metals"],
                "macro_sensitivities": ["industrial_demand", "dollar_strength"],
                "related_terms": ["photovoltaic", "gold-silver ratio"],
            },
            "DBC": {
                "instrument_type": "etf",
                "asset_class": "commodities",
                "sector": "broad_commodities",
                "underlying": "commodity_index",
                "themes": ["commodities", "inflation", "global_growth"],
                "keywords": ["commodities", "dbc", "commodity index", "oil", "copper"],
                "macro_sensitivities": ["global_growth", "dollar_strength"],
                "related_terms": ["contango", "backwardation"],
            },
            "ETHA": {
                "instrument_type": "etf",
                "asset_class": "crypto",
                "sector": "digital_assets",
                "underlying": "ethereum",
                "themes": ["ethereum", "defi", "smart_contracts"],
                "keywords": ["ethereum", "eth", "defi", "staking"],
                "macro_sensitivities": ["risk_appetite", "tech_sentiment"],
                "related_terms": ["gas fees", "merge", "proof of stake"],
            },
        }
        with open(data_dir / "ticker_metadata.yaml", "w") as f:
            yaml.dump(metadata, f)

        # Run the pipeline
        run_date = date(2026, 4, 3)
        result = run_pipeline(
            run_date=run_date,
            positions_path=str(config_dir / "positions.yaml"),
            sources_path=str(config_dir / "sources.yaml"),
            metadata_path=str(data_dir / "ticker_metadata.yaml"),
            output_base=str(output_dir),
            use_ollama=False,
        )

        # Verify result dict
        assert result["date"] == "2026-04-03"
        assert result["positions_count"] == 12
        assert result["articles_normalized"] > 0
        assert result["articles_scored"] > 0
        assert result["elapsed_seconds"] >= 0

        # Verify output directory and files exist
        analysis_dir = output_dir / "2026-04-03-analysis"
        assert analysis_dir.exists()

        expected_files = [
            "daily-positions-2026-04-03.yaml",
            "raw_articles-2026-04-03.json",
            "ranked_articles-2026-04-03.json",
            "market_digest-2026-04-03.md",
            "summary_payload-2026-04-03.json",
            "run_log-2026-04-03.txt",
        ]
        for filename in expected_files:
            filepath = analysis_dir / filename
            assert filepath.exists(), f"Missing output file: {filename}"

        # Verify digest content
        digest_content = (analysis_dir / "market_digest-2026-04-03.md").read_text()
        assert "# Market Digest - 2026-04-03" in digest_content
        assert "## Position Analysis" in digest_content
        assert "IBIT" in digest_content
        assert "QQQ" in digest_content

        # Verify daily positions snapshot
        with open(analysis_dir / "daily-positions-2026-04-03.yaml") as f:
            pos_data = yaml.safe_load(f)
        assert pos_data["date"] == "2026-04-03"
        assert len(pos_data["positions"]) == 12

        # Verify ranked articles contain scoring
        with open(analysis_dir / "ranked_articles-2026-04-03.json") as f:
            ranked = json.load(f)
        assert len(ranked) > 0
        # Top article should have a non-zero score
        assert ranked[0]["portfolio_score"] > 0

        # Verify relevance: bitcoin article should score higher for IBIT
        btc_articles = [
            a for a in ranked
            if "bitcoin" in a["article"]["title"].lower()
        ]
        if btc_articles:
            assert btc_articles[0]["position_scores"]["IBIT"] > 0

    @patch("src.main.get_rates_to", return_value={"USD": 1.0})
    @patch("src.main.get_prices", return_value={"SPY": 450.0})
    @patch("src.main.fetch_all_sources", return_value=[])
    def test_pipeline_with_no_articles(self, mock_fetch, mock_prices, mock_forex, tmp_path):
        """Pipeline should complete gracefully even with no articles."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        data_dir = tmp_path / "data" / "metadata"
        data_dir.mkdir(parents=True)

        positions_data = {
            "currencies": ["USD", "CAD", "BTC"],
            "positions": [{"ticker": "SPY", "shares": 100, "currency": "USD"}],
        }
        with open(config_dir / "positions.yaml", "w") as f:
            yaml.dump(positions_data, f)

        sources_data = {"rss": []}
        with open(config_dir / "sources.yaml", "w") as f:
            yaml.dump(sources_data, f)

        with open(data_dir / "ticker_metadata.yaml", "w") as f:
            yaml.dump({}, f)

        result = run_pipeline(
            run_date=date(2026, 4, 3),
            positions_path=str(config_dir / "positions.yaml"),
            sources_path=str(config_dir / "sources.yaml"),
            metadata_path=str(data_dir / "ticker_metadata.yaml"),
            output_base=str(tmp_path / "output"),
            use_ollama=False,
        )

        assert result["articles_normalized"] == 0
        assert result["articles_scored"] == 0

        # Digest should still be generated
        digest_path = (
            tmp_path / "output" / "2026-04-03-analysis" / "market_digest-2026-04-03.md"
        )
        assert digest_path.exists()
