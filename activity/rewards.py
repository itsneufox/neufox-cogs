"""LWD$ payouts through the Economy cog: level milestones and periodic leaderboard rewards."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import discord

from .common import CURRENCY_NAME, DEFAULT_REWARD_AMOUNTS, MAX_LEVEL, REWARD_CATEGORIES


REWARD_CHECK_INTERVAL = 3600

log = logging.getLogger("red.neufox.activity")


def level_cash_total(old_level: int, new_level: int, interval: int, per_level: int) -> int:
    """Cash for every multiple of ``interval`` crossed between the two levels, paid as ``level x per_level``."""
    if interval <= 0 or per_level <= 0:
        return 0
    first = (int(old_level) // interval + 1) * interval
    return sum(level * per_level for level in range(first, min(int(new_level), MAX_LEVEL) + 1, interval))


def reward_amounts(stored: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    amounts = {category: dict(DEFAULT_REWARD_AMOUNTS[category]) for category in REWARD_CATEGORIES}
    for category, rewards in (stored or {}).items():
        if category in REWARD_CATEGORIES and isinstance(rewards, dict):
            amounts[category] = {str(rank): max(0, int(amount)) for rank, amount in rewards.items()}
    return amounts


class RewardsMixin:
    def _economy(self):
        economy = self.bot.get_cog("Economy")
        return economy if economy is not None and hasattr(economy, "add_balance") else None

    async def _level_cash_rule(self, guild: discord.Guild) -> tuple[int, int]:
        conf = self.config.guild(guild)
        return int(await conf.level_cash_interval()), int(await conf.level_cash_per_level())

    async def _pay_level_cash(self, guild, member, kind: str, old_level: int, new_level: int) -> int:
        economy = self._economy()
        if economy is None:
            return 0
        total = level_cash_total(old_level, new_level, *await self._level_cash_rule(guild))
        if total <= 0:
            return 0
        try:
            await economy.add_balance(member.id, total, actor_id=None, guild_id=guild.id, reason=f"leveling {kind} level {new_level} reward")
        except Exception:
            log.exception("Failed to pay level reward for %s in guild %s.", member.id, guild.id)
            return 0
        return total

    async def _reward_loop(self):
        await self.bot.wait_until_ready()
        while True:
            for guild in list(self.bot.guilds):
                try:
                    await self._pay_due_rewards(guild)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Activity reward payout failed in guild %s.", guild.id)
            await asyncio.sleep(REWARD_CHECK_INTERVAL)

    async def _pay_due_rewards(self, guild: discord.Guild) -> bool:
        paid = False
        for category in REWARD_CATEGORIES:
            result = await self._pay_leaderboard_rewards(guild, category)
            if result:
                paid = True
                await self._send_reward_log(guild, result)
        return paid

    async def _pay_leaderboard_rewards(self, guild: discord.Guild, category: str) -> str | None:
        economy = self._economy()
        if economy is None:
            return None
        data = await self.stats_config.guild(guild).all()
        if not data["reward_enabled"]:
            return None
        now = int(time.time())
        cooldown = int(data["reward_cooldown"])
        if cooldown and now - int(data["reward_last_paid"].get(category, 0)) < cooldown:
            return None
        amounts = reward_amounts(data["reward_amounts"]).get(category, {})
        if not amounts:
            return None
        scores = await self._board_scores(guild, category)
        entries = sorted(((int(uid), score) for uid, score in scores.items() if score > 0), key=lambda item: item[1], reverse=True)[:10]
        lines = []
        for rank, (user_id, _score) in enumerate(entries, start=1):
            amount = int(amounts.get(str(rank), 0))
            if amount <= 0:
                continue
            try:
                await economy.add_balance(
                    user_id,
                    amount,
                    actor_id=self.bot.user.id if self.bot.user else None,
                    guild_id=guild.id,
                    reason=f"Activity {category} leaderboard reward for rank {rank}",
                )
            except Exception:
                log.exception("Failed to pay %s leaderboard reward to %s.", category, user_id)
                continue
            lines.append(f"#{rank} {await self._display_name(guild, user_id)}: {amount:,} {CURRENCY_NAME}")
        if not lines:
            return None
        async with self.stats_config.guild(guild).reward_last_paid() as last_paid:
            last_paid[category] = now
        return f"Paid {category} leaderboard rewards:\n" + "\n".join(lines)

    async def _send_reward_log(self, guild: discord.Guild, text: str):
        channel_id = await self.stats_config.guild(guild).reward_log_channel_id()
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass
