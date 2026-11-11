from __future__ import annotations

import asyncio
import copy
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
import discord
from redbot.core import app_commands, commands

from .constants import (
    CASH,
    CURRENCY_NAME,
    DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT,
    DEFAULT_LWDMILLIONS_SEED_JACKPOT,
    DEFAULT_LWDMILLIONS_TICKET_PRICE,
    DRAND_FETCH_TIMEOUT_SECONDS,
    DRAND_QUICKNET_ENDPOINTS,
    DRAND_REQUIRED_CONSENSUS,
    LWDMILLIONS_DRAW_POLL_SECONDS,
    LWDMILLIONS_HISTORY_LIMIT,
    LWDMILLIONS_HISTORY_WINNER_LIMIT,
    MAX_AMOUNT,
)
from .errors import EconomyError
from .layouts import send_embed
from .lwdmillions import (
    DRAND_QUICKNET_CHAIN_HASH,
    DRAND_QUICKNET_PUBLIC_KEY,
    LWD_MILLIONS_PRIZE_MULTIPLIERS,
    LWD_MILLIONS_PRIZE_TIER_ORDER,
    calculate_lwdmillions_prize,
    committed_lwdmillions_draw,
    detect_lwdmillions_proof_version,
    drand_round_after,
    drand_round_timestamp,
    format_lwdmillions_ticket,
    legacy_committed_lwdmillions_draw,
    legacy_lwdmillions_commitment,
    lwdmillions_commitment,
    lwdmillions_match_label,
    match_lwdmillions_ticket,
    next_lwdmillions_draw,
    parse_lwdmillions_ticket,
    random_lwdmillions_ticket,
    validate_lwdmillions_ticket,
    validate_drand_quicknet_beacon,
    verify_lwdmillions_draw,
    verify_legacy_lwdmillions_draw,
    verify_migrated_lwdmillions_draw,
)

try:
    import drand_verify
except ImportError:  # The cog requirement normally installs this before loading.
    drand_verify = None
from .casino import CasinoMixin

log = logging.getLogger("red.neufox.economy")


class LotteryMixin:
    """The LWDMillions lottery."""

    async def _lottery_status_embed(self) -> discord.Embed:
        state = await self._lwdmillions_state_snapshot()
        next_draw = int(state["next_draw"])
        tickets = [ticket for ticket in state["tickets"] if isinstance(ticket, dict)]
        players = {int(ticket.get("user_id", 0)) for ticket in tickets}
        if not self._lwdmillions_state_commitment_valid(state):
            status = "Paused — draw proof needs repair"
        else:
            status = "Open" if state["enabled"] else "Ticket sales closed"
        embed = discord.Embed(
            title="LWDMillions",
            description=(
                "Pick **5 main numbers from 1-50** and **2 Lucky Stars from 1-12**. "
                "This game uses virtual LWD$ only."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Current Jackpot",
            value=f"**{int(state['jackpot']):,} {CURRENCY_NAME}**",
            inline=True,
        )
        embed.add_field(
            name="Ticket",
            value=f"{int(state['ticket_price']):,} {CURRENCY_NAME} per line",
            inline=True,
        )
        embed.add_field(name="Sales", value=status, inline=True)
        embed.add_field(
            name=f"Draw #{int(state['draw_number']):,}",
            value=f"<t:{next_draw}:F> (<t:{next_draw}:R>)",
            inline=False,
        )
        embed.add_field(
            name="Play",
            value=(
                "`/casino lottery play 1 2 3 4 5 | 1 2`\n"
                "`/casino lottery quickpick [lines]`\n"
                "`/casino lottery tickets`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Information",
            value=(
                "`/casino lottery prizes` — prize table\n"
                "`/casino lottery results [draw]` — recent results\n"
                "`/casino lottery verify [draw]` — reproduce a completed draw"
            ),
            inline=False,
        )
        embed.add_field(
            name="Published Draw Commitment",
            value=f"`{state['commitment']}`",
            inline=False,
        )
        proof_version = int(state.get("proof_version", 1))
        if proof_version == 1:
            embed.add_field(
                name="Original Draw Proof Preserved",
                value=(
                    "Tickets for this in-progress draw retain the exact v1 commitment "
                    "published when sales began. Public drand entropy starts next draw."
                ),
                inline=False,
            )
        else:
            if proof_version == 3:
                embed.add_field(
                    name="Existing Tickets Upgraded",
                    value=(
                        "All selections and financial values are unchanged. The published v1 "
                        "commitment remains the secret anchor, now mixed with the deterministic "
                        "public beacon below."
                    ),
                    inline=False,
                )
            beacon_round = int(state.get("beacon_round", 0))
            if beacon_round > 0:
                beacon_time = drand_round_timestamp(beacon_round)
                embed.add_field(
                    name="Future Public Entropy",
                    value=(
                        f"League of Entropy Quicknet round **#{beacon_round:,}**, published "
                        f"<t:{beacon_time}:R>. The bot will wait for its verified beacon."
                    ),
                    inline=False,
                )
        embed.set_footer(
            text=(
                f"{len(tickets):,} lines from {len(players):,} players | "
                "Draws Tuesday and Friday at 20:00 UTC"
            )
        )
        return embed

    @CasinoMixin.casino.group(name="lottery", aliases=["lwdmillions", "lwdm", "lotto"], fallback="status")
    async def lottery(self, ctx: commands.Context):
        """Play the twice-weekly LWDMillions lottery with virtual LWD$."""
        await send_embed(ctx, await self._lottery_status_embed())

    @lottery.command(name="play", aliases=["pick", "buy"])
    @app_commands.describe(numbers="5 main numbers, a bar, then 2 stars: 1 2 3 4 5 | 1 2")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_lwdmillions_play(self, ctx: commands.Context, *, numbers: str):
        """Buy one line using five main numbers and two Lucky Stars."""
        try:
            ticket = parse_lwdmillions_ticket(numbers)
            purchased, account, state = await self._purchase_lwdmillions_tickets(ctx, [ticket])
        except (EconomyError, ValueError) as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return
        await self._send_lwdmillions_purchase_receipt(ctx, purchased, account, state)

    @lottery.command(name="quickpick", aliases=["quick", "random"])
    @app_commands.describe(lines="How many lines to buy.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_lwdmillions_quickpick(
        self,
        ctx: commands.Context,
        lines: int = 1,
    ):
        """Buy one or more securely generated Quick Pick lines."""
        max_lines = int(await self.config.lwdmillions_max_lines())
        if lines < 1 or lines > max_lines:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(
                f"Choose from 1 to {max_lines} Quick Pick lines."
            )
            return
        selections = [random_lwdmillions_ticket() for _ in range(lines)]
        try:
            purchased, account, state = await self._purchase_lwdmillions_tickets(
                ctx,
                selections,
            )
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return
        await self._send_lwdmillions_purchase_receipt(ctx, purchased, account, state)

    @lottery.command(name="tickets", aliases=["ticket", "mine"])
    async def economy_lwdmillions_tickets(self, ctx: commands.Context):
        """Show your lines for the upcoming draw."""
        state = await self._lwdmillions_state_snapshot()
        tickets = [
            ticket
            for ticket in state["tickets"]
            if isinstance(ticket, dict) and int(ticket.get("user_id", 0)) == ctx.author.id
        ]
        if not tickets:
            await ctx.send("You have no tickets in the upcoming LWDMillions draw.")
            return

        lines = []
        for ticket in tickets:
            try:
                formatted = format_lwdmillions_ticket(ticket["main"], ticket["stars"])
            except (KeyError, TypeError, ValueError):
                continue
            lines.append(f"`#{int(ticket.get('id', 0))}` {formatted}")
        embed = discord.Embed(
            title=f"Your LWDMillions Tickets — Draw #{int(state['draw_number']):,}",
            description="\n".join(lines) or "No readable tickets.",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Draw Time",
            value=f"<t:{int(state['next_draw'])}:F> (<t:{int(state['next_draw'])}:R>)",
            inline=False,
        )
        proof_version = int(state.get("proof_version", 1))
        if proof_version == 1:
            embed.add_field(
                name="Ticket Migration",
                value=(
                    "Your lines and original v1 commitment are unchanged. This draw settles "
                    "under its original proof; public drand entropy begins next draw."
                ),
                inline=False,
            )
        else:
            if proof_version == 3:
                embed.add_field(
                    name="Ticket Migration",
                    value=(
                        "Your lines, price, and original commitment are unchanged. This draw "
                        "now also uses the deterministic public beacon below."
                    ),
                    inline=False,
                )
            beacon_round = int(state.get("beacon_round", 0))
            if beacon_round > 0:
                embed.add_field(
                    name="Locked Public Beacon",
                    value=(
                        f"Quicknet round **#{beacon_round:,}** at "
                        f"<t:{drand_round_timestamp(beacon_round)}:F>"
                    ),
                    inline=False,
                )
        embed.set_footer(text=f"{len(tickets):,}/{int(await self.config.lwdmillions_max_lines())} lines")
        await send_embed(ctx, embed)

    @lottery.command(name="prizes", aliases=["payouts", "rules"])
    async def economy_lwdmillions_prizes(self, ctx: commands.Context):
        """Show the LWDMillions prize tiers."""
        state = await self._lwdmillions_state_snapshot()
        price = int(state["ticket_price"])
        lines = []
        for tier in LWD_MILLIONS_PRIZE_TIER_ORDER:
            label = lwdmillions_match_label(*tier)
            if tier == (5, 2):
                prize = f"share of **{int(state['jackpot']):,} {CURRENCY_NAME}**"
            else:
                prize = f"{price * LWD_MILLIONS_PRIZE_MULTIPLIERS[tier]:,} {CURRENCY_NAME}"
            lines.append(f"**{label}:** {prize}")

        embed = discord.Embed(
            title="LWDMillions Prize Table",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Jackpot",
            value=(
                "All 5 + 2 winning lines split the current jackpot. If there are none, "
                f"it rolls over. {int(state['jackpot_contribution_percent'])}% of every "
                "ticket price is added to it."
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Current ticket price: {price:,} {CURRENCY_NAME} | "
                "Jackpot odds per line: 1 in 139,838,160"
            )
        )
        await send_embed(ctx, embed)

    @lottery.command(name="results", aliases=["result", "history"])
    @app_commands.describe(draw_number="Which draw. Leave empty for the latest.")
    async def economy_lwdmillions_results(
        self,
        ctx: commands.Context,
        draw_number: int | None = None,
    ):
        """Show a recent LWDMillions result."""
        state = await self._lwdmillions_state_snapshot()
        record = self._find_lwdmillions_history(state, draw_number)
        if record is None:
            message = (
                "There are no completed LWDMillions draws yet."
                if draw_number is None
                else f"Draw #{draw_number:,} is not in the recent history."
            )
            await ctx.send(message)
            return
        await send_embed(ctx, self._lwdmillions_result_embed(record, ctx.clean_prefix))

    @lottery.command(name="verify", aliases=["proof", "fairness"])
    @app_commands.describe(draw_number="Which draw. Leave empty for the latest.")
    async def economy_lwdmillions_verify(
        self,
        ctx: commands.Context,
        draw_number: int | None = None,
    ):
        """Verify a recent result against its pre-published commitment."""
        state = await self._lwdmillions_state_snapshot()
        record = self._find_lwdmillions_history(state, draw_number)
        if record is None:
            message = (
                "There are no completed LWDMillions draws to verify."
                if draw_number is None
                else f"Draw #{draw_number:,} is not in the recent history."
            )
            await ctx.send(message)
            return

        try:
            proof_version = int(record.get("proof_version", 1))
        except (TypeError, ValueError):
            proof_version = 0
        try:
            if proof_version == 1:
                valid = verify_legacy_lwdmillions_draw(
                    str(record["secret"]),
                    int(record["draw_number"]),
                    str(record["commitment"]),
                    record["main"],
                    record["stars"],
                )
            elif proof_version in (2, 3):
                if proof_version == 2:
                    proof_valid = verify_lwdmillions_draw(
                        str(record["secret"]),
                        int(record["draw_number"]),
                        int(record["beacon_round"]),
                        str(record["beacon_randomness"]),
                        str(record["beacon_signature"]),
                        str(record["commitment"]),
                        record["main"],
                        record["stars"],
                    )
                else:
                    proof_valid = verify_migrated_lwdmillions_draw(
                        str(record["secret"]),
                        int(record["draw_number"]),
                        int(record["scheduled_for"]),
                        int(record["beacon_round"]),
                        str(record["beacon_randomness"]),
                        str(record["beacon_signature"]),
                        str(record["commitment"]),
                        record["main"],
                        record["stars"],
                    )
                verified_randomness = (
                    str(
                        drand_verify.verify_quicknet(
                            int(record["beacon_round"]),
                            str(record["beacon_signature"]),
                            DRAND_QUICKNET_PUBLIC_KEY,
                        )
                    ).casefold()
                    if proof_valid and drand_verify is not None
                    else ""
                )
                beacon_valid = secrets.compare_digest(
                    verified_randomness,
                    str(record["beacon_randomness"]).casefold(),
                )
                chain_valid = secrets.compare_digest(
                    str(record.get("beacon_chain_hash", "")),
                    DRAND_QUICKNET_CHAIN_HASH,
                )
                valid = proof_valid and beacon_valid and chain_valid
            else:
                valid = False
        except (KeyError, OverflowError, TypeError, ValueError):
            valid = False
        embed = discord.Embed(
            title=f"LWDMillions Draw #{int(record.get('draw_number', 0)):,} Verification",
            description=(
                (
                    "✅ The revealed secret reproduces the original v1 commitment and "
                    "winning numbers. This proof was preserved for tickets bought before "
                    "the public-beacon upgrade."
                    if proof_version == 1
                    else (
                        "✅ The original v1 commitment, deterministic public-beacon round, "
                        "BLS-verified beacon, and revealed secret reproduce the winning numbers."
                        if proof_version == 3
                        else "✅ The BLS-verified public beacon and revealed secret reproduce "
                        "the published commitment and winning numbers."
                    )
                )
                if valid
                else "❌ This stored draw does not pass commitment verification."
            ),
            color=discord.Color.green() if valid else discord.Color.red(),
        )
        embed.add_field(name="Commitment", value=f"`{record.get('commitment', '')}`", inline=False)
        embed.add_field(name="Revealed Secret", value=f"`{record.get('secret', '')}`", inline=False)
        if proof_version in (2, 3):
            beacon_round = int(record.get("beacon_round", 0))
            beacon_url = (
                f"https://api.drand.sh/{DRAND_QUICKNET_CHAIN_HASH}/public/{beacon_round}"
            )
            embed.add_field(
                name=f"League of Entropy Quicknet Round #{beacon_round:,}",
                value=(
                    f"Randomness: `{record.get('beacon_randomness', '')}`\n"
                    f"Signature: `{record.get('beacon_signature', '')}`\n"
                    f"[Open the public beacon]({beacon_url})"
                ),
                inline=False,
            )
        embed.add_field(
            name="Winning Numbers",
            value=format_lwdmillions_ticket(record.get("main", []), record.get("stars", [])),
            inline=False,
        )
        await send_embed(ctx, embed)

    async def _set_lwdmillions_setting(self, key: str, value: Any):
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state[key] = value

    async def _set_lwdmillions_channel(self, guild_id: int, channel_id: int | None) -> int | None:
        """Set or clear a server's draw channel and return the previous channel ID."""
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                previous = state["announcement_channels"].get(str(guild_id))
                if channel_id is None:
                    state["announcement_channels"].pop(str(guild_id), None)
                else:
                    state["announcement_channels"][str(guild_id)] = channel_id
        return previous

    @staticmethod
    def _normalize_lwdmillions_state(state: dict[str, Any]):
        """Fill missing lottery state without replacing tickets from older installs."""

        def positive_integer(key: str, default: int, maximum: int = MAX_AMOUNT) -> int:
            try:
                value = int(state.get(key, default))
            except (TypeError, ValueError):
                value = default
            if value <= 0 or value > maximum:
                value = default
            state[key] = value
            return value

        state["enabled"] = bool(state.get("enabled", True))
        maximum_price = MAX_AMOUNT // max(LWD_MILLIONS_PRIZE_MULTIPLIERS.values())
        positive_integer("ticket_price", DEFAULT_LWDMILLIONS_TICKET_PRICE, maximum_price)
        positive_integer("seed_jackpot", DEFAULT_LWDMILLIONS_SEED_JACKPOT)
        positive_integer("jackpot", DEFAULT_LWDMILLIONS_SEED_JACKPOT)
        draw_number = positive_integer("draw_number", 1)
        positive_integer("next_ticket_id", 1)

        try:
            contribution = int(
                state.get(
                    "jackpot_contribution_percent",
                    DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT,
                )
            )
        except (TypeError, ValueError):
            contribution = DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT
        if contribution < 0 or contribution > 100:
            contribution = DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT
        state["jackpot_contribution_percent"] = contribution

        try:
            next_draw = int(state.get("next_draw", 0))
        except (TypeError, ValueError):
            next_draw = 0
        if next_draw <= 0:
            next_draw = int(next_lwdmillions_draw().timestamp())
        state["next_draw"] = next_draw
        try:
            expected_beacon_round = drand_round_after(next_draw)
        except ValueError:
            next_draw = int(next_lwdmillions_draw().timestamp())
            state["next_draw"] = next_draw
            expected_beacon_round = drand_round_after(next_draw)

        if not isinstance(state.get("tickets"), list):
            state["tickets"] = []
        if not isinstance(state.get("history"), list):
            state["history"] = []
        else:
            del state["history"][:-LWDMILLIONS_HISTORY_LIMIT]
        if not isinstance(state.get("announcement_channels"), dict):
            state["announcement_channels"] = {}

        secret = str(state.get("secret", ""))
        commitment = str(state.get("commitment", ""))
        try:
            beacon_round = int(state.get("beacon_round", 0))
        except (TypeError, ValueError):
            beacon_round = 0
        if beacon_round < 0:
            beacon_round = 0
        raw_proof_version = state.get("proof_version")
        try:
            proof_version = int(raw_proof_version) if raw_proof_version is not None else 0
        except (TypeError, ValueError):
            proof_version = 0

        detected_version = (
            detect_lwdmillions_proof_version(
                secret,
                draw_number,
                commitment,
                beacon_round,
            )
            if secret and commitment
            else None
        )

        if state["tickets"]:
            # Never rotate proof material after somebody has paid. Detect an old live
            # draw from its actual v1 commitment, even if nested defaults supplied a
            # misleading proof_version value during the upgrade.
            if detected_version == 1:
                # Keep the exact old commitment as the secret anchor, then add the
                # objectively selected first Quicknet round after the original draw
                # time. Ticket selections and all financial values remain untouched.
                state["proof_version"] = 3
                state["beacon_round"] = expected_beacon_round
                return
            if detected_version == 2:
                state["proof_version"] = 2
                state["beacon_round"] = beacon_round
                return

            # An unknown or damaged proof must fail closed, but its original values
            # are retained so an owner can investigate without losing paid tickets.
            retained_version = proof_version if proof_version in (1, 2, 3) else 2
            state["proof_version"] = retained_version
            state["beacon_round"] = 0 if retained_version == 1 else beacon_round
            return

        # With no paid tickets, safely establish (or repair) the current v2 proof.
        if not secret or beacon_round != expected_beacon_round or detected_version != 2:
            secret = secrets.token_hex(32)
            state["secret"] = secret
            commitment = lwdmillions_commitment(
                secret,
                draw_number,
                expected_beacon_round,
            )
            state["commitment"] = commitment
        state["proof_version"] = 2
        state["beacon_round"] = expected_beacon_round

    @staticmethod
    def _lwdmillions_state_commitment_valid(state: dict[str, Any]) -> bool:
        """Return whether the current state's versioned commitment is intact."""
        try:
            proof_version = int(state.get("proof_version", 1))
            secret = str(state["secret"])
            draw_number = int(state["draw_number"])
            commitment = str(state["commitment"])
            if proof_version == 1:
                expected = legacy_lwdmillions_commitment(secret, draw_number)
            elif proof_version == 2:
                expected = lwdmillions_commitment(
                    secret,
                    draw_number,
                    int(state["beacon_round"]),
                )
            elif proof_version == 3:
                if int(state["beacon_round"]) != drand_round_after(int(state["next_draw"])):
                    return False
                expected = legacy_lwdmillions_commitment(secret, draw_number)
            else:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        return secrets.compare_digest(expected, commitment)

    async def _lwdmillions_state_snapshot(self) -> dict[str, Any]:
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                return copy.deepcopy(dict(state))

    @staticmethod
    def _find_lwdmillions_history(
        state: dict[str, Any],
        draw_number: int | None,
    ) -> dict[str, Any] | None:
        history = [record for record in state.get("history", []) if isinstance(record, dict)]
        if draw_number is None:
            return history[-1] if history else None
        for record in reversed(history):
            if int(record.get("draw_number", 0)) == int(draw_number):
                return record
        return None

    async def _purchase_lwdmillions_tickets(
        self,
        ctx: commands.Context,
        selections: list[tuple[tuple[int, ...], tuple[int, ...]]],
    ) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
        if not selections:
            raise EconomyError("Choose at least one LWDMillions line.")
        max_lines = int(await self.config.lwdmillions_max_lines())
        max_tickets = int(await self.config.lwdmillions_max_tickets())
        if len(selections) > max_lines:
            raise EconomyError(
                f"You can hold at most {max_lines} lines per draw."
            )
        validated = [validate_lwdmillions_ticket(*selection) for selection in selections]

        exclusions = await self._active_casino_exclusions(ctx.author.id)
        if exclusions:
            details = " ".join(self._casino_exclusion_status_lines(exclusions))
            raise EconomyError(
                f"You cannot buy chance-game tickets while casino-excluded. {details}"
            )

        # Settle an overdue draw before allowing a line into the next one.
        await self._run_lwdmillions_draw(force=False)
        now = int(time.time())
        guild_id = ctx.guild.id if ctx.guild else None
        channel_id = ctx.channel.id if ctx.channel else None

        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                if now >= int(state["next_draw"]):
                    raise EconomyError("The draw is being settled. Please try again in a moment.")
                if not state["enabled"]:
                    raise EconomyError("LWDMillions ticket sales are currently closed.")
                if not self._lwdmillions_state_commitment_valid(state):
                    raise EconomyError(
                        "LWDMillions ticket sales are paused because the draw proof needs repair."
                    )

                existing_count = sum(
                    1
                    for ticket in state["tickets"]
                    if isinstance(ticket, dict)
                    and int(ticket.get("user_id", 0)) == ctx.author.id
                )
                if existing_count + len(validated) > max_lines:
                    remaining = max_lines - existing_count
                    raise EconomyError(
                        f"You can hold at most {max_lines} lines per draw. "
                        f"You have room for {max(0, remaining)} more."
                    )
                if len(state["tickets"]) + len(validated) > max_tickets:
                    raise EconomyError("This LWDMillions draw has reached its ticket limit.")

                ticket_price = int(state["ticket_price"])
                total = ticket_price * len(validated)
                if total > MAX_AMOUNT:
                    raise EconomyError(f"Purchase total cannot exceed {MAX_AMOUNT:,} {CURRENCY_NAME}.")

                async with self.config.balances() as balances:
                    account = self._account_from_mapping(balances, ctx.author.id)
                    if account[CASH] < total:
                        raise EconomyError(
                            f"Insufficient funds. {len(validated):,} line"
                            f"{'s cost' if len(validated) != 1 else ' costs'} "
                            f"{total:,} {CURRENCY_NAME}."
                        )
                    account[CASH] -= total
                    balances[str(ctx.author.id)] = account

                next_ticket_id = int(state["next_ticket_id"])
                purchased = []
                for offset, (main_numbers, lucky_stars) in enumerate(validated):
                    ticket = {
                        "id": next_ticket_id + offset,
                        "user_id": ctx.author.id,
                        "main": list(main_numbers),
                        "stars": list(lucky_stars),
                        "price": ticket_price,
                        "purchased_at": now,
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                    }
                    state["tickets"].append(ticket)
                    purchased.append(ticket)
                state["next_ticket_id"] = next_ticket_id + len(purchased)

                requested_contribution = (
                    total * int(state["jackpot_contribution_percent"]) // 100
                )
                previous_jackpot = int(state["jackpot"])
                state["jackpot"] = min(
                    MAX_AMOUNT,
                    previous_jackpot + requested_contribution,
                )
                contribution = int(state["jackpot"]) - previous_jackpot
                draw_number = int(state["draw_number"])
                snapshot = copy.deepcopy(dict(state))

            try:
                await self._append_ledger(
                    "lwdmillions_ticket",
                    from_user_id=ctx.author.id,
                    to_user_id=None,
                    amount=total,
                    actor_id=ctx.author.id,
                    guild_id=guild_id,
                    reason=(
                        f"LWDMillions draw {draw_number}; {len(purchased)} line(s); "
                        f"{contribution} added to jackpot"
                    ),
                )
            except Exception:
                log.exception(
                    "Could not append LWDMillions draw %s ticket purchase for user %s",
                    draw_number,
                    ctx.author.id,
                )
        return copy.deepcopy(purchased), account, snapshot

    async def _send_lwdmillions_purchase_receipt(
        self,
        ctx: commands.Context,
        tickets: list[dict[str, Any]],
        account: dict[str, int],
        state: dict[str, Any],
    ):
        lines = [
            f"`#{int(ticket['id'])}` {format_lwdmillions_ticket(ticket['main'], ticket['stars'])}"
            for ticket in tickets
        ]
        total = sum(int(ticket.get("price", 0)) for ticket in tickets)
        embed = discord.Embed(
            title=f"LWDMillions — {len(tickets):,} Line{'s' if len(tickets) != 1 else ''} Bought",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.add_field(
            name=f"Draw #{int(state['draw_number']):,}",
            value=f"<t:{int(state['next_draw'])}:F> (<t:{int(state['next_draw'])}:R>)",
            inline=True,
        )
        embed.add_field(name="Paid", value=f"{total:,} {CURRENCY_NAME}", inline=True)
        embed.add_field(
            name="Jackpot",
            value=f"{int(state['jackpot']):,} {CURRENCY_NAME}",
            inline=True,
        )
        embed.add_field(
            name="Draw Commitment",
            value=f"`{state['commitment']}`",
            inline=False,
        )
        proof_version = int(state.get("proof_version", 1))
        if proof_version == 1:
            embed.add_field(
                name="Original Proof Preserved",
                value="This draw uses its already-published v1 proof; drand starts next draw.",
                inline=False,
            )
        else:
            if proof_version == 3:
                embed.add_field(
                    name="Existing Ticket Upgrade",
                    value=(
                        "The original v1 commitment still anchors this paid draw; the public "
                        "beacon below is now mixed into its result."
                    ),
                    inline=False,
                )
            beacon_round = int(state.get("beacon_round", 0))
            if beacon_round > 0:
                embed.add_field(
                    name="Locked Public Beacon",
                    value=(
                        f"Quicknet round **#{beacon_round:,}** at "
                        f"<t:{drand_round_timestamp(beacon_round)}:F>"
                    ),
                    inline=False,
                )
        embed.set_footer(text=f"Balance: {account[CASH]:,} {CURRENCY_NAME}")
        await send_embed(ctx, embed)

    def _lwdmillions_result_embed(
        self,
        record: dict[str, Any],
        prefix: str = "[p]",
    ) -> discord.Embed:
        winners = [winner for winner in record.get("winners", []) if isinstance(winner, dict)]
        winner_count = int(record.get("winner_count", len(winners)))
        draw_number = int(record.get("draw_number", 0))
        try:
            timestamp = datetime.fromtimestamp(int(record.get("drawn_at", 0)), timezone.utc)
        except (OSError, OverflowError, TypeError, ValueError):
            timestamp = discord.utils.utcnow()

        formatted_numbers = format_lwdmillions_ticket(
            record.get("main", []), record.get("stars", [])
        )
        main_numbers, lucky_stars = formatted_numbers.split(" | ⭐ ", 1)
        embed = discord.Embed(
            title=f"LWDMillions — Draw #{draw_number:,}",
            color=discord.Color.green() if winner_count else discord.Color.gold(),
            timestamp=timestamp,
            description=(
                "**Winning Numbers**\n"
                f"`{main_numbers}`  ·  ⭐ `{lucky_stars}`"
            ),
        )

        jackpot_before = int(record.get("jackpot_before", 0))
        jackpot_after = int(record.get("jackpot_after", 0))
        jackpot_winning_lines = int(record.get("jackpot_winning_lines", 0))
        if jackpot_winning_lines:
            jackpot_text = (
                f"**{jackpot_before:,} {CURRENCY_NAME}** split\n"
                f"{jackpot_winning_lines:,} winning line"
                f"{'s' if jackpot_winning_lines != 1 else ''} · "
                f"Next: {jackpot_after:,} {CURRENCY_NAME}"
            )
        else:
            jackpot_text = f"**{jackpot_before:,} {CURRENCY_NAME}** rolls over\nNo 5 + 2 winner"
        embed.add_field(name="💰 Jackpot", value=jackpot_text, inline=True)

        embed.add_field(
            name="📋 Draw Summary",
            value=(
                f"{int(record.get('ticket_count', 0)):,} lines · "
                f"{int(record.get('player_count', 0)):,} players\n"
                f"**{int(record.get('total_paid', 0)):,} {CURRENCY_NAME}** paid"
            ),
            inline=True,
        )

        tier_counts = record.get("tier_counts", {})
        tier_lines = []
        if isinstance(tier_counts, dict):
            for main_matches, star_matches in LWD_MILLIONS_PRIZE_TIER_ORDER:
                count = int(tier_counts.get(f"{main_matches}+{star_matches}", 0))
                if count:
                    tier_lines.append(
                        f"**{lwdmillions_match_label(main_matches, star_matches)}** · "
                        f"{count:,} line{'s' if count != 1 else ''}"
                    )
        embed.add_field(
            name="🏅 Winning Tiers",
            value="\n".join(tier_lines) or "No winning lines this draw.",
            inline=False,
        )

        if winners:
            top_winners = sorted(
                winners,
                key=lambda winner: int(winner.get("amount", 0)),
                reverse=True,
            )[:10]
            medals = ("🥇", "🥈", "🥉")
            winner_lines = [
                f"{medals[index] if index < len(medals) else '•'} "
                f"<@{int(winner.get('user_id', 0))}> — "
                f"**{int(winner.get('amount', 0)):,} {CURRENCY_NAME}**"
                for index, winner in enumerate(top_winners)
            ]
            if winner_count > len(top_winners):
                winner_lines.append(f"…and {winner_count - len(top_winners):,} more")
            embed.add_field(name="🏆 Winners", value="\n".join(winner_lines), inline=False)

        try:
            proof_version = int(record.get("proof_version", 1))
        except (TypeError, ValueError):
            proof_version = 0
        if proof_version == 1:
            proof_status = "✅ Commitment/reveal proof published."
        elif proof_version == 3:
            proof_status = "✅ Original commitment + public Quicknet beacon verified."
        elif proof_version == 2:
            proof_status = "✅ Commitment + public Quicknet beacon verified."
        else:
            proof_status = "⚠️ Proof details are available from the verification command."

        proof_lines = [
            proof_status,
            f"Run `/casino lottery verify {draw_number}` for the full proof details.",
        ]
        beacon_round = int(record.get("beacon_round", 0))
        if proof_version in (2, 3) and beacon_round > 0:
            beacon_url = (
                f"https://api.drand.sh/{DRAND_QUICKNET_CHAIN_HASH}/public/{beacon_round}"
            )
            proof_lines.append(f"[Open public beacon]({beacon_url})")
        embed.add_field(
            name="🔐 Verification",
            value="\n".join(proof_lines),
            inline=False,
        )
        embed.set_footer(text="Full commitment, secret, and beacon data: use the verify command")
        return embed

    @staticmethod
    async def _fetch_drand_endpoint(
        session: ClientSession,
        endpoint: str,
        round_number: int,
    ) -> dict[str, Any] | None:
        url = (
            f"{endpoint}/{DRAND_QUICKNET_CHAIN_HASH}/public/{int(round_number)}"
        )
        try:
            async with session.get(
                url,
                headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            ) as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            returned_round = int(payload["round"])
            randomness = str(payload["randomness"]).casefold()
            signature = str(payload["signature"]).casefold()
        except (KeyError, TypeError, ValueError):
            return None
        if returned_round != int(round_number):
            return None
        if not validate_drand_quicknet_beacon(
            returned_round,
            randomness,
            signature,
        ):
            return None
        return {
            "round": returned_round,
            "randomness": randomness,
            "signature": signature,
            "endpoint": endpoint,
        }

    async def _fetch_drand_beacon(self, round_number: int) -> dict[str, Any] | None:
        """Require relay consensus and a valid Quicknet BLS signature."""
        if drand_verify is None:
            log.error("drand-verify is unavailable; LWDMillions draw settlement is paused")
            return None

        timeout = ClientTimeout(total=DRAND_FETCH_TIMEOUT_SECONDS)
        async with ClientSession(timeout=timeout) as session:
            responses = await asyncio.gather(
                *(
                    self._fetch_drand_endpoint(session, endpoint, round_number)
                    for endpoint in DRAND_QUICKNET_ENDPOINTS
                ),
                return_exceptions=True,
            )

        matching: dict[tuple[str, str], list[str]] = {}
        for response in responses:
            if not isinstance(response, dict):
                continue
            key = (str(response["randomness"]), str(response["signature"]))
            matching.setdefault(key, []).append(str(response["endpoint"]))
        if not matching:
            log.warning("No valid drand responses for Quicknet round %s", round_number)
            return None

        (randomness, signature), sources = max(
            matching.items(),
            key=lambda item: len(item[1]),
        )
        if len(sources) < DRAND_REQUIRED_CONSENSUS:
            log.warning(
                "Only %s drand relay confirmed Quicknet round %s; settlement needs %s",
                len(sources),
                round_number,
                DRAND_REQUIRED_CONSENSUS,
            )
            return None

        try:
            verified_randomness = str(
                drand_verify.verify_quicknet(
                    int(round_number),
                    signature,
                    DRAND_QUICKNET_PUBLIC_KEY,
                )
            ).casefold()
        except (TypeError, ValueError):
            log.error("BLS verification failed for drand Quicknet round %s", round_number)
            return None
        if not secrets.compare_digest(verified_randomness, randomness):
            log.error("Verified drand randomness differs for Quicknet round %s", round_number)
            return None
        return {
            "round": int(round_number),
            "randomness": randomness,
            "signature": signature,
            "sources": sources,
            "chain_hash": DRAND_QUICKNET_CHAIN_HASH,
            "verified": True,
        }

    async def _run_lwdmillions_draw(
        self,
        *,
        force: bool,
        skip_channel_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Atomically settle the current draw and open the next ticket period."""
        async with self._lwdmillions_draw_lock:
            now = int(time.time())
            pending = await self._lwdmillions_state_snapshot()
            scheduled_for = int(pending["next_draw"])
            if not force and now < scheduled_for:
                return None

            draw_number = int(pending["draw_number"])
            secret = str(pending["secret"])
            commitment = str(pending["commitment"])
            proof_version = int(pending.get("proof_version", 1))
            if not self._lwdmillions_state_commitment_valid(pending):
                log.error(
                    "Refusing to settle LWDMillions draw %s: v%s commitment mismatch",
                    draw_number,
                    proof_version,
                )
                return None

            beacon: dict[str, Any] | None = None
            if proof_version in (2, 3):
                beacon_round = int(pending["beacon_round"])
                if now < drand_round_timestamp(beacon_round):
                    return None
                beacon = await self._fetch_drand_beacon(beacon_round)
                if beacon is None:
                    return None
            elif proof_version != 1:
                log.error(
                    "Refusing to settle LWDMillions draw %s: unknown proof version %s",
                    draw_number,
                    proof_version,
                )
                return None

            async with self._lock:
                async with self.config.lwdmillions() as state:
                    self._normalize_lwdmillions_state(state)
                    now = int(time.time())
                    scheduled_for = int(state["next_draw"])
                    if not force and now < scheduled_for:
                        return None

                    draw_number = int(state["draw_number"])
                    secret = str(state["secret"])
                    commitment = str(state["commitment"])
                    current_proof_version = int(state.get("proof_version", 1))
                    if (
                        draw_number != int(pending["draw_number"])
                        or commitment != str(pending["commitment"])
                        or scheduled_for != int(pending["next_draw"])
                        or current_proof_version != proof_version
                    ):
                        return None
                    if not self._lwdmillions_state_commitment_valid(state):
                        log.error(
                            "Refusing to settle LWDMillions draw %s: v%s commitment mismatch",
                            draw_number,
                            current_proof_version,
                        )
                        return None

                    if current_proof_version == 1:
                        draw_main, draw_stars = legacy_committed_lwdmillions_draw(
                            secret,
                            draw_number,
                        )
                        proof_record: dict[str, Any] = {"proof_version": 1}
                    else:
                        beacon_round = int(state["beacon_round"])
                        if beacon is None or beacon_round != int(beacon["round"]):
                            return None
                        draw_main, draw_stars = committed_lwdmillions_draw(
                            secret,
                            draw_number,
                            beacon_round,
                            str(beacon["randomness"]),
                        )
                        proof_record = {
                            "proof_version": current_proof_version,
                            "beacon_chain_hash": DRAND_QUICKNET_CHAIN_HASH,
                            "beacon_round": beacon_round,
                            "beacon_round_time": drand_round_timestamp(beacon_round),
                            "beacon_randomness": str(beacon["randomness"]),
                            "beacon_signature": str(beacon["signature"]),
                            "beacon_sources": list(beacon["sources"]),
                            "beacon_verified": bool(beacon["verified"]),
                        }
                    tickets = [
                        copy.deepcopy(ticket)
                        for ticket in state["tickets"]
                        if isinstance(ticket, dict)
                    ]
                    jackpot_before = int(state["jackpot"])
                    evaluations: list[dict[str, Any]] = []
                    players: set[int] = set()
                    for ticket in tickets:
                        try:
                            user_id = int(ticket["user_id"])
                            main_numbers, lucky_stars = validate_lwdmillions_ticket(
                                ticket["main"],
                                ticket["stars"],
                            )
                            ticket_price = int(ticket["price"])
                            if user_id <= 0 or ticket_price <= 0:
                                raise ValueError("invalid stored ticket")
                        except (KeyError, TypeError, ValueError):
                            log.warning(
                                "Ignoring malformed LWDMillions ticket %r in draw %s",
                                ticket.get("id"),
                                draw_number,
                            )
                            continue
                        tier = match_lwdmillions_ticket(
                            main_numbers,
                            lucky_stars,
                            draw_main,
                            draw_stars,
                        )
                        players.add(user_id)
                        evaluations.append(
                            {
                                "ticket": ticket,
                                "user_id": user_id,
                                "price": ticket_price,
                                "tier": tier,
                                "payout": 0,
                            }
                        )

                    jackpot_winners = sorted(
                        (entry for entry in evaluations if entry["tier"] == (5, 2)),
                        key=lambda entry: int(entry["ticket"].get("id", 0)),
                    )
                    if jackpot_winners:
                        base_share, extra_shares = divmod(jackpot_before, len(jackpot_winners))
                        for index, entry in enumerate(jackpot_winners):
                            entry["payout"] = base_share + int(index < extra_shares)

                    payout_by_user: dict[int, int] = {}
                    winner_details: dict[int, dict[str, Any]] = {}
                    tier_counts: dict[str, int] = {}
                    winning_ticket_count = 0
                    for entry in evaluations:
                        main_matches, star_matches = entry["tier"]
                        tier_key = f"{main_matches}+{star_matches}"
                        if entry["tier"] in LWD_MILLIONS_PRIZE_TIER_ORDER:
                            tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1
                            winning_ticket_count += 1
                        if entry["tier"] != (5, 2):
                            entry["payout"] = calculate_lwdmillions_prize(
                                entry["price"],
                                main_matches,
                                star_matches,
                            )
                        payout = int(entry["payout"])
                        if payout <= 0:
                            continue
                        user_id = int(entry["user_id"])
                        payout_by_user[user_id] = payout_by_user.get(user_id, 0) + payout
                        detail = winner_details.setdefault(
                            user_id,
                            {"amount": 0, "tiers": {}},
                        )
                        detail["amount"] = int(detail["amount"]) + payout
                        detail["tiers"][tier_key] = int(detail["tiers"].get(tier_key, 0)) + 1

                    async with self.config.balances() as balances:
                        for user_id, payout in payout_by_user.items():
                            account = self._account_from_mapping(balances, user_id)
                            account[CASH] += payout
                            balances[str(user_id)] = account

                    jackpot_after = (
                        int(state["seed_jackpot"])
                        if jackpot_winners
                        else jackpot_before
                    )
                    state["jackpot"] = jackpot_after
                    state["tickets"] = []
                    state["draw_number"] = draw_number + 1
                    schedule_after = max(now, scheduled_for) if force else now
                    state["next_draw"] = int(next_lwdmillions_draw(schedule_after).timestamp())
                    state["beacon_round"] = drand_round_after(int(state["next_draw"]))
                    next_secret = secrets.token_hex(32)
                    state["secret"] = next_secret
                    state["proof_version"] = 2
                    state["commitment"] = lwdmillions_commitment(
                        next_secret,
                        int(state["draw_number"]),
                        int(state["beacon_round"]),
                    )

                    all_winners = sorted(
                        (
                            {"user_id": user_id, "amount": payout}
                            for user_id, payout in payout_by_user.items()
                        ),
                        key=lambda winner: int(winner["amount"]),
                        reverse=True,
                    )
                    record = {
                        "draw_number": draw_number,
                        "scheduled_for": scheduled_for,
                        "drawn_at": now,
                        "main": list(draw_main),
                        "stars": list(draw_stars),
                        "commitment": commitment,
                        "secret": secret,
                        "ticket_count": len(evaluations),
                        "player_count": len(players),
                        "winning_ticket_count": winning_ticket_count,
                        "winner_count": len(all_winners),
                        "winners": all_winners[:LWDMILLIONS_HISTORY_WINNER_LIMIT],
                        "tier_counts": tier_counts,
                        "jackpot_before": jackpot_before,
                        "jackpot_winning_lines": len(jackpot_winners),
                        "jackpot_after": jackpot_after,
                        "total_paid": sum(payout_by_user.values()),
                        **proof_record,
                    }
                    state["history"].append(record)
                    del state["history"][:-LWDMILLIONS_HISTORY_LIMIT]
                    announcement_channels = copy.deepcopy(state["announcement_channels"])

                for user_id, payout in payout_by_user.items():
                    try:
                        await self._append_ledger(
                            "lwdmillions_win",
                            from_user_id=None,
                            to_user_id=user_id,
                            amount=payout,
                            actor_id=None,
                            guild_id=None,
                            reason=f"LWDMillions draw {draw_number} prize",
                            log_to_channel=False,
                        )
                    except Exception:
                        log.exception(
                            "Could not append the LWDMillions draw %s payout for user %s",
                            draw_number,
                            user_id,
                        )

            await self._announce_lwdmillions_draw(
                record,
                announcement_channels,
                skip_channel_id=skip_channel_id,
            )
            notification_task = self.bot.loop.create_task(
                self._notify_lwdmillions_winners(record, winner_details)
            )
            self._lwdmillions_notification_tasks.add(notification_task)
            notification_task.add_done_callback(
                self._lwdmillions_notification_tasks.discard
            )
            return copy.deepcopy(record)

    async def _announce_lwdmillions_draw(
        self,
        record: dict[str, Any],
        announcement_channels: dict[str, Any],
        *,
        skip_channel_id: int | None,
    ):
        embed = self._lwdmillions_result_embed(record)
        for guild_id, channel_id in announcement_channels.items():
            try:
                guild = self.bot.get_guild(int(guild_id))
                if guild is None or int(channel_id) == skip_channel_id:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not isinstance(channel, discord.TextChannel):
                    continue
                await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (TypeError, ValueError, discord.HTTPException):
                log.exception(
                    "Could not announce LWDMillions draw %s in guild %s",
                    record.get("draw_number"),
                    guild_id,
                )

    async def _notify_lwdmillions_winners(
        self,
        record: dict[str, Any],
        winner_details: dict[int, dict[str, Any]],
    ):
        for user_id, details in winner_details.items():
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                tier_lines = []
                for main_matches, star_matches in LWD_MILLIONS_PRIZE_TIER_ORDER:
                    count = int(details["tiers"].get(f"{main_matches}+{star_matches}", 0))
                    if count:
                        tier_lines.append(
                            f"{count:,}x {lwdmillions_match_label(main_matches, star_matches)}"
                        )
                embed = discord.Embed(
                    title=f"You won LWDMillions Draw #{int(record['draw_number']):,}!",
                    description=(
                        f"Your winning lines paid **{int(details['amount']):,} {CURRENCY_NAME}**."
                    ),
                    color=discord.Color.green(),
                )
                embed.add_field(
                    name="Winning Numbers",
                    value=format_lwdmillions_ticket(record["main"], record["stars"]),
                    inline=False,
                )
                embed.add_field(
                    name="Your Winning Tiers",
                    value="\n".join(tier_lines),
                    inline=False,
                )
                await user.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException, AttributeError):
                log.info(
                    "Could not DM LWDMillions draw %s winner %s",
                    record.get("draw_number"),
                    user_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "Unexpected error while notifying LWDMillions draw %s winner %s",
                    record.get("draw_number"),
                    user_id,
                )

    async def _lwdmillions_draw_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                state = await self._lwdmillions_state_snapshot()
                ready_at = int(state["next_draw"])
                beacon_round = int(state.get("beacon_round", 0))
                if int(state.get("proof_version", 1)) in (2, 3) and beacon_round > 0:
                    ready_at = max(ready_at, drand_round_timestamp(beacon_round))
                delay = ready_at - int(time.time())
                if delay <= 0:
                    record = await self._run_lwdmillions_draw(force=False)
                    if record is None:
                        await asyncio.sleep(LWDMILLIONS_DRAW_POLL_SECONDS)
                else:
                    await asyncio.sleep(min(delay, LWDMILLIONS_DRAW_POLL_SECONDS))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Unhandled error while processing an LWDMillions draw")
                await asyncio.sleep(LWDMILLIONS_DRAW_POLL_SECONDS)

