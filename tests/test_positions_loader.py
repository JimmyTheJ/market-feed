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
        assert result.positions[0].weight == 0.35

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
            Position(ticker="", weight=0.5)

    def test_whitespace_ticker_rejected(self):
        with pytest.raises(ValueError, match="ticker must be non-empty"):
            Position(ticker="   ", weight=0.5)

    def test_ticker_uppercased(self):
        p = Position(ticker="qqq", weight=0.3)
        assert p.ticker == "QQQ"

    def test_ticker_stripped(self):
        p = Position(ticker="  SPY  ", weight=0.1)
        assert p.ticker == "SPY"

    def test_negative_weight_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            Position(ticker="SPY", weight=-0.5)

    def test_zero_weight_allowed(self):
        p = Position(ticker="SPY", weight=0.0)
        assert p.weight == 0.0


class TestPositionsFile:
    def test_weight_sum(self, sample_positions_file):
        assert abs(sample_positions_file.weight_sum() - 1.0) < 0.001

    def test_weight_warning_none_when_valid(self, sample_positions_file):
        assert sample_positions_file.weight_warning() is None

    def test_weight_warning_when_sum_off(self):
        pf = PositionsFile(
            positions=[
                Position(ticker="A", weight=0.5),
                Position(ticker="B", weight=0.3),
            ]
        )
        warning = pf.weight_warning()
        assert warning is not None
        assert "0.8" in warning

    def test_duplicate_tickers_rejected(self):
        with pytest.raises(ValueError, match="Duplicate ticker"):
            PositionsFile(
                positions=[
                    Position(ticker="SPY", weight=0.5),
                    Position(ticker="SPY", weight=0.3),
                ]
            )


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
            assert abs(orig.weight - loaded.weight) < 0.001

    def test_save_creates_parent_dirs(self, tmp_path, sample_positions_file):
        filepath = tmp_path / "deep" / "nested" / "positions.yaml"
        save_positions(sample_positions_file, filepath)
        assert filepath.exists()
