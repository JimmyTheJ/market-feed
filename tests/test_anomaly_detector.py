"""Tests for ledger anomaly detection."""

from datetime import date

import pytest

from src.anomaly_detector import detect_anomalies, detect_profile_anomalies
from src.models import TransactionRecord


def _equity_buy(ticker: str, qty: float, d: str = "2024-01-01") -> TransactionRecord:
    return TransactionRecord(
        date=date.fromisoformat(d),
        ticker=ticker,
        action="buy",
        quantity=qty,
        price=100.0,
        currency="USD",
    )


def _equity_sell(ticker: str, qty: float, d: str = "2024-06-01") -> TransactionRecord:
    return TransactionRecord(
        date=date.fromisoformat(d),
        ticker=ticker,
        action="sell",
        quantity=qty,
        price=110.0,
        currency="USD",
    )


def _option_buy(
    ticker: str,
    qty: float,
    expiration: str,
    d: str = "2024-01-01",
) -> TransactionRecord:
    return TransactionRecord(
        date=date.fromisoformat(d),
        ticker=ticker,
        action="buy",
        quantity=qty,
        price=2.50,
        currency="USD",
        position_type="option",
        option_type="CALL",
        option_direction="LONG",
        strike=300.0,
        expiration=expiration,
    )


class TestOversellDetection:
    def test_detects_option_oversell(self):
        txs = [
            _option_buy("TSLA", 5, "2026-06-20", "2024-01-01"),
            TransactionRecord(
                date=date.fromisoformat("2024-06-01"),
                ticker="TSLA",
                action="sell",
                quantity=20,
                price=6.0,
                currency="USD",
                position_type="option",
                option_type="CALL",
                option_direction="LONG",
                strike=300.0,
                expiration="2026-06-20",
            ),
        ]
        result = detect_anomalies(txs)
        codes = [a.code for a in result.anomalies]
        assert "OVERSELL" in codes
        oversell = next(a for a in result.anomalies if a.code == "OVERSELL")
        assert oversell.severity == "error"
        assert oversell.details["unmatched_quantity"] == 15.0

    def test_equity_oversell_opens_short_instead(self):
        txs = [
            _equity_buy("TSLA", 5, "2024-01-01"),
            _equity_sell("TSLA", 20, "2024-06-01"),
        ]
        result = detect_anomalies(txs)
        codes = [a.code for a in result.anomalies]
        assert "OVERSELL" not in codes
        assert "SHORT_OPEN" in codes

    def test_no_oversell_when_balanced(self):
        txs = [
            _equity_buy("TSLA", 20, "2024-01-01"),
            _equity_sell("TSLA", 20, "2024-06-01"),
        ]
        result = detect_anomalies(txs)
        assert result.anomaly_count == 0


class TestSellWithoutBuy:
    def test_equity_sell_without_buy_opens_short(self):
        txs = [_equity_sell("TSLA", 10, "2024-06-01")]
        result = detect_anomalies(txs)
        codes = [a.code for a in result.anomalies]
        assert "SHORT_OPEN" in codes
        assert "SELL_WITHOUT_BUY" not in codes

    def test_short_option_open_is_valid(self):
        txs = [
            TransactionRecord(
                date=date.fromisoformat("2024-06-01"),
                ticker="TSLA",
                action="sell",
                quantity=2,
                price=3.0,
                currency="USD",
                position_type="option",
                option_type="CALL",
                option_direction="SHORT",
                strike=400.0,
                expiration="2030-09-20",
            )
        ]
        result = detect_anomalies(txs)
        codes = [a.code for a in result.anomalies]
        assert "OVERSELL" not in codes
        assert "SELL_WITHOUT_BUY" not in codes
        assert "NAKED_CALL" in codes  # no underlying shares to cover


class TestExpiredOptions:
    def test_detects_expired_open_option(self):
        txs = [_option_buy("TSLA", 3, "2020-01-17", "2019-06-01")]
        result = detect_anomalies(txs, as_of=date(2024, 1, 1))
        codes = [a.code for a in result.anomalies]
        assert "EXPIRED_OPTION_OPEN" in codes
        assert len(result.suggested_fixes) == 1
        fix = result.suggested_fixes[0]
        assert fix.code == "EXPIRED_OPTION_CLOSE"
        assert fix.transaction.action == "sell"
        assert fix.transaction.price == 0.0
        assert fix.transaction.quantity == 3

    def test_no_expired_flag_for_future_option(self):
        txs = [_option_buy("TSLA", 1, "2030-06-20", "2024-01-01")]
        result = detect_anomalies(txs, as_of=date(2024, 6, 1))
        codes = [a.code for a in result.anomalies]
        assert "EXPIRED_OPTION_OPEN" not in codes


class TestNakedCall:
    def test_detects_naked_call(self):
        txs = [
            _short_call_sell("TSLA", 2),
        ]
        result = detect_anomalies(txs)
        codes = [a.code for a in result.anomalies]
        assert "NAKED_CALL" in codes

    def test_covered_call_not_naked(self):
        txs = [
            _equity_buy("TSLA", 100),
            _short_call_sell("TSLA", 1),
        ]
        result = detect_anomalies(txs)
        codes = [a.code for a in result.anomalies]
        assert "NAKED_CALL" not in codes


def _short_call_sell(ticker: str, qty: float) -> TransactionRecord:
    return TransactionRecord(
        date=date.fromisoformat("2024-03-01"),
        ticker=ticker,
        action="sell",
        quantity=qty,
        price=5.0,
        currency="USD",
        position_type="option",
        option_type="CALL",
        option_direction="SHORT",
        strike=300.0,
        expiration="2026-06-20",
    )


class TestProfileScan:
    def test_scans_per_account(self):
        acct_txs = {
            "acct1": [
                _option_buy("AAPL", 10, "2026-06-20"),
                TransactionRecord(
                    date=date.fromisoformat("2024-06-01"),
                    ticker="AAPL",
                    action="sell",
                    quantity=50,
                    price=3.0,
                    currency="USD",
                    position_type="option",
                    option_type="CALL",
                    option_direction="LONG",
                    strike=300.0,
                    expiration="2026-06-20",
                ),
            ],
            "acct2": [_equity_buy("MSFT", 5), _equity_sell("MSFT", 5)],
        }
        result = detect_profile_anomalies(
            "test-profile",
            acct_txs,
            {"acct1": "TFSA", "acct2": "RRSP"},
        )
        oversells = [a for a in result.anomalies if a.code == "OVERSELL"]
        assert len(oversells) == 1
        assert oversells[0].account_name == "TFSA"
