from __future__ import annotations

import asyncio
import secrets
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from PIL import Image, ImageDraw

if TYPE_CHECKING:
    from .economy import Economy


ASSETS_PATH = Path(__file__).parent / "assets"
CARD_ASSETS_PATH = ASSETS_PATH / "cards"
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS = ("C", "D", "H", "S")


@dataclass
class BlackjackCard:
    rank: str
    suit: str
    down: bool = False

    @property
    def image_name(self) -> str:
        return "red_back.png" if self.down else f"{self.rank}{self.suit}.png"

    @property
    def label(self) -> str:
        if self.down:
            return "Hidden card"
        names = {"J": "Jack", "Q": "Queen", "K": "King", "A": "Ace"}
        suits = {"C": "Clubs", "D": "Diamonds", "H": "Hearts", "S": "Spades"}
        return f"{names.get(self.rank, self.rank)} of {suits[self.suit]}"


@dataclass
class BlackjackHand:
    cards: list[BlackjackCard]
    bet: int
    stood: bool = False
    surrendered: bool = False
    forfeited: bool = False
    split_aces: bool = False
    has_acted: bool = False

    @property
    def finished(self) -> bool:
        return self.stood or self.surrendered or self.forfeited


def hand_value(cards: list[BlackjackCard], *, reveal_hidden: bool = False) -> tuple[int, bool]:
    total = 0
    aces = 0
    for card in cards:
        if card.down and not reveal_hidden:
            continue
        if card.rank == "A":
            aces += 1
        elif card.rank in {"J", "Q", "K"}:
            total += 10
        else:
            total += int(card.rank)
    total += aces
    soft = bool(aces and total + 10 <= 21)
    if soft:
        total += 10
    return total, soft


def is_blackjack(cards: list[BlackjackCard], *, reveal_hidden: bool = False) -> bool:
    visible = sum(1 for card in cards if reveal_hidden or not card.down)
    return visible == 2 and hand_value(cards, reveal_hidden=reveal_hidden)[0] == 21


def split_value(card: BlackjackCard) -> int:
    if card.rank in {"10", "J", "Q", "K"}:
        return 10
    if card.rank == "A":
        return 11
    return int(card.rank)


def _card_images(cards: list[BlackjackCard]) -> list[Image.Image]:
    images = []
    for card in cards:
        with Image.open(CARD_ASSETS_PATH / card.image_name) as image:
            images.append(image.convert("RGBA"))
    return images


def render_blackjack_table(
    dealer_cards: list[BlackjackCard],
    player_hands: list[BlackjackHand],
    active_hand_index: int | None,
) -> BytesIO:
    """Render a blackjack table in memory using the attributed Casino Bot assets."""
    dealer_images = _card_images(dealer_cards)
    hand_images = [_card_images(hand.cards) for hand in player_hands]
    resized: list[Image.Image] = []
    table: Image.Image | None = None
    try:
        with Image.open(ASSETS_PATH / "table.png") as background:
            table = background.convert("RGBA")
        width, height = table.size
        padding = 18
        row_gap = 22
        card_gap = 8
        base_width, base_height = dealer_images[0].size
        columns = 2 if len(hand_images) > 1 else 1
        rows = max(1, (len(hand_images) + columns - 1) // columns)
        lane_width = (width - (padding * 2) - (24 if columns == 2 else 0)) // columns
        maximum_cards = max(len(dealer_images), *(len(hand) for hand in hand_images))
        width_scale = (lane_width - card_gap * (maximum_cards - 1)) / (base_width * maximum_cards)
        height_scale = (
            height - (padding * 2) - 34 - (row_gap * max(0, rows - 1))
        ) / (base_height * (rows + 1))
        scale = max(0.2, min(1.0, width_scale, height_scale))
        card_width = max(1, int(base_width * scale))
        card_height = max(1, int(base_height * scale))
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

        def resize_row(row: list[Image.Image]) -> list[Image.Image]:
            rendered = []
            for image in row:
                if image.size == (card_width, card_height):
                    rendered.append(image)
                else:
                    new_image = image.resize((card_width, card_height), resample)
                    rendered.append(new_image)
                    resized.append(new_image)
            return rendered

        rendered_dealer = resize_row(dealer_images)
        rendered_hands = [resize_row(hand) for hand in hand_images]
        draw = ImageDraw.Draw(table, "RGBA")

        dealer_width = len(rendered_dealer) * card_width + (len(rendered_dealer) - 1) * card_gap
        dealer_x = (width - dealer_width) // 2
        dealer_y = padding
        for image in rendered_dealer:
            table.alpha_composite(image, (dealer_x, dealer_y))
            dealer_x += card_width + card_gap

        player_top = dealer_y + card_height + 34
        lane_gap = 24 if columns == 2 else 0
        if columns == 2:
            draw.line(
                ((width // 2, player_top - 10), (width // 2, height - padding)),
                fill=(255, 255, 255, 150),
                width=3,
            )

        for index, hand in enumerate(rendered_hands):
            column = index % columns
            row = index // columns
            lane_left = padding + column * (lane_width + lane_gap)
            hand_width = len(hand) * card_width + (len(hand) - 1) * card_gap
            x = lane_left + max(0, (lane_width - hand_width) // 2)
            y = player_top + row * (card_height + row_gap)
            if index == active_hand_index:
                draw.rounded_rectangle(
                    (x - 8, y - 8, x + hand_width + 8, y + card_height + 8),
                    radius=8,
                    fill=(255, 215, 0, 45),
                    outline=(255, 215, 0, 255),
                    width=5,
                )
            for image in hand:
                table.alpha_composite(image, (x, y))
                x += card_width + card_gap

        output = BytesIO()
        table.save(output, format="PNG", optimize=True)
        output.seek(0)
        return output
    finally:
        if table is not None:
            table.close()
        for image in dealer_images:
            image.close()
        for hand in hand_images:
            for image in hand:
                image.close()
        for image in resized:
            image.close()


class BlackjackView(discord.ui.View):
    MAX_HANDS = 4
    DEALER_HITS_SOFT_17 = False

    def __init__(
        self,
        cog: Economy,
        ctx,
        wager: int,
        *,
        currency_name: str,
        timeout: float = 90,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.player = ctx.author
        self.guild_id = ctx.guild.id if ctx.guild else None
        self.currency_name = currency_name
        self.base_wager = wager
        self.total_wager = wager
        self.insurance_bet = 0
        self.phase = "dealing"
        self.active_hand_index = 0
        self.message: discord.Message | None = None
        self.result_title = "Blackjack"
        self.result_lines: list[str] = []
        self.final_balance: int | None = None
        self._action_lock = asyncio.Lock()
        self._action_version = 0

        self.deck = [BlackjackCard(rank, suit) for rank in RANKS for suit in SUITS]
        secrets.SystemRandom().shuffle(self.deck)
        self.hands = [BlackjackHand([self.deck.pop()], wager)]
        self.dealer_cards = [self.deck.pop()]
        self.hands[0].cards.append(self.deck.pop())
        hidden = self.deck.pop()
        hidden.down = True
        self.dealer_cards.append(hidden)

    async def start(self):
        dealer_upcard = self.dealer_cards[0]
        if dealer_upcard.rank == "A" and self.base_wager >= 2:
            self.phase = "insurance"
        else:
            await self._after_insurance_choice()
        self._sync_buttons()
        await self._send_initial_message()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                "Only the player who started this blackjack round can use these buttons.",
                ephemeral=True,
            )
            return False
        if self.phase == "ended":
            await interaction.response.send_message("This blackjack round is over.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        async with self._action_lock:
            if self.phase == "ended":
                return
            if self.phase == "insurance":
                await self._after_insurance_choice()
                if self.phase == "ended":
                    self._sync_buttons()
                    await self._edit_message()
                    return
            for hand in self.hands:
                if not hand.finished:
                    hand.forfeited = True
            await self._finish_round(timed_out=True)
            self._sync_buttons()
            await self._edit_message()

    async def cancel_and_refund(self):
        """Safely close an active round when the cog is unloaded."""
        async with self._action_lock:
            if self.phase == "ended":
                return
            await self._end_round(
                "Blackjack Canceled",
                self.total_wager,
                ["The economy cog was reloaded; all committed stakes were returned."],
            )
            self._action_version += 1
            self._sync_buttons()
            await self._edit_message()

    @discord.ui.button(label="Hit", emoji="🃏", style=discord.ButtonStyle.primary, row=0)
    async def hit_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version:
                return
            hand = self._active_hand()
            if self.phase != "playing" or hand is None:
                return
            hand.has_acted = True
            hand.cards.append(self.deck.pop())
            if hand_value(hand.cards)[0] >= 21:
                hand.stood = True
                await self._advance_hand()
            await self._refresh_after_action()

    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.secondary, row=0)
    async def stand_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version:
                return
            hand = self._active_hand()
            if self.phase != "playing" or hand is None:
                return
            hand.has_acted = True
            hand.stood = True
            await self._advance_hand()
            await self._refresh_after_action()

    @discord.ui.button(label="Double", emoji="⏫", style=discord.ButtonStyle.success, row=0)
    async def double_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version:
                return
            hand = self._active_hand()
            if self.phase != "playing" or hand is None or len(hand.cards) != 2:
                return
            from .economy import EconomyError

            try:
                account = await self.cog._reserve_blackjack_wager(
                    self.player.id,
                    hand.bet,
                    guild_id=self.guild_id,
                    reason="blackjack double down",
                )
            except EconomyError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            self.final_balance = account["cash"]
            self.total_wager += hand.bet
            hand.bet *= 2
            hand.has_acted = True
            hand.cards.append(self.deck.pop())
            hand.stood = True
            await self._advance_hand()
            await self._refresh_after_action()

    @discord.ui.button(label="Split", emoji="✂️", style=discord.ButtonStyle.success, row=1)
    async def split_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version:
                return
            hand = self._active_hand()
            if not self._can_split(hand):
                return
            from .economy import EconomyError

            try:
                account = await self.cog._reserve_blackjack_wager(
                    self.player.id,
                    hand.bet,
                    guild_id=self.guild_id,
                    reason="blackjack split",
                )
            except EconomyError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            self.final_balance = account["cash"]
            self.total_wager += hand.bet
            moved_card = hand.cards.pop()
            new_hand = BlackjackHand([moved_card], hand.bet)
            split_aces = hand.cards[0].rank == "A" and moved_card.rank == "A"
            hand.cards.append(self.deck.pop())
            new_hand.cards.append(self.deck.pop())
            if split_aces:
                hand.split_aces = True
                new_hand.split_aces = True
                hand.stood = True
                new_hand.stood = True
            else:
                if hand_value(hand.cards)[0] >= 21:
                    hand.stood = True
                if hand_value(new_hand.cards)[0] >= 21:
                    new_hand.stood = True
            self.hands.insert(self.active_hand_index + 1, new_hand)
            if hand.stood:
                await self._advance_hand()
            await self._refresh_after_action()

    @discord.ui.button(label="Surrender", emoji="🏳️", style=discord.ButtonStyle.danger, row=1)
    async def surrender_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version:
                return
            hand = self._active_hand()
            if not self._can_surrender(hand):
                return
            hand.has_acted = True
            hand.surrendered = True
            await self._advance_hand()
            await self._refresh_after_action()

    @discord.ui.button(label="Buy Insurance", emoji="🛡️", style=discord.ButtonStyle.success, row=2)
    async def insurance_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version:
                return
            if self.phase != "insurance":
                return
            insurance_bet = self.base_wager // 2
            from .economy import EconomyError

            try:
                account = await self.cog._reserve_blackjack_wager(
                    self.player.id,
                    insurance_bet,
                    guild_id=self.guild_id,
                    reason="blackjack insurance",
                )
            except EconomyError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            self.final_balance = account["cash"]
            self.insurance_bet = insurance_bet
            self.total_wager += insurance_bet
            await self._after_insurance_choice()
            await self._refresh_after_action()

    @discord.ui.button(label="No Insurance", emoji="❌", style=discord.ButtonStyle.secondary, row=2)
    async def no_insurance_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        version = self._action_version
        await interaction.response.defer()
        async with self._action_lock:
            if version != self._action_version:
                return
            if self.phase != "insurance":
                return
            await self._after_insurance_choice()
            await self._refresh_after_action()

    def _active_hand(self) -> BlackjackHand | None:
        if 0 <= self.active_hand_index < len(self.hands):
            return self.hands[self.active_hand_index]
        return None

    def _can_split(self, hand: BlackjackHand | None) -> bool:
        return bool(
            self.phase == "playing"
            and hand is not None
            and len(hand.cards) == 2
            and split_value(hand.cards[0]) == split_value(hand.cards[1])
            and len(self.hands) < self.MAX_HANDS
            and not hand.split_aces
        )

    def _can_surrender(self, hand: BlackjackHand | None) -> bool:
        return bool(
            self.phase == "playing"
            and hand is not None
            and len(self.hands) == 1
            and self.active_hand_index == 0
            and len(hand.cards) == 2
            and not hand.has_acted
        )

    async def _after_insurance_choice(self):
        dealer_blackjack = is_blackjack(self.dealer_cards, reveal_hidden=True)
        player_blackjack = is_blackjack(self.hands[0].cards, reveal_hidden=True)
        if dealer_blackjack:
            payout = self.insurance_bet * 3
            lines = []
            if player_blackjack:
                payout += self.base_wager
                lines.append(f"Main hand: push, returned {self.base_wager:,}")
            else:
                lines.append(f"Main hand: dealer blackjack, lost {self.base_wager:,}")
            if self.insurance_bet:
                lines.append(f"Insurance won, returned {self.insurance_bet * 3:,}")
            await self._end_round("Dealer Blackjack", payout, lines)
            return
        if player_blackjack:
            payout = self.base_wager + (self.base_wager * 3 // 2)
            lines = [f"Natural blackjack paid 3:2, returned {payout:,}"]
            if self.insurance_bet:
                lines.append(f"Insurance lost {self.insurance_bet:,}")
            await self._end_round("Blackjack!", payout, lines)
            return
        self.phase = "playing"

    async def _advance_hand(self):
        while self.active_hand_index < len(self.hands):
            hand = self.hands[self.active_hand_index]
            if not hand.finished and hand_value(hand.cards)[0] < 21:
                return
            hand.stood = True
            self.active_hand_index += 1
        await self._finish_round()

    async def _finish_round(self, *, timed_out: bool = False):
        self.dealer_cards[1].down = False
        eligible = [
            hand
            for hand in self.hands
            if not hand.surrendered and not hand.forfeited and hand_value(hand.cards)[0] <= 21
        ]
        dealer_total, dealer_soft = hand_value(self.dealer_cards, reveal_hidden=True)
        while eligible and (
            dealer_total < 17
            or (self.DEALER_HITS_SOFT_17 and dealer_total == 17 and dealer_soft)
        ):
            self.dealer_cards.append(self.deck.pop())
            dealer_total, dealer_soft = hand_value(self.dealer_cards, reveal_hidden=True)

        payout = 0
        lines = []
        if self.insurance_bet:
            lines.append(f"Insurance lost {self.insurance_bet:,}")
        for index, hand in enumerate(self.hands, start=1):
            total = hand_value(hand.cards)[0]
            if hand.forfeited:
                returned = 0
                outcome = "forfeit (timeout)"
            elif hand.surrendered:
                returned = hand.bet // 2
                outcome = "surrender"
            elif total > 21:
                returned = 0
                outcome = "bust"
            elif dealer_total > 21 or total > dealer_total:
                returned = hand.bet * 2
                outcome = "win"
            elif total == dealer_total:
                returned = hand.bet
                outcome = "push"
            else:
                returned = 0
                outcome = "loss"
            payout += returned
            lines.append(
                f"Hand {index}: {outcome} ({total} vs dealer {dealer_total}), returned {returned:,}"
            )
        title = "Blackjack - Timed Out" if timed_out else "Blackjack Results"
        await self._end_round(title, payout, lines)

    async def _end_round(self, title: str, payout: int, lines: list[str]):
        self.dealer_cards[1].down = False
        if payout:
            account = await self.cog._credit_blackjack_payout(
                self.player.id,
                payout,
                guild_id=self.guild_id,
                reason=f"blackjack payout; wager {self.total_wager}; returned {payout}",
            )
        else:
            account = await self.cog.get_balance(self.player.id)
        net = payout - self.total_wager
        if net > 0:
            lines.append(f"Net result: +{net:,} {self.currency_name}")
        elif net < 0:
            lines.append(f"Net result: -{-net:,} {self.currency_name}")
        else:
            lines.append(f"Net result: 0 {self.currency_name}")
        self.result_title = title
        self.result_lines = lines
        self.final_balance = account["cash"]
        self.phase = "ended"
        self.stop()

    async def _refresh_after_action(self):
        self._action_version += 1
        self._sync_buttons()
        await self._edit_message()

    def _sync_buttons(self):
        playing = self.phase == "playing"
        insurance = self.phase == "insurance"
        hand = self._active_hand()
        self.hit_button.disabled = not playing or hand is None
        self.stand_button.disabled = not playing or hand is None
        self.double_button.disabled = not playing or hand is None or len(hand.cards) != 2
        self.split_button.disabled = not self._can_split(hand)
        self.surrender_button.disabled = not self._can_surrender(hand)
        self.insurance_button.disabled = not insurance
        self.no_insurance_button.disabled = not insurance

    async def _build_embed(self) -> discord.Embed:
        if self.phase == "ended":
            color = discord.Color.gold()
            net = None
            if self.result_lines:
                for line in reversed(self.result_lines):
                    if line.startswith("Net result: +"):
                        net = 1
                        break
                    if line.startswith("Net result: -"):
                        net = -1
                        break
            if net == 1:
                color = discord.Color.green()
            elif net == -1:
                color = discord.Color.red()
            embed = discord.Embed(
                title=self.result_title,
                description="\n".join(self.result_lines),
                color=color,
            )
        elif self.phase == "insurance":
            cost = self.base_wager // 2
            embed = discord.Embed(
                title="Blackjack - Insurance?",
                description=(
                    f"Dealer shows an Ace. Insurance costs **{cost:,} {self.currency_name}** "
                    "and pays 2:1 if the dealer has blackjack."
                ),
                color=discord.Color.gold(),
            )
        else:
            lines = [f"Dealer shows: **{hand_value(self.dealer_cards)[0]}**", ""]
            for index, hand in enumerate(self.hands, start=1):
                marker = "➤" if index - 1 == self.active_hand_index else "•"
                total = hand_value(hand.cards)[0]
                status = "active" if marker == "➤" else "waiting"
                if hand.stood:
                    status = "stood"
                if total > 21:
                    status = "bust"
                lines.append(
                    f"{marker} Hand {index}: **{total}** | bet {hand.bet:,} | {status}"
                )
            embed = discord.Embed(
                title="Blackjack - Your Turn",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
        balance = self.final_balance
        if balance is None:
            balance = (await self.cog.get_balance(self.player.id))["cash"]
        embed.set_footer(
            text=(
                f"Committed: {self.total_wager:,} {self.currency_name} | "
                f"Balance: {balance:,} {self.currency_name}"
            )
        )
        embed.set_image(url=f"attachment://blackjack-{self.player.id}.png")
        return embed

    async def _render_file(self) -> tuple[discord.Embed, discord.File]:
        active = self.active_hand_index if self.phase == "playing" else None
        buffer = await asyncio.to_thread(
            render_blackjack_table,
            self.dealer_cards,
            self.hands,
            active,
        )
        filename = f"blackjack-{self.player.id}.png"
        return await self._build_embed(), discord.File(buffer, filename=filename)

    async def _send_initial_message(self):
        embed, file = await self._render_file()
        try:
            self.message = await self.ctx.send(
                embed=embed,
                file=file,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            file.close()

    async def _edit_message(self):
        if self.message is None:
            return
        embed, file = await self._render_file()
        try:
            await self.message.edit(embed=embed, attachments=[file], view=self)
        except discord.HTTPException:
            with suppress(discord.HTTPException):
                await self.message.edit(embed=embed, view=self)
        finally:
            file.close()
