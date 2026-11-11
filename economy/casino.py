from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

import discord
from redbot.core import app_commands, commands

from .constants import (
    CASH,
    CASINO_GAMES,
    CURRENCY_NAME,
    DEFAULT_CASINO_MAX_BET,
    GOLD,
    MAX_AMOUNT,
)
from .errors import EconomyError
from .layouts import send_embed
from . import sinks
from .blackjack import (
    BlackjackCard,
    BlackjackHand,
    BlackjackShoe,
    BlackjackView,
    render_blackjack_table,
)
from .holdem_table import HoldemTable
from .holdem_escrow import recover_escrows, reserve_buyin, resolve_escrow
from .casino_games import (
    MINES_MAX_COUNT,
    MINES_MAX_PAYOUT_MULTIPLIER,
    MINES_MIN_COUNT,
    can_extend_casino_self_exclusion,
    calculate_high_card_payout,
    calculate_roulette_payout,
    draw_high_card,
    high_card_label,
    normalize_roulette_bet,
    parse_casino_exclusion_duration,
    roulette_bet_label,
    roulette_number_color,
)
from .mines import DEFAULT_MINES, MinesView
from .slots import (
    SLOT_EMOJIS,
    SLOT_TRIPLE_MULTIPLIERS,
    calculate_slot_payout,
    draw_slot_spin,
    render_slot_spin,
)

log = logging.getLogger("red.neufox.economy")


class CasinoMixin:
    """Casino games, Hold'em tables and player protection."""

    async def _casino_games_off(self, guild_id: int | None = None) -> set[str]:
        """Games switched off by the bot owner, or by this server's admins."""
        off = {game for game in CASINO_GAMES if not await getattr(self.config, f"casino_{game}_enabled")()}
        if guild_id is not None:
            guild_config = self.config.guild_from_id(guild_id)
            off |= {game for game in CASINO_GAMES if not await getattr(guild_config, f"casino_{game}_allowed")()}
        return off

    async def _casino_menu_embed(self, user_id: int, guild: discord.Guild | None = None) -> discord.Embed:
        enabled = bool(await self.config.casino_enabled())
        coinflip = int(await self.config.casino_coinflip_payout_percent()) / 100
        dice = int(await self.config.casino_dice_payout_percent()) / 100
        games_off = await self._casino_games_off(guild.id if guild else None)
        minimum = int(await self.config.casino_min_bet())
        maximum = int(await self.config.casino_max_bet())
        rake = float(await self.config.casino_rake_percent())
        active_exclusions = await self._active_casino_exclusions(user_id)
        rake_text = f" | House rake: **{rake:g}%** of winnings" if rake > 0 else ""
        embed = discord.Embed(
            title="LWD$ Casino",
            description=(
                "Play using virtual LWD$ only. Each wager is taken from your global balance.\n"
                f"Status: **{'Open' if enabled else 'Closed'}** | Bets: **{minimum:,}-{maximum:,} {CURRENCY_NAME}**"
                f"{rake_text}"
            ),
            color=GOLD,
        )
        games = [
            ("Coin Flip", f"`/casino coinflip <bet> [heads|tails]` — a correct call returns {coinflip:g}x."),
            ("Dice", f"`/casino dice <bet> <1-6>` — a correct guess returns {dice:g}x."),
            ("High Card", "`/casino highcard <bet>` — higher rank returns 2x; equal ranks push."),
            (
                "European Roulette",
                "`/casino roulette <bet> <choice>` — 0-36 (36x), red/black, odd/even, low/high (2x), "
                "1st12/2nd12/3rd12 (3x).",
            ),
            ("Slots", "`/casino slots <bet>` — exact triples 4x-80x; one lone cherry returns half."),
            (
                "Mines",
                f"`/casino mines <bet> [mines]` — reveal safe tiles and cash out. {MINES_MIN_COUNT}-"
                f"{MINES_MAX_COUNT} mines (default {DEFAULT_MINES}), capped at {MINES_MAX_PAYOUT_MULTIPLIER}x.",
            ),
            (
                "Blackjack",
                "`/casino blackjack <bet>` — hit, stand, double, split, surrender and insurance on your "
                "own 6-deck shoe.",
            ),
            (
                "Texas Hold'em",
                "`/casino holdem open <buyin>` — a six-seat, one-hand no-limit table. House players fill "
                "empty seats; join, start and play with buttons.",
            ),
            ("LWDMillions", "`/casino lottery` — the twice-weekly lottery. Draws Tuesday and Friday at 20:00 UTC."),
        ]
        closed_names = {"European Roulette" if game == "roulette" else CASINO_GAMES[game] for game in games_off}
        for name, value in games:
            if name in closed_names:
                value = f"*Closed.* {value}"
            embed.add_field(name=name, value=value, inline=False)
        protection_lines = [
            "`/casino selfexclude <duration|permanent> confirm` · `/casino exclusion`",
            "Durations use `m`, `h`, `d`, `w`, or `y` (for example `30d` or `1y`). "
            "You cannot shorten or remove your own exclusion; a server admin can lift it.",
        ]
        if active_exclusions:
            protection_lines.extend(
                ["", "**Your current exclusions:**", *self._casino_exclusion_status_lines(active_exclusions)]
            )
        embed.add_field(name="Player Protection", value="\n".join(protection_lines), inline=False)
        embed.add_field(
            name="Administrators",
            value="`/casino exclude <member> <duration> [reason]` · `/casino unexclude <member>`",
            inline=False,
        )
        return embed

    @commands.hybrid_group(name="casino", aliases=["gamble"], fallback="games")
    async def casino(self, ctx: commands.Context):
        """Casino games and the LWDMillions lottery, played with virtual LWD$."""
        await send_embed(ctx, await self._casino_menu_embed(ctx.author.id, ctx.guild))

    @casino.command(name="coinflip", aliases=["coin", "flip"])
    @app_commands.describe(wager="Your bet in LWD$.", side="heads or tails.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_coinflip(
        self,
        ctx: commands.Context,
        wager: int,
        side: str = "heads",
    ):
        """Bet on heads or tails."""
        choices = {
            "h": "heads",
            "head": "heads",
            "heads": "heads",
            "t": "tails",
            "tail": "tails",
            "tails": "tails",
        }
        selected = choices.get(side.casefold())
        if selected is None:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("Choose `heads` or `tails`.")
            return
        try:
            await self._validate_casino_wager(wager, ctx.author.id, game="coinflip", guild_id=ctx.guild.id if ctx.guild else None)
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return

        result = "heads" if secrets.randbelow(2) == 0 else "tails"
        payout_percent = int(await self.config.casino_coinflip_payout_percent())
        payout = wager * payout_percent // 100 if selected == result else 0
        await self._finish_casino_command(ctx, "coinflip", wager, payout, f"called {selected}; landed {result}")

    @casino.command(name="dice", aliases=["die"])
    @app_commands.describe(wager="Your bet in LWD$.", guess="Your guess, 1 to 6.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_dice(self, ctx: commands.Context, wager: int, guess: int):
        """Guess a six-sided die roll."""
        if guess < 1 or guess > 6:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("Your dice guess must be from 1 to 6.")
            return
        try:
            await self._validate_casino_wager(wager, ctx.author.id, game="dice", guild_id=ctx.guild.id if ctx.guild else None)
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return

        result = secrets.randbelow(6) + 1
        payout_percent = int(await self.config.casino_dice_payout_percent())
        payout = wager * payout_percent // 100 if guess == result else 0
        await self._finish_casino_command(ctx, "dice", wager, payout, f"guessed {guess}; rolled {result}")

    @casino.command(name="highcard", aliases=["war"])
    @app_commands.describe(wager="Your bet in LWD$.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_highcard(self, ctx: commands.Context, wager: int):
        """Draw a high card against the dealer."""
        try:
            await self._validate_casino_wager(wager, ctx.author.id, game="highcard", guild_id=ctx.guild.id if ctx.guild else None)
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return

        dealer_card, player_card = draw_high_card()
        payout, payout_rule = calculate_high_card_payout(wager, dealer_card, player_card)
        await self._finish_highcard_command(
            ctx,
            wager,
            payout,
            dealer_card=dealer_card,
            player_card=player_card,
            payout_rule=payout_rule,
        )

    @casino.command(name="roulette", aliases=["wheel"])
    @app_commands.describe(wager="Your bet in LWD$.", choice="0-36, red, black, odd, even, low, high, 1st12, 2nd12 or 3rd12.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_roulette(
        self,
        ctx: commands.Context,
        wager: int,
        choice: str,
    ):
        """Bet on a European roulette spin."""
        selected = normalize_roulette_bet(choice)
        if selected is None:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(
                "Choose a number from `0` to `36`, `red`, `black`, `odd`, `even`, "
                "`low`, `high`, `1st12`, `2nd12`, or `3rd12`."
            )
            return
        try:
            await self._validate_casino_wager(wager, ctx.author.id, game="roulette", guild_id=ctx.guild.id if ctx.guild else None)
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return
        if selected.startswith("number:") and wager > MAX_AMOUNT // 36:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(f"Straight-up wager cannot exceed {MAX_AMOUNT // 36:,} {CURRENCY_NAME}.")
            return

        result = secrets.randbelow(37)
        payout, payout_rule = calculate_roulette_payout(wager, selected, result)
        color = roulette_number_color(result)
        await self._finish_casino_command(
            ctx,
            "roulette",
            wager,
            payout,
            (
                f"bet {roulette_bet_label(selected)}; wheel landed on "
                f"{result} {color}; {payout_rule}"
            ),
        )

    @casino.command(name="slots", aliases=["slot"])
    @app_commands.describe(wager="Your bet in LWD$.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_slots(self, ctx: commands.Context, wager: int):
        """Spin the slot machine."""
        if ctx.author.id in self._slot_players:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("Your previous slot spin is still rendering.")
            return
        self._slot_players.add(ctx.author.id)
        try:
            try:
                await self._validate_casino_wager(wager, ctx.author.id, game="slots", guild_id=ctx.guild.id if ctx.guild else None)
            except EconomyError as error:
                ctx.command.reset_cooldown(ctx)
                await ctx.send(str(error))
                return
            safe_maximum = MAX_AMOUNT // max(SLOT_TRIPLE_MULTIPLIERS.values())
            if wager > safe_maximum:
                ctx.command.reset_cooldown(ctx)
                await ctx.send(f"Slot wager cannot exceed {safe_maximum:,} {CURRENCY_NAME}.")
                return

            symbols, stops = draw_slot_spin()
            payout, payout_rule = calculate_slot_payout(wager, symbols)
            await self._finish_slots_command(
                ctx,
                wager,
                payout,
                symbols=symbols,
                stops=stops,
                payout_rule=payout_rule,
            )
        finally:
            self._slot_players.discard(ctx.author.id)

    @casino.command(name="mines", aliases=["mine"])
    @app_commands.describe(wager="Your bet in LWD$.", mine_count="How many mines, 1 to 10.")
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def economy_casino_mines(
        self,
        ctx: commands.Context,
        wager: int,
        mine_count: int = DEFAULT_MINES,
    ):
        """Play an interactive game of Mines."""
        await self._run_mines_round(ctx, wager, mine_count)

    @casino.command(name="blackjack", aliases=["bj"])
    @app_commands.describe(wager="Your bet in LWD$.")
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def economy_casino_blackjack(self, ctx: commands.Context, wager: int):
        """Play an interactive hand of blackjack."""
        await self._run_blackjack_round(ctx, wager)

    @casino.group(name="holdem", aliases=["poker"], fallback="open")
    @app_commands.describe(buyin="The fixed buy-in every player pays.")
    async def holdem(self, ctx: commands.Context, buyin: int | None = None):
        """Open a one-hand Texas Hold'em table with a fixed buy-in."""
        if ctx.guild is None:
            await ctx.send("Hold'em tables are only available in server channels.")
            return
        if buyin is None:
            await ctx.send("Use `/casino holdem open <buy-in>` to open a table here.")
            return
        try:
            async with self._holdem_lock:
                if ctx.channel.id in self._holdem_tables:
                    raise EconomyError("This channel already has a Hold'em table.")
                if any(any(p.user_id == ctx.author.id for p in table.players) for table in self._holdem_tables.values()):
                    raise EconomyError("Finish your current Hold'em table first.")
                await self._validate_casino_wager(buyin, ctx.author.id, game="holdem", guild_id=ctx.guild.id)
                table = HoldemTable(self, ctx, buyin)
                table.lobby_seconds = int(await self.config.holdem_lobby_seconds())
                table.turn_seconds = int(await self.config.holdem_turn_seconds())
                self._holdem_tables[ctx.channel.id] = table
                try:
                    await table.join(ctx.author)
                except Exception:
                    self._holdem_tables.pop(ctx.channel.id, None)
                    raise
                table._schedule(table.lobby_seconds)
        except (EconomyError, ValueError) as error:
            await ctx.send(str(error))

    @holdem.command(name="join")
    async def economy_casino_holdem_join(self, ctx: commands.Context):
        """Join the Hold'em table in this channel."""
        try:
            async with self._holdem_lock:
                table = self._get_holdem_table(ctx)
                if any(any(p.user_id == ctx.author.id for p in other.players) for other in self._holdem_tables.values()):
                    raise EconomyError("Finish your current Hold'em table first.")
                await table.join(ctx.author)
        except (EconomyError, ValueError) as error:
            await ctx.send(str(error))
            return
        await self._ack_slash(ctx)

    @holdem.command(name="start")
    async def economy_casino_holdem_start(self, ctx: commands.Context):
        """Start the host's Hold'em table."""
        try:
            await self._get_holdem_table(ctx).start(ctx.author.id)
        except (EconomyError, ValueError) as error:
            await ctx.send(str(error))
            return
        await self._ack_slash(ctx)

    @holdem.command(name="cancel")
    async def economy_casino_holdem_cancel(self, ctx: commands.Context):
        """Cancel a waiting table and refund every buy-in."""
        try:
            await self._get_holdem_table(ctx).cancel(ctx.author.id)
        except (EconomyError, ValueError) as error:
            await ctx.send(str(error))
            return
        await self._ack_slash(ctx)

    @holdem.command(name="fold")
    async def economy_casino_holdem_fold(self, ctx: commands.Context):
        """Fold at the Hold'em table in this channel."""
        await self._holdem_action(ctx, "fold")

    @holdem.command(name="check")
    async def economy_casino_holdem_check(self, ctx: commands.Context):
        """Check at the Hold'em table in this channel."""
        await self._holdem_action(ctx, "check")

    @holdem.command(name="call")
    async def economy_casino_holdem_call(self, ctx: commands.Context):
        """Call at the Hold'em table in this channel."""
        await self._holdem_action(ctx, "call")

    @holdem.command(name="raise")
    @app_commands.describe(amount="Your new total bet for this betting round.")
    async def economy_casino_holdem_raise(self, ctx: commands.Context, amount: int):
        """Raise to a total street bet, or your all-in amount."""
        await self._holdem_action(ctx, "raise", amount)

    @casino.command(name="selfexclude", aliases=["self-exclude"])
    @app_commands.describe(duration="For example 30d, 1y, or permanent.", confirmation="Type confirm to lock it in.")
    async def economy_casino_selfexclude(
        self,
        ctx: commands.Context,
        duration: str,
        confirmation: str = "",
    ):
        """Exclude yourself from casino games for a fixed period or permanently."""
        seconds = parse_casino_exclusion_duration(duration)
        if seconds is None:
            await ctx.send(
                "Use a duration like `12h`, `30d`, `6w`, or `1y`, or use `permanent`."
            )
            return
        if confirmation.casefold() != "confirm":
            await ctx.send(
                "You cannot cancel or shorten a self-exclusion yourself, although a server "
                "admin can remove it. If you are certain, run "
                f"`{ctx.clean_prefix}{ctx.command.qualified_name} {duration} confirm`."
            )
            return

        expires_at = int(time.time()) + seconds if seconds else 0
        async with self._casino_exclusion_lock:
            active = await self._active_casino_exclusions(ctx.author.id)
            current = active.get("self")
            if current:
                current_expiry = int(current.get("expires_at", 0) or 0)
                if not can_extend_casino_self_exclusion(current_expiry, expires_at):
                    if current_expiry == 0:
                        await ctx.send("Your permanent casino self-exclusion is already active.")
                        return
                    await ctx.send(
                        "You cannot shorten your self-exclusion. It currently ends "
                        f"<t:{current_expiry}:F> (<t:{current_expiry}:R>)."
                    )
                    return

            await self._set_casino_exclusion(
                ctx.author.id,
                "self",
                expires_at=expires_at,
                actor_id=ctx.author.id,
                reason="Voluntary self-exclusion",
            )
        await self._cancel_excluded_casino_games(ctx.author.id)
        if expires_at:
            await ctx.send(
                "Your casino self-exclusion is now active until "
                f"<t:{expires_at}:F> (<t:{expires_at}:R>). You cannot cancel or shorten it "
                "yourself; a server admin can remove it."
            )
        else:
            await ctx.send(
                "Your permanent casino self-exclusion is now active. You cannot cancel it "
                "yourself; a server admin can remove it."
            )

    @casino.command(name="exclusion", aliases=["excluded", "status"])
    @app_commands.describe(member="Whose status to show (administrators only).")
    async def economy_casino_exclusion(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ):
        """Show your casino exclusion status; administrators may inspect another member."""
        target = member or ctx.author
        if target.id != ctx.author.id:
            is_owner = await self.bot.is_owner(ctx.author)
            is_admin = bool(
                ctx.guild
                and isinstance(ctx.author, discord.Member)
                and ctx.author.guild_permissions.administrator
            )
            if not is_owner and not is_admin:
                await ctx.send("Only administrators can view another player's exclusion status.")
                return

        active = await self._active_casino_exclusions(target.id)
        if not active:
            await ctx.send(f"{target.mention} has no active casino exclusion.")
            return
        await ctx.send(
            f"Casino exclusions for {target.mention}:\n"
            + "\n".join(self._casino_exclusion_status_lines(active)),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @casino.command(name="exclude")
    @app_commands.describe(member="Who to exclude.", duration="For example 30d, 1y, or permanent.", reason="Why.")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def economy_casino_exclude(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str = "No reason provided.",
    ):
        """Administratively exclude a member from casino games."""
        seconds = parse_casino_exclusion_duration(duration)
        if seconds is None:
            await ctx.send(
                "Use a duration like `12h`, `30d`, `6w`, or `1y`, or use `permanent`."
            )
            return
        expires_at = int(time.time()) + seconds if seconds else 0
        reason = reason.strip()[:500] or "No reason provided."
        await self._set_casino_exclusion(
            member.id,
            "admin",
            expires_at=expires_at,
            actor_id=ctx.author.id,
            reason=reason,
        )
        await self._cancel_excluded_casino_games(member.id)
        duration_text = (
            f"until <t:{expires_at}:F> (<t:{expires_at}:R>)" if expires_at else "permanently"
        )
        await ctx.send(
            f"{member.mention} is excluded from casino games {duration_text}. Reason: {reason}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @casino.command(name="unexclude")
    @app_commands.describe(member="Whose exclusions to remove.")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def economy_casino_unexclude(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ):
        """Remove all active casino exclusions, including a player's self-exclusion."""
        removed = await self._clear_casino_exclusions(member.id)
        if not removed:
            await ctx.send(f"{member.mention} has no active casino exclusion.")
            return
        labels = []
        if "self" in removed:
            labels.append("self-exclusion")
        if "admin" in removed:
            labels.append("administrator-imposed exclusion")
        await ctx.send(
            f"Removed the {' and '.join(labels)} for {member.mention}. They can use chance "
            "games again."
        )

    async def _run_mines_round(
        self,
        ctx: commands.Context,
        wager: int,
        mine_count: int,
    ) -> bool:
        """Run one Mines game for a command or replay interaction."""
        existing_view = self._mines_views.get(ctx.author.id)
        if (
            ctx.author.id in self._mines_players
            and (existing_view is None or existing_view.phase != "ended")
        ):
            await ctx.send("You already have an active Mines game.")
            return False
        if mine_count < MINES_MIN_COUNT or mine_count > MINES_MAX_COUNT:
            await ctx.send(
                f"Choose between {MINES_MIN_COUNT} and {MINES_MAX_COUNT} mines."
            )
            return False

        self._mines_players.add(ctx.author.id)
        view: MinesView | None = None
        try:
            try:
                await self._validate_casino_wager(wager, ctx.author.id, game="mines", guild_id=ctx.guild.id if ctx.guild else None)
                safe_maximum = MAX_AMOUNT // MINES_MAX_PAYOUT_MULTIPLIER
                if wager > safe_maximum:
                    raise EconomyError(
                        f"Mines wager cannot exceed {safe_maximum:,} {CURRENCY_NAME}."
                    )
                account = await self._reserve_mines_wager(
                    ctx.author.id,
                    wager,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    reason=f"mines initial wager; {mine_count} mines",
                )
            except EconomyError as error:
                await ctx.send(str(error))
                return False

            try:
                view = MinesView(
                    self,
                    ctx,
                    wager,
                    mine_count,
                    currency_name=CURRENCY_NAME,
                )
                self._mines_views[ctx.author.id] = view
                view.final_balance = account[CASH]
                await view.start()
                await view.wait()
            except Exception:
                log.exception("Could not run Mines for user %s", ctx.author.id)
                if view is None or view.message is None:
                    if view is None or view.phase != "ended":
                        await self._credit_mines_payout(
                            ctx.author.id,
                            wager,
                            guild_id=ctx.guild.id if ctx.guild else None,
                            reason="mines canceled after startup error; wager refunded",
                        )
                        await ctx.send("Mines could not start, so your wager was refunded.")
                    else:
                        await ctx.send(
                            "The Mines display failed after settlement. "
                            f"Balance: {view.final_balance:,} {CURRENCY_NAME}."
                        )
                else:
                    await ctx.send(
                        "That Mines game hit an unexpected error and will time out safely."
                    )
            return True
        finally:
            if view is None:
                current_view = self._mines_views.get(ctx.author.id)
                if current_view is None or current_view.phase == "ended":
                    self._mines_players.discard(ctx.author.id)
            elif self._mines_views.get(ctx.author.id) is view:
                self._mines_players.discard(ctx.author.id)
                self._mines_views.pop(ctx.author.id, None)

    def _get_holdem_table(self, ctx: commands.Context) -> HoldemTable:
        table = self._holdem_tables.get(ctx.channel.id)
        if table is None or table.closed:
            raise EconomyError("There is no active Hold'em table in this channel.")
        return table

    async def _holdem_action(self, ctx: commands.Context, action: str, amount: int = 0):
        try:
            table = self._get_holdem_table(ctx)
            if await self._active_casino_exclusions(ctx.author.id):
                raise EconomyError("You cannot act in casino games while excluded.")
            await table.act(ctx.author.id, action, amount)
        except (EconomyError, ValueError) as error:
            await ctx.send(str(error))
            return
        await self._ack_slash(ctx)

    async def _upgrade_casino_limit(self):
        """Raise the old default once while preserving deliberately higher limits."""
        if await self.config.casino_50k_limit_applied():
            return
        if int(await self.config.casino_max_bet()) == 10_000:
            await self.config.casino_max_bet.set(DEFAULT_CASINO_MAX_BET)
        await self.config.casino_50k_limit_applied.set(True)

    async def _run_blackjack_round(self, ctx: commands.Context, wager: int) -> bool:
        """Run one blackjack round for a command or Play Again interaction."""
        existing_view = self._blackjack_views.get(ctx.author.id)
        if (
            ctx.author.id in self._blackjack_players
            and (existing_view is None or existing_view.phase != "ended")
        ):
            await ctx.send("You already have an active blackjack hand.")
            return False
        self._blackjack_players.add(ctx.author.id)
        view: BlackjackView | None = None
        try:
            try:
                await self._validate_casino_wager(wager, ctx.author.id, game="blackjack", guild_id=ctx.guild.id if ctx.guild else None)
                account = await self._reserve_blackjack_wager(
                    ctx.author.id,
                    wager,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    reason="blackjack initial wager",
                )
            except EconomyError as error:
                await ctx.send(str(error))
                return False

            try:
                decks = int(await self.config.blackjack_decks())
                shoe = self._blackjack_shoes.get(ctx.author.id)
                if shoe is None or shoe.decks != decks:
                    shoe = self._blackjack_shoes[ctx.author.id] = BlackjackShoe(decks)
                view = BlackjackView(
                    self,
                    ctx,
                    wager,
                    currency_name=CURRENCY_NAME,
                    shoe=shoe,
                )
                self._blackjack_views[ctx.author.id] = view
                view.final_balance = account[CASH]
                await view.start()
                await view.wait()
            except Exception:
                log.exception("Could not run blackjack for user %s", ctx.author.id)
                if view is None or view.message is None:
                    if view is None or view.phase != "ended":
                        await self._credit_blackjack_payout(
                            ctx.author.id,
                            wager,
                            guild_id=ctx.guild.id if ctx.guild else None,
                            reason="blackjack canceled after startup error",
                        )
                        await ctx.send("Blackjack could not start, so your wager was refunded.")
                    else:
                        await ctx.send(
                            "The blackjack display failed after settlement. "
                            f"Balance: {view.final_balance:,} {CURRENCY_NAME}."
                        )
                else:
                    try:
                        await view.cancel_and_refund("The hand hit an unexpected error")
                    except Exception:
                        log.exception("Could not refund blackjack for user %s", ctx.author.id)
                        await ctx.send(
                            "That blackjack hand hit an unexpected error and could not be refunded "
                            "automatically. Please contact staff."
                        )
            return True
        finally:
            if view is None:
                current_view = self._blackjack_views.get(ctx.author.id)
                if current_view is None or current_view.phase == "ended":
                    self._blackjack_players.discard(ctx.author.id)
            elif self._blackjack_views.get(ctx.author.id) is view:
                self._blackjack_players.discard(ctx.author.id)
                self._blackjack_views.pop(ctx.author.id, None)

    async def _active_casino_exclusions(self, user_id: int) -> dict[str, dict[str, Any]]:
        """Return active exclusions and lazily discard expired records."""
        now = int(time.time())
        user_key = str(user_id)
        active: dict[str, dict[str, Any]] = {}
        async with self.config.casino_exclusions() as exclusions:
            stored = exclusions.get(user_key)
            if not isinstance(stored, dict):
                if stored is not None:
                    exclusions.pop(user_key, None)
                return active

            for source in ("self", "admin"):
                record = stored.get(source)
                if not isinstance(record, dict):
                    stored.pop(source, None)
                    continue
                expires_at = int(record.get("expires_at", 0) or 0)
                if expires_at and expires_at <= now:
                    stored.pop(source, None)
                    continue
                active[source] = dict(record)

            if stored:
                exclusions[user_key] = stored
            else:
                exclusions.pop(user_key, None)
        return active

    async def _set_casino_exclusion(
        self,
        user_id: int,
        source: str,
        *,
        expires_at: int,
        actor_id: int,
        reason: str,
    ):
        if source not in {"self", "admin"}:
            raise ValueError("unknown casino exclusion source")
        async with self.config.casino_exclusions() as exclusions:
            user_key = str(user_id)
            stored = exclusions.get(user_key)
            if not isinstance(stored, dict):
                stored = {}
            stored[source] = {
                "expires_at": int(expires_at),
                "created_at": int(time.time()),
                "actor_id": int(actor_id),
                "reason": str(reason)[:500],
            }
            exclusions[user_key] = stored

    async def _clear_casino_exclusions(self, user_id: int) -> set[str]:
        """Remove both player and administrator exclusions for an admin command."""
        user_key = str(user_id)
        async with self.config.casino_exclusions() as exclusions:
            stored = exclusions.get(user_key)
            if not isinstance(stored, dict):
                if stored is not None:
                    exclusions.pop(user_key, None)
                return set()
            removed = {source for source in ("self", "admin") if source in stored}
            for source in removed:
                stored.pop(source, None)
            if stored:
                exclusions[user_key] = stored
            else:
                exclusions.pop(user_key, None)
            return removed

    @staticmethod
    def _casino_exclusion_status_lines(active: dict[str, dict[str, Any]]) -> list[str]:
        lines = []
        for source, label in (("self", "Self-exclusion"), ("admin", "Admin exclusion")):
            record = active.get(source)
            if not record:
                continue
            expires_at = int(record.get("expires_at", 0) or 0)
            duration = (
                f"until <t:{expires_at}:F> (<t:{expires_at}:R>)"
                if expires_at
                else "permanent"
            )
            line = f"- **{label}:** {duration}"
            reason = str(record.get("reason", "")).strip()
            if source == "admin" and reason:
                line += f" — {reason}"
            lines.append(line)
        return lines

    async def _cancel_excluded_blackjack(self, user_id: int):
        view = self._blackjack_views.get(user_id)
        if view is not None and view.phase != "ended":
            await view.cancel_for_exclusion()

    async def _cancel_excluded_casino_games(self, user_id: int):
        await self._cancel_excluded_blackjack(user_id)
        view = self._mines_views.get(user_id)
        if view is not None and view.phase != "ended":
            await view.cancel_for_exclusion()
        for table in list(self._holdem_tables.values()):
            if any(player.user_id == user_id for player in table.players):
                await table.cancel(user_id, force=True)

    async def _validate_casino_wager(
        self, wager: int, user_id: int | None = None, *, game: str | None = None, guild_id: int | None = None,
    ):
        if not await self.config.casino_enabled():
            raise EconomyError("The casino is currently closed.")
        if game is not None and game in await self._casino_games_off(guild_id):
            raise EconomyError(f"{CASINO_GAMES[game]} is currently closed.")
        if user_id is not None:
            active = await self._active_casino_exclusions(user_id)
            if active:
                details = " ".join(self._casino_exclusion_status_lines(active))
                raise EconomyError(f"You cannot use casino games while excluded. {details}")
        minimum = int(await self.config.casino_min_bet())
        maximum = int(await self.config.casino_max_bet())
        if wager < minimum or wager > maximum:
            raise EconomyError(
                f"Wager must be between {minimum:,} and {maximum:,} {CURRENCY_NAME}."
            )

    async def _finish_casino_command(
        self,
        ctx: commands.Context,
        game: str,
        wager: int,
        payout: int,
        outcome: str,
    ):
        try:
            account = await self._settle_casino_wager(
                ctx.author.id,
                wager,
                payout,
                game=game,
                outcome=outcome,
                guild_id=ctx.guild.id if ctx.guild else None,
            )
        except EconomyError as error:
            await ctx.send(str(error))
            return

        payout -= int(account.get("rake", 0))
        net = payout - wager
        if net > 0:
            result = (
                f"You won **{net:,} {CURRENCY_NAME}**! "
                f"Total returned: {payout:,} {CURRENCY_NAME}."
            )
            color = discord.Color.green()
        elif net == 0:
            result = f"Push. Your **{wager:,} {CURRENCY_NAME}** wager was returned."
            color = discord.Color.gold()
        elif payout:
            result = (
                f"You lost **{-net:,} {CURRENCY_NAME}**. "
                f"Returned: {payout:,} {CURRENCY_NAME}."
            )
            color = discord.Color.red()
        else:
            result = f"You lost **{wager:,} {CURRENCY_NAME}**."
            color = discord.Color.red()

        game_title = {
            "coinflip": "Coin Flip",
            "dice": "Dice",
            "highcard": "High Card",
            "roulette": "European Roulette",
            "slots": "Slots",
        }.get(
            game,
            game.title(),
        )
        embed = discord.Embed(title=f"Casino - {game_title}", color=color)
        embed.set_author(
            name=f"Player: {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )
        embed.add_field(name="Outcome", value=outcome, inline=False)
        embed.add_field(name="Result", value=result, inline=False)
        embed.set_footer(text=f"Balance: {account[CASH]:,} {CURRENCY_NAME}{self._rake_note(account)}")
        await send_embed(ctx, embed)

    async def _finish_highcard_command(
        self,
        ctx: commands.Context,
        wager: int,
        payout: int,
        *,
        dealer_card: tuple[str, str],
        player_card: tuple[str, str],
        payout_rule: str,
    ):
        dealer_label = high_card_label(dealer_card)
        player_label = high_card_label(player_card)
        outcome = f"dealer {dealer_label}; player {player_label}; {payout_rule}"
        try:
            account = await self._settle_casino_wager(
                ctx.author.id,
                wager,
                payout,
                game="highcard",
                outcome=outcome,
                guild_id=ctx.guild.id if ctx.guild else None,
            )
        except EconomyError as error:
            await ctx.send(str(error))
            return

        payout -= int(account.get("rake", 0))
        net = payout - wager
        if net > 0:
            title = "You Win"
            result = f"You won **{net:,} {CURRENCY_NAME}**. Total returned: {payout:,}."
            color = discord.Color.green()
        elif net == 0:
            title = "Push"
            result = f"Your **{wager:,} {CURRENCY_NAME}** wager was returned."
            color = discord.Color.gold()
        else:
            title = "Dealer Wins"
            result = f"You lost **{wager:,} {CURRENCY_NAME}**."
            color = discord.Color.red()

        embed = discord.Embed(title=f"Casino - High Card: {title}", color=color)
        embed.set_author(
            name=f"Player: {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )
        embed.add_field(name="Dealer", value=dealer_label, inline=True)
        embed.add_field(name="Player", value=player_label, inline=True)
        embed.add_field(name="Result", value=result, inline=False)
        embed.set_footer(text=f"Balance: {account[CASH]:,} {CURRENCY_NAME}{self._rake_note(account)}")

        buffer = None
        file = None
        try:
            dealer = BlackjackCard(*dealer_card)
            hand = BlackjackHand([BlackjackCard(*player_card)], wager, stood=True)
            buffer = await asyncio.to_thread(render_blackjack_table, [dealer], [hand], None)
            filename = f"highcard-{ctx.author.id}-{secrets.token_hex(4)}.png"
            file = discord.File(buffer, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            await send_embed(ctx, embed, file=file)
        except Exception:
            log.exception("Could not render high card table for user %s", ctx.author.id)
            embed.remove_image()
            await send_embed(ctx, embed)
        finally:
            if file is not None:
                file.close()
            elif buffer is not None:
                buffer.close()

    async def _finish_slots_command(
        self,
        ctx: commands.Context,
        wager: int,
        payout: int,
        *,
        symbols: tuple[str, str, str],
        stops: tuple[int, int, int],
        payout_rule: str,
    ):
        outcome = " | ".join(SLOT_EMOJIS[symbol] for symbol in symbols)
        try:
            account = await self._settle_casino_wager(
                ctx.author.id,
                wager,
                payout,
                game="slots",
                outcome=f"{outcome}; {payout_rule}",
                guild_id=ctx.guild.id if ctx.guild else None,
            )
        except EconomyError as error:
            await ctx.send(str(error))
            return

        payout -= int(account.get("rake", 0))
        net = payout - wager
        if net > 0:
            result = (
                f"You won **{net:,} {CURRENCY_NAME}**! "
                f"Total returned: {payout:,} {CURRENCY_NAME}."
            )
            color = discord.Color.green()
        elif payout:
            result = (
                f"You lost **{-net:,} {CURRENCY_NAME}**. "
                f"Returned: {payout:,} {CURRENCY_NAME}."
            )
            color = discord.Color.red()
        else:
            result = f"You lost **{wager:,} {CURRENCY_NAME}**."
            color = discord.Color.red()

        embed = discord.Embed(title="Casino - Slots", color=color)
        embed.set_author(
            name=f"Player: {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )
        embed.add_field(name="Reels", value=outcome, inline=False)
        embed.add_field(name="Result", value=result, inline=False)
        embed.set_footer(text=f"Balance: {account[CASH]:,} {CURRENCY_NAME} | {payout_rule}{self._rake_note(account)}")

        try:
            async with self._slot_render_semaphore:
                buffer = await asyncio.to_thread(render_slot_spin, stops)
            filename = f"slots-{ctx.author.id}-{secrets.token_hex(4)}.gif"
            file = discord.File(buffer, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            try:
                await send_embed(ctx, embed, file=file)
            finally:
                file.close()
        except Exception:
            log.exception("Could not render animated slots for user %s", ctx.author.id)
            embed.remove_image()
            await send_embed(ctx, embed)

    async def _settle_casino_wager(
        self,
        user_id: int,
        wager: int,
        payout: int,
        *,
        game: str,
        outcome: str,
        guild_id: int | None,
    ) -> dict[str, int]:
        """Atomically take a casino wager and apply its payout."""
        wager = self._require_amount(wager, allow_zero=False)
        payout = self._require_amount(payout, allow_zero=True)
        rake = await self._casino_rake(payout, wager)
        payout -= rake
        net = payout - wager
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                if account[CASH] < wager:
                    raise EconomyError("Insufficient funds.")
                account[CASH] += net
                balances[str(user_id)] = account

            if net > 0:
                tx_type = "casino_win"
                from_user_id = None
                to_user_id = user_id
            elif net < 0:
                tx_type = "casino_loss"
                from_user_id = user_id
                to_user_id = None
            else:
                tx_type = "casino_push"
                from_user_id = user_id
                to_user_id = user_id
            await self._append_ledger(
                tx_type,
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                amount=abs(net),
                actor_id=user_id,
                guild_id=guild_id,
                reason=(
                    f"{game}: wager {wager}; payout {payout}; outcome {outcome}"
                ),
            )
            if rake:
                await self._append_ledger(
                    "rake",
                    from_user_id=user_id,
                    to_user_id=None,
                    amount=rake,
                    actor_id=user_id,
                    guild_id=guild_id,
                    reason=f"{game} house rake",
                    log_to_channel=False,
                )
        return {**account, "rake": rake}

    @staticmethod
    def _rake_note(account: dict[str, Any]) -> str:
        rake = int(account.get("rake", 0) or 0)
        return f" | House rake: {rake:,} {CURRENCY_NAME}" if rake else ""

    async def _casino_rake(self, payout: int, wager: int) -> int:
        """House rake on the profit of a winning result; 0 unless the owner enables it."""
        return sinks.casino_rake(payout, wager, await self.config.casino_rake_percent())

    async def _reserve_holdem_buyin(self, user_id: int, buyin: int, guild_id: int, table_id: str):
        buyin = self._require_amount(buyin, allow_zero=False)
        await self._holdem_recovery_task
        async with self._lock:
            async with self.config.balances() as balances:
                try:
                    reserve_buyin(balances, user_id, table_id, buyin)
                except ValueError as error:
                    raise EconomyError(str(error)) from error
            await self._append_ledger(
                "holdem_buyin", from_user_id=user_id, to_user_id=None,
                amount=buyin, actor_id=user_id, guild_id=guild_id,
                reason="Texas Hold'em table buy-in",
            )

    async def _refund_holdem_buyin(self, user_id: int, table_id: str, guild_id: int, reason: str):
        async with self._lock:
            async with self.config.balances() as balances:
                escrow = balances.get(str(user_id), {}).get("holdem_escrow", {})
                amount = int(escrow.get("buyin", 0)) if escrow.get("table_id") == table_id else 0
                changed = amount > 0 and resolve_escrow(balances, user_id, table_id, amount)
            if changed:
                await self._append_ledger(
                    "holdem_refund", from_user_id=None, to_user_id=user_id,
                    amount=amount, actor_id=user_id, guild_id=guild_id, reason=reason,
                )

    async def _settle_holdem_table(self, table_id: str, payouts: dict[int, int], guild_id: int):
        """Save the complete result before crediting anyone, then credit each once."""
        async with self._lock:
            async with self.config.holdem_settlements() as plans:
                plans[table_id] = {str(user_id): int(amount) for user_id, amount in payouts.items()}
            credited = []
            async with self.config.balances() as balances:
                for user_id, amount in payouts.items():
                    if resolve_escrow(balances, user_id, table_id, amount):
                        credited.append((user_id, amount))
            async with self.config.holdem_settlements() as plans:
                plans.pop(table_id, None)
            for user_id, amount in credited:
                if amount:
                    await self._append_ledger(
                        "holdem_payout", from_user_id=None, to_user_id=user_id,
                        amount=amount, actor_id=user_id, guild_id=guild_id,
                        reason="Hold'em hand settlement",
                    )

    async def _recover_holdem_escrows(self):
        """Refund interrupted hands, or finish a saved showdown exactly once."""
        async with self._lock:
            plans = await self.config.holdem_settlements()
            async with self.config.balances() as balances:
                resolved = recover_escrows(balances, plans)
            await self.config.holdem_settlements.set({})
        if resolved:
            log.warning("Recovered %s interrupted Hold'em escrow(s)", len(resolved))

    async def _reserve_blackjack_wager(
        self,
        user_id: int,
        wager: int,
        *,
        guild_id: int | None,
        reason: str,
    ) -> dict[str, int]:
        """Reserve blackjack funds immediately so they cannot be spent mid-hand."""
        wager = self._require_amount(wager, allow_zero=False)
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                if account[CASH] < wager:
                    raise EconomyError("Insufficient funds.")
                account[CASH] -= wager
                balances[str(user_id)] = account
            await self._append_ledger(
                "blackjack_wager",
                from_user_id=user_id,
                to_user_id=None,
                amount=wager,
                actor_id=user_id,
                guild_id=guild_id,
                reason=reason,
            )
        return account

    async def _credit_blackjack_payout(
        self,
        user_id: int,
        payout: int,
        *,
        guild_id: int | None,
        reason: str,
        wager: int | None = None,
    ) -> dict[str, int]:
        """Credit the total return from a reserved blackjack wager."""
        payout = self._require_amount(payout, allow_zero=False)
        rake = await self._casino_rake(payout, wager) if wager is not None else 0
        payout -= rake
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                account[CASH] += payout
                balances[str(user_id)] = account
            await self._append_ledger(
                "blackjack_payout",
                from_user_id=None,
                to_user_id=user_id,
                amount=payout,
                actor_id=user_id,
                guild_id=guild_id,
                reason=reason,
            )
            if rake:
                await self._append_ledger(
                    "rake",
                    from_user_id=user_id,
                    to_user_id=None,
                    amount=rake,
                    actor_id=user_id,
                    guild_id=guild_id,
                    reason="blackjack house rake",
                    log_to_channel=False,
                )
        return {**account, "rake": rake}

    async def _reserve_mines_wager(
        self,
        user_id: int,
        wager: int,
        *,
        guild_id: int | None,
        reason: str,
    ) -> dict[str, int]:
        """Reserve Mines funds immediately so they cannot be spent mid-game."""
        wager = self._require_amount(wager, allow_zero=False)
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                if account[CASH] < wager:
                    raise EconomyError("Insufficient funds.")
                account[CASH] -= wager
                balances[str(user_id)] = account
            await self._append_ledger(
                "mines_wager",
                from_user_id=user_id,
                to_user_id=None,
                amount=wager,
                actor_id=user_id,
                guild_id=guild_id,
                reason=reason,
            )
        return account

    async def _credit_mines_payout(
        self,
        user_id: int,
        payout: int,
        *,
        guild_id: int | None,
        reason: str,
        wager: int | None = None,
    ) -> dict[str, int]:
        """Credit the total return from a reserved Mines wager."""
        payout = self._require_amount(payout, allow_zero=False)
        rake = await self._casino_rake(payout, wager) if wager is not None else 0
        payout -= rake
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                account[CASH] += payout
                balances[str(user_id)] = account
            await self._append_ledger(
                "mines_payout",
                from_user_id=None,
                to_user_id=user_id,
                amount=payout,
                actor_id=user_id,
                guild_id=guild_id,
                reason=reason,
            )
            if rake:
                await self._append_ledger(
                    "rake",
                    from_user_id=user_id,
                    to_user_id=None,
                    amount=rake,
                    actor_id=user_id,
                    guild_id=guild_id,
                    reason="mines house rake",
                    log_to_channel=False,
                )
        return {**account, "rake": rake}

    @staticmethod
    async def _ack_slash(ctx: commands.Context):
        if ctx.interaction is not None:
            await ctx.send("Done.", ephemeral=True)

