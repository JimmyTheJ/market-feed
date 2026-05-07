"""Manage brokerage accounts within a profile.

Each profile can have multiple accounts (e.g. TFSA, RRSP, Margin).
Account data is stored at:
  config/profiles/{profile_id}/accounts.yaml

Per-account transactions are stored at:
  config/profiles/{profile_id}/accounts/{account_id}/transactions.yaml
"""

import logging
import shutil
from pathlib import Path

import yaml

from .models import Account, AccountsFile, TransactionRecord

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path("config/profiles")


def get_accounts_path(profile: str) -> Path:
    """Return path to the profile's accounts.yaml index."""
    return _PROFILES_DIR / profile / "accounts.yaml"


def get_account_transactions_path(profile: str, account_id: str) -> Path:
    """Return path to a specific account's transactions.yaml."""
    return _PROFILES_DIR / profile / "accounts" / account_id / "transactions.yaml"


def load_accounts(profile: str) -> AccountsFile:
    """Load the accounts index for a profile, sorted by order."""
    path = get_accounts_path(profile)
    if not path.exists():
        return AccountsFile()
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        result = AccountsFile(**data)
        result.accounts.sort(key=lambda a: a.order)
        return result
    except Exception as e:
        logger.warning(f"Failed to load accounts for profile {profile!r}: {e}")
        return AccountsFile()


def save_accounts(accounts_file: AccountsFile, profile: str) -> None:
    """Persist the accounts index for a profile."""
    path = get_accounts_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    account_list = [
        {
            "id": a.id,
            "name": a.name,
            "order": a.order,
            "currency": a.currency,
            "description": a.description,
            **({"source_account_id": a.source_account_id} if a.source_account_id else {}),
        }
        for a in accounts_file.accounts
    ]
    with open(path, "w") as f:
        yaml.dump({"accounts": account_list}, f, default_flow_style=False, sort_keys=False)
    logger.debug(f"Saved {len(account_list)} accounts for profile {profile!r}")


def has_account_transactions(profile: str) -> bool:
    """Return True if any account for this profile has at least one transaction."""
    accts = load_accounts(profile)
    for acct in accts.accounts:
        tx_path = get_account_transactions_path(profile, acct.id)
        if tx_path.exists():
            try:
                with open(tx_path) as f:
                    data = yaml.safe_load(f) or {}
                if data.get("transactions"):
                    return True
            except Exception:
                pass
    return False


def load_all_account_transactions(profile: str) -> list[TransactionRecord]:
    """Load and combine transactions from all accounts for a profile.

    Returns a flat list sorted by date ascending.
    """
    from .transactions_loader import load_transactions

    accts = load_accounts(profile)
    all_txs: list[TransactionRecord] = []
    for acct in accts.accounts:
        tx_path = get_account_transactions_path(profile, acct.id)
        txs_file = load_transactions(path=tx_path)
        all_txs.extend(txs_file.transactions)

    all_txs.sort(key=lambda t: t.date)
    return all_txs


def find_account_by_source_id(profile: str, source_account_id: str) -> "Account | None":
    """Return the account whose source_account_id matches, or None."""
    from .models import Account  # already imported at module level but kept explicit

    accts = load_accounts(profile)
    for a in accts.accounts:
        if a.source_account_id == source_account_id:
            return a
    return None


def delete_account(profile: str, account_id: str) -> bool:
    """Delete an account from the index and remove its transactions directory.

    Returns True if the account existed and was removed.
    """
    accts = load_accounts(profile)
    before = len(accts.accounts)
    accts.accounts = [a for a in accts.accounts if a.id != account_id]
    if len(accts.accounts) == before:
        return False

    # Re-assign sequential order values
    for i, a in enumerate(accts.accounts):
        a.order = i

    save_accounts(accts, profile)

    acct_dir = _PROFILES_DIR / profile / "accounts" / account_id
    if acct_dir.exists():
        shutil.rmtree(acct_dir)
        logger.info(f"Deleted account directory: {acct_dir}")

    return True
