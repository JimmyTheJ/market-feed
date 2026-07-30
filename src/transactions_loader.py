"""Load and save the per-profile/per-account transaction ledger (transactions.yaml)."""

import logging
from datetime import date
from pathlib import Path

import yaml

from .models import TransactionRecord, TransactionsFile

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path("config/profiles")
_FALLBACK_PATH = Path("config/transactions.yaml")


def get_transactions_path(
    profile: str | None = None,
    account_id: str | None = None,
) -> Path:
    """Return the filesystem path for a transactions.yaml.

    If account_id is given, returns the account-scoped path.
    If only profile is given, returns the legacy profile-root path.
    """
    if profile and account_id:
        return _PROFILES_DIR / profile / "accounts" / account_id / "transactions.yaml"
    if profile:
        return _PROFILES_DIR / profile / "transactions.yaml"
    return _FALLBACK_PATH


def has_transactions(profile: str | None = None) -> bool:
    """Return True if any non-empty transactions exist for the profile.

    Checks account-scoped transactions first, then falls back to the
    legacy profile-root transactions.yaml.
    """
    if profile:
        try:
            from .accounts_manager import has_account_transactions
            if has_account_transactions(profile):
                return True
        except Exception:
            pass

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
    account_id: str | None = None,
) -> TransactionsFile:
    """Load transactions from YAML.

    Resolution order:
    1. *path* if explicitly provided
    2. Account-scoped path if both *profile* and *account_id* given
    3. Profile-root (legacy) path if only *profile* given
    4. Global fallback path

    Returns an empty TransactionsFile if the file does not exist.
    """
    if path is None:
        path = get_transactions_path(profile, account_id)
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
    account_id: str | None = None,
) -> None:
    """Persist a TransactionsFile to YAML."""
    if path is None:
        path = get_transactions_path(profile, account_id)
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
        if tx.external_id:
            entry["external_id"] = tx.external_id
        if tx.import_batch_id:
            entry["import_batch_id"] = tx.import_batch_id
        tx_list.append(entry)

    with open(path, "w") as f:
        yaml.dump({"transactions": tx_list}, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Saved {len(tx_list)} transactions to {path}")


def load_all_profile_transactions(profile: str | None = None) -> TransactionsFile:
    """Load all transactions for a profile, aggregating across all accounts.

    Falls back to the legacy profile-root transactions.yaml when no accounts exist.
    Returns an empty TransactionsFile if nothing is found.
    """
    if profile:
        try:
            from .accounts_manager import has_account_transactions, load_all_account_transactions
            if has_account_transactions(profile):
                all_txs = load_all_account_transactions(profile)
                return TransactionsFile(transactions=all_txs)
        except Exception:
            pass
    return load_transactions(profile=profile)


def bulk_delete_transactions(
    ids: set[str],
    profile: str | None = None,
    account_id: str | None = None,
) -> int:
    """Remove transactions whose IDs are in *ids* from one ledger file.

    Returns the number of transactions removed.
    """
    if not ids:
        return 0
    txs = load_transactions(profile=profile, account_id=account_id)
    before = len(txs.transactions)
    txs.transactions = [t for t in txs.transactions if t.id not in ids]
    removed = before - len(txs.transactions)
    if removed:
        save_transactions(txs, profile=profile, account_id=account_id)
    return removed


def rollback_import_batch(profile: str, batch_id: str) -> dict:
    """Remove all transactions tagged with *batch_id* across a profile.

    Scans every account ledger plus the legacy profile-root transactions file.
    Returns a summary with deleted count and touched account IDs.
    """
    deleted = 0
    accounts_touched: list[str] = []

    try:
        from .accounts_manager import get_account_transactions_path, load_accounts

        accts = load_accounts(profile)
        for acct in accts.accounts:
            tx_path = get_account_transactions_path(profile, acct.id)
            txs_file = load_transactions(path=tx_path)
            before = len(txs_file.transactions)
            txs_file.transactions = [
                t for t in txs_file.transactions if t.import_batch_id != batch_id
            ]
            removed = before - len(txs_file.transactions)
            if removed:
                save_transactions(txs_file, path=tx_path)
                deleted += removed
                accounts_touched.append(acct.id)
    except Exception:
        pass

    legacy = load_transactions(profile=profile)
    before = len(legacy.transactions)
    legacy.transactions = [
        t for t in legacy.transactions if t.import_batch_id != batch_id
    ]
    removed = before - len(legacy.transactions)
    if removed:
        save_transactions(legacy, profile=profile)
        deleted += removed

    return {
        "deleted": deleted,
        "accounts_touched": accounts_touched,
        "batch_id": batch_id,
    }
