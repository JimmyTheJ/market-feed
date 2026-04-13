"""Tests for positions_loader module."""

import pytest
import yaml

from src.models import Position, PositionsFile
from src.positions_loader import load_positions, save_positions


class TestLoadPositions:
    def test_load_valid_positions(self, tmp_positions_file):
        result = load_positions(tmp_positions_file)
        assert isinstance(result, PositionsFile)
        assert len(result.positions) == 4
        assert result.positions[0].ticker == "IBIT"
        assert result.positions[0].shares == 100
        assert result.positions[0].currency == "USD"

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_positions(tmp_path / "nonexistent.yaml")

    def test_load_invalid_structure(self, tmp_path):
        filepath = tmp_path / "bad.yaml"
        filepath.write_text("some_key: value")
        with pytest.raises(ValueError, match="missing 'positions' key"):
            load_positions(filepath)

    def test_load_empty_file(self, tmp_path):
        filepath = tmp_path / "empty.yaml"
        filepath.write_text("")
        with pytest.raises(ValueError):
            load_positions(filepath)


class TestPositionValidation:
    def test_empty_ticker_rejected(self):
        with pytest.raises(ValueError, match="ticker must be non-empty"):
            Position(ticker="", shares=10)

    def test_whitespace_ticker_rejected(self):
        with pytest.raises(ValueError, match="ticker must be non-empty"):
            Position(ticker="   ", shares=10)

    def test_ticker_uppercased(self):
        p = Position(ticker="qqq", shares=50)
        assert p.ticker == "QQQ"

    def test_ticker_stripped(self):
        p = Position(ticker="  SPY  ", shares=10)
        assert p.ticker == "SPY"

    def test_negative_shares_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            Position(ticker="SPY", shares=-5)

    def test_zero_shares_allowed(self):
        p = Position(ticker="SPY", shares=0.0)
        assert p.shares == 0.0

    def test_currency_defaults_to_usd(self):
        p = Position(ticker="SPY", shares=10)
        assert p.currency == "USD"

    def test_currency_uppercased(self):
        p = Position(ticker="SPY", shares=10, currency="cad")
        assert p.currency == "CAD"

    def test_empty_currency_rejected(self):
        with pytest.raises(ValueError, match="currency must be non-empty"):
            Position(ticker="SPY", shares=10, currency="")


class TestPositionsFile:
    def test_duplicate_tickers_rejected(self):
        with pytest.raises(ValueError, match="Duplicate ticker"):
            PositionsFile(
                positions=[
                    Position(ticker="SPY", shares=10),
                    Position(ticker="SPY", shares=20),
                ]
            )

    def test_currencies_default(self):
        pf = PositionsFile(positions=[Position(ticker="SPY", shares=10)])
        assert pf.currencies == ["USD", "CAD", "BTC"]

    def test_custom_currencies(self):
        pf = PositionsFile(
            currencies=["USD", "EUR"],
            positions=[Position(ticker="SPY", shares=10)],
        )
        assert pf.currencies == ["USD", "EUR"]


class TestSavePositions:
    def test_save_and_reload(self, tmp_path, sample_positions_file):
        filepath = tmp_path / "out.yaml"
        save_positions(sample_positions_file, filepath)

        assert filepath.exists()

        reloaded = load_positions(filepath)
        assert len(reloaded.positions) == len(sample_positions_file.positions)
        for orig, loaded in zip(
            sample_positions_file.positions, reloaded.positions
        ):
            assert orig.ticker == loaded.ticker
            assert abs(orig.shares - loaded.shares) < 0.001
            assert orig.currency == loaded.currency
        assert reloaded.currencies == sample_positions_file.currencies

    def test_save_creates_parent_dirs(self, tmp_path, sample_positions_file):
        filepath = tmp_path / "deep" / "nested" / "positions.yaml"
        save_positions(sample_positions_file, filepath)
        assert filepath.exists()
