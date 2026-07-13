"""Tests for transaction bulk delete and import batch rollback."""

from datetime import date
from pathlib import Path

import yaml

from src.accounts_manager import save_accounts
from src.models import Account, AccountsFile, TransactionRecord, TransactionsFile
from src.transactions_loader import (
    bulk_delete_transactions,
    load_transactions,
    rollback_import_batch,
    save_transactions,
)


def _write_accounts(profile_dir: Path, accounts: list[Account]) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    save_accounts(AccountsFile(accounts=accounts), profile_dir.name)


def _make_tx(
    tx_id: str,
    *,
    batch_id: str | None = None,
    ticker: str = "AAPL",
) -> TransactionRecord:
    return TransactionRecord(
        id=tx_id,
        date=date(2024, 1, 15),
        ticker=ticker,
        action="buy",
        quantity=10,
        price=100.0,
        import_batch_id=batch_id,
    )


class TestBulkDeleteTransactions:
    def test_removes_matching_ids(self, tmp_path, monkeypatch):
        profile = "testprof"
        acct_id = "acct-1"
        profiles_dir = tmp_path / "config" / "profiles"
        profile_dir = profiles_dir / profile
        monkeypatch.setattr("src.transactions_loader._PROFILES_DIR", profiles_dir)
        monkeypatch.setattr("src.accounts_manager._PROFILES_DIR", profiles_dir)

        _write_accounts(profile_dir, [Account(id=acct_id, name="TFSA", order=0)])

        txs = TransactionsFile(
            transactions=[
                _make_tx("tx-1"),
                _make_tx("tx-2"),
                _make_tx("tx-3"),
            ]
        )
        tx_path = profile_dir / "accounts" / acct_id / "transactions.yaml"
        save_transactions(txs, path=tx_path)

        removed = bulk_delete_transactions({"tx-1", "tx-3"}, profile=profile, account_id=acct_id)
        assert removed == 2

        remaining = load_transactions(path=tx_path)
        assert [t.id for t in remaining.transactions] == ["tx-2"]

    def test_returns_zero_when_no_matches(self, tmp_path, monkeypatch):
        profile = "testprof"
        acct_id = "acct-1"
        profiles_dir = tmp_path / "config" / "profiles"
        profile_dir = profiles_dir / profile
        monkeypatch.setattr("src.transactions_loader._PROFILES_DIR", profiles_dir)

        _write_accounts(profile_dir, [Account(id=acct_id, name="TFSA", order=0)])

        txs = TransactionsFile(transactions=[_make_tx("tx-1")])
        tx_path = profile_dir / "accounts" / acct_id / "transactions.yaml"
        save_transactions(txs, path=tx_path)

        removed = bulk_delete_transactions({"missing"}, profile=profile, account_id=acct_id)
        assert removed == 0
        assert len(load_transactions(path=tx_path).transactions) == 1


class TestRollbackImportBatch:
    def test_removes_batch_across_accounts(self, tmp_path, monkeypatch):
        profile = "testprof"
        batch_a = "batch-abc"
        batch_b = "batch-xyz"
        acct1 = "acct-1"
        acct2 = "acct-2"
        profiles_dir = tmp_path / "config" / "profiles"
        profile_dir = profiles_dir / profile
        monkeypatch.setattr("src.transactions_loader._PROFILES_DIR", profiles_dir)
        monkeypatch.setattr("src.accounts_manager._PROFILES_DIR", profiles_dir)

        _write_accounts(
            profile_dir,
            [
                Account(id=acct1, name="TFSA", order=0),
                Account(id=acct2, name="RRSP", order=1),
            ],
        )

        path1 = profile_dir / "accounts" / acct1 / "transactions.yaml"
        path2 = profile_dir / "accounts" / acct2 / "transactions.yaml"
        save_transactions(
            TransactionsFile(
                transactions=[
                    _make_tx("a1", batch_id=batch_a),
                    _make_tx("a2", batch_id=batch_b),
                ]
            ),
            path=path1,
        )
        save_transactions(
            TransactionsFile(
                transactions=[
                    _make_tx("b1", batch_id=batch_a),
                    _make_tx("b2"),
                ]
            ),
            path=path2,
        )

        result = rollback_import_batch(profile, batch_a)
        assert result["deleted"] == 2
        assert set(result["accounts_touched"]) == {acct1, acct2}

        left1 = load_transactions(path=path1)
        assert [t.id for t in left1.transactions] == ["a2"]

        left2 = load_transactions(path=path2)
        assert [t.id for t in left2.transactions] == ["b2"]

    def test_persists_import_batch_id_in_yaml(self, tmp_path):
        path = tmp_path / "transactions.yaml"
        save_transactions(
            TransactionsFile(transactions=[_make_tx("tx-1", batch_id="batch-123")]),
            path=path,
        )
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["transactions"][0]["import_batch_id"] == "batch-123"
