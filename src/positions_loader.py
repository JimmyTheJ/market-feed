"""Load and validate positions.yaml."""

import logging
from pathlib import Path

import yaml

from .models import PositionsFile

logger = logging.getLogger(__name__)


def load_positions(path: str | Path = "config/positions.yaml") -> PositionsFile:
    """Load positions from YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Positions file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    if not data or "positions" not in data:
        raise ValueError("Invalid positions file: missing 'positions' key")

    positions_file = PositionsFile(**data)

    logger.info(f"Loaded {len(positions_file.positions)} positions")
    return positions_file


def save_positions(
    positions_file: PositionsFile, path: str | Path = "config/positions.yaml"
) -> None:
    """Save positions to YAML file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "currencies": positions_file.currencies,
        "positions": [
            {"ticker": p.ticker, "shares": p.shares, "currency": p.currency}
            for p in positions_file.positions
        ],
    }

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Saved {len(positions_file.positions)} positions to {path}")
