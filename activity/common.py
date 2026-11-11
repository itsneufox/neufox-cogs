"""Constants and small helpers shared by the Activity modules. No Config access here."""

from __future__ import annotations

from typing import Any

import discord


ACCENT = discord.Color.blurple()
CURRENCY_NAME = "LWD$"

# Old cog identities. Activity reopens both Config namespaces so existing data keeps working.
LEVELING_CONFIG = {"identifier": 902410375, "cog_name": "Leveling"}
STATS_CONFIG = {"identifier": 248935701, "cog_name": "ActivityStats"}

XP_KINDS = ("chat", "voice")
MAX_LEVEL = 999999
PROGRESS_BAR_WIDTH = 14
PAGE_SIZE = 10
RANK_PREFIXES = {1: "🥇", 2: "🥈", 3: "🥉"}

# Leaderboard types: key -> (label, short description).
BOARDS = {
    "xp": ("Chat XP", "Chat levels and XP"),
    "voicexp": ("Voice XP", "Voice levels and XP"),
    "messages": ("Messages", "Most messages sent"),
    "voice": ("Voice time", "Most time in voice"),
    "reactions": ("Reactions", "Most reactions received"),
}
BOARD_ALIASES = {
    "chat": "xp",
    "level": "xp",
    "levels": "xp",
    "chatxp": "xp",
    "vxp": "voicexp",
    "msgs": "messages",
    "message": "messages",
    "vc": "voice",
    "voicetime": "voice",
    "reacts": "reactions",
    "reaction": "reactions",
}

TOP_ROLE_TIERS = ("first", "second", "third")
REWARD_CATEGORIES = ("messages", "voice", "reactions")
DEFAULT_REWARD_AMOUNTS = {category: {"1": 100, "2": 50, "3": 25} for category in REWARD_CATEGORIES}


def normalize_board(value: str | None) -> str | None:
    if not value:
        return "xp"
    value = value.lower().strip()
    return value if value in BOARDS else BOARD_ALIASES.get(value)


def format_duration(seconds: Any) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = [f"{value}{unit}" for value, unit in ((days, "d"), (hours, "h"), (minutes, "m")) if value]
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def progress_bar(current: int, needed: int) -> str:
    filled = int(PROGRESS_BAR_WIDTH * min(current, needed) / max(needed, 1))
    return "▰" * filled + "▱" * (PROGRESS_BAR_WIDTH - filled)


def rank_of(scores: dict[str, Any], user_id: int) -> int | None:
    """1-based rank of a user in a {user_id: score} mapping, or None without a positive score."""
    score = int(scores.get(str(user_id), 0) or 0)
    if score <= 0:
        return None
    return sum(1 for other in scores.values() if int(other or 0) > score) + 1


def emoji_key(emoji: Any) -> str:
    if isinstance(emoji, str):
        return emoji
    if getattr(emoji, "id", None):
        return str(emoji)
    return getattr(emoji, "name", None) or str(emoji)


def channel_ignore_ids(channel: Any) -> set[str]:
    """IDs that can ignore a channel: itself, its thread parent, and their categories."""
    ids = {str(channel.id)}
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is not None:
        ids.add(str(parent_id))
    category = getattr(channel, "category", None)
    if category is not None:
        ids.add(str(category.id))
    parent_category = getattr(getattr(channel, "parent", None), "category", None)
    if parent_category is not None:
        ids.add(str(parent_category.id))
    return ids


def channel_reference(channel: Any) -> str:
    if hasattr(channel, "mention"):
        return channel.mention
    return f"{channel.name} (`{channel.id}`)"


def can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
    me = guild.me
    if me is None or role.is_default() or role.managed:
        return False
    return me.guild_permissions.manage_roles and role < me.top_role


def entry_author_id(entry: Any) -> Any:
    return entry.get("author_id") if isinstance(entry, dict) else entry


def entry_channel_id(entry: Any) -> Any:
    return entry.get("channel_id") if isinstance(entry, dict) else None


def apply_reaction_delta(received: dict[str, dict[str, int]], author_id: str, emoji: str, delta: int):
    user_reactions = received.setdefault(author_id, {})
    count = int(user_reactions.get(emoji, 0)) + delta
    if count <= 0:
        user_reactions.pop(emoji, None)
    else:
        user_reactions[emoji] = count
    if not user_reactions:
        received.pop(author_id, None)
