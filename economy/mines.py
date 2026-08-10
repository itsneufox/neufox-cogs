from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from .casino_games import (
    MINES_MAX_COUNT,
    MINES_MAX_PAYOUT_MULTIPLIER,
    MINES_MIN_COUNT,
    MINES_RETURN_PERCENT,
    MINES_TILE_COUNT,
    calculate_mines_payout,
    draw_mines,
)

if TYPE_CHECKING:
    from .economy import Economy


DEFAULT_MINES = 3


def _multiplier_label(payout: int, wager: int) -> str:
    hundredths = payout * 100 // wager
    return f"{hundredths // 100}.{hundredths % 100:02d}x"


class MinesSetupModal(discord.ui.Modal):
    def __init__(self, replay_view: "MinesReplayView"):
        super().__init__(title="Mines - Change Game")
        self.replay_view = replay_view
        self.wager_input = discord.ui.TextInput(
            label="New wager",
            placeholder="Enter a whole number of LWD$",
            default=str(replay_view.wager),
            min_length=1,
            max_length=20,
        )
        self.mine_count_input = discord.ui.TextInput(
            label=f"Mines ({MINES_MIN_COUNT}-{MINES_MAX_COUNT})",
            placeholder="More mines increase risk and rewards",
            default=str(replay_view.mine_count),
            min_length=1,
            max_length=2,
        )
        self.add_item(self.wager_input)
        self.add_item(self.mine_count_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.replay_view.player.id:
            await interaction.response.send_message(
                "Only the original player can change this Mines game.",
                ephemeral=True,
            )
            return
        try:
            wager = int(str(self.wager_input.value).replace(",", "").strip())
            mine_count = int(str(self.mine_count_input.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Enter whole numbers for both the wager and mine count.",
                ephemeral=True,
            )
            return
        if wager <= 0:
            await interaction.response.send_message(
                "Wager must be positive.",
                ephemeral=True,
            )
            return
        if mine_count < MINES_MIN_COUNT or mine_count > MINES_MAX_COUNT:
            await interaction.response.send_message(
                f"Choose between {MINES_MIN_COUNT} and {MINES_MAX_COUNT} mines.",
                ephemeral=True,
            )
            return
        await self.replay_view.start_round(interaction, wager, mine_count)


class MinesReplayView(discord.ui.View):
    def __init__(
        self,
        cog: Economy,
        ctx,
        wager: int,
        mine_count: int,
        mines: frozenset[int],
        safe_picks: set[int],
        exploded_tile: int | None,
        *,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.player = ctx.author
        self.wager = wager
        self.mine_count = mine_count
        self.message: discord.Message | None = None
        self._launching = False

        self.same_bet_button.label = f"Same Game ({wager:,})"
        self.half_bet_button.label = f"Half ({max(1, wager // 2):,})"
        self.double_bet_button.label = f"Double ({wager * 2:,})"
        for tile_index in range(MINES_TILE_COUNT):
            if tile_index in mines:
                emoji = "💥" if tile_index == exploded_tile else "💣"
                style = discord.ButtonStyle.danger
            elif tile_index in safe_picks:
                emoji = "💎"
                style = discord.ButtonStyle.success
            else:
                emoji = "⬜"
                style = discord.ButtonStyle.secondary
            self.add_item(
                discord.ui.Button(
                    emoji=emoji,
                    style=style,
                    disabled=True,
                    row=tile_index // 5,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                "Only the original player can replay this Mines game.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        self._set_replay_buttons_disabled(True)
        if self.message is not None:
            with suppress(discord.HTTPException):
                await self.message.edit(view=self)

    def _set_replay_buttons_disabled(self, disabled: bool):
        for item in (
            self.same_bet_button,
            self.half_bet_button,
            self.double_bet_button,
            self.change_game_button,
        ):
            item.disabled = disabled

    async def start_round(
        self,
        interaction: discord.Interaction,
        wager: int,
        mine_count: int,
    ):
        if self._launching:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "A replay is already starting.",
                    ephemeral=True,
                )
            return

        self._launching = True
        if not interaction.response.is_done():
            await interaction.response.defer()
        self._set_replay_buttons_disabled(True)
        if self.message is not None:
            with suppress(discord.HTTPException):
                await self.message.edit(view=self)

        started = await self.cog._run_mines_round(self.ctx, wager, mine_count)
        if not started:
            self._launching = False
            self._set_replay_buttons_disabled(False)
            if self.message is not None:
                with suppress(discord.HTTPException):
                    await self.message.edit(view=self)

    @discord.ui.button(label="Same Game", emoji="🔁", style=discord.ButtonStyle.success, row=4)
    async def same_bet_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        await self.start_round(interaction, self.wager, self.mine_count)

    @discord.ui.button(label="Half", emoji="➗", style=discord.ButtonStyle.secondary, row=4)
    async def half_bet_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        await self.start_round(
            interaction,
            max(1, self.wager // 2),
            self.mine_count,
        )

    @discord.ui.button(label="Double", emoji="✖️", style=discord.ButtonStyle.secondary, row=4)
    async def double_bet_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        await self.start_round(interaction, self.wager * 2, self.mine_count)

    @discord.ui.button(label="Change Game", emoji="✏️", style=discord.ButtonStyle.primary, row=4)
    async def change_game_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        await interaction.response.send_modal(MinesSetupModal(self))


class MinesTileButton(discord.ui.Button):
    def __init__(self, tile_index: int):
        super().__init__(
            emoji="⬜",
            style=discord.ButtonStyle.secondary,
            row=tile_index // 5,
        )
        self.tile_index = tile_index

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if isinstance(view, MinesView):
            await view.reveal_tile(interaction, self.tile_index)


class MinesView(discord.ui.View):
    def __init__(
        self,
        cog: Economy,
        ctx,
        wager: int,
        mine_count: int,
        *,
        currency_name: str,
        timeout: float = 90,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.player = ctx.author
        self.guild_id = ctx.guild.id if ctx.guild else None
        self.wager = wager
        self.mine_count = mine_count
        self.currency_name = currency_name
        self.mines = draw_mines(mine_count)
        self.safe_picks: set[int] = set()
        self.exploded_tile: int | None = None
        self.phase = "playing"
        self.message: discord.Message | None = None
        self.final_balance: int | None = None
        self.result_title = "Casino - Mines"
        self.result_lines: list[str] = []
        self.replay_view: MinesReplayView | None = None
        self._action_lock = asyncio.Lock()
        self._action_version = 0
        self.tile_buttons: dict[int, MinesTileButton] = {}

        for tile_index in range(MINES_TILE_COUNT):
            button = MinesTileButton(tile_index)
            self.tile_buttons[tile_index] = button
            self.add_item(button)
        self._sync_buttons()

    async def start(self):
        self.message = await self.ctx.send(
            embed=await self._build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                "Only the player who started this Mines game can use these buttons.",
                ephemeral=True,
            )
            return False
        if self.phase == "ended":
            await interaction.response.send_message(
                "This Mines game is over.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        async with self._action_lock:
            if self.phase == "ended":
                return
            if self.safe_picks:
                payout = self.current_payout
                title = "Mines - Timed Out"
                lines = [
                    f"Your current return was automatically cashed out for "
                    f"**{payout:,} {self.currency_name}**."
                ]
                reason = "mines timeout auto cash-out"
            else:
                payout = self.wager
                title = "Mines - Timed Out"
                lines = [
                    f"No tile was selected, so your **{self.wager:,} "
                    f"{self.currency_name}** wager was returned."
                ]
                reason = "mines timeout before first pick; wager refunded"
            await self._end_round(title, payout, lines, reason=reason)
            self._action_version += 1
            await self._edit_message()

    async def cancel_and_refund(self):
        """Safely close an active game when the cog is unloaded."""
        async with self._action_lock:
            if self.phase == "ended":
                return
            await self._end_round(
                "Mines Canceled",
                self.wager,
                ["The economy cog was reloaded; your committed wager was returned."],
                reason="mines canceled after cog reload; wager refunded",
                allow_replay=False,
            )
            self._action_version += 1
            await self._edit_message()

    async def cancel_for_exclusion(self):
        """Close and refund an active game when its player becomes excluded."""
        async with self._action_lock:
            if self.phase == "ended":
                return
            await self._end_round(
                "Mines Canceled",
                self.wager,
                ["Casino exclusion activated; your committed wager was returned."],
                reason="mines canceled by casino exclusion; wager refunded",
                allow_replay=False,
            )
            self._action_version += 1
            await self._edit_message()

    @property
    def current_payout(self) -> int:
        return calculate_mines_payout(
            self.wager,
            self.mine_count,
            len(self.safe_picks),
        )

    async def reveal_tile(self, interaction: discord.Interaction, tile_index: int):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version or self.phase != "playing":
                return
            if tile_index in self.safe_picks:
                return

            if tile_index in self.mines:
                self.exploded_tile = tile_index
                await self._end_round(
                    "Mines - Boom!",
                    0,
                    [
                        f"Tile {tile_index + 1} contained a mine. You lost "
                        f"**{self.wager:,} {self.currency_name}**."
                    ],
                    reason=(
                        f"mines loss; {self.mine_count} mines; "
                        f"{len(self.safe_picks)} safe picks"
                    ),
                )
            else:
                self.safe_picks.add(tile_index)
                payout = self.current_payout
                safe_tile_count = MINES_TILE_COUNT - self.mine_count
                if len(self.safe_picks) == safe_tile_count:
                    await self._end_round(
                        "Mines - Board Cleared!",
                        payout,
                        [
                            f"You found every safe tile and received "
                            f"**{payout:,} {self.currency_name}**."
                        ],
                        reason=(
                            f"mines board cleared; {self.mine_count} mines; "
                            f"{len(self.safe_picks)} safe picks"
                        ),
                    )
                elif payout >= self.wager * MINES_MAX_PAYOUT_MULTIPLIER:
                    await self._end_round(
                        "Mines - Maximum Win!",
                        payout,
                        [
                            f"You reached the {MINES_MAX_PAYOUT_MULTIPLIER}x maximum and "
                            f"automatically received **{payout:,} {self.currency_name}**."
                        ],
                        reason=(
                            f"mines maximum payout; {self.mine_count} mines; "
                            f"{len(self.safe_picks)} safe picks"
                        ),
                    )
            self._action_version += 1
            self._sync_buttons()
            await self._edit_message()

    @discord.ui.button(
        label="Cash Out",
        emoji="💰",
        style=discord.ButtonStyle.success,
        row=4,
        disabled=True,
    )
    async def cashout_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if (
                version != self._action_version
                or self.phase != "playing"
                or not self.safe_picks
            ):
                return
            payout = self.current_payout
            await self._end_round(
                "Mines - Cashed Out",
                payout,
                [
                    f"You cashed out after {len(self.safe_picks)} safe "
                    f"{'tile' if len(self.safe_picks) == 1 else 'tiles'} for "
                    f"**{payout:,} {self.currency_name}** "
                    f"({_multiplier_label(payout, self.wager)})."
                ],
                reason=(
                    f"mines cash-out; {self.mine_count} mines; "
                    f"{len(self.safe_picks)} safe picks"
                ),
            )
            self._action_version += 1
            self._sync_buttons()
            await self._edit_message()

    @discord.ui.button(
        label="0 safe",
        style=discord.ButtonStyle.secondary,
        row=4,
        disabled=True,
    )
    async def safe_count_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        pass

    @discord.ui.button(
        label="Return: —",
        style=discord.ButtonStyle.secondary,
        row=4,
        disabled=True,
    )
    async def payout_info_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        pass

    async def _end_round(
        self,
        title: str,
        payout: int,
        lines: list[str],
        *,
        reason: str,
        allow_replay: bool = True,
    ):
        if payout:
            account = await self.cog._credit_mines_payout(
                self.player.id,
                payout,
                guild_id=self.guild_id,
                reason=(
                    f"{reason}; wager {self.wager}; returned {payout}"
                ),
            )
        else:
            account = await self.cog.get_balance(self.player.id)

        net = payout - self.wager
        if net > 0:
            lines.append(f"Net result: **+{net:,} {self.currency_name}**")
        elif net < 0:
            lines.append(f"Net result: **-{abs(net):,} {self.currency_name}**")
        else:
            lines.append(f"Net result: **0 {self.currency_name}**")

        self.result_title = title
        self.result_lines = lines
        self.final_balance = account["cash"]
        self.phase = "ended"
        self._sync_buttons()
        if allow_replay:
            self.replay_view = MinesReplayView(
                self.cog,
                self.ctx,
                self.wager,
                self.mine_count,
                self.mines,
                self.safe_picks,
                self.exploded_tile,
            )
        self.stop()

    def _sync_buttons(self):
        for tile_index, button in self.tile_buttons.items():
            if tile_index in self.safe_picks:
                button.emoji = "💎"
                button.style = discord.ButtonStyle.success
                button.disabled = True
            elif self.phase == "ended" and tile_index in self.mines:
                button.emoji = "💥" if tile_index == self.exploded_tile else "💣"
                button.style = discord.ButtonStyle.danger
                button.disabled = True
            else:
                button.emoji = "⬜"
                button.style = discord.ButtonStyle.secondary
                button.disabled = self.phase == "ended"

        safe_count = len(self.safe_picks)
        self.safe_count_button.label = f"{safe_count} safe"
        self.cashout_button.disabled = self.phase != "playing" or safe_count == 0
        if safe_count:
            payout = self.current_payout
            self.payout_info_button.label = (
                f"Return: {payout:,} ({_multiplier_label(payout, self.wager)})"
            )
        else:
            self.payout_info_button.label = "Return: —"

    async def _build_embed(self) -> discord.Embed:
        if self.phase == "ended":
            net = (self.final_balance is not None) and bool(self.result_lines)
            color = discord.Color.gold()
            if net and self.result_lines[-1].startswith("Net result: **+"):
                color = discord.Color.green()
            elif net and self.result_lines[-1].startswith("Net result: **-"):
                color = discord.Color.red()
            embed = discord.Embed(
                title=self.result_title,
                description="\n".join(self.result_lines),
                color=color,
            )
        else:
            embed = discord.Embed(
                title="Casino - Mines",
                description=(
                    "Reveal 💎 tiles to build your return, then cash out before "
                    "you uncover a 💣. Cash-out values use a "
                    f"{100 - MINES_RETURN_PERCENT}% house edge and are capped at "
                    f"{MINES_MAX_PAYOUT_MULTIPLIER}x."
                ),
                color=discord.Color.gold(),
            )

        embed.set_author(
            name=f"Player: {self.player.display_name}",
            icon_url=self.player.display_avatar.url,
        )
        embed.add_field(
            name="Wager",
            value=f"{self.wager:,} {self.currency_name}",
            inline=True,
        )
        embed.add_field(name="Mines", value=str(self.mine_count), inline=True)
        embed.add_field(
            name="Safe Picks",
            value=f"{len(self.safe_picks)}/{MINES_TILE_COUNT - self.mine_count}",
            inline=True,
        )

        if self.phase == "playing" and self.safe_picks:
            payout = self.current_payout
            embed.add_field(
                name="Current Cash Out",
                value=(
                    f"{payout:,} {self.currency_name} "
                    f"({_multiplier_label(payout, self.wager)})"
                ),
                inline=True,
            )
            next_pick = len(self.safe_picks) + 1
            safe_tile_count = MINES_TILE_COUNT - self.mine_count
            if next_pick <= safe_tile_count:
                next_payout = calculate_mines_payout(
                    self.wager,
                    self.mine_count,
                    next_pick,
                )
                embed.add_field(
                    name="Next Safe Tile",
                    value=(
                        f"{next_payout:,} {self.currency_name} "
                        f"({_multiplier_label(next_payout, self.wager)})"
                    ),
                    inline=True,
                )

        balance = self.final_balance
        if balance is None:
            balance = (await self.cog.get_balance(self.player.id))["cash"]
        embed.set_footer(
            text=(
                f"Committed: {self.wager:,} {self.currency_name} | "
                f"Balance: {balance:,} {self.currency_name}"
            )
        )
        return embed

    async def _edit_message(self):
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=await self._build_embed(),
                view=self._message_view(),
            )
        except discord.HTTPException:
            return
        if self.replay_view is not None:
            self.replay_view.message = self.message

    def _message_view(self) -> discord.ui.View:
        return self.replay_view or self
