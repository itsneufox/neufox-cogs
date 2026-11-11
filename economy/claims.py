from __future__ import annotations

import random
import time

import discord
from redbot.core import commands

from . import sinks
from .constants import CASH, CURRENCY_NAME, GOLD
from .layouts import send_embed

CLAIM_LABELS = {
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "annual": "Annual",
    "work": "Work",
}


class ClaimsMixin:
    """One /claim that collects every ready reward, with a daily streak."""

    @commands.hybrid_command(name="claim", aliases=["daily", "weekly", "monthly", "annual", "work"])
    async def claim(self, ctx: commands.Context):
        """Claim every LWD$ reward that is ready: daily, weekly, monthly, annual and work."""
        await send_embed(ctx, await self._claim_all(ctx.author, ctx.guild))

    async def _claim_settings(self) -> dict[str, dict[str, int]]:
        settings = {}
        for kind in sinks.CALENDAR_CLAIM_TYPES:
            settings[kind] = {
                "amount": int(await getattr(self.config, f"{kind}_amount")()),
                "cooldown": int(await getattr(self.config, f"{kind}_cooldown")()),
            }
        low = max(0, int(await self.config.work_min()))
        high = max(0, int(await self.config.work_max()))
        settings["work"] = {
            "amount": random.randint(min(low, high), high) if high > 0 else 0,
            "cooldown": int(await self.config.work_cooldown()),
        }
        return settings

    async def _claim_all(self, user: discord.abc.User, guild: discord.Guild | None) -> discord.Embed:
        """Claim every ready reward atomically and return the receipt."""
        now = int(time.time())
        settings = await self._claim_settings()
        per_day = int(await self.config.streak_bonus_per_day())
        cap = int(await self.config.streak_bonus_cap())
        multiplier = await self.get_multiplier(user.id, "claim")
        savers = int(await self.config.user(user).streak_savers())
        guild_id = guild.id if guild else None

        bonus = 0
        savers_used = 0
        total = 0
        account = None
        async with self._lock:
            async with self.config.claims() as claims:
                user_claims = claims.setdefault(str(user.id), {})
                plan = sinks.plan_claims(now, user_claims, settings)
                ready = plan["ready"]
                streak = int(user_claims.get("streak", 0) or 0)
                if "daily" in ready:
                    streak, savers_used = sinks.advance_streak(streak, user_claims.get("daily", 0), now, savers)
                    bonus = sinks.streak_bonus(streak, per_day, cap)
                    user_claims["streak"] = streak
                for kind in ready:
                    user_claims[kind] = now
                last_daily = int(user_claims.get("daily", 0) or 0)
            base = sum(ready.values())
            total = sinks.apply_multiplier(base + bonus, multiplier)
            if total > 0:
                total = self._require_amount(total, allow_zero=True)
                async with self.config.balances() as balances:
                    account = self._account_from_mapping(balances, user.id)
                    account[CASH] += total
                    balances[str(user.id)] = account
                await self._append_ledger(
                    "claim",
                    from_user_id=None,
                    to_user_id=user.id,
                    amount=total,
                    actor_id=user.id,
                    guild_id=guild_id,
                    reason=f"claim: {', '.join(ready)}; streak {streak}; x{multiplier:g}",
                )
        if savers_used:
            await self.config.user(user).streak_savers.set(max(0, savers - savers_used))
        if account is None:
            account = await self.get_balance(user.id)
        return self._claim_receipt(
            plan, settings, now, streak=streak, bonus=bonus, savers_used=savers_used,
            multiplier=multiplier, total=total, balance=int(account[CASH]), last_daily=last_daily,
        )

    def _claim_receipt(
        self,
        plan: dict,
        settings: dict[str, dict[str, int]],
        now: int,
        *,
        streak: int,
        bonus: int,
        savers_used: int,
        multiplier: float,
        total: int,
        balance: int,
        last_daily: int,
    ) -> discord.Embed:
        ready = plan["ready"]
        if ready:
            lines = [f"**{CLAIM_LABELS[kind]}:** {amount:,} {CURRENCY_NAME}" for kind, amount in ready.items()]
            if bonus:
                lines.append(f"**Streak bonus:** {bonus:,} {CURRENCY_NAME}")
            base = sum(ready.values()) + bonus
            if total > base:
                lines.append(f"**Claim booster x{multiplier:g}:** +{total - base:,} {CURRENCY_NAME}")
            embed = discord.Embed(
                title=f"You claimed {total:,} {CURRENCY_NAME}",
                description="\n".join(lines),
                color=GOLD,
            )
        else:
            embed = discord.Embed(
                title="Nothing to claim yet",
                description="Every reward is on cooldown. Next resets are below.",
                color=discord.Color.light_grey(),
            )

        streak_line = f"{streak:,} day{'s' if streak != 1 else ''}"
        if savers_used:
            streak_line += f" (used {savers_used} streak saver{'s' if savers_used != 1 else ''})"
        missed = sinks.streak_at_risk(last_daily, now)
        if missed and "daily" not in ready:
            streak_line += f" — {missed} day(s) missed; a streak saver from `/shop perk` keeps it"
        embed.add_field(name="Daily streak", value=streak_line, inline=True)
        embed.add_field(name="Balance", value=f"{balance:,} {CURRENCY_NAME}", inline=True)

        resets = []
        for kind in sinks.CLAIM_TYPES:
            if kind in plan["disabled"]:
                continue
            if kind in ready:
                next_at = sinks.next_claim_time(kind, now, settings[kind]["cooldown"])
            else:
                next_at = plan["waiting"][kind]
            resets.append(f"**{CLAIM_LABELS[kind]}:** <t:{next_at}:R>")
        if resets:
            embed.add_field(name="Next resets", value="\n".join(resets), inline=False)
        embed.set_footer(text="Daily, weekly, monthly and annual reset at 00:00 UTC; work is a rolling cooldown.")
        return embed
