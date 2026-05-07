"""Load and save the per-profile transaction ledger (transactions.yaml)."""

import logging
from datetime import date
from pathlib import Path

import yaml

from .models import TransactionRecord, TransactionsFile

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path("config/profiles")
_FALLBACK_PATH = Path("config/transactions.yaml")


def get_transactions_path(profile: str | None = None) -> Path:
    """Return the filesystem path for the given profile's transactions.yaml."""
    if profile:
        return _PROFILES_DIR / profile / "transactions.yaml"
    return _FALLBACK_PATH


def has_transactions(profile: str | None = None) -> bool:
    """Return True if a non-empty transactions.yaml exists for the profile."""
    path = get_transactions_path(profile)
    if not path.exists():
        return False
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return bool(data.get("transactions"))
    except Exception:
        return False


def load_transactions(
    path: str | Path | None = None,
    profile: str | None = None,
) -> TransactionsFile:
    """Load transactions from YAML.

    If *path* is not given, uses the profile-specific location.
    Returns an empty TransactionsFile if the file does not exist.
    """
    if path is None:
        path = get_transactions_path(profile)
    path = Path(path)
    if not path.exists():
        return TransactionsFile()
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    txs = TransactionsFile(**data)
    logger.debug(f"Loaded {len(txs.transactions)} transactions from {path}")
    return txs


def save_transactions(
    txs_file: TransactionsFile,
    path: str | Path | None = None,
    profile: str | None = None,
) -> None:
    """Persist a TransactionsFile to YAML."""
    if path is None:
        path = get_transactions_path(profile)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tx_list = []
    for tx in txs_file.transactions:
        entry: dict = {
            "id": tx.id,
            "date": tx.date.isoformat() if isinstance(tx.date, date) else tx.date,
            "ticker": tx.ticker,
            "action": tx.action,
            "quantity": tx.quantity,
            "price": tx.price,
            "currency": tx.currency,
            "position_type": tx.position_type,
        }
        if tx.commission:
            entry["commission"] = tx.commission
        if tx.position_type == "option":
            if tx.option_type:
                entry["option_type"] = tx.option_type
            if tx.option_direction:
                entry["option_direction"] = tx.option_direction
            if tx.strike is not None:
                entry["strike"] = tx.strike
            if tx.expiration:
                entry["expiration"] = tx.expiration
        if tx.lot_id:
            entry["lot_id"] = tx.lot_id
        if tx.notes:
            entry["notes"] = tx.notes
        tx_list.append(entry)

    with open(path, "w") as f:
        yaml.dump({"transactions": tx_list}, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Saved {len(tx_list)} transactions to {path}")
