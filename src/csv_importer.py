"""CSV importer for brokerage activity exports (Wealthsimple format).

Supports parsing Trade rows into TransactionRecord objects, OCC-style option
symbol parsing, external_id generation for deduplication, and a two-phase
preview/confirm import flow.

CSV columns expected:
  transaction_date, settlement_date, account_id, account_type, activity_type,
  activity_sub_type, direction, symbol, underlying_symbol, name, currency,
  quantity, unit_price, commission, net_cash_amount
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import date
from typing import Optional

from .models import Account, TransactionRecord

logger = logging.getLogger(__name__)

# Only these activity types are imported as transactions
_IMPORTABLE_ACTIVITY_TYPES = {"Trade"}
_IMPORTABLE_SUB_TYPES = {"BUY", "SELL"}

# OCC option symbol: up to 6-char root (space-padded) + YYMMDD + C/P + 8-digit strike*1000
# e.g. "IBIT  250919C00062000"
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z0-9 ]{1,6})\s*"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<opt_type>[CP])"
    r"(?P<strike_raw>\d{8})$"
)


def _parse_occ_symbol(symbol: str) -> Optional[dict]:
    """Parse an OCC-format option symbol.

    Returns a dict with keys: underlying, expiration (ISO), option_type, strike.
    Returns None if symbol doesn't match OCC format.
    """
    stripped = symbol.strip()
    # Try matching after collapsing internal spaces in the root
    m = _OCC_RE.match(stripped)
    if not m:
        return None
    root = m.group("root").strip()
    yy = int(m.group("yy"))
    mm = int(m.group("mm"))
    dd = int(m.group("dd"))
    year = 2000 + yy
    opt_type = "CALL" if m.group("opt_type") == "C" else "PUT"
    strike = int(m.group("strike_raw")) / 1000.0
    try:
        expiry = date(year, mm, dd).isoformat()
    except ValueError:
        return None
    return {
        "underlying": root,
        "expiration": expiry,
        "option_type": opt_type,
        "strike": strike,
    }


def _generate_external_id(row: dict) -> str:
    """Stable hash of the key fields in a CSV row for deduplication."""
    key = "|".join([
        row.get("transaction_date", ""),
        row.get("account_id", ""),
        row.get("activity_sub_type", ""),
        row.get("symbol", ""),
        row.get("quantity", ""),
        row.get("unit_price", ""),
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val.strip()) if val and val.strip() else default
    except (ValueError, AttributeError):
        return default


def _parse_trade_row(row: dict) -> Optional[TransactionRecord]:
    """Convert a CSV trade row to a TransactionRecord.

    Returns None if the row is not a Trade BUY/SELL or lacks required fields.
    """
    if row.get("activity_type", "").strip() not in _IMPORTABLE_ACTIVITY_TYPES:
        return None
    sub_type = row.get("activity_sub_type", "").strip().upper()
    if sub_type not in _IMPORTABLE_SUB_TYPES:
        return None

    tx_date_str = row.get("transaction_date", "").strip()
    symbol_raw = row.get("symbol", "").strip()
    if not tx_date_str or not symbol_raw:
        return None

    try:
        tx_date = date.fromisoformat(tx_date_str)
    except ValueError:
        logger.warning(f"Bad date in CSV row: {tx_date_str!r}")
        return None

    action = "buy" if sub_type == "BUY" else "sell"
    quantity = abs(_safe_float(row.get("quantity", "")))
    price = abs(_safe_float(row.get("unit_price", "")))
    commission = abs(_safe_float(row.get("commission", "")))
    currency = row.get("currency", "USD").strip() or "USD"
    notes = row.get("name", "").strip()
    direction = row.get("direction", "").strip().upper()  # LONG or SHORT
    external_id = _generate_external_id(row)

    if quantity <= 0:
        logger.debug(f"Skipping zero-quantity row: {row}")
        return None

    # Detect option by OCC symbol format (contains spaces or matches OCC pattern)
    occ = _parse_occ_symbol(symbol_raw)
    if occ:
        ticker = occ["underlying"]
        option_type = occ["option_type"]
        option_direction = direction if direction in ("LONG", "SHORT") else "LONG"
        return TransactionRecord(
            date=tx_date,
            ticker=ticker,
            action=action,
            quantity=quantity,
            price=price,
            currency=currency,
            commission=commission,
            position_type="option",
            option_type=option_type,
            option_direction=option_direction,
            strike=occ["strike"],
            expiration=occ["expiration"],
            notes=notes,
            external_id=external_id,
        )
    else:
        return TransactionRecord(
            date=tx_date,
            ticker=symbol_raw,
            action=action,
            quantity=quantity,
            price=price,
            currency=currency,
            commission=commission,
            position_type="equity",
            notes=notes,
            external_id=external_id,
        )


# ── Public API ─────────────────────────────────────────────────────────────────


def parse_csv(content: str | bytes) -> list[dict]:
    """Parse CSV bytes/str into a list of row dicts."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(content))
    return [row for row in reader]


def group_rows_by_account(rows: list[dict]) -> dict[str, dict]:
    """Group CSV rows by account_id, returning metadata per account."""
    groups: dict[str, dict] = {}
    for row in rows:
        src_id = row.get("account_id", "").strip()
        if not src_id:
            continue
        if src_id not in groups:
            groups[src_id] = {
                "source_account_id": src_id,
                "account_type": row.get("account_type", "").strip(),
                "rows": [],
            }
        groups[src_id]["rows"].append(row)
    return groups


def preview_import(
    csv_content: str | bytes,
    existing_accounts: list[Account],
    existing_tx_by_account: dict[str, list[TransactionRecord]],
) -> dict:
    """Analyse a CSV and return a preview of what would be imported.

    Args:
        csv_content: raw CSV file content
        existing_accounts: current accounts for the profile
        existing_tx_by_account: dict of account_id → existing transactions

    Returns a dict with full preview info for the UI.
    """
    rows = parse_csv(csv_content)
    groups = group_rows_by_account(rows)

    # Build a lookup: source_account_id → internal Account
    src_id_map: dict[str, Account] = {
        a.source_account_id: a
        for a in existing_accounts
        if a.source_account_id
    }

    # Build external_id sets per account for deduplication
    ext_id_sets: dict[str, set[str]] = {}
    for acct in existing_accounts:
        txs = existing_tx_by_account.get(acct.id, [])
        ext_id_sets[acct.id] = {t.external_id for t in txs if t.external_id}

    account_previews = []
    total_new = 0
    total_dups = 0
    total_skipped = 0

    for src_id, grp in groups.items():
        matched = src_id_map.get(src_id)
        tx_previews = []
        new_count = dup_count = skip_count = 0

        for row in grp["rows"]:
            tx = _parse_trade_row(row)
            if tx is None:
                skip_count += 1
                continue

            # Deduplication check
            is_dup = False
            dup_of = None
            if matched:
                ext_ids = ext_id_sets.get(matched.id, set())
                if tx.external_id and tx.external_id in ext_ids:
                    is_dup = True

            if is_dup:
                dup_count += 1
            else:
                new_count += 1

            tx_previews.append({
                "transaction": _tx_to_dict(tx),
                "is_duplicate": is_dup,
            })

        account_previews.append({
            "source_account_id": src_id,
            "account_type": grp["account_type"],
            "matched_account_id": matched.id if matched else None,
            "matched_account_name": matched.name if matched else None,
            "to_create": matched is None,
            "new_count": new_count,
            "duplicate_count": dup_count,
            "skipped_count": skip_count,
            "transactions": tx_previews,
        })
        total_new += new_count
        total_dups += dup_count
        total_skipped += skip_count

    return {
        "account_previews": account_previews,
        "total_rows": len(rows),
        "total_new": total_new,
        "total_duplicates": total_dups,
        "total_skipped": total_skipped,
    }


def _tx_to_dict(tx: TransactionRecord) -> dict:
    """Serialize a TransactionRecord to a plain dict for the API response."""
    return {
        "date": tx.date.isoformat() if hasattr(tx.date, "isoformat") else str(tx.date),
        "ticker": tx.ticker,
        "action": tx.action,
        "quantity": tx.quantity,
        "price": tx.price,
        "currency": tx.currency,
        "commission": tx.commission,
        "position_type": tx.position_type,
        "option_type": tx.option_type,
        "option_direction": tx.option_direction,
        "strike": tx.strike,
        "expiration": tx.expiration,
        "notes": tx.notes,
        "external_id": tx.external_id,
    }
