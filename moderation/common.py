"""Constants and helpers shared by every part of the Moderation cog."""

from __future__ import annotations

from datetime import datetime, timezone

import discord


DEFAULT_COLOR = discord.Color.red()
DEFAULT_REASON = "No reason provided."

# Storage of the cogs that were merged into Moderation. Config is keyed by
# (cog name, identifier), so opening these keeps every server's existing data.
ADMINHELPER_STORAGE = ("AdminHelper", 911223335)
AUTOMOD_STORAGE = ("AntiAbuse", 911223334)
HONEYPOT_STORAGE = ("Honeypot", 948372645)

REQUIRED_PERMISSIONS = {
    "timeout": "moderate_members",
    "kick": "kick_members",
    "ban": "ban_members",
    "warn": None,
}


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def audit_reason(moderator: discord.abc.User, reason: str) -> str:
    return f"{moderator} ({moderator.id}): {reason or DEFAULT_REASON}"[:512]


def avatar_url(user: discord.abc.User | None) -> str:
    if user is None:
        return ""
    for attribute in ("display_avatar", "avatar_url", "default_avatar_url"):
        value = getattr(user, attribute, None)
        if value is None:
            continue
        return str(getattr(value, "url", value))
    return ""


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def can_moderate_member(
    bot,
    guild: discord.Guild | None,
    member: discord.Member,
    action: str,
    *,
    actor: discord.Member | None = None,
) -> tuple[bool, str]:
    """Check hierarchy and permissions before acting on a member."""
    if guild is None:
        return False, "this command can only be used in a server."
    if member.guild.id != guild.id:
        return False, "that member is not in this server."
    if member.id == guild.owner_id:
        return False, "I cannot moderate the server owner."

    bot_member = guild.me
    if bot_member is None:
        return False, "I cannot find my server member record."
    if member.id == bot_member.id:
        return False, "I cannot moderate myself."
    try:
        is_owner = await bot.is_owner(member)
    except Exception:
        is_owner = False
    if is_owner:
        return False, "I cannot moderate a bot owner."

    if action not in REQUIRED_PERMISSIONS:
        return False, "unknown moderation action."
    required_permission = REQUIRED_PERMISSIONS[action]
    if required_permission and not getattr(bot_member.guild_permissions, required_permission, False):
        return False, f"I am missing the `{required_permission}` permission."
    if action != "warn" and member.top_role >= bot_member.top_role:
        return False, "that member's top role is higher than or equal to mine."

    if actor is not None:
        if member.id == actor.id:
            return False, "you cannot moderate yourself."
        if actor.id != guild.owner_id and member.top_role >= actor.top_role:
            return False, "that member's top role is higher than or equal to yours."
    return True, ""
