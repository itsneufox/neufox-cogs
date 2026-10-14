"""Discord lifecycle for a single multiplayer Hold'em hand."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets

import discord

from .holdem import HAND_NAMES, HoldemHand, Player, hand_rank
from .holdem_render import render_holdem_table, render_hole_cards

log = logging.getLogger("red.neufox.economy.holdem")
LOBBY_SECONDS = 180
TURN_SECONDS = 90
SETTLEMENT_RETRY_SECONDS = 10


class HoleCardsView(discord.ui.View):
    def __init__(self, table: "HoldemTable"):
        super().__init__(timeout=None)
        self.table = table

    @discord.ui.button(label="Show my cards", style=discord.ButtonStyle.secondary)
    async def show_cards(self, interaction: discord.Interaction, button: discord.ui.Button):
        hand = self.table.hand
        if hand is None or hand.finished:
            await interaction.response.send_message("There is no active hand.", ephemeral=True)
            return
        player = next((p for p in hand.players if p.user_id == interaction.user.id), None)
        if player is None:
            await interaction.response.send_message("You are not at this table.", ephemeral=True)
            return
        try:
            buffer = await asyncio.to_thread(render_hole_cards, player.cards)
            file = discord.File(buffer, filename=f"holdem-private-{player.user_id}.png")
            embed = discord.Embed(title="Your Hold'em cards", description="Only you can see these cards.")
            embed.set_image(url=f"attachment://{file.filename}")
            try:
                await interaction.response.send_message(embed=embed, file=file, ephemeral=True)
            finally:
                file.close()
        except Exception:
            log.exception("Could not render private Hold'em cards")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Your hole cards: **{' '.join(player.cards)}**", ephemeral=True)

    async def _run(self, interaction: discord.Interaction, action: str):
        await interaction.response.defer()
        try:
            if action == "join":
                await self.table.cog_join(interaction.user)
            elif action == "start":
                await self.table.start(interaction.user.id)
            elif action == "cancel":
                await self.table.cancel(interaction.user.id)
            else:
                await self.table.act(interaction.user.id, action)
        except Exception as error:
            if isinstance(error, ValueError) or error.__class__.__name__ == "EconomyError":
                await interaction.followup.send(str(error), ephemeral=True)
                return
            log.exception("Hold'em button failed")
            await interaction.followup.send("That action failed. Try again or contact an administrator.", ephemeral=True)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, row=1)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "join")

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary, row=1)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "start")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "cancel")

    @discord.ui.button(label="Fold", style=discord.ButtonStyle.danger, row=2)
    async def fold(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "fold")

    @discord.ui.button(label="Check", style=discord.ButtonStyle.secondary, row=2)
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "check")

    @discord.ui.button(label="Call", style=discord.ButtonStyle.primary, row=2)
    async def call(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "call")

    @discord.ui.button(label="Raise", style=discord.ButtonStyle.success, row=2)
    async def raise_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.table.hand is None or self.table.closed:
            await interaction.response.send_message("There is no active hand.", ephemeral=True)
            return
        await interaction.response.send_modal(RaiseModal(self.table))


class RaiseModal(discord.ui.Modal, title="Texas Hold'em · Raise"):
    total = discord.ui.TextInput(label="New total bet this street", placeholder="For example: 500", min_length=1, max_length=15)

    def __init__(self, table: "HoldemTable"):
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.total.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await self.table.act(interaction.user.id, "raise", amount)
            await interaction.followup.send(f"Raised to {amount:,} chips.", ephemeral=True)
        except Exception as error:
            if isinstance(error, ValueError):
                await interaction.followup.send(str(error), ephemeral=True)
                return
            log.exception("Hold'em raise failed")
            await interaction.followup.send("That raise failed. Try again or contact an administrator.", ephemeral=True)


class HoldemTable:
    def __init__(self, cog, ctx, buyin: int):
        self.cog = cog
        self.ctx = ctx
        self.channel_id = ctx.channel.id
        self.guild_id = ctx.guild.id
        self.owner_id = ctx.author.id
        self.buyin = buyin
        self.table_id = secrets.token_hex(16)
        self.players: list[Player] = []
        self.human_ids: set[int] = set()
        self.house_ids: set[int] = set()
        self.avatars: dict[int, bytes] = {}
        self.hand: HoldemHand | None = None
        self.message: discord.Message | None = None
        self.lock = asyncio.Lock()
        self.timer: asyncio.Task | None = None
        self.closed = False
        self.settlement_started = False
        self.view = HoleCardsView(self)

    async def cog_join(self, member):
        async with self.cog._holdem_lock:
            if any(any(p.user_id == member.id for p in other.players) for other in self.cog._holdem_tables.values()):
                raise ValueError("Finish your current Hold'em table first.")
            await self.join(member)

    def _schedule(self, seconds: int):
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = self.cog.bot.loop.create_task(self._timeout(seconds))

    async def _timeout(self, seconds: int):
        try:
            await asyncio.sleep(seconds)
            async with self.lock:
                if self.closed:
                    return
                if self.hand is None:
                    await self._cancel_locked("The Hold'em lobby expired. Buy-ins were refunded.")
                elif self.hand.finished:
                    await self._after_action_locked()
                else:
                    actor = self.hand.actor
                    self.hand.act(actor.user_id, "fold")
                    self.hand.action_log[-1] = f"{actor.name} timed out and folds."
                    await self._after_action_locked()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Hold'em timeout failed in channel %s", self.channel_id)

    def _display(self) -> discord.Embed:
        hand = self.hand
        embed = discord.Embed(title="Casino · Texas Hold'em", color=discord.Color.gold())
        if hand is None:
            embed.description = (
                f"One hand · buy-in **{self.buyin:,} LWD$** · up to 6 humans.\n"
                "Join and start with the buttons below. House players fill any empty seats."
            )
            embed.add_field(name="Players", value="\n".join(p.name for p in self.players), inline=False)
            embed.set_footer(text="Lobby expires after 3 minutes. Cancel refunds every buy-in.")
            return embed
        embed.description = (
            f"**{('Showdown' if hand.showdown else 'Hand over' if hand.finished else ('Preflop', 'Flop', 'Turn', 'River')[hand.street])}**"
            f" · Board: **{' '.join(hand.board) or '—'}** · Pot: **{hand.pot:,} LWD$**"
        )
        lines = []
        for p in hand.players:
            state = "folded" if p.folded else ("all in" if not p.stack and not hand.finished else "")
            lines.append(f"{p.name}: {p.stack:,} chips · put in {p.contributed:,}" + (f" · {state}" if state else ""))
        embed.add_field(name="Seats", value="\n".join(lines), inline=False)
        if hand.finished:
            result = []
            for p in hand.players:
                if p.folded:
                    continue
                if hand.showdown:
                    rank = hand_rank(p.cards + hand.board)
                    label = HAND_NAMES[rank[0]]
                    result.append(f"{p.name}: {' '.join(p.cards)} · {label} · won {hand.payouts[p.user_id]:,}")
                else:
                    result.append(f"{p.name} wins without showing cards · won {hand.payouts[p.user_id]:,}")
            embed.add_field(name="Result", value="\n".join(result), inline=False)
        else:
            actor = hand.actor
            to_call = min(actor.stack, hand.current_bet - actor.street_bet)
            embed.add_field(
                name=f"Turn: {actor.name}",
                value=(
                    f"Call: **{to_call:,}** · Minimum raise-to: "
                    f"**{hand.current_bet + hand.minimum_raise:,}** (or all in)\n"
                    f"`{self.ctx.clean_prefix}casino holdem fold|check|call|raise <total>`"
                ), inline=False,
            )
        embed.add_field(name="Recent actions", value="\n".join(hand.action_log[-5:])[:1024], inline=False)
        embed.set_footer(text="Use Show my cards for a private view. A turn expires after 90 seconds.")
        return embed

    async def _refresh(self):
        for button in self.view.children:
            if button.label in {"Join", "Start", "Cancel"}:
                button.disabled = self.hand is not None or self.closed
            elif button.label in {"Fold", "Check", "Call", "Raise"}:
                button.disabled = self.hand is None or self.closed
        embed = self._display()
        file = None
        try:
            hand = self.hand
            buffer = await asyncio.to_thread(
                render_holdem_table,
                self.players,
                hand.board if hand else [],
                self.avatars,
                active_id=hand.actor.user_id if hand and not hand.finished and not self.closed else None,
                finished=bool(hand and hand.finished),
                showdown=bool(hand and hand.showdown),
            )
            file = discord.File(buffer, filename=f"holdem-{self.channel_id}.png")
            embed.set_image(url=f"attachment://{file.filename}")
        except Exception:
            log.exception("Could not render Hold'em table in channel %s", self.channel_id)
        try:
            if self.message is None:
                sent = await self.ctx.send(embed=embed, file=file, view=self.view, allowed_mentions=discord.AllowedMentions.none())
                self.message = self.ctx.channel.get_partial_message(sent.id)
            else:
                try:
                    await self.message.edit(embed=embed, attachments=[file] if file else [], view=None if self.closed else self.view, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    if file is None:
                        raise
                    embed.set_image(url=None)
                    await self.message.edit(embed=embed, attachments=[], view=None if self.closed else self.view, allowed_mentions=discord.AllowedMentions.none())
        finally:
            if file:
                file.close()

    async def join(self, member):
        async with self.lock:
            if self.closed or self.hand is not None:
                raise ValueError("This table has already started or closed.")
            if any(p.user_id == member.id for p in self.players):
                raise ValueError("You are already seated.")
            if len(self.players) >= 6:
                raise ValueError("This table is full (six human players).")
            await self.cog._validate_casino_wager(self.buyin, member.id)
            try:
                avatar = await asyncio.wait_for(member.display_avatar.with_size(64).read(), timeout=5)
            except Exception:
                avatar = None
            if member.bot:
                raise ValueError("Bots cannot join as human players.")
            await self.cog._reserve_holdem_buyin(member.id, self.buyin, self.guild_id, self.table_id)
            self.players.append(Player(member.id, member.display_name, self.buyin))
            self.human_ids.add(member.id)
            if avatar:
                self.avatars[member.id] = avatar
            try:
                await self._refresh()
            except Exception:
                self.players.pop()
                self.human_ids.discard(member.id)
                self.avatars.pop(member.id, None)
                await self.cog._refund_holdem_buyin(member.id, self.table_id, self.guild_id, "join display failed; refund")
                raise

    async def start(self, user_id: int):
        async with self.lock:
            if self.closed or self.hand is not None:
                raise ValueError("This table has already started or closed.")
            if user_id != self.owner_id:
                raise ValueError("Only the host can start the hand.")
            if not await self.cog.config.casino_enabled():
                raise ValueError("The casino is currently closed.")
            for number in range(1, 7 - len(self.players)):
                house_id = -number
                self.house_ids.add(house_id)
                self.players.append(Player(house_id, f"House {number}", self.buyin))
            try:
                avatar = await asyncio.wait_for(self.cog.bot.user.display_avatar.with_size(64).read(), timeout=5)
                for house_id in self.house_ids:
                    self.avatars[house_id] = avatar
            except Exception:
                pass
            try:
                self.hand = HoldemHand(self.players, self.buyin)
                await self._after_action_locked()
            except Exception:
                if not self.settlement_started:
                    await self._cancel_locked("The Hold'em table could not start. Buy-ins were refunded.")
                raise

    async def act(self, user_id: int, action: str, amount: int = 0):
        async with self.lock:
            if self.hand is None or self.closed:
                raise ValueError("There is no active hand.")
            if await self.cog._active_casino_exclusions(user_id):
                raise ValueError("You cannot act in casino games while excluded.")
            self.hand.act(user_id, action, amount)
            await self._after_action_locked()

    async def _after_action_locked(self):
        while not self.hand.finished and self.hand.actor.user_id in self.house_ids:
            action, amount = self._house_move()
            self.hand.act(self.hand.actor.user_id, action, amount)
        if self.hand.finished:
            payouts = {player.user_id: player.stack for player in self.players if player.user_id in self.human_ids}
            self.settlement_started = True
            try:
                await self.cog._settle_holdem_table(self.table_id, payouts, self.guild_id)
            except Exception:
                self._schedule(SETTLEMENT_RETRY_SECONDS)
                raise
            self.closed = True
            if self.timer and self.timer is not asyncio.current_task():
                self.timer.cancel()
            self.cog._holdem_tables.pop(self.channel_id, None)
            await self._refresh()
            self.view.stop()
        else:
            self._schedule(TURN_SECONDS)
            await self._refresh()

    def _house_move(self) -> tuple[str, int]:
        """Choose a legal action using only the house's cards and public board."""
        hand = self.hand
        player = hand.actor
        to_call = hand.current_bet - player.street_bet
        if hand.board:
            strong = hand_rank(player.cards + hand.board)[0] >= 1
        else:
            strong = player.cards[0][0] == player.cards[1][0] or any(card[0] in "TJQKA" for card in player.cards)
        roll = secrets.randbelow(100)
        if to_call:
            if to_call > player.stack // 2 and not strong and roll < 70:
                return "fold", 0
            return "call", 0
        target = min(player.street_bet + player.stack, hand.current_bet + hand.minimum_raise)
        if player.stack and target > hand.current_bet and strong and roll < 35:
            return "raise", target
        return "check", 0

    async def cancel(self, user_id: int, *, force: bool = False):
        async with self.lock:
            if not force and user_id != self.owner_id:
                raise ValueError("Only the host can cancel this lobby.")
            if self.hand is not None and not force:
                raise ValueError("A started hand cannot be canceled by its host.")
            await self._cancel_locked("The Hold'em table was canceled. Buy-ins were refunded.")

    async def _cancel_locked(self, notice: str):
        if self.closed:
            return
        self.closed = True
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.cog._holdem_tables.pop(self.channel_id, None)
        if not self.settlement_started:
            for player in self.players:
                if player.user_id in self.human_ids:
                    await self.cog._refund_holdem_buyin(player.user_id, self.table_id, self.guild_id, "Hold'em table canceled; refund")
        with contextlib.suppress(discord.HTTPException):
            await self._refresh()
        self.view.stop()
        with contextlib.suppress(discord.HTTPException):
            await self.ctx.channel.send(notice)
