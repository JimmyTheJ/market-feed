"""Tests for CSV import parsing and profile-scoped deduplication."""

from src.csv_importer import (
    _generate_external_id,
    _parse_trade_row,
    parse_csv,
    preview_import,
)
from src.models import Account, TransactionRecord

SAMPLE_CSV = """\
transaction_date,settlement_date,account_id,account_type,activity_type,activity_sub_type,direction,symbol,underlying_symbol,name,currency,quantity,unit_price,commission,net_cash_amount
2024-01-15,2024-01-17,WS-123,TFSA,Trade,BUY,LONG,AAPL,,Apple Inc,USD,10,150.0,0,-1500
2024-02-01,2024-02-03,WS-123,TFSA,Trade,SELL,LONG,AAPL,,Apple Inc,USD,5,160.0,0,800
"""


def _tx_from_row(row: dict) -> TransactionRecord:
    tx = _parse_trade_row(row)
    assert tx is not None
    return tx


class TestParseTradeRow:
    def test_parses_buy(self):
        rows = parse_csv(SAMPLE_CSV)
        tx = _parse_trade_row(rows[0])
        assert tx is not None
        assert tx.ticker == "AAPL"
        assert tx.action == "buy"
        assert tx.quantity == 10
        assert tx.external_id

    def test_skips_non_trade(self):
        row = parse_csv(SAMPLE_CSV)[0]
        row["activity_type"] = "Dividend"
        assert _parse_trade_row(row) is None


class TestPreviewDedup:
    def test_empty_profile_imports_all(self):
        result = preview_import(SAMPLE_CSV, [], {})
        assert result["total_new"] == 2
        assert result["total_duplicates"] == 0

    def test_same_profile_blocks_duplicates(self):
        rows = parse_csv(SAMPLE_CSV)
        txs = [_tx_from_row(r) for r in rows]
        acct = Account(id="acctA", name="TFSA", source_account_id="WS-123")

        result = preview_import(SAMPLE_CSV, [acct], {"acctA": txs})
        assert result["total_new"] == 0
        assert result["total_duplicates"] == 2

    def test_other_profile_data_does_not_block(self):
        """Transactions that exist only in another profile's data must not
        affect preview for an empty (or different) profile.
        """
        rows = parse_csv(SAMPLE_CSV)
        other_profile_txs = [_tx_from_row(r) for r in rows]

        # Simulate profile B: no accounts / no txs passed in — even though
        # the same external_ids exist "elsewhere", preview must treat all as new.
        result = preview_import(SAMPLE_CSV, [], {})
        assert result["total_new"] == 2
        assert result["total_duplicates"] == 0

        # Profile B with a matching source_account_id but empty ledger
        acct_b = Account(id="acctB", name="TFSA", source_account_id="WS-123")
        result_b = preview_import(SAMPLE_CSV, [acct_b], {"acctB": []})
        assert result_b["total_new"] == 2
        assert result_b["total_duplicates"] == 0

        # Sanity: those external_ids would be dups if they belonged to this profile
        assert {t.external_id for t in other_profile_txs} == {
            t.external_id for t in [_tx_from_row(r) for r in rows]
        }

    def test_mapping_to_empty_account_allows_reimport(self):
        """Same-profile re-import into a different empty account should not
        be blocked by duplicates on the auto-matched account.
        """
        rows = parse_csv(SAMPLE_CSV)
        txs = [_tx_from_row(r) for r in rows]
        filled = Account(id="filled", name="TFSA", source_account_id="WS-123")
        empty = Account(id="empty", name="RRSP")

        # Auto-match hits the filled account → all dups
        auto = preview_import(
            SAMPLE_CSV, [filled, empty], {"filled": txs, "empty": []}
        )
        assert auto["total_new"] == 0
        assert auto["total_duplicates"] == 2

        # Explicit mapping to the empty account → all new
        mapped = preview_import(
            SAMPLE_CSV,
            [filled, empty],
            {"filled": txs, "empty": []},
            mapping={"WS-123": {"target": "existing", "account_id": "empty"}},
        )
        assert mapped["total_new"] == 2
        assert mapped["total_duplicates"] == 0

    def test_mapping_to_new_allows_reimport(self):
        rows = parse_csv(SAMPLE_CSV)
        txs = [_tx_from_row(r) for r in rows]
        filled = Account(id="filled", name="TFSA", source_account_id="WS-123")

        mapped = preview_import(
            SAMPLE_CSV,
            [filled],
            {"filled": txs},
            mapping={"WS-123": {"target": "new", "name": "RRSP"}},
        )
        assert mapped["total_new"] == 2
        assert mapped["total_duplicates"] == 0

    def test_account_external_ids_exposed_for_ui(self):
        rows = parse_csv(SAMPLE_CSV)
        txs = [_tx_from_row(r) for r in rows]
        acct = Account(id="acctA", name="TFSA", source_account_id="WS-123")
        result = preview_import(SAMPLE_CSV, [acct], {"acctA": txs})
        assert "acctA" in result["account_external_ids"]
        assert set(result["account_external_ids"]["acctA"]) == {
            t.external_id for t in txs
        }

    def test_external_id_stable(self):
        rows = parse_csv(SAMPLE_CSV)
        a = _generate_external_id(rows[0])
        b = _generate_external_id(rows[0])
        assert a == b
        assert a != _generate_external_id(rows[1])
