"""Tests for storage module."""

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from src.models import DailyPositionsSnapshot, NormalizedArticle, ScoredArticle
from src.storage import (
    ensure_output_dir,
    write_daily_positions,
    write_digest,
    write_ranked_articles,
    write_raw_articles,
    write_run_log,
    write_summary_payload,
)


class TestEnsureOutputDir:
    def test_creates_dated_directory(self, tmp_path):
        d = date(2026, 4, 3)
        result = ensure_output_dir(tmp_path, d)
        assert result.exists()
        assert result.name == "2026-04-03-analysis"

    def test_idempotent(self, tmp_path):
        d = date(2026, 4, 3)
        result1 = ensure_output_dir(tmp_path, d)
        result2 = ensure_output_dir(tmp_path, d)
        assert result1 == result2
        assert result1.exists()


class TestWriteDailyPositions:
    def test_writes_yaml(self, tmp_path, sample_enriched_positions, run_date):
        output_dir = tmp_path / "test-analysis"
        output_dir.mkdir()

        snapshot = DailyPositionsSnapshot(
            date=run_date, positions=sample_enriched_positions
        )
        filepath = write_daily_positions(output_dir, snapshot)

        assert filepath.exists()
        assert filepath.name == f"daily-positions-{run_date.isoformat()}.yaml"

        with open(filepath) as f:
            data = yaml.safe_load(f)
        assert data["date"] == run_date.isoformat()
        assert len(data["positions"]) == 2


class TestWriteRawArticles:
    def test_writes_json(self, tmp_path, sample_articles, run_date):
        filepath = write_raw_articles(tmp_path, run_date, sample_articles)
        assert filepath.exists()

        with open(filepath) as f:
            data = json.load(f)
        assert len(data) == len(sample_articles)


class TestWriteRankedArticles:
    def test_writes_json(self, tmp_path, sample_scored_articles, run_date):
        filepath = write_ranked_articles(tmp_path, run_date, sample_scored_articles)
        assert filepath.exists()

        with open(filepath) as f:
            data = json.load(f)
        assert len(data) == len(sample_scored_articles)


class TestWriteDigest:
    def test_writes_markdown(self, tmp_path, run_date):
        content = "# Test Digest\n\nThis is a test."
        filepath = write_digest(tmp_path, run_date, content)
        assert filepath.exists()
        assert filepath.suffix == ".md"
        assert filepath.read_text() == content


class TestWriteSummaryPayload:
    def test_writes_json(self, tmp_path, run_date):
        payload = {"test": "data", "count": 42}
        filepath = write_summary_payload(tmp_path, run_date, payload)
        assert filepath.exists()

        with open(filepath) as f:
            data = json.load(f)
        assert data["test"] == "data"


class TestWriteRunLog:
    def test_writes_log(self, tmp_path, run_date):
        lines = ["[10:00:00] Starting pipeline", "[10:00:05] Done"]
        filepath = write_run_log(tmp_path, run_date, lines)
        assert filepath.exists()
        content = filepath.read_text()
        assert "Starting pipeline" in content
        assert "Done" in content
