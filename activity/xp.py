"""XP and levels. Data lives in the old Leveling Config namespace (``self.config``)."""

from __future__ import annotations

import inspect
import logging
import math
import random
import time
from typing import Any, Iterable

import discord

from .common import ACCENT, CURRENCY_NAME, MAX_LEVEL, can_manage_role, channel_ignore_ids, progress_bar
from .layouts import NO_PINGS


DEFAULT_CURVE = 2.0
MAX_XP_MULTIPLIER = 10.0
DEFAULT_LEVELUP_MESSAGE = "🎉 {member} reached {kind} level **{level}**{cash}!"
LEVELUP_PLACEHOLDERS = ("member", "name", "kind", "level", "old_level", "cash", "server")

log = logging.getLogger("red.neufox.activity")


def curve_scale(value: Any) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CURVE
    return min(20.0, max(0.1, scale))


def total_xp_for_level(level: int, curve: float = DEFAULT_CURVE) -> int:
    if level <= 0:
        return 0
    base_xp = 5 * level * (2 * level * level + 27 * level + 91) // 6
    return math.ceil(base_xp * curve_scale(curve))


def level_from_xp(xp: int, curve: float = DEFAULT_CURVE) -> int:
    xp = max(0, int(xp))
    if xp >= total_xp_for_level(MAX_LEVEL, curve):
        return MAX_LEVEL
    low, high = 0, MAX_LEVEL
    while low + 1 < high:
        mid = (low + high) // 2
        if total_xp_for_level(mid, curve) <= xp:
            low = mid
        else:
            high = mid
    return low


def format_levelup_message(template: str, **values: Any) -> str:
    """Fill a level-up template. A malformed template falls back to the default text."""
    filled = {key: values.get(key, "") for key in LEVELUP_PLACEHOLDERS}
    try:
        return (template or DEFAULT_LEVELUP_MESSAGE).format_map(filled)
    except (KeyError, IndexError, ValueError, AttributeError):
        return DEFAULT_LEVELUP_MESSAGE.format_map(filled)


def levelup_message_error(template: str) -> str | None:
    try:
        (template or "").format_map({key: "" for key in LEVELUP_PLACEHOLDERS})
    except (KeyError, IndexError, ValueError, AttributeError):
        return "Use only these placeholders: " + ", ".join(f"{{{key}}}" for key in LEVELUP_PLACEHOLDERS) + "."
    return None


def bonus_multiplier(member: Any, channel: Any, settings: dict[str, Any]) -> float:
    """Combined bonus from the server's bonus roles and bonus channels. 1.0 when neither applies."""
    multiplier = 1.0
    role_ids = set(map(str, settings.get("bonus_xp_role_ids") or []))
    if role_ids and any(str(role.id) in role_ids for role in getattr(member, "roles", [])):
        multiplier *= float(settings.get("bonus_role_multiplier", 1.0))
    channel_ids = set(map(str, settings.get("bonus_xp_channel_ids") or []))
    if channel is not None and channel_ids and channel_ignore_ids(channel) & channel_ids:
        multiplier *= float(settings.get("bonus_channel_multiplier", 1.0))
    return max(0.0, multiplier)


def normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_xp": int(record.get("chat_xp", 0)),
        "voice_xp": int(record.get("voice_xp", 0)),
        "display_name": str(record.get("display_name", "")),
        "announce_levelups": bool(record.get("announce_levelups", True)),
    }


def level_up_is_milestone(old_level: int, new_level: int, interval: int, cash_paid: int, role_levels: Iterable[Any]) -> bool:
    if cash_paid or any(old_level < int(level) <= new_level for level in role_levels):
        return True
    interval = max(1, int(interval))
    return new_level // interval > old_level // interval


def next_reward_text(guild: discord.Guild, level: int, kind_roles: dict[str, Any], cash_interval: int, cash_per_level: int) -> str | None:
    """Describe the nearest level above ``level`` that pays cash or awards a role."""
    rewards: dict[int, list[str]] = {}
    if cash_interval > 0 and cash_per_level > 0:
        cash_level = (level // cash_interval + 1) * cash_interval
        if cash_level <= MAX_LEVEL:
            rewards[cash_level] = [f"{cash_level * cash_per_level:,} {CURRENCY_NAME}"]
    for level_key, role_id in kind_roles.items():
        try:
            required, role = int(level_key), guild.get_role(int(role_id))
        except (TypeError, ValueError):
            continue
        if role is not None and required > level:
            rewards.setdefault(required, []).append(role.mention)
    if not rewards:
        return None
    nearest = min(rewards)
    return f"level {nearest}: " + " and ".join(rewards[nearest])


def rank_block(kind: str, xp: int, rank: int | None, curve: float, reward: str | None) -> str:
    level = level_from_xp(xp, curve)
    lines = [f"### {kind.title()}: Level {level}", f"Rank **{f'#{rank}' if rank else 'Unranked'}**  ·  **{xp:,}** XP"]
    if level >= MAX_LEVEL:
        lines.append("Max level")
    else:
        floor = total_xp_for_level(level, curve)
        needed = max(1, total_xp_for_level(level + 1, curve) - floor)
        into = max(0, xp - floor)
        lines.append(f"{progress_bar(into, needed)} {min(100, into * 100 // needed)}%  ·  {into:,}/{needed:,} XP")
    if reward:
        lines.append(f"-# Next reward at {reward}")
    return "\n".join(lines)


def xp_rank(users: dict[str, Any], user_id: int, kind: str) -> int | None:
    key = f"{kind}_xp"
    scores = {uid: normalized_record(record)[key] for uid, record in users.items()}
    mine = scores.get(str(user_id), 0)
    if mine <= 0:
        return None
    return sum(1 for score in scores.values() if score > mine) + 1


class XPMixin:
    """Awards XP, applies level roles and posts level-up messages."""

    async def _xp_multiplier(self, user_id: int) -> float:
        """XP booster multiplier sold by the Economy cog. 1.0 when Economy or the hook is missing."""
        economy = self.bot.get_cog("Economy")
        getter = getattr(economy, "get_multiplier", None) if economy is not None else None
        if not callable(getter):
            return 1.0
        try:
            value = getter(user_id, "xp")
            if inspect.isawaitable(value):
                value = await value
            value = float(value)
        except Exception:
            log.debug("Economy.get_multiplier failed for %s.", user_id, exc_info=True)
            return 1.0
        if not math.isfinite(value) or value <= 0:
            return 1.0
        return min(value, MAX_XP_MULTIPLIER)

    async def _boosted(self, user_id: int, amount: int) -> int:
        if amount <= 0:
            return 0
        multiplier = await self._xp_multiplier(user_id)
        return max(1, round(amount * multiplier)) if multiplier != 1.0 else amount

    async def _with_bonus(self, guild: discord.Guild, member: discord.Member, channel, amount: int) -> int:
        """Apply the server's bonus roles and, for chat, bonus channels."""
        conf = self.config.guild(guild)
        settings = {
            "bonus_xp_role_ids": await conf.bonus_xp_role_ids(),
            "bonus_role_multiplier": await conf.bonus_role_multiplier(),
            "bonus_xp_channel_ids": await conf.bonus_xp_channel_ids() if channel is not None else [],
            "bonus_channel_multiplier": await conf.bonus_channel_multiplier(),
        }
        multiplier = bonus_multiplier(member, channel, settings)
        if multiplier == 1.0 or amount <= 0:
            return amount
        return max(0, round(amount * multiplier))

    async def _award_chat_xp(self, message: discord.Message):
        guild, member = message.guild, message.author
        conf = self.config.guild(guild)
        cooldown = int(await conf.chat_cooldown())
        key = (guild.id, member.id)
        now = time.monotonic()
        if cooldown > 0 and now - self._chat_cooldowns.get(key, 0) < cooldown:
            return
        minimum, maximum = int(await conf.chat_xp_min()), int(await conf.chat_xp_max())
        if maximum <= 0:
            return
        self._chat_cooldowns[key] = now
        amount = await self._with_bonus(guild, member, message.channel, random.randint(minimum, maximum))
        amount = await self._boosted(member.id, amount)
        await self._grant_xp(guild, member, "chat", amount, message.channel)

    async def _award_voice_xp(self, guild: discord.Guild, member: discord.Member, minutes: int):
        if minutes <= 0:
            return
        conf = self.config.guild(guild)
        minimum, maximum = int(await conf.voice_xp_min()), int(await conf.voice_xp_max())
        if maximum <= 0:
            return
        amount = sum(random.randint(minimum, maximum) for _ in range(minutes))
        amount = await self._boosted(member.id, await self._with_bonus(guild, member, None, amount))
        await self._grant_xp(guild, member, "voice", amount, None)

    async def _grant_xp(self, guild, member, kind: str, amount: int, source_channel):
        level_up = await self._add_xp(guild, member, kind, amount)
        if level_up is None:
            return
        old_level, new_level = level_up
        await self._apply_level_roles(guild, member, kind, new_level)
        cash = await self._pay_level_cash(guild, member, kind, old_level, new_level)
        await self._announce_level_up(guild, member, kind, old_level, new_level, cash, source_channel)

    async def _add_xp(self, guild, member, kind: str, amount: int) -> tuple[int, int] | None:
        if amount <= 0:
            return None
        curve = curve_scale(await self.config.guild(guild).level_curve_scale())
        async with self._lock_for(guild.id):
            async with self.config.guild(guild).users() as users:
                user_id = str(member.id)
                record = normalized_record(users.get(user_id, {}))
                old_level = level_from_xp(record[f"{kind}_xp"], curve)
                record[f"{kind}_xp"] += int(amount)
                record["display_name"] = member.display_name
                users[user_id] = record
                new_level = level_from_xp(record[f"{kind}_xp"], curve)
        return (old_level, new_level) if new_level > old_level else None

    async def _apply_level_roles(self, guild: discord.Guild, member: discord.Member, kind: str, level: int):
        me = guild.me
        if member.id == guild.owner_id or me is None or not me.guild_permissions.manage_roles or me.top_role <= member.top_role:
            return
        conf = self.config.guild(guild)
        level_roles = await conf.level_roles()
        stack = bool(await conf.level_roles_stack())
        kind_roles = level_roles.get(kind, {})
        changed = False
        earned: list[tuple[int, discord.Role]] = []
        for level_key, role_id in list(kind_roles.items()):
            try:
                required, role = int(level_key), guild.get_role(int(role_id))
            except (TypeError, ValueError):
                required, role = 0, None
            if role is None:
                kind_roles.pop(level_key, None)
                changed = True
                continue
            if required <= level:
                earned.append((required, role))
        # Stacking keeps every earned role; otherwise only the highest one is kept.
        keep = earned if stack else sorted(earned, key=lambda item: item[0])[-1:]
        keep_roles = {role for _, role in keep}
        for _, role in keep:
            if role in member.roles or not can_manage_role(guild, role):
                continue
            try:
                await member.add_roles(role, reason=f"Activity {kind} level {level}")
            except discord.HTTPException:
                continue
        if not stack and keep_roles:
            stale = [role for _, role in earned if role not in keep_roles and role in member.roles and can_manage_role(guild, role)]
            if stale:
                try:
                    await member.remove_roles(*stale, reason=f"Activity {kind} level {level}: replaced by a higher level role")
                except discord.HTTPException:
                    pass
        if changed:
            level_roles[kind] = kind_roles
            await conf.level_roles.set(level_roles)

    async def _announce_level_up(self, guild, member, kind: str, old_level: int, level: int, cash_paid: int, source_channel):
        conf = self.config.guild(guild)
        if not await conf.announce_levelups():
            return
        role_levels = (await conf.level_roles()).get(kind, {})
        if not level_up_is_milestone(old_level, level, await conf.announce_interval(), cash_paid, role_levels):
            return
        if not await self._announce_preference(guild, member.id):
            return
        if await conf.levelup_dm():
            channel = member
        else:
            channel_id = await conf.levelup_channel_id()
            channel = guild.get_channel(channel_id) if channel_id else (source_channel if kind == "chat" else None)
        if channel is None or not hasattr(channel, "send"):
            return
        cash_text = f" and earned **{cash_paid:,}** {CURRENCY_NAME}" if cash_paid else ""
        text = format_levelup_message(
            await conf.levelup_message(),
            member=member.mention,
            name=member.display_name,
            kind=kind,
            level=level,
            old_level=old_level,
            cash=cash_text,
            server=guild.name,
        )
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(text[:4000]), accent_color=ACCENT))
        pings = discord.AllowedMentions(users=[member]) if await conf.levelup_ping() else NO_PINGS
        try:
            await channel.send(view=view, allowed_mentions=pings)
        except discord.HTTPException:
            pass

    async def _announce_preference(self, guild: discord.Guild, user_id: int) -> bool:
        users = await self.config.guild(guild).users()
        return normalized_record(users.get(str(user_id), {}))["announce_levelups"]

    async def _set_announce_preference(self, guild: discord.Guild, member: discord.Member, enabled: bool):
        async with self._lock_for(guild.id):
            async with self.config.guild(guild).users() as users:
                record = normalized_record(users.get(str(member.id), {}))
                record["announce_levelups"] = bool(enabled)
                record["display_name"] = member.display_name
                users[str(member.id)] = record
