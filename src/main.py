"""Main pipeline orchestrator.

Coordinates the complete daily market intelligence pipeline:
1. Load positions
2. Enrich with metadata
3. Fetch market content
4. Normalize articles
5. Score and rank by relevance
6. Summarize
7. Write dated digest and artifacts
"""

import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from .config_loader import load_yaml_config, resolve_config_path, resolve_data_path
from .date_utils import today_date
from .digest_writer import generate_digest
from .ingestion import fetch_all_sources, load_sources
from .metadata_lookup import enrich_all_positions
from .models import DailyPositionsSnapshot
from .normalization import normalize_all
from .positions_loader import load_positions
from .scoring import score_and_rank
from .storage import (
    ensure_output_dir,
    write_daily_positions,
    write_digest,
    write_ranked_articles,
    write_raw_articles,
    write_run_log,
    write_summary_payload,
)
from .summarizer import generate_portfolio_summary

load_dotenv()

logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging for the pipeline."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def run_pipeline(
    run_date: date | None = None,
    positions_path: str | None = None,
    sources_path: str | None = None,
    metadata_path: str | None = None,
    output_base: str = "output",
    use_ollama: bool = True,
) -> dict:
    """Execute the complete market pipeline.

    Paths default to the config_loader resolution (user override → default).
    Returns a dict with run statistics.
    """
    start_time = time.time()
    run_date = run_date or today_date()
    log_lines: list[str] = []

    def log(msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        log_lines.append(entry)
        logger.info(msg)

    log(f"=== Market Pipeline Run: {run_date.isoformat()} ===")

    # Resolve paths via config_loader if not explicitly provided
    if not positions_path:
        positions_path = str(resolve_config_path("positions.yaml"))
    if not sources_path:
        sources_path = str(resolve_config_path("sources.yaml"))
    if not metadata_path:
        metadata_path = str(resolve_data_path("metadata/ticker_metadata.yaml"))

    # Step 1: Create output directory
    output_dir = ensure_output_dir(output_base, run_date)
    log(f"Output directory: {output_dir}")

    # Step 2: Load positions
    log("Loading positions...")
    positions_file = load_positions(positions_path)
    log(f"Loaded {len(positions_file.positions)} positions")
    warning = positions_file.weight_warning()
    if warning:
        log(f"WARNING: {warning}")

    # Step 3: Enrich positions
    log("Enriching positions with metadata...")
    enriched = enrich_all_positions(positions_file.positions, metadata_path)
    log(f"Enriched {len(enriched)} positions")

    # Step 4: Write daily positions snapshot
    snapshot = DailyPositionsSnapshot(date=run_date, positions=enriched)
    write_daily_positions(output_dir, snapshot)
    log("Wrote daily positions snapshot")

    # Step 5: Load and fetch sources
    log("Loading source configuration...")
    sources = load_sources(sources_path)

    log("Fetching content from sources...")
    raw_articles = fetch_all_sources(sources)
    log(f"Fetched {len(raw_articles)} raw articles")

    # Step 6: Normalize articles
    log("Normalizing articles...")
    normalized = normalize_all(raw_articles)
    log(f"Normalized {len(normalized)} articles")

    write_raw_articles(output_dir, run_date, normalized)

    # Step 7: Score and rank
    log("Scoring and ranking articles...")
    source_priorities = {}
    for feed in sources.get("rss", []):
        source_priorities[feed.get("name", "")] = feed.get("priority", 5)

    scored = score_and_rank(normalized, enriched, source_priorities)
    log(f"Scored {len(scored)} articles")

    write_ranked_articles(output_dir, run_date, scored)

    # Step 8: Summarize
    log("Generating portfolio summary...")
    summary = generate_portfolio_summary(run_date, enriched, scored, use_ollama)
    log("Generated portfolio summary")

    write_summary_payload(output_dir, run_date, summary.model_dump(mode="json"))

    # Step 9: Write digest
    log("Writing market digest...")
    digest_content = generate_digest(summary, snapshot)
    write_digest(output_dir, run_date, digest_content)
    log("Wrote market digest")

    elapsed = time.time() - start_time
    log(f"=== Pipeline complete in {elapsed:.1f}s ===")

    write_run_log(output_dir, run_date, log_lines)

    return {
        "date": run_date.isoformat(),
        "output_dir": str(output_dir),
        "positions_count": len(enriched),
        "articles_fetched": len(raw_articles),
        "articles_normalized": len(normalized),
        "articles_scored": len(scored),
        "elapsed_seconds": round(elapsed, 1),
    }


def main():
    """CLI entry point."""
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))

    use_ollama = os.getenv("USE_OLLAMA", "true").lower() == "true"
    output_base = os.getenv("OUTPUT_BASE_PATH", "output")

    try:
        result = run_pipeline(output_base=output_base, use_ollama=use_ollama)
        logger.info(f"Pipeline result: {result}")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
