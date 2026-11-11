from __future__ import annotations

import logging
import time
from typing import Any

import discord

from .constants import (
    CASH,
    CURRENCY_NAME,
    MAX_AMOUNT,
    MAX_LEDGER_ENTRIES,
)
from .errors import EconomyError
from .holdem_escrow import account_from_mapping as holdem_account_from_mapping

log = logging.getLogger("red.neufox.economy")


class CoreMixin:
    """Balances, the ledger and economy logs."""

    async def get_balance(self, user_id: int) -> dict[str, int]:
        """Public cog API: return a user's balances."""
        balances = await self.config.balances()
        return {CASH: self._account_from_mapping(balances, user_id)[CASH]}

    async def add_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        guild_id: int | None = None,
        reason: str = "api add",
    ) -> dict[str, int]:
        """Public cog API: add LWD$ to a user."""
        return await self._adjust_balance(user_id, amount, actor_id=actor_id, guild_id=guild_id, reason=reason, operation="add")

    async def remove_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        guild_id: int | None = None,
        reason: str = "api remove",
    ) -> dict[str, int]:
        """Public cog API: remove LWD$ from a user."""
        return await self._adjust_balance(user_id, amount, actor_id=actor_id, guild_id=guild_id, reason=reason, operation="remove")

    async def set_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        guild_id: int | None = None,
        reason: str = "api set",
    ) -> dict[str, int]:
        """Public cog API: set a user's LWD$ balance."""
        return await self._adjust_balance(user_id, amount, actor_id=actor_id, guild_id=guild_id, reason=reason, operation="set")

    async def transfer_balance(
        self,
        from_user_id: int,
        to_user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        guild_id: int | None = None,
        reason: str = "api transfer",
    ) -> dict[str, dict[str, int]]:
        """Public cog API: transfer LWD$ between users."""
        amount = self._require_amount(amount, allow_zero=False)
        if from_user_id == to_user_id:
            raise EconomyError("You cannot transfer to yourself.")
        async with self._lock:
            async with self.config.balances() as balances:
                source = self._account_from_mapping(balances, from_user_id)
                target = self._account_from_mapping(balances, to_user_id)
                if source[CASH] < amount:
                    raise EconomyError("Insufficient funds.")
                source[CASH] -= amount
                target[CASH] += amount
                balances[str(from_user_id)] = source
                balances[str(to_user_id)] = target
            await self._append_ledger(
                "transfer",
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                amount=amount,
                actor_id=actor_id,
                guild_id=guild_id,
                reason=reason,
            )
        return {"from": source, "to": target}

    async def _adjust_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None,
        guild_id: int | None,
        reason: str,
        operation: str,
    ) -> dict[str, int]:
        amount = self._require_amount(amount, allow_zero=True)
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                if operation == "add":
                    account[CASH] += amount
                elif operation == "remove":
                    if account[CASH] < amount:
                        raise EconomyError("Insufficient funds.")
                    account[CASH] -= amount
                elif operation == "set":
                    account[CASH] = amount
                else:
                    raise EconomyError("Unknown operation.")
                balances[str(user_id)] = account
            await self._append_ledger(
                operation,
                from_user_id=None,
                to_user_id=user_id,
                amount=amount,
                actor_id=actor_id,
                guild_id=guild_id,
                reason=reason,
            )
        return account

    async def _append_ledger(
        self,
        tx_type: str,
        *,
        from_user_id: int | None,
        to_user_id: int | None,
        amount: int,
        actor_id: int | None,
        guild_id: int | None,
        reason: str,
        log_to_channel: bool = True,
    ):
        tx_id = int(await self.config.next_tx())
        await self.config.next_tx.set(tx_id + 1)
        entry = {
            "id": tx_id,
            "type": tx_type,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "amount": amount,
            "actor_id": actor_id,
            "guild_id": guild_id,
            "reason": str(reason or "")[:250],
            "created_at": int(time.time()),
        }
        async with self.config.ledger() as ledger:
            ledger.append(entry)
            del ledger[:-MAX_LEDGER_ENTRIES]
        if guild_id and log_to_channel:
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self._send_economy_log(
                    guild,
                    tx_type,
                    amount=amount,
                    actor_id=actor_id,
                    target_id=to_user_id,
                    reason=reason,
                    item_name=None,
                    item_quantity=None,
                )

    async def _send_economy_log(
        self,
        guild: discord.Guild,
        action: str,
        *,
        amount: int | None,
        actor_id: int | None,
        target_id: int | None,
        reason: str,
        item_name: str | None,
        item_quantity: int | None,
    ):
        channels = await self.config.log_channels()
        channel_id = channels.get(str(guild.id))
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(title="Economy Log", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(
            name="Amount",
            value=f"{amount:,} {CURRENCY_NAME}" if amount is not None else "N/A",
            inline=True,
        )
        if actor_id:
            embed.add_field(name="Executor", value=f"<@{actor_id}>", inline=True)
        if target_id:
            embed.add_field(name="Recipient", value=f"<@{target_id}>", inline=True)
        if item_name:
            embed.add_field(name="Item", value=item_name, inline=True)
        if item_quantity:
            embed.add_field(name="Item Amount", value=f"{item_quantity:,}", inline=True)
        if reason:
            embed.add_field(name="Reason", value=str(reason)[:1024], inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Could not send economy log in guild %s", guild.id)

    @staticmethod
    def _account_from_mapping(balances: dict[str, Any], user_id: int) -> dict[str, Any]:
        return holdem_account_from_mapping(balances, user_id)

    async def _display_user(self, guild: discord.Guild | None, user_id: int) -> str:
        if guild is not None:
            member = guild.get_member(user_id)
            if member:
                return member.display_name
        user = self.bot.get_user(user_id)
        if user:
            return user.name
        return f"Unknown user ({user_id})"

    @staticmethod
    def _require_amount(amount: Any, *, allow_zero: bool) -> int:
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise EconomyError("Amount must be an integer.")
        if amount < 0 or (amount == 0 and not allow_zero):
            raise EconomyError("Amount must be positive.")
        if amount > MAX_AMOUNT:
            raise EconomyError(f"Amount cannot exceed {MAX_AMOUNT:,}.")
        return amount

    async def _set_log_channel(self, guild_id: int, channel_id: int | None):
        async with self.config.log_channels() as channels:
            if channel_id is None:
                channels.pop(str(guild_id), None)
            else:
                channels[str(guild_id)] = channel_id

    async def _charge(
        self,
        user_id: int,
        amount: int,
        *,
        tx_type: str,
        reason: str,
        guild_id: int | None = None,
        actor_id: int | None = None,
        log_to_channel: bool = True,
    ) -> dict[str, int]:
        """Take LWD$ out of circulation for a money sink, with a ledger entry."""
        amount = self._require_amount(amount, allow_zero=False)
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                if account[CASH] < amount:
                    raise EconomyError(
                        f"Insufficient funds. That costs {amount:,} {CURRENCY_NAME}; "
                        f"you have {account[CASH]:,} {CURRENCY_NAME}."
                    )
                account[CASH] -= amount
                balances[str(user_id)] = account
            await self._append_ledger(
                tx_type,
                from_user_id=user_id,
                to_user_id=None,
                amount=amount,
                actor_id=actor_id or user_id,
                guild_id=guild_id,
                reason=reason,
                log_to_channel=log_to_channel,
            )
        return account

    async def _credit(
        self,
        user_id: int,
        amount: int,
        *,
        tx_type: str,
        reason: str,
        guild_id: int | None = None,
        actor_id: int | None = None,
        log_to_channel: bool = True,
    ) -> dict[str, int]:
        """Pay LWD$ to a user (refunds and escrow releases), with a ledger entry."""
        amount = self._require_amount(amount, allow_zero=False)
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                account[CASH] += amount
                balances[str(user_id)] = account
            await self._append_ledger(
                tx_type,
                from_user_id=None,
                to_user_id=user_id,
                amount=amount,
                actor_id=actor_id,
                guild_id=guild_id,
                reason=reason,
                log_to_channel=log_to_channel,
            )
        return account

    async def _pay_out(
        self,
        payer_id: int | None,
        payouts: dict[int, int],
        burned: int,
        *,
        tx_type: str,
        guild_id: int | None,
        reason: str,
    ) -> dict[str, int] | None:
        """Atomically pay several users and burn a house cut.

        With ``payer_id`` the payer is charged ``sum(payouts) + burned``; without
        one the money comes from escrow that was already taken out earlier.
        """
        payouts = {int(user_id): self._require_amount(amount, allow_zero=True) for user_id, amount in payouts.items()}
        burned = self._require_amount(burned, allow_zero=True)
        total = sum(payouts.values()) + burned
        payer_account = None
        async with self._lock:
            async with self.config.balances() as balances:
                if payer_id is not None:
                    payer_account = self._account_from_mapping(balances, payer_id)
                    if payer_account[CASH] < total:
                        raise EconomyError(
                            f"Insufficient funds. That costs {total:,} {CURRENCY_NAME}; "
                            f"you have {payer_account[CASH]:,} {CURRENCY_NAME}."
                        )
                    payer_account[CASH] -= total
                    balances[str(payer_id)] = payer_account
                for user_id, amount in payouts.items():
                    if amount <= 0:
                        continue
                    account = self._account_from_mapping(balances, user_id)
                    account[CASH] += amount
                    balances[str(user_id)] = account
            for user_id, amount in payouts.items():
                if amount > 0:
                    await self._append_ledger(
                        tx_type,
                        from_user_id=payer_id,
                        to_user_id=user_id,
                        amount=amount,
                        actor_id=payer_id,
                        guild_id=guild_id,
                        reason=reason,
                        log_to_channel=False,
                    )
            if burned > 0:
                await self._append_ledger(
                    f"{tx_type}_cut",
                    from_user_id=payer_id,
                    to_user_id=None,
                    amount=burned,
                    actor_id=payer_id,
                    guild_id=guild_id,
                    reason=f"house cut: {reason}",
                    log_to_channel=False,
                )
        return payer_account

    async def transfer_with_tax(
        self,
        from_user_id: int,
        to_user_id: int,
        amount: int,
        tax: int,
        *,
        guild_id: int | None,
        reason: str,
    ) -> dict[str, dict[str, int]]:
        """Move ``amount`` to the recipient and burn ``tax`` from the sender, atomically."""
        amount = self._require_amount(amount, allow_zero=False)
        tax = self._require_amount(tax, allow_zero=True)
        if from_user_id == to_user_id:
            raise EconomyError("You cannot transfer to yourself.")
        async with self._lock:
            async with self.config.balances() as balances:
                source = self._account_from_mapping(balances, from_user_id)
                target = self._account_from_mapping(balances, to_user_id)
                if source[CASH] < amount + tax:
                    raise EconomyError(
                        f"Insufficient funds. You need {amount + tax:,} {CURRENCY_NAME} "
                        f"including {tax:,} {CURRENCY_NAME} tax."
                    )
                source[CASH] -= amount + tax
                target[CASH] += amount
                balances[str(from_user_id)] = source
                balances[str(to_user_id)] = target
            await self._append_ledger(
                "transfer",
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                amount=amount,
                actor_id=from_user_id,
                guild_id=guild_id,
                reason=reason,
            )
            if tax:
                await self._append_ledger(
                    "tax",
                    from_user_id=from_user_id,
                    to_user_id=None,
                    amount=tax,
                    actor_id=from_user_id,
                    guild_id=guild_id,
                    reason="transfer tax",
                    log_to_channel=False,
                )
        return {"from": source, "to": target}
