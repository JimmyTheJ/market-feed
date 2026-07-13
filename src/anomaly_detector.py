"""Ledger anomaly detection.

Scans transaction histories for inconsistencies such as:
  - Selling more long contracts/shares than held (oversell)
  - Buying to close more short contracts than held
  - Open options past their expiration date (worthless expiry not recorded)
  - Naked short calls (short calls exceeding covered shares)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from .models import Anomaly, AnomalyScanResultWithFixes, SuggestedFix, TransactionRecord
from .portfolio_ledger import (
    CONTRACT_MULTIPLIER,
    _instrument_key,
    _option_label,
    _PositionBook,
    build_position_books,
)

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _replay_oversell_anomalies(
    transactions: list[TransactionRecord],
    account_id: str | None = None,
    account_name: str | None = None,
) -> list[Anomaly]:
    """Detect oversells by replaying with signed position logic."""
    anomalies: list[Anomaly] = []
    long_qty = 0.0
    short_qty = 0.0
    books: dict[str, _PositionBook] = {}

    for tx in sorted(transactions, key=lambda t: (t.date, t.id)):
        key = _instrument_key(tx)
        if key not in books:
            books[key] = _PositionBook()
        book = books[key]
        before_long = book.long_qty
        before_short = book.short_qty

        if tx.position_type == "option":
            direction = (tx.option_direction or "LONG").upper()
            mult = CONTRACT_MULTIPLIER
            if tx.action == "sell" and direction == "LONG":
                if tx.quantity > before_long + 1e-9:
                    unmatched = tx.quantity - before_long
                    label = _option_label(tx)
                    anomalies.append(
                        Anomaly(
                            severity="error",
                            code="OVERSELL",
                            message=(
                                f"Sell-to-close {tx.quantity} {label} on {tx.date} exceeds "
                                f"long position of {before_long:.4g} by {unmatched:.4g}."
                            ),
                            instrument_key=key,
                            ticker=tx.ticker,
                            transaction_id=tx.id,
                            account_id=account_id,
                            account_name=account_name,
                            details={
                                "sell_quantity": tx.quantity,
                                "available_quantity": before_long,
                                "unmatched_quantity": unmatched,
                                "date": tx.date.isoformat(),
                            },
                        )
                    )
            elif tx.action == "buy" and direction == "SHORT":
                if tx.quantity > before_short + 1e-9:
                    unmatched = tx.quantity - before_short
                    label = _option_label(tx)
                    anomalies.append(
                        Anomaly(
                            severity="error",
                            code="OVERBUY_CLOSE",
                            message=(
                                f"Buy-to-close {tx.quantity} {label} on {tx.date} exceeds "
                                f"short position of {before_short:.4g} by {unmatched:.4g}."
                            ),
                            instrument_key=key,
                            ticker=tx.ticker,
                            transaction_id=tx.id,
                            account_id=account_id,
                            account_name=account_name,
                            details={
                                "buy_quantity": tx.quantity,
                                "available_quantity": before_short,
                                "unmatched_quantity": unmatched,
                                "date": tx.date.isoformat(),
                            },
                        )
                    )
        elif tx.position_type == "equity":
            if tx.action == "sell" and tx.quantity > before_long + 1e-9:
                unmatched = tx.quantity - before_long
                if before_long <= 1e-9:
                    anomalies.append(
                        Anomaly(
                            severity="info",
                            code="SHORT_OPEN",
                            message=(
                                f"Sell of {tx.quantity} {tx.ticker} on {tx.date} opens a "
                                f"short equity position ({unmatched:.4g} shares)."
                            ),
                            instrument_key=key,
                            ticker=tx.ticker,
                            transaction_id=tx.id,
                            account_id=account_id,
                            account_name=account_name,
                            details={
                                "quantity": unmatched,
                                "date": tx.date.isoformat(),
                                "position_type": "equity",
                            },
                        )
                    )
                else:
                    anomalies.append(
                        Anomaly(
                            severity="info",
                            code="SHORT_OPEN",
                            message=(
                                f"Sell of {tx.quantity} {tx.ticker} on {tx.date} closes "
                                f"{before_long:.4g} long and opens {unmatched:.4g} short."
                            ),
                            instrument_key=key,
                            ticker=tx.ticker,
                            transaction_id=tx.id,
                            account_id=account_id,
                            account_name=account_name,
                            details={
                                "short_quantity": unmatched,
                                "date": tx.date.isoformat(),
                                "position_type": "equity",
                            },
                        )
                    )

        # Apply transaction to book (reuse ledger engine)
        from .portfolio_ledger import _process_transaction

        _process_transaction(book, tx)

    return anomalies


def _detect_expired_options(
    books: dict[str, _PositionBook],
    meta: dict[str, TransactionRecord],
    as_of: date,
    account_id: str | None = None,
    account_name: str | None = None,
) -> tuple[list[Anomaly], list[SuggestedFix]]:
    """Flag open option positions past expiration and suggest $0 close trades."""
    anomalies: list[Anomaly] = []
    fixes: list[SuggestedFix] = []

    for key, book in books.items():
        tx_meta = meta.get(key)
        if not tx_meta or tx_meta.position_type != "option":
            continue
        if not tx_meta.expiration:
            continue

        try:
            exp_date = date.fromisoformat(tx_meta.expiration)
        except ValueError:
            continue
        if exp_date >= as_of:
            continue

        ticker = tx_meta.ticker
        label = _option_label(tx_meta)

        if book.long_qty > 1e-9:
            open_qty = book.long_qty
            total_cost = sum(lot.cost_per_unit * lot.quantity for lot in book.long_lots)
            loss_native = total_cost * CONTRACT_MULTIPLIER
            anomalies.append(
                Anomaly(
                    severity="warning",
                    code="EXPIRED_OPTION_OPEN",
                    message=(
                        f"{open_qty} long contract(s) of {label} expired on {tx_meta.expiration} "
                        f"but remain open — should be closed at $0 (loss ≈ "
                        f"{loss_native:.2f} {tx_meta.currency})."
                    ),
                    instrument_key=key,
                    ticker=ticker,
                    account_id=account_id,
                    account_name=account_name,
                    details={
                        "open_quantity": open_qty,
                        "expiration": tx_meta.expiration,
                        "estimated_loss_native": round(loss_native, 2),
                        "currency": tx_meta.currency,
                        "direction": "LONG",
                    },
                )
            )
            fixes.append(
                SuggestedFix(
                    code="EXPIRED_OPTION_CLOSE",
                    account_id=account_id,
                    account_name=account_name,
                    transaction=TransactionRecord(
                        date=exp_date,
                        ticker=ticker,
                        action="sell",
                        quantity=open_qty,
                        price=0.0,
                        currency=tx_meta.currency,
                        position_type="option",
                        option_type=tx_meta.option_type,
                        option_direction="LONG",
                        strike=tx_meta.strike,
                        expiration=tx_meta.expiration,
                        notes="Auto-closed: option expired worthless",
                    ),
                    message=f"Close {open_qty} long contract(s) of {label} at $0 on {tx_meta.expiration}",
                )
            )

        if book.short_qty > 1e-9:
            open_qty = book.short_qty
            total_credit = sum(lot.cost_per_unit * lot.quantity for lot in book.short_lots)
            gain_native = total_credit * CONTRACT_MULTIPLIER
            anomalies.append(
                Anomaly(
                    severity="warning",
                    code="EXPIRED_OPTION_OPEN",
                    message=(
                        f"{open_qty} short contract(s) of {label} expired on {tx_meta.expiration} "
                        f"but remain open — should be closed at $0 (gain ≈ "
                        f"{gain_native:.2f} {tx_meta.currency})."
                    ),
                    instrument_key=key,
                    ticker=ticker,
                    account_id=account_id,
                    account_name=account_name,
                    details={
                        "open_quantity": open_qty,
                        "expiration": tx_meta.expiration,
                        "estimated_gain_native": round(gain_native, 2),
                        "currency": tx_meta.currency,
                        "direction": "SHORT",
                    },
                )
            )
            fixes.append(
                SuggestedFix(
                    code="EXPIRED_OPTION_CLOSE",
                    account_id=account_id,
                    account_name=account_name,
                    transaction=TransactionRecord(
                        date=exp_date,
                        ticker=ticker,
                        action="buy",
                        quantity=open_qty,
                        price=0.0,
                        currency=tx_meta.currency,
                        position_type="option",
                        option_type=tx_meta.option_type,
                        option_direction="SHORT",
                        strike=tx_meta.strike,
                        expiration=tx_meta.expiration,
                        notes="Auto-closed: option expired worthless",
                    ),
                    message=f"Close {open_qty} short contract(s) of {label} at $0 on {tx_meta.expiration}",
                )
            )

    return anomalies, fixes


def _detect_naked_calls(
    books: dict[str, _PositionBook],
    meta: dict[str, TransactionRecord],
    account_id: str | None = None,
    account_name: str | None = None,
) -> list[Anomaly]:
    """Warn when short calls exceed shares available to cover."""
    long_shares: dict[str, float] = defaultdict(float)
    short_calls: dict[str, float] = defaultdict(float)

    for key, book in books.items():
        tx_meta = meta.get(key)
        if not tx_meta:
            continue
        if tx_meta.position_type == "equity" and book.long_qty > 1e-9:
            long_shares[tx_meta.ticker] += book.long_qty
        elif (
            tx_meta.position_type == "option"
            and (tx_meta.option_type or "").upper() == "CALL"
            and book.short_qty > 1e-9
        ):
            short_calls[tx_meta.ticker] += book.short_qty

    anomalies: list[Anomaly] = []
    for ticker, short_qty in short_calls.items():
        covered = long_shares.get(ticker, 0.0) / CONTRACT_MULTIPLIER
        if short_qty > covered + 1e-9:
            naked = short_qty - covered
            anomalies.append(
                Anomaly(
                    severity="warning",
                    code="NAKED_CALL",
                    message=(
                        f"{short_qty:.4g} short CALL contract(s) on {ticker} but only "
                        f"{covered:.4g} covered by long shares "
                        f"({long_shares.get(ticker, 0):.0f} shares) — "
                        f"{naked:.4g} contract(s) appear naked."
                    ),
                    ticker=ticker,
                    account_id=account_id,
                    account_name=account_name,
                    details={
                        "short_call_contracts": short_qty,
                        "long_shares": long_shares.get(ticker, 0.0),
                        "covered_contracts": covered,
                        "naked_contracts": naked,
                    },
                )
            )
    return anomalies


def _build_meta(transactions: list[TransactionRecord]) -> dict[str, TransactionRecord]:
    meta: dict[str, TransactionRecord] = {}
    for tx in sorted(transactions, key=lambda t: t.date):
        key = _instrument_key(tx)
        if key not in meta:
            meta[key] = tx
    return meta


def detect_anomalies(
    transactions: list[TransactionRecord],
    *,
    profile: str = "",
    account_id: str | None = None,
    account_name: str | None = None,
    as_of: date | None = None,
) -> AnomalyScanResultWithFixes:
    """Scan a transaction list for ledger anomalies."""
    as_of = as_of or date.today()
    books, _ = build_position_books(transactions)
    meta = _build_meta(transactions)

    replay_anomalies = _replay_oversell_anomalies(
        transactions, account_id=account_id, account_name=account_name
    )
    expired_anomalies, suggested_fixes = _detect_expired_options(
        books, meta, as_of, account_id=account_id, account_name=account_name
    )
    naked_anomalies = _detect_naked_calls(
        books, meta, account_id=account_id, account_name=account_name
    )

    all_anomalies = replay_anomalies + expired_anomalies + naked_anomalies
    all_anomalies.sort(key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9), a.code, a.ticker or ""))

    error_count = sum(1 for a in all_anomalies if a.severity == "error")
    warning_count = sum(1 for a in all_anomalies if a.severity == "warning")
    info_count = sum(1 for a in all_anomalies if a.severity == "info")

    return AnomalyScanResultWithFixes(
        scanned_at=datetime.now(),
        profile=profile,
        account_id=account_id,
        scope="account" if account_id else "profile",
        anomaly_count=len(all_anomalies),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        anomalies=all_anomalies,
        suggested_fixes=suggested_fixes,
    )


def detect_profile_anomalies(
    profile: str,
    account_transactions: dict[str, list[TransactionRecord]],
    account_names: dict[str, str] | None = None,
    *,
    as_of: date | None = None,
    per_account: bool = True,
) -> AnomalyScanResultWithFixes:
    """Scan all accounts in a profile."""
    account_names = account_names or {}
    as_of = as_of or date.today()
    all_anomalies: list[Anomaly] = []
    all_fixes: list[SuggestedFix] = []

    if per_account:
        for acct_id, txs in account_transactions.items():
            if not txs:
                continue
            result = detect_anomalies(
                txs,
                profile=profile,
                account_id=acct_id,
                account_name=account_names.get(acct_id),
                as_of=as_of,
            )
            all_anomalies.extend(result.anomalies)
            all_fixes.extend(result.suggested_fixes)

    combined = []
    for txs in account_transactions.values():
        combined.extend(txs)
    if len(account_transactions) > 1 and combined:
        agg = detect_anomalies(combined, profile=profile, as_of=as_of)
        seen_tx_ids = {a.transaction_id for a in all_anomalies if a.transaction_id}
        for a in agg.anomalies:
            if a.transaction_id and a.transaction_id in seen_tx_ids:
                continue
            if a.code in ("OVERSELL", "OVERBUY_CLOSE"):
                a.message = f"[All accounts combined] {a.message}"
                a.account_id = None
                a.account_name = "All accounts"
                all_anomalies.append(a)

    seen_fix_keys: set[str] = set()
    unique_fixes: list[SuggestedFix] = []
    for fix in all_fixes:
        tx = fix.transaction
        fix_key = (
            f"{fix.account_id}|{tx.ticker}|{tx.option_type}|{tx.strike}|"
            f"{tx.expiration}|{tx.action}"
        )
        if fix_key not in seen_fix_keys:
            seen_fix_keys.add(fix_key)
            unique_fixes.append(fix)

    all_anomalies.sort(key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9), a.code, a.ticker or ""))
    error_count = sum(1 for a in all_anomalies if a.severity == "error")
    warning_count = sum(1 for a in all_anomalies if a.severity == "warning")
    info_count = sum(1 for a in all_anomalies if a.severity == "info")

    return AnomalyScanResultWithFixes(
        scanned_at=datetime.now(),
        profile=profile,
        account_id=None,
        scope="profile",
        anomaly_count=len(all_anomalies),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        anomalies=all_anomalies,
        suggested_fixes=unique_fixes,
    )


def preview_import_anomalies(
    new_transactions: list[TransactionRecord],
    existing_transactions: list[TransactionRecord],
) -> list[Anomaly]:
    """Detect anomalies that would appear if new transactions were merged."""
    combined = existing_transactions + new_transactions
    result = detect_anomalies(combined)
    return result.anomalies
