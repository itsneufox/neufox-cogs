"""Crash-safe Hold'em buy-ins stored beside each player's cash balance."""

from __future__ import annotations

from typing import Any


ESCROW_KEY = "holdem_escrow"


def account_from_mapping(balances: dict[str, Any], user_id: int) -> dict[str, Any]:
    stored = balances.get(str(user_id), {})
    account = {"cash": int(stored.get("cash", 0))}
    escrow = stored.get(ESCROW_KEY)
    if isinstance(escrow, dict):
        account[ESCROW_KEY] = dict(escrow)
    return account


def reserve_buyin(balances: dict[str, Any], user_id: int, table_id: str, buyin: int) -> None:
    account = account_from_mapping(balances, user_id)
    if ESCROW_KEY in account:
        raise ValueError("Finish your current Hold'em table first.")
    if account["cash"] < buyin:
        raise ValueError("Insufficient funds.")
    account["cash"] -= buyin
    account[ESCROW_KEY] = {"table_id": table_id, "buyin": buyin}
    balances[str(user_id)] = account


def resolve_escrow(balances: dict[str, Any], user_id: int, table_id: str, payout: int) -> bool:
    """Credit once and clear the marker in one balances write."""
    account = account_from_mapping(balances, user_id)
    escrow = account.get(ESCROW_KEY)
    if not escrow or escrow.get("table_id") != table_id:
        return False
    if payout < 0:
        raise ValueError("Payout cannot be negative.")
    account["cash"] += payout
    account.pop(ESCROW_KEY)
    balances[str(user_id)] = account
    return True


def recover_escrows(balances: dict[str, Any], settlements: dict[str, dict[str, int]]) -> list[tuple[int, int, str]]:
    """Settle saved results, or refund buy-ins for interrupted hands."""
    resolved = []
    for user_key, stored in list(balances.items()):
        escrow = stored.get(ESCROW_KEY) if isinstance(stored, dict) else None
        if not isinstance(escrow, dict):
            continue
        table_id = str(escrow.get("table_id", ""))
        buyin = int(escrow.get("buyin", 0))
        if not table_id or buyin <= 0:
            continue
        payout = int(settlements.get(table_id, {}).get(user_key, buyin))
        if resolve_escrow(balances, int(user_key), table_id, payout):
            resolved.append((int(user_key), payout, table_id))
    return resolved
