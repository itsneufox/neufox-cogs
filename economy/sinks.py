"""Pure claim, fee and money-sink rules for the Economy cog.

Nothing here touches Discord, Red or Config, so every rule can be unit tested on
its own. All amounts are whole LWD$; percentages are floats that are converted
to basis points so rounding is predictable (always down, in the player's favour).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

CALENDAR_CLAIM_TYPES = ("daily", "weekly", "monthly", "annual")
CLAIM_TYPES = (*CALENDAR_CLAIM_TYPES, "work")
CALENDAR_CLAIM_PERIODS = {
    "daily": "UTC day",
    "weekly": "UTC week",
    "monthly": "UTC month",
    "annual": "UTC year",
}
BASIS_POINTS = 10_000
MAX_PERCENT = 100.0
MAX_MULTIPLIER = 10.0
DAY = 86_400
HOUR = 3_600

UPKEEP_ACTIVE = "active"
UPKEEP_RENEW = "renew"
UPKEEP_LAPSE = "lapse"

_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")
_MASS_MENTION = re.compile(r"@(everyone|here)", re.IGNORECASE)


# Percentages and fees -------------------------------------------------------


def percent_to_bp(percent: Any) -> int:
    """Clamp a percentage to 0-100 and convert it to basis points."""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return 0
    if value != value or value <= 0:
        return 0
    return int(round(min(value, MAX_PERCENT) * 100))


def percent_of(amount: int, percent: Any) -> int:
    """Floor ``percent`` of ``amount``. Never negative, never above the amount."""
    amount = max(0, int(amount))
    return min(amount, amount * percent_to_bp(percent) // BASIS_POINTS)


def transfer_tax(amount: int, percent: Any) -> int:
    """Tax charged to the sender on top of a /pay transfer."""
    return percent_of(amount, percent)


def house_cut(amount: int, percent: Any) -> int:
    """House share removed from rain, bounty and auction payouts."""
    return percent_of(amount, percent)


def casino_rake(payout: int, wager: int, percent: Any) -> int:
    """Rake taken from the profit of a winning casino result only."""
    profit = int(payout) - int(wager)
    if profit <= 0:
        return 0
    return percent_of(profit, percent)


def wealth_tax(balance: int, threshold: int, percent: Any, cap: int) -> int:
    """Daily tax on the part of a balance above ``threshold``; ``cap`` 0 means uncapped."""
    balance = int(balance)
    threshold = max(0, int(threshold))
    if balance <= threshold:
        return 0
    tax = percent_of(balance - threshold, percent)
    if int(cap) > 0:
        tax = min(tax, int(cap))
    return max(0, min(tax, balance))


def split_rain(amount: int, recipients: list[int], cut_percent: Any) -> dict[str, Any]:
    """Split ``amount`` evenly between recipients after the house cut.

    The integer-division remainder is burned with the cut, so the sender is
    always charged exactly ``amount``.
    """
    amount = int(amount)
    unique = list(dict.fromkeys(int(user_id) for user_id in recipients))
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if not unique:
        raise ValueError("Nobody has been active here recently.")
    cut = house_cut(amount, cut_percent)
    pool = amount - cut
    share = pool // len(unique)
    if share <= 0:
        raise ValueError("That amount is too small to split between everyone.")
    remainder = pool - share * len(unique)
    return {
        "cut": cut,
        "share": share,
        "burned": cut + remainder,
        "payouts": {user_id: share for user_id in unique},
    }


def apply_multiplier(amount: int, multiplier: Any) -> int:
    try:
        value = float(multiplier)
    except (TypeError, ValueError):
        value = 1.0
    if value != value or value < 1.0:
        value = 1.0
    value = min(value, MAX_MULTIPLIER)
    return int(int(amount) * value)


# Time helpers ---------------------------------------------------------------


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, DAY)
    hours, seconds = divmod(seconds, HOUR)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def format_utc_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def utc_day(timestamp: int) -> int:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).date().toordinal()


def next_calendar_reset(kind: str, now: datetime) -> int:
    now = now.astimezone(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "daily":
        next_reset = midnight + timedelta(days=1)
    elif kind == "weekly":
        next_reset = midnight - timedelta(days=now.weekday()) + timedelta(days=7)
    elif kind == "monthly":
        year = now.year + int(now.month == 12)
        month = 1 if now.month == 12 else now.month + 1
        next_reset = datetime(year, month, 1, tzinfo=timezone.utc)
    elif kind == "annual":
        next_reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_reset = now
    return int(next_reset.timestamp())


def format_claim_interval(kind: str, cooldown_seconds: int) -> str:
    if int(cooldown_seconds) <= 0:
        return "disabled"
    if kind in CALENDAR_CLAIM_TYPES:
        return f"once per {CALENDAR_CLAIM_PERIODS[kind]}"
    return f"every {format_duration(cooldown_seconds)}"


# Claims ---------------------------------------------------------------------


def claim_enabled(amount: int, cooldown: int) -> bool:
    """A claim with no reward or no cooldown is switched off."""
    return int(amount) > 0 and int(cooldown) > 0


def next_claim_time(kind: str, last_claimed: int, cooldown: int) -> int:
    """When ``kind`` can next be claimed; 0 means right away."""
    last_claimed = int(last_claimed or 0)
    if last_claimed <= 0:
        return 0
    if kind in CALENDAR_CLAIM_TYPES:
        return next_calendar_reset(kind, datetime.fromtimestamp(last_claimed, timezone.utc))
    return last_claimed + max(0, int(cooldown))


def plan_claims(now: int, last: dict[str, Any], settings: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Decide which claims are ready.

    ``settings`` maps each claim type to ``{"amount": int, "cooldown": int}``. The
    work amount must already be rolled by the caller. Returns ready amounts,
    the next time for each waiting claim and the disabled claim types.
    """
    ready: dict[str, int] = {}
    waiting: dict[str, int] = {}
    disabled: list[str] = []
    for kind in CLAIM_TYPES:
        setting = settings.get(kind)
        if not setting or not claim_enabled(setting.get("amount", 0), setting.get("cooldown", 0)):
            disabled.append(kind)
            continue
        next_at = next_claim_time(kind, int(last.get(kind, 0) or 0), int(setting["cooldown"]))
        if now >= next_at:
            ready[kind] = int(setting["amount"])
        else:
            waiting[kind] = next_at
    return {"ready": ready, "waiting": waiting, "disabled": disabled}


def advance_streak(streak: int, last_daily: int, now: int, savers: int) -> tuple[int, int]:
    """Return the streak after claiming the daily reward now, and the savers used.

    Claiming on the next UTC day continues the streak. Each missed day needs one
    streak saver; without enough savers the streak restarts at 1.
    """
    streak = max(0, int(streak))
    last_daily = int(last_daily or 0)
    if streak <= 0 or last_daily <= 0:
        return 1, 0
    missed = utc_day(now) - utc_day(last_daily) - 1
    if missed < 0:
        return streak, 0
    if missed == 0:
        return streak + 1, 0
    if int(savers) >= missed:
        return streak + 1, missed
    return 1, 0


def streak_bonus(streak: int, per_day: int, cap: int) -> int:
    """Flat bonus for every consecutive day after the first, optionally capped."""
    if int(streak) <= 1 or int(per_day) <= 0:
        return 0
    bonus = int(per_day) * (int(streak) - 1)
    return min(bonus, int(cap)) if int(cap) > 0 else bonus


def streak_at_risk(last_daily: int, now: int) -> int:
    """Days already missed since the last daily claim (0 while the streak is safe)."""
    if int(last_daily or 0) <= 0:
        return 0
    return max(0, utc_day(now) - utc_day(last_daily) - 1)


# Rentals, boosters and expiries ---------------------------------------------


def extend_until(until: int, now: int, seconds: int) -> int:
    """Stack time onto an active deadline, or start a new one from now."""
    return max(int(until or 0), int(now)) + max(0, int(seconds))


def upkeep_decision(until: int, now: int, renew: bool, balance: int, price: int) -> str:
    """What to do with a rental whose deadline is ``until``."""
    if int(now) < int(until):
        return UPKEEP_ACTIVE
    if renew and int(price) > 0 and int(balance) >= int(price):
        return UPKEEP_RENEW
    return UPKEEP_LAPSE


def is_expired(until: int, now: int) -> bool:
    return int(until or 0) <= int(now)


def active_multiplier(boosters: dict[str, Any] | None, kind: str, now: int) -> float:
    """Return the booster multiplier for ``kind`` or 1.0 when none is active."""
    if not isinstance(boosters, dict):
        return 1.0
    booster = boosters.get(kind)
    if not isinstance(booster, dict):
        return 1.0
    try:
        until = int(booster.get("until", 0))
        multiplier = float(booster.get("multiplier", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if until <= int(now) or multiplier != multiplier or multiplier < 1.0:
        return 1.0
    return min(multiplier, MAX_MULTIPLIER)


def min_next_bid(starting_bid: int, current_bid: int, has_bid: bool, increment_percent: int = 5) -> int:
    """The lowest acceptable bid: the start price, or the top bid plus 5% (at least 1)."""
    if not has_bid:
        return max(1, int(starting_bid))
    current_bid = int(current_bid)
    return current_bid + max(1, current_bid * int(increment_percent) // 100)


# Text -----------------------------------------------------------------------


def parse_hex_color(text: str) -> int | None:
    match = _HEX_COLOR.match(str(text or "").strip())
    return int(match.group(1), 16) if match else None


def clean_text(text: str, limit: int, *, single_line: bool = True) -> str:
    """Trim user text for titles, role names and shoutouts; neutralise mass mentions."""
    value = str(text or "")
    if single_line:
        value = " ".join(value.split())
    else:
        value = value.strip()
    value = _MASS_MENTION.sub(lambda match: "@​" + match.group(1), value)
    return value[: max(0, int(limit))]
