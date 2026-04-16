"""Handle writing dated outputs and structured run artifacts."""

import json
import logging
from datetime import date
from pathlib import Path

import yaml

from .date_utils import analysis_dir_name, dated_filename
from .models import DailyPositionsSnapshot, NormalizedArticle, ScoredArticle

logger = logging.getLogger(__name__)


def ensure_output_dir(
    base_path: str | Path, run_date: date, run_label: str = ""
) -> Path:
    """Create and return the dated output directory."""
    base = Path(base_path)
    dir_name = analysis_dir_name(run_date, run_label)
    output_dir = base / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {output_dir}")
    return output_dir


def write_daily_positions(
    output_dir: Path, snapshot: DailyPositionsSnapshot, run_label: str = ""
) -> Path:
    """Write the daily enriched positions snapshot."""
    filename = dated_filename("daily-positions", snapshot.date, "yaml", run_label)
    filepath = output_dir / filename

    data = {
        "date": snapshot.date.isoformat(),
        "generated_from": snapshot.generated_from,
        "positions": [pos.model_dump() for pos in snapshot.positions],
    }

    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    logger.info(f"Wrote daily positions: {filepath}")
    return filepath


def write_raw_articles(
    output_dir: Path, run_date: date, articles: list[NormalizedArticle],
    run_label: str = "",
) -> Path:
    """Write raw normalized articles JSON."""
    filename = dated_filename("raw_articles", run_date, "json", run_label)
    filepath = output_dir / filename

    data = [a.model_dump(mode="json") for a in articles]

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Wrote {len(articles)} raw articles: {filepath}")
    return filepath


def write_ranked_articles(
    output_dir: Path, run_date: date, scored: list[ScoredArticle],
    run_label: str = "",
) -> Path:
    """Write ranked articles JSON."""
    filename = dated_filename("ranked_articles", run_date, "json", run_label)
    filepath = output_dir / filename

    data = [s.model_dump(mode="json") for s in scored]

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Wrote {len(scored)} ranked articles: {filepath}")
    return filepath


def write_digest(
    output_dir: Path, run_date: date, content: str, run_label: str = ""
) -> Path:
    """Write the Markdown digest."""
    filename = dated_filename("market_digest", run_date, "md", run_label)
    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"Wrote digest: {filepath}")
    return filepath


def write_summary_payload(
    output_dir: Path, run_date: date, payload: dict, run_label: str = ""
) -> Path:
    """Write the summary payload JSON."""
    filename = dated_filename("summary_payload", run_date, "json", run_label)
    filepath = output_dir / filename

    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info(f"Wrote summary payload: {filepath}")
    return filepath


def write_run_log(
    output_dir: Path, run_date: date, log_lines: list[str], run_label: str = ""
) -> Path:
    """Write the run log."""
    filename = dated_filename("run_log", run_date, "txt", run_label)
    filepath = output_dir / filename

    with open(filepath, "w") as f:
        f.write("\n".join(log_lines))

    logger.info(f"Wrote run log: {filepath}")
    return filepath
