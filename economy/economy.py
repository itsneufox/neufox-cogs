from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import random
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, web
import discord
from redbot.core import Config, commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

from . import nightlife
from .blackjack import BlackjackCard, BlackjackHand, BlackjackView, render_blackjack_table
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
from .mines import DEFAULT_MINES, MinesView
from .nightlife_invite import NightlifeInviteView
from .slots import (
    SLOT_EMOJIS,
    SLOT_TRIPLE_MULTIPLIERS,
    calculate_slot_payout,
    draw_slot_spin,
    render_slot_spin,
)


log = logging.getLogger("red.neufox.economy")

try:
    import drand_verify
except ImportError:  # The cog requirement normally installs this before loading.
    drand_verify = None

CASH = "cash"
CURRENCY_NAME = "LWD$"
CURRENCY_FIELD_NAME = "LWD$"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8787
DEFAULT_DAILY_AMOUNT = 250
DEFAULT_DAILY_COOLDOWN = 86400
DEFAULT_WEEKLY_AMOUNT = 1000
DEFAULT_WEEKLY_COOLDOWN = 604800
DEFAULT_MONTHLY_AMOUNT = 2500
DEFAULT_MONTHLY_COOLDOWN = 2592000
DEFAULT_ANNUAL_AMOUNT = 12000
DEFAULT_ANNUAL_COOLDOWN = 31536000
DEFAULT_WORK_AMOUNT = 75
DEFAULT_WORK_MIN = 25
DEFAULT_WORK_MAX = 100
DEFAULT_WORK_COOLDOWN = 3600
DEFAULT_CASINO_MIN_BET = 10
DEFAULT_CASINO_MAX_BET = 10000
CASINO_COINFLIP_PAYOUT_PERCENT = 195
CASINO_DICE_PAYOUT_PERCENT = 570
DEFAULT_LWDMILLIONS_TICKET_PRICE = 100
DEFAULT_LWDMILLIONS_SEED_JACKPOT = 1_000_000
DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT = 50
MAX_LWDMILLIONS_LINES_PER_PLAYER = 20
MAX_LWDMILLIONS_TOTAL_TICKETS = 10_000
LWDMILLIONS_HISTORY_LIMIT = 10
LWDMILLIONS_HISTORY_WINNER_LIMIT = 100
LWDMILLIONS_DRAW_POLL_SECONDS = 30
DRAND_FETCH_TIMEOUT_SECONDS = 12
DRAND_REQUIRED_CONSENSUS = 2
DRAND_QUICKNET_ENDPOINTS = (
    "https://api.drand.sh",
    "https://api2.drand.sh",
    "https://api3.drand.sh",
    "https://drand.cloudflare.com",
)
MAX_LEDGER_ENTRIES = 500
MAX_AMOUNT = 10**15
TOP_LIMIT = 10
SHOP_PAGE_SIZE = 8
REDEEM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REDEEM_CODE_GROUPS = 3
REDEEM_CODE_GROUP_SIZE = 4
CALENDAR_CLAIM_TYPES = {"daily", "weekly", "monthly", "annual"}
CALENDAR_CLAIM_PERIODS = {
    "daily": "UTC day",
    "weekly": "UTC week",
    "monthly": "UTC month",
    "annual": "UTC year",
}


class CodeRevealView(discord.ui.View):
    def __init__(self, owner_id: int, title: str, lines: list[str]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.title = title
        self.lines = lines

    @discord.ui.button(label="View privately", style=discord.ButtonStyle.primary)
    async def view_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These codes are not yours.", ephemeral=True)
            return

        content = self._content()
        await interaction.response.send_message(content, ephemeral=True)

    def _content(self) -> str:
        content = f"**{self.title}**\n" + "\n".join(self.lines)
        if len(content) <= 1900:
            return content
        truncated = content[:1850].rsplit("\n", 1)[0]
        return f"{truncated}\n...truncated. Run the command again for the full list."


class ShopBuyButton(discord.ui.Button):
    def __init__(self, cog: "Economy", guild_id: int, item_key: str, item: dict[str, Any]):
        self.cog = cog
        self.guild_id = guild_id
        self.item_key = item_key
        price = int(item.get("price", 0))
        label = f"Buy for {price:,} {CURRENCY_NAME}"
        stock = item.get("stock")
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success,
            custom_id=cog._shop_button_custom_id(guild_id, item_key),
            disabled=stock is not None and int(stock) <= 0,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.cog._shop_button_buy(interaction, self.guild_id, self.item_key)


class ShopItemView(discord.ui.View):
    def __init__(self, cog: "Economy", guild_id: int, item_key: str, item: dict[str, Any]):
        super().__init__(timeout=None)
        self.add_item(ShopBuyButton(cog, guild_id, item_key, item))


class Economy(commands.Cog):
    """Global economy with API access."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=830271946)
        self.config.register_global(
            balances={},
            ledger=[],
            next_tx=1,
            api_enabled=False,
            api_host=DEFAULT_API_HOST,
            api_port=DEFAULT_API_PORT,
            api_tokens={},
            daily_amount=DEFAULT_DAILY_AMOUNT,
            daily_cooldown=DEFAULT_DAILY_COOLDOWN,
            weekly_amount=DEFAULT_WEEKLY_AMOUNT,
            weekly_cooldown=DEFAULT_WEEKLY_COOLDOWN,
            monthly_amount=DEFAULT_MONTHLY_AMOUNT,
            monthly_cooldown=DEFAULT_MONTHLY_COOLDOWN,
            annual_amount=DEFAULT_ANNUAL_AMOUNT,
            annual_cooldown=DEFAULT_ANNUAL_COOLDOWN,
            work_amount=DEFAULT_WORK_AMOUNT,
            work_min=DEFAULT_WORK_MIN,
            work_max=DEFAULT_WORK_MAX,
            work_cooldown=DEFAULT_WORK_COOLDOWN,
            casino_enabled=True,
            casino_min_bet=DEFAULT_CASINO_MIN_BET,
            casino_max_bet=DEFAULT_CASINO_MAX_BET,
            casino_exclusions={},
            nightlife_players={},
            nightlife_disabled_guilds=[],
            claims={},
            shops={},
            inventories={},
            redeem_codes={},
            shop_channels={},
            shop_messages={},
            log_channels={},
            lwdmillions={
                "enabled": True,
                "ticket_price": DEFAULT_LWDMILLIONS_TICKET_PRICE,
                "seed_jackpot": DEFAULT_LWDMILLIONS_SEED_JACKPOT,
                "jackpot": DEFAULT_LWDMILLIONS_SEED_JACKPOT,
                "jackpot_contribution_percent": DEFAULT_LWDMILLIONS_JACKPOT_CONTRIBUTION_PERCENT,
                "draw_number": 1,
                "next_draw": 0,
                "beacon_round": 0,
                "proof_version": 2,
                "secret": "",
                "commitment": "",
                "next_ticket_id": 1,
                "tickets": [],
                "history": [],
                "announcement_channels": {},
            },
        )
        self._lock = asyncio.Lock()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._blackjack_players: set[int] = set()
        self._blackjack_views: dict[int, BlackjackView] = {}
        self._mines_players: set[int] = set()
        self._mines_views: dict[int, MinesView] = {}
        self._nightlife_invites: dict[int, NightlifeInviteView] = {}
        self._casino_exclusion_lock = asyncio.Lock()
        self._slot_players: set[int] = set()
        self._slot_render_semaphore = asyncio.Semaphore(2)
        self._lwdmillions_draw_lock = asyncio.Lock()
        self._lwdmillions_notification_tasks: set[asyncio.Task] = set()
        self._startup_task = self.bot.loop.create_task(self._start_api_if_enabled())
        self._lwdmillions_task = self.bot.loop.create_task(self._lwdmillions_draw_loop())

    def cog_unload(self):
        for view in set(self._nightlife_invites.values()):
            view.close()
            self.bot.loop.create_task(view.edit_message("Invitation cancelled because Economy was unloaded."))
        self._startup_task.cancel()
        self._lwdmillions_task.cancel()
        for task in list(self._lwdmillions_notification_tasks):
            task.cancel()
        for view in list(self._blackjack_views.values()):
            self.bot.loop.create_task(view.cancel_and_refund())
        for view in list(self._mines_views.values()):
            self.bot.loop.create_task(view.cancel_and_refund())
        self.bot.loop.create_task(self._stop_api())

    @commands.group(name="eco", aliases=["economy"], invoke_without_command=True)
    async def economy(self, ctx: commands.Context):
        """View economy commands."""
        await ctx.invoke(self.economy_balance)

    @economy.command(name="help", aliases=["commands"])
    async def economy_help(self, ctx: commands.Context):
        """Show economy help."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Economy Help",
            description=(
                "Global LWD$ balances with claims, transfers, LWDMillions, shop items, "
                "and API access."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="User Commands",
            value="\n".join(
                [
                    f"`{prefix}eco balance [member]` - show a balance",
                    f"`{prefix}eco pay <member> <amount>` - pay another member",
                    f"`{prefix}eco daily` - claim daily LWD$",
                    f"`{prefix}eco weekly` - claim weekly LWD$",
                    f"`{prefix}eco monthly` - claim monthly LWD$",
                    f"`{prefix}eco annual` - claim annual LWD$",
                    f"`{prefix}eco work` - work for random LWD$",
                    f"`{prefix}eco resets` - show UTC claim reset times",
                    f"`{prefix}eco top` - show the leaderboard",
                    f"`{prefix}eco shop` - view the server shop",
                    f"`{prefix}eco buy <item> [quantity]` - buy a shop item",
                    f"`{prefix}eco gift <member> <item> [quantity]` - gift an inventory item",
                    f"`{prefix}eco inventory [member]` - show inventory",
                    f"`{prefix}eco codes` - DM your unredeemed in-game item codes",
                    f"`{prefix}eco lwdmillions` - play the twice-weekly LWD$ lottery",
                    f"`{prefix}eco casino` - play virtual-currency casino games",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Nightlife",
            value=f"`{prefix}sex help` - escorts, supplies, and clinic",
            inline=False,
        )
        embed.add_field(
            name="Shortcuts",
            value="\n".join(
                [
                    f"`{prefix}balance [member]`, `{prefix}bal [member]`",
                    f"`{prefix}pay <member> <amount>`",
                    f"`{prefix}daily`, `{prefix}weekly`, `{prefix}monthly`, `{prefix}annual`, `{prefix}work`",
                    f"`{prefix}shop`, `{prefix}buy <item> [quantity]`",
                    f"`{prefix}inventory [member]`, `{prefix}inv [member]`",
                    f"`{prefix}gift <member> <item> [quantity]`",
                    f"`{prefix}codes`, `{prefix}ecotop`",
                    f"`{prefix}lwdmillions`, `{prefix}lwdm`, `{prefix}millions`",
                    f"`{prefix}casino` - casino games and payout rules",
                ]
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command(name="balance", aliases=["bal"])
    async def economy_balance_short(self, ctx: commands.Context, member: discord.Member | None = None):
        """Shortcut for eco balance."""
        await ctx.invoke(self.economy_balance, member=member)

    @commands.command(name="pay")
    async def economy_pay_short(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Shortcut for eco pay."""
        await ctx.invoke(self.economy_pay, member=member, amount=amount)

    @commands.command(name="daily")
    async def economy_daily_short(self, ctx: commands.Context):
        """Shortcut for eco daily."""
        await ctx.invoke(self.economy_daily)

    @commands.command(name="weekly")
    async def economy_weekly_short(self, ctx: commands.Context):
        """Shortcut for eco weekly."""
        await ctx.invoke(self.economy_weekly)

    @commands.command(name="monthly", aliases=["month"])
    async def economy_monthly_short(self, ctx: commands.Context):
        """Shortcut for eco monthly."""
        await ctx.invoke(self.economy_monthly)

    @commands.command(name="annual", aliases=["yearly", "year"])
    async def economy_annual_short(self, ctx: commands.Context):
        """Shortcut for eco annual."""
        await ctx.invoke(self.economy_annual)

    @commands.command(name="work")
    async def economy_work_short(self, ctx: commands.Context):
        """Shortcut for eco work."""
        await ctx.invoke(self.economy_work)

    @commands.command(name="shop")
    @commands.guild_only()
    async def economy_shop_short(self, ctx: commands.Context):
        """Shortcut for eco shop."""
        await ctx.invoke(self.economy_shop)

    @commands.command(name="buy")
    @commands.guild_only()
    async def economy_buy_short(self, ctx: commands.Context, item_name: str, quantity: int = 1):
        """Shortcut for eco buy."""
        await ctx.invoke(self.economy_buy, item_name=item_name, quantity=quantity)

    @commands.command(name="gift", aliases=["giveitem"])
    @commands.guild_only()
    async def economy_gift_short(
        self,
        ctx: commands.Context,
        member: discord.Member,
        item_name: str,
        quantity: int = 1,
    ):
        """Shortcut for eco gift."""
        await ctx.invoke(self.economy_gift, member=member, item_name=item_name, quantity=quantity)

    @commands.command(name="codes", aliases=["redeemcodes"])
    @commands.guild_only()
    async def economy_codes_short(self, ctx: commands.Context):
        """Shortcut for eco codes."""
        await ctx.invoke(self.economy_codes)

    @commands.command(name="inventory", aliases=["inv"])
    @commands.guild_only()
    async def economy_inventory_short(self, ctx: commands.Context, member: discord.Member | None = None):
        """Shortcut for eco inventory."""
        await ctx.invoke(self.economy_inventory, member=member)

    @commands.command(name="ecotop", aliases=["richtop"])
    async def economy_top_short(self, ctx: commands.Context):
        """Shortcut for eco top."""
        await ctx.invoke(self.economy_top)

    @commands.group(
        name="lwdmillions",
        aliases=["lwdm", "millions"],
        invoke_without_command=True,
    )
    async def economy_lwdmillions_short(self, ctx: commands.Context):
        """Shortcut for eco lwdmillions."""
        await ctx.invoke(self.economy_lwdmillions)

    @economy_lwdmillions_short.command(name="play", aliases=["pick", "buy"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_lwdmillions_play_short(self, ctx: commands.Context, *, numbers: str):
        """Shortcut for eco lwdmillions play."""
        await ctx.invoke(self.economy_lwdmillions_play, numbers=numbers)

    @economy_lwdmillions_short.command(name="quickpick", aliases=["quick", "random"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_lwdmillions_quickpick_short(
        self,
        ctx: commands.Context,
        lines: int = 1,
    ):
        """Shortcut for eco lwdmillions quickpick."""
        await ctx.invoke(self.economy_lwdmillions_quickpick, lines=lines)

    @economy_lwdmillions_short.command(name="tickets", aliases=["ticket", "mine"])
    async def economy_lwdmillions_tickets_short(self, ctx: commands.Context):
        """Shortcut for eco lwdmillions tickets."""
        await ctx.invoke(self.economy_lwdmillions_tickets)

    @economy_lwdmillions_short.command(name="prizes", aliases=["payouts", "rules"])
    async def economy_lwdmillions_prizes_short(self, ctx: commands.Context):
        """Shortcut for eco lwdmillions prizes."""
        await ctx.invoke(self.economy_lwdmillions_prizes)

    @economy_lwdmillions_short.command(name="results", aliases=["result", "history"])
    async def economy_lwdmillions_results_short(
        self,
        ctx: commands.Context,
        draw_number: int | None = None,
    ):
        """Shortcut for eco lwdmillions results."""
        await ctx.invoke(self.economy_lwdmillions_results, draw_number=draw_number)

    @economy_lwdmillions_short.command(name="verify", aliases=["proof", "fairness"])
    async def economy_lwdmillions_verify_short(
        self,
        ctx: commands.Context,
        draw_number: int | None = None,
    ):
        """Shortcut for eco lwdmillions verify."""
        await ctx.invoke(self.economy_lwdmillions_verify, draw_number=draw_number)

    @commands.group(name="casino", aliases=["gamble"], invoke_without_command=True)
    async def economy_casino_short(self, ctx: commands.Context):
        """Shortcut for eco casino."""
        await ctx.invoke(self.economy_casino)

    @economy_casino_short.command(name="coinflip", aliases=["coin", "flip"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_coinflip_short(
        self,
        ctx: commands.Context,
        wager: int,
        side: str = "heads",
    ):
        """Shortcut for eco casino coinflip."""
        await ctx.invoke(self.economy_casino_coinflip, wager=wager, side=side)

    @economy_casino_short.command(name="dice", aliases=["die"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_dice_short(self, ctx: commands.Context, wager: int, guess: int):
        """Shortcut for eco casino dice."""
        await ctx.invoke(self.economy_casino_dice, wager=wager, guess=guess)

    @economy_casino_short.command(name="highcard", aliases=["war"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_highcard_short(self, ctx: commands.Context, wager: int):
        """Shortcut for eco casino highcard."""
        await ctx.invoke(self.economy_casino_highcard, wager=wager)

    @economy_casino_short.command(name="roulette", aliases=["wheel"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_roulette_short(
        self,
        ctx: commands.Context,
        wager: int,
        choice: str,
    ):
        """Shortcut for eco casino roulette."""
        await ctx.invoke(self.economy_casino_roulette, wager=wager, choice=choice)

    @economy_casino_short.command(name="slots", aliases=["slot"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_slots_short(self, ctx: commands.Context, wager: int):
        """Shortcut for eco casino slots."""
        await ctx.invoke(self.economy_casino_slots, wager=wager)

    @economy_casino_short.command(name="mines", aliases=["mine"])
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def economy_casino_mines_short(
        self,
        ctx: commands.Context,
        wager: int,
        mine_count: int = DEFAULT_MINES,
    ):
        """Shortcut for eco casino mines."""
        await ctx.invoke(
            self.economy_casino_mines,
            wager=wager,
            mine_count=mine_count,
        )

    @economy_casino_short.command(name="blackjack", aliases=["bj"])
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def economy_casino_blackjack_short(self, ctx: commands.Context, wager: int):
        """Shortcut for eco casino blackjack."""
        await ctx.invoke(self.economy_casino_blackjack, wager=wager)

    @economy_casino_short.command(name="selfexclude", aliases=["self-exclude"])
    async def economy_casino_selfexclude_short(
        self,
        ctx: commands.Context,
        duration: str,
        confirmation: str = "",
    ):
        """Shortcut for eco casino selfexclude."""
        await ctx.invoke(
            self.economy_casino_selfexclude,
            duration=duration,
            confirmation=confirmation,
        )

    @economy_casino_short.command(name="exclusion", aliases=["excluded", "status"])
    async def economy_casino_exclusion_short(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ):
        """Shortcut for eco casino exclusion."""
        await ctx.invoke(self.economy_casino_exclusion, member=member)

    @economy_casino_short.command(name="exclude")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def economy_casino_exclude_short(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str = "No reason provided.",
    ):
        """Shortcut for eco casino exclude."""
        await ctx.invoke(
            self.economy_casino_exclude,
            member=member,
            duration=duration,
            reason=reason,
        )

    @economy_casino_short.command(name="unexclude")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def economy_casino_unexclude_short(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ):
        """Shortcut for eco casino unexclude."""
        await ctx.invoke(self.economy_casino_unexclude, member=member)

    @economy.command(name="balance", aliases=["bal"])
    async def economy_balance(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show your balance, or another member's balance."""
        member = member or ctx.author
        balances = await self.get_balance(member.id)
        embed = discord.Embed(title=f"{member.display_name}'s Balance", color=discord.Color.gold())
        embed.add_field(name=CURRENCY_FIELD_NAME, value=f"{balances[CASH]:,}", inline=True)
        await ctx.send(embed=embed)

    @economy.command(name="pay")
    async def economy_pay(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Pay LWD$ to another member."""
        if member.bot:
            await ctx.send("You cannot pay bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("You cannot pay yourself.")
            return
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        try:
            await self.transfer_balance(
                ctx.author.id,
                member.id,
                amount,
                actor_id=ctx.author.id,
                guild_id=ctx.guild.id if ctx.guild else None,
                reason=f"Discord pay command in guild {ctx.guild.id if ctx.guild else 'dm'}",
            )
        except EconomyError as error:
            await ctx.send(str(error))
            return

        await ctx.send(
            f"Paid {member.mention} {amount:,} {CURRENCY_NAME}.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @economy.command(name="daily")
    async def economy_daily(self, ctx: commands.Context):
        """Claim your daily LWD$."""
        await self._claim_reward(ctx, "daily")

    @economy.command(name="weekly")
    async def economy_weekly(self, ctx: commands.Context):
        """Claim your weekly LWD$."""
        await self._claim_reward(ctx, "weekly")

    @economy.command(name="monthly", aliases=["month"])
    async def economy_monthly(self, ctx: commands.Context):
        """Claim your monthly LWD$."""
        await self._claim_reward(ctx, "monthly")

    @economy.command(name="annual", aliases=["yearly", "year"])
    async def economy_annual(self, ctx: commands.Context):
        """Claim your annual LWD$."""
        await self._claim_reward(ctx, "annual")

    @economy.command(name="work")
    async def economy_work(self, ctx: commands.Context):
        """Work for some LWD$."""
        await self._claim_reward(ctx, "work")

    @economy.command(name="resets", aliases=["reset"])
    async def economy_resets(self, ctx: commands.Context):
        """Show current UTC time and upcoming claim reset times."""
        now = datetime.now(timezone.utc)
        embed = discord.Embed(title="Economy Claim Resets", color=discord.Color.gold())
        embed.description = f"Current UTC time: `{self._format_utc_datetime(now)}`"
        embed.set_footer(text="Discord timestamps display in each user's local timezone.")

        for claim_type in ("daily", "weekly", "monthly", "annual"):
            cooldown = int(await getattr(self.config, f"{claim_type}_cooldown")())
            if cooldown <= 0:
                value = "No cooldown."
            else:
                reset_ts = self._next_calendar_reset_timestamp(claim_type, now)
                reset_at = datetime.fromtimestamp(reset_ts, timezone.utc)
                value = (
                    f"`{self._format_utc_datetime(reset_at)}`\n"
                    f"<t:{reset_ts}:F> (<t:{reset_ts}:R>)"
                )
            embed.add_field(name=claim_type.title(), value=value, inline=False)

        work_cooldown = int(await self.config.work_cooldown())
        work_value = (
            "No cooldown."
            if work_cooldown <= 0
            else f"Rolling cooldown: {self._format_duration(work_cooldown)}"
        )
        embed.add_field(
            name="Work",
            value=work_value,
            inline=False,
        )
        await ctx.send(embed=embed)

    @economy.command(name="shop")
    @commands.guild_only()
    async def economy_shop(self, ctx: commands.Context):
        """Show this server's LWD$ shop."""
        pages = await self._shop_embeds(ctx.guild, prefix=ctx.clean_prefix, panel=False)
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            await menu(ctx, pages, DEFAULT_CONTROLS)

    @economy.command(name="buy")
    @commands.guild_only()
    async def economy_buy(self, ctx: commands.Context, item_name: str, quantity: int = 1):
        """Buy an item from this server's shop."""
        if quantity <= 0:
            await ctx.send("Quantity must be positive.")
            return
        if quantity > 1000:
            await ctx.send("Quantity cannot exceed 1,000.")
            return

        try:
            content, view = await self._complete_purchase(
                ctx.guild,
                ctx.author,
                item_name,
                quantity,
                reveal_codes_inline=False,
            )
        except EconomyError as error:
            await ctx.send(str(error))
            return

        await ctx.send(
            content,
            allowed_mentions=discord.AllowedMentions(roles=False),
            view=view,
        )
        await self._refresh_shop_panel(ctx.guild)

    @economy.command(name="gift", aliases=["giveitem"])
    @commands.guild_only()
    async def economy_gift(
        self,
        ctx: commands.Context,
        member: discord.Member,
        item_name: str,
        quantity: int = 1,
    ):
        """Gift an allowed inventory item to another member."""
        if member.bot:
            await ctx.send("You cannot gift items to bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("You cannot gift items to yourself.")
            return
        if quantity <= 0:
            await ctx.send("Quantity must be positive.")
            return
        if quantity > 1000:
            await ctx.send("Quantity cannot exceed 1,000.")
            return

        try:
            result = await self.gift_item(ctx.guild, ctx.author, member, item_name, quantity)
        except EconomyError as error:
            await ctx.send(str(error))
            return

        code_note = ""
        if result["codes_transferred"]:
            code_note = f" Transferred {result['codes_transferred']:,} redeem code(s) too; they can run `{ctx.clean_prefix}eco codes`."
        await ctx.send(
            f"Gifted {quantity:,}x **{result['item_name']}** to {member.mention}.{code_note}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @economy.command(name="codes", aliases=["redeemcodes"])
    @commands.guild_only()
    async def economy_codes(self, ctx: commands.Context):
        """DM your unredeemed in-game item codes for this server."""
        codes = await self._get_unredeemed_codes(ctx.guild.id, ctx.author.id)
        if not codes:
            await ctx.send("You do not have any unredeemed codes in this server.")
            return

        lines = []
        for code, entry in codes:
            lines.append(
                f"`{code}` - **{entry.get('item_name', 'Unknown item')}** x{int(entry.get('quantity', 1)):,} "
                f"(created <t:{int(entry.get('created_at', 0))}:R>)"
            )
        message = f"Unredeemed codes for **{ctx.guild.name}**:\n" + "\n".join(lines)
        try:
            await ctx.author.send(message)
        except discord.HTTPException:
            await ctx.send(
                "I could not DM your codes. Use the button below to view them privately.",
                view=CodeRevealView(ctx.author.id, f"Unredeemed codes for {ctx.guild.name}", lines),
            )
            return
        await ctx.send("I sent your unredeemed codes in DMs.")

    @economy.command(name="inventory", aliases=["inv"])
    @commands.guild_only()
    async def economy_inventory(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show your inventory, or another member's inventory."""
        member = member or ctx.author
        inventory = await self._get_inventory(ctx.guild.id, member.id)
        entries = [(name, int(quantity)) for name, quantity in inventory.items() if int(quantity) > 0]
        if not entries:
            await ctx.send(f"{member.display_name} has no items.")
            return

        lines = [f"**{name}** x{quantity:,}" for name, quantity in sorted(entries)]
        embed = discord.Embed(
            title=f"{member.display_name}'s Inventory",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    @economy.command(name="top", aliases=["leaderboard"])
    async def economy_top(self, ctx: commands.Context):
        """Show the LWD$ leaderboard."""
        balances = await self.config.balances()
        entries = sorted(
            (
                (int(user_id), int(account.get(CASH, 0)))
                for user_id, account in balances.items()
                if int(account.get(CASH, 0)) > 0
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:TOP_LIMIT]
        if not entries:
            await ctx.send("There are no economy balances yet.")
            return

        lines = []
        for rank, (user_id, amount) in enumerate(entries, start=1):
            lines.append(f"{rank}. **{await self._display_user(ctx.guild, user_id)}** - {amount:,} {CURRENCY_NAME}")
        embed = discord.Embed(
            title="LWD$ Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    @economy.group(
        name="lwdmillions",
        aliases=["lwdm", "millions"],
        invoke_without_command=True,
    )
    async def economy_lwdmillions(self, ctx: commands.Context):
        """Play the scheduled LWDMillions lottery with virtual LWD$."""
        state = await self._lwdmillions_state_snapshot()
        next_draw = int(state["next_draw"])
        tickets = [ticket for ticket in state["tickets"] if isinstance(ticket, dict)]
        players = {int(ticket.get("user_id", 0)) for ticket in tickets}
        if not self._lwdmillions_state_commitment_valid(state):
            status = "Paused — draw proof needs repair"
        else:
            status = "Open" if state["enabled"] else "Ticket sales closed"
        prefix = ctx.clean_prefix

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
                f"`{prefix}lwdmillions play 1 2 3 4 5 | 1 2`\n"
                f"`{prefix}lwdmillions quickpick [lines]`\n"
                f"`{prefix}lwdmillions tickets`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Information",
            value=(
                f"`{prefix}lwdmillions prizes` — prize table\n"
                f"`{prefix}lwdmillions results [draw]` — recent results\n"
                f"`{prefix}lwdmillions verify [draw]` — reproduce a completed draw"
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
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @economy_lwdmillions.command(name="play", aliases=["pick", "buy"])
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

    @economy_lwdmillions.command(name="quickpick", aliases=["quick", "random"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_lwdmillions_quickpick(
        self,
        ctx: commands.Context,
        lines: int = 1,
    ):
        """Buy one or more securely generated Quick Pick lines."""
        if lines < 1 or lines > MAX_LWDMILLIONS_LINES_PER_PLAYER:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(
                f"Choose from 1 to {MAX_LWDMILLIONS_LINES_PER_PLAYER} Quick Pick lines."
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

    @economy_lwdmillions.command(name="tickets", aliases=["ticket", "mine"])
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
        embed.set_footer(text=f"{len(tickets):,}/{MAX_LWDMILLIONS_LINES_PER_PLAYER} lines")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @economy_lwdmillions.command(name="prizes", aliases=["payouts", "rules"])
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
        await ctx.send(embed=embed)

    @economy_lwdmillions.command(name="results", aliases=["result", "history"])
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
        await ctx.send(
            embed=self._lwdmillions_result_embed(record, ctx.clean_prefix),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @economy_lwdmillions.command(name="verify", aliases=["proof", "fairness"])
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
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @economy.group(name="casino", aliases=["gamble"], invoke_without_command=True)
    async def economy_casino(self, ctx: commands.Context):
        """Play casino games with virtual LWD$."""
        enabled = bool(await self.config.casino_enabled())
        minimum = int(await self.config.casino_min_bet())
        maximum = int(await self.config.casino_max_bet())
        active_exclusions = await self._active_casino_exclusions(ctx.author.id)
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="LWD$ Casino",
            description=(
                "Play using virtual LWD$ only. Each wager is taken from your global balance.\n"
                f"Status: **{'Open' if enabled else 'Closed'}** | Bets: **{minimum:,}-{maximum:,} {CURRENCY_NAME}**"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Coin Flip",
            value=(
                f"`{prefix}casino coinflip <bet> [heads|tails]`\n"
                "Correct call returns 1.95x your wager."
            ),
            inline=False,
        )
        embed.add_field(
            name="Dice",
            value=(
                f"`{prefix}casino dice <bet> <1-6>`\n"
                "Correct guess returns 5.7x your wager."
            ),
            inline=False,
        )
        embed.add_field(
            name="High Card",
            value=(
                f"`{prefix}casino highcard <bet>`\n"
                "Draw against the dealer. Higher rank returns 2x; equal ranks push."
            ),
            inline=False,
        )
        embed.add_field(
            name="European Roulette",
            value=(
                f"`{prefix}casino roulette <bet> <choice>`\n"
                "Choose 0-36, red/black, odd/even, low/high, or 1st12/2nd12/3rd12. "
                "Returns 36x, 2x, or 3x respectively."
            ),
            inline=False,
        )
        embed.add_field(
            name="Slots",
            value=(
                f"`{prefix}casino slots <bet>`\n"
                "Exact triples return 4x-80x according to the machine; "
                "one cherry on an otherwise unmatched spin returns half."
            ),
            inline=False,
        )
        embed.add_field(
            name="Mines",
            value=(
                f"`{prefix}casino mines <bet> [mines]`\n"
                f"Reveal safe tiles and cash out before hitting a mine. Choose "
                f"{MINES_MIN_COUNT}-{MINES_MAX_COUNT} mines; the default is {DEFAULT_MINES}. "
                f"Returns are capped at {MINES_MAX_PAYOUT_MULTIPLIER}x."
            ),
            inline=False,
        )
        embed.add_field(
            name="Blackjack",
            value=(
                f"`{prefix}casino blackjack <bet>`\n"
                "Interactive blackjack with hit, stand, double, split, surrender, and insurance."
            ),
            inline=False,
        )
        protection_lines = [
            f"`{prefix}casino selfexclude <duration|permanent> confirm`",
            f"`{prefix}casino exclusion` — check your exclusion status.",
            "Durations use `m`, `h`, `d`, `w`, or `y` (for example `30d` or `1y`). "
            "Players cannot shorten or remove their own exclusion; a server admin can lift it.",
        ]
        if active_exclusions:
            protection_lines.extend(
                ["", "**Your current exclusions:**", *self._casino_exclusion_status_lines(active_exclusions)]
            )
        embed.add_field(
            name="Player Protection",
            value="\n".join(protection_lines),
            inline=False,
        )
        embed.add_field(
            name="Administrator Controls",
            value=(
                f"`{prefix}casino exclude <member> <duration|permanent> [reason]`\n"
                f"`{prefix}casino unexclude <member>` — remove admin and self-exclusions\n"
                f"`{prefix}casino exclusion [member]`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @economy_casino.command(name="selfexclude", aliases=["self-exclude"])
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

    @economy_casino.command(name="exclusion", aliases=["excluded", "status"])
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

    @economy_casino.command(name="exclude")
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

    @economy_casino.command(name="unexclude")
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

    @economy_casino.command(name="coinflip", aliases=["coin", "flip"])
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
            await self._validate_casino_wager(wager, ctx.author.id)
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return

        result = "heads" if secrets.randbelow(2) == 0 else "tails"
        payout = wager * CASINO_COINFLIP_PAYOUT_PERCENT // 100 if selected == result else 0
        await self._finish_casino_command(ctx, "coinflip", wager, payout, f"called {selected}; landed {result}")

    @economy_casino.command(name="dice", aliases=["die"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_dice(self, ctx: commands.Context, wager: int, guess: int):
        """Guess a six-sided die roll."""
        if guess < 1 or guess > 6:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("Your dice guess must be from 1 to 6.")
            return
        try:
            await self._validate_casino_wager(wager, ctx.author.id)
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return

        result = secrets.randbelow(6) + 1
        payout = wager * CASINO_DICE_PAYOUT_PERCENT // 100 if guess == result else 0
        await self._finish_casino_command(ctx, "dice", wager, payout, f"guessed {guess}; rolled {result}")

    @economy_casino.command(name="highcard", aliases=["war"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def economy_casino_highcard(self, ctx: commands.Context, wager: int):
        """Draw a high card against the dealer."""
        try:
            await self._validate_casino_wager(wager, ctx.author.id)
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

    @economy_casino.command(name="roulette", aliases=["wheel"])
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
            await self._validate_casino_wager(wager, ctx.author.id)
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

    @economy_casino.command(name="slots", aliases=["slot"])
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
                await self._validate_casino_wager(wager, ctx.author.id)
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

    @economy_casino.command(name="mines", aliases=["mine"])
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def economy_casino_mines(
        self,
        ctx: commands.Context,
        wager: int,
        mine_count: int = DEFAULT_MINES,
    ):
        """Play an interactive game of Mines."""
        await self._run_mines_round(ctx, wager, mine_count)

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
                await self._validate_casino_wager(wager, ctx.author.id)
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

    @economy_casino.command(name="blackjack", aliases=["bj"])
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def economy_casino_blackjack(self, ctx: commands.Context, wager: int):
        """Play an interactive hand of blackjack."""
        await self._run_blackjack_round(ctx, wager)

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
                await self._validate_casino_wager(wager, ctx.author.id)
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
                view = BlackjackView(self, ctx, wager, currency_name=CURRENCY_NAME)
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
                    await ctx.send("That blackjack hand hit an unexpected error and will time out safely.")
            return True
        finally:
            if view is None:
                current_view = self._blackjack_views.get(ctx.author.id)
                if current_view is None or current_view.phase == "ended":
                    self._blackjack_players.discard(ctx.author.id)
            elif self._blackjack_views.get(ctx.author.id) is view:
                self._blackjack_players.discard(ctx.author.id)
                self._blackjack_views.pop(ctx.author.id, None)

    async def _nightlife_allowed(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            await ctx.send("Nightlife is only available in server channels.")
            return False
        if ctx.guild.id in await self.config.nightlife_disabled_guilds():
            await ctx.send("Nightlife is disabled in this server.")
            return False
        return True

    async def _nightlife_run(self, ctx: commands.Context, action: str, **kwargs):
        if not await self._nightlife_allowed(ctx):
            return
        try:
            async with self._lock:
                if action in ("encounter", "use") and ctx.author.id in self._nightlife_invites:
                    raise ValueError("Finish or cancel your pending invitation first.")
                async with self.config.nightlife_players() as players:
                    async with self.config.balances() as balances:
                        account = self._account_from_mapping(balances, ctx.author.id)
                        player, remaining, message, cost = nightlife.apply_action(
                            players.get(str(ctx.author.id)), account[CASH], action,
                            now=int(time.time()), **kwargs,
                        )
                        players[str(ctx.author.id)] = player
                        if cost:
                            account[CASH] = remaining
                            balances[str(ctx.author.id)] = account
                if cost:
                    await self._append_ledger(
                        "nightlife", from_user_id=ctx.author.id, to_user_id=None,
                        amount=cost, actor_id=ctx.author.id, guild_id=ctx.guild.id,
                        reason=f"Nightlife {action}", log_to_channel=False,
                    )
        except ValueError as error:
            await ctx.send(str(error).replace("`sex", f"`{ctx.clean_prefix}sex"))
            return
        if cost:
            message += f"\nSpent {cost:,} {CURRENCY_NAME}. Balance: {remaining:,} {CURRENCY_NAME}."
        message = message.replace("`sex", f"`{ctx.clean_prefix}sex")
        await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())

    async def _invite_nightlife_partner(
        self, ctx: commands.Context, partner: discord.Member, venue: str = "motel", protected: bool = True,
    ):
        if not await self._nightlife_allowed(ctx):
            return
        if partner.id == ctx.author.id or partner.bot:
            await ctx.send("Choose another player, not yourself or a bot.")
            return
        if partner.guild.id != ctx.guild.id:
            await ctx.send("Choose someone in this server.")
            return
        if not ctx.channel.permissions_for(partner).view_channel:
            await ctx.send("That player can't see this channel.")
            return
        view = None
        try:
            async with self._lock:
                if any(user_id in self._nightlife_invites for user_id in (ctx.author.id, partner.id)):
                    raise ValueError("One of you already has a pending invitation.")
                players = await self.config.nightlife_players()
                balances = await self.config.balances()
                _, _, cost = nightlife.prepare_player_encounter(
                    players.get(str(ctx.author.id)), players.get(str(partner.id)),
                    self._account_from_mapping(balances, ctx.author.id)[CASH],
                    now=int(time.time()), venue=venue, protected=protected,
                )
                view = NightlifeInviteView(self, ctx, partner, venue, protected)
                for user_id in view.participant_ids:
                    self._nightlife_invites[user_id] = view
            view.message = await ctx.send(
                f"{partner.mention}, {ctx.author.mention} wants to have sex with you at the {venue}.\n"
                f"{'With a condom' if protected else 'Without a condom'}. {ctx.author.mention} pays {cost:,} LWD$"
                f"{' and supplies the condom' if protected else ''}.\nAccept or decline within 90 seconds.",
                view=view, allowed_mentions=discord.AllowedMentions(users=[partner]),
            )
        except ValueError as error:
            if view is not None:
                view.close()
            await ctx.send(str(error).replace("`sex", f"`{ctx.clean_prefix}sex"))
        except BaseException:
            if view is not None:
                view.close()
            raise

    async def _accept_nightlife_invite(self, view: NightlifeInviteView) -> str:
        ctx = view.ctx
        first_id, second_id = view.participant_ids
        async with self._lock:
            if (view.closed or time.monotonic() >= view.deadline
                    or self.bot.get_cog("Economy") is not self
                    or any(self._nightlife_invites.get(user_id) is not view for user_id in view.participant_ids)):
                raise ValueError("This invitation has ended.")
            if ctx.guild.id in await self.config.nightlife_disabled_guilds():
                raise ValueError("Nightlife is disabled in this server.")
            for user_id in view.participant_ids:
                member = ctx.guild.get_member(user_id)
                if member is None or member.bot or not ctx.channel.permissions_for(member).view_channel:
                    raise ValueError("Both players must still be in this server and able to see this channel.")
            async with self.config.nightlife_players() as players:
                async with self.config.balances() as balances:
                    account = self._account_from_mapping(balances, first_id)
                    first, second, remaining, cost, protection, first_after, second_after = nightlife.apply_player_encounter(
                        players.get(str(first_id)), players.get(str(second_id)), account[CASH],
                        now=int(time.time()), venue=view.venue, protected=view.protected,
                    )
                    players[str(first_id)] = first
                    players[str(second_id)] = second
                    account[CASH] = remaining
                    balances[str(first_id)] = account
            await self._append_ledger(
                "nightlife", from_user_id=first_id, to_user_id=None, amount=cost,
                actor_id=first_id, guild_id=ctx.guild.id, reason="Nightlife player encounter", log_to_channel=False,
            )
        lines = [f"<@{first_id}> and <@{second_id}> had sex at the {view.venue}.", protection]
        for user_id, aftermath in ((first_id, first_after), (second_id, second_after)):
            if aftermath:
                lines.append(f"<@{user_id}>: {aftermath}")
        lines.append(f"<@{first_id}> spent {cost:,} LWD$. Balance: {remaining:,} LWD$.")
        return "\n".join(lines).replace("`sex", f"`{ctx.clean_prefix}sex")

    @commands.group(name="sex", invoke_without_command=True, ignore_extra=False)
    @commands.guild_only()
    async def nightlife_sex(self, ctx: commands.Context, member: discord.Member | None = None):
        """Have sex. Use sex help for commands."""
        if member is not None:
            await self._invite_nightlife_partner(ctx, member)
        else:
            await self._nightlife_run(ctx, "encounter")

    @nightlife_sex.command(name="help")
    async def nightlife_help(self, ctx: commands.Context):
        """Show Nightlife commands."""
        if not await self._nightlife_allowed(ctx):
            return
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Nightlife",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Commands", value="\n".join([
            f"`{prefix}sex` — have sex",
            f"`{prefix}sex @member` — invite a player",
            f"`{prefix}sex hookers` — browse escorts and venues",
            f"`{prefix}sex encounter <escort|@member> [venue] [true|false]` — book; condom on/off",
            f"`{prefix}sex status` — your profile",
        ]), inline=False)
        embed.add_field(name="Supplies and health", value="\n".join([
            f"`{prefix}sex shop` — browse supplies",
            f"`{prefix}sex buy <item> [quantity]` — buy supplies",
            f"`{prefix}sex use <item>` — use an item",
            f"`{prefix}sex clinic` — clinic prices",
            f"`{prefix}sex clinic test` — get tested",
            f"`{prefix}sex clinic cure` — get treated",
            f"`{prefix}sex clinic pay` — pay hospital bills",
        ]), inline=False)
        await ctx.send(embed=embed)

    @nightlife_sex.command(name="escorts", aliases=["hookers", "npcs"])
    async def nightlife_escorts(self, ctx: commands.Context):
        """Browse escorts and venues."""
        if not await self._nightlife_allowed(ctx):
            return
        lines = ["**Escorts**"]
        for key, escort in nightlife.ESCORTS.items():
            lines.append(f"`{key}` — {escort['name']}: {escort['price']:,} LWD$")
        lines.append("\n**Venues** (added to the escort fee)")
        for key, venue in nightlife.VENUES.items():
            lines.append(f"`{key}` — {venue['price']:,} LWD$")
        await ctx.send("\n".join(lines))

    @nightlife_sex.command(name="shop")
    async def nightlife_shop(self, ctx: commands.Context):
        """Browse supplies."""
        if not await self._nightlife_allowed(ctx):
            return
        lines = ["**Nightlife shop**"]
        for key, item in nightlife.ITEMS.items():
            lines.append(f"`{key}` — {item['price']:,} LWD$: {item['description']}")
        lines.append(f"`{ctx.clean_prefix}sex buy <item> [quantity]`")
        await ctx.send("\n".join(lines))

    @nightlife_sex.command(name="buy")
    async def nightlife_buy(self, ctx: commands.Context, item: str, quantity: int = 1):
        """Buy Nightlife items with LWD$."""
        await self._nightlife_run(ctx, "buy", item=item, quantity=quantity)

    @nightlife_sex.command(name="use")
    async def nightlife_use(self, ctx: commands.Context, item: str):
        """Use an item."""
        await self._nightlife_run(ctx, "use", item=item)

    @nightlife_sex.command(name="encounter", aliases=["book"])
    async def nightlife_encounter(
        self, ctx: commands.Context, escort: str = "alex", venue: str = "motel", protected: bool = True,
    ):
        """Book an escort or invite a player. Set protected to true or false to choose condom use."""
        if escort.startswith("<@"):
            if ctx.guild is None:
                await self._nightlife_allowed(ctx)
                return
            partner = await commands.MemberConverter().convert(ctx, escort)
            await self._invite_nightlife_partner(ctx, partner, venue.lower(), protected)
        else:
            await self._nightlife_run(ctx, "encounter", escort=escort.lower(), venue=venue.lower(), protected=protected)

    @nightlife_sex.command(name="status", aliases=["profile", "inventory", "inv"])
    async def nightlife_status(self, ctx: commands.Context):
        """View your profile and inventory."""
        if not await self._nightlife_allowed(ctx):
            return
        async with self._lock:
            players = await self.config.nightlife_players()
            player = nightlife.refreshed_profile(players.get(str(ctx.author.id)), int(time.time()))
        diagnosed = [nightlife.DISEASES[key]["name"] for key, value in player["infections"].items() if value["diagnosed"]]
        last = player["last_encounter"]
        remaining = max(0, nightlife.ENCOUNTER_COOLDOWN - (int(time.time()) - last)) if last is not None else 0
        lines = [
            f"**Your Nightlife profile — {nightlife.title_for(player['encounters'])}**",
            f"Stamina: {player['stamina']}/100 • Encounter cooldown: {remaining}s",
            f"Encounters: {player['encounters']:,} • Total satisfaction: {player['satisfaction']:,}",
            f"Total spent: {player['spent']:,} LWD$",
            "Inventory: " + (", ".join(f"{key} ×{value}" for key, value in player["inventory"].items() if value) or "empty"),
            "Next-encounter boosts: " + (", ".join(player["boosts"]) or "none"),
            "Diagnoses: " + (", ".join(diagnosed) or "none"),
            f"Hospital bills: {player['medical_bill']:,} LWD$ • Hospital visits: {player['hospital_visits']:,}",
        ]
        if player["recovery_until"]:
            lines.append(f"{player['recovery_reason']} until <t:{player['recovery_until']}:R>.")
        await ctx.send("\n".join(lines))

    @nightlife_sex.group(name="clinic", invoke_without_command=True)
    async def nightlife_clinic(self, ctx: commands.Context):
        """View clinic prices."""
        if not await self._nightlife_allowed(ctx):
            return
        lines = [f"**Nightlife clinic** — a test costs {nightlife.TEST_PRICE} LWD$."]
        for disease in nightlife.DISEASES.values():
            lines.append(f"{disease['name']}: {disease['treatment']:,} LWD$ to treat")
        lines.append(f"`{ctx.clean_prefix}sex clinic test` — get tested\n"
                     f"`{ctx.clean_prefix}sex clinic cure` — get treated\n"
                     f"`{ctx.clean_prefix}sex clinic pay` — pay hospital bills")
        await ctx.send("\n".join(lines))

    @nightlife_clinic.command(name="test")
    async def nightlife_test(self, ctx: commands.Context):
        """Get tested."""
        await self._nightlife_run(ctx, "test")

    @nightlife_clinic.command(name="cure", aliases=["treat"])
    async def nightlife_cure(self, ctx: commands.Context):
        """Get treated."""
        await self._nightlife_run(ctx, "cure")

    @nightlife_clinic.command(name="pay")
    async def nightlife_pay_bill(self, ctx: commands.Context):
        """Pay hospital bills."""
        await self._nightlife_run(ctx, "pay")

    @economy.group(name="admin", invoke_without_command=True)
    @commands.is_owner()
    async def economy_admin(self, ctx: commands.Context):
        """Owner-only economy management."""
        await ctx.invoke(self.economy_admin_help)

    @economy_admin.command(name="sex")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_sex(self, ctx: commands.Context, enabled: bool):
        """Enable or disable Nightlife in this server."""
        async with self._lock:
            async with self.config.nightlife_disabled_guilds() as disabled:
                if enabled and ctx.guild.id in disabled:
                    disabled.remove(ctx.guild.id)
                elif not enabled and ctx.guild.id not in disabled:
                    disabled.append(ctx.guild.id)
        await ctx.send(f"Nightlife is {'enabled' if enabled else 'disabled'} in this server.")

    @economy_admin.command(name="help", aliases=["commands"])
    @commands.is_owner()
    async def economy_admin_help(self, ctx: commands.Context):
        """Show owner economy help."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Economy Admin Help",
            description=(
                "Owner-only balance, claim, casino, LWDMillions, shop, log, and API commands."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Balances",
            value="\n".join(
                [
                    f"`{prefix}eco admin add <member> <amount> [reason]`",
                    f"`{prefix}eco admin remove <member> <amount> [reason]`",
                    f"`{prefix}eco admin set <member> <amount> [reason]`",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Claims And Logs",
            value="\n".join(
                [
                    f"`{prefix}eco admin claim show`",
                    f"`{prefix}eco admin claim daily <amount> [0 disables]`",
                    f"`{prefix}eco admin claim weekly <amount> [0 disables]`",
                    f"`{prefix}eco admin claim monthly <amount> [0 disables]`",
                    f"`{prefix}eco admin claim annual <amount> [0 disables]`",
                    f"`{prefix}eco admin claim work <amount> [cooldown_seconds]`",
                    f"`{prefix}eco admin claim workrange <min> <max> [cooldown_seconds]`",
                    f"`{prefix}eco admin logchannel [channel]`",
                    f"`{prefix}eco admin clearlog`",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Casino",
            value="\n".join(
                [
                    f"`{prefix}eco admin casino show`",
                    f"`{prefix}eco admin casino toggle`",
                    f"`{prefix}eco admin casino limits <minimum> <maximum>`",
                    f"`{prefix}casino exclude <member> <duration|permanent> [reason]`",
                    f"`{prefix}casino unexclude <member>`",
                    f"`{prefix}casino exclusion [member]`",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Nightlife",
            value=f"`{prefix}eco admin sex <true|false>` - enable or disable in this server",
            inline=False,
        )
        embed.add_field(
            name="LWDMillions",
            value="\n".join(
                [
                    f"`{prefix}eco admin lwdmillions show`",
                    f"`{prefix}eco admin lwdmillions toggle`",
                    f"`{prefix}eco admin lwdmillions ticketprice <amount>`",
                    f"`{prefix}eco admin lwdmillions contribution <0-100>`",
                    f"`{prefix}eco admin lwdmillions seed <amount>`",
                    f"`{prefix}eco admin lwdmillions jackpot <amount>`",
                    f"`{prefix}eco admin lwdmillions channel [channel]`",
                    f"`{prefix}eco admin lwdmillions clearchannel`",
                    f"`{prefix}eco admin lwdmillions draw confirm`",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Shop",
            value="\n".join(
                [
                    f"`{prefix}eco admin shop add <name> <price> [stock] [description]`",
                    f"`{prefix}eco admin shop remove <name>`",
                    f"`{prefix}eco admin shop role <name> [role]`",
                    f"`{prefix}eco admin shop code <name> [true|false]`",
                    f"`{prefix}eco admin shop stock <name> <stock>`",
                    f"`{prefix}eco admin shop limit <name> <limit>`",
                    f"`{prefix}eco admin shop giftable <name> [true|false]`",
                    f"`{prefix}eco admin shop channel [channel]`",
                    f"`{prefix}eco admin shop post [channel]`",
                    f"`{prefix}eco admin shop clearchannel`",
                    "`stock -1` and `limit -1` mean unlimited.",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="API",
            value="\n".join(
                [
                    f"`{prefix}eco api status`",
                    f"`{prefix}eco api start [host] [port]`",
                    f"`{prefix}eco api stop`",
                    f"`{prefix}eco api token list`",
                    f"`{prefix}eco api token create <name>`",
                    f"`{prefix}eco api token revoke <name>`",
                    f"`{prefix}eco api token revokeall confirm`",
                ]
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @economy_admin.command(name="add")
    @commands.is_owner()
    async def economy_admin_add(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "owner adjustment",
    ):
        """Add LWD$ to a user."""
        await self._owner_adjust(ctx, member, amount, "add", reason)

    @economy_admin.command(name="remove")
    @commands.is_owner()
    async def economy_admin_remove(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "owner adjustment",
    ):
        """Remove LWD$ from a user."""
        await self._owner_adjust(ctx, member, amount, "remove", reason)

    @economy_admin.command(name="set")
    @commands.is_owner()
    async def economy_admin_set(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "owner set",
    ):
        """Set a user's LWD$ balance."""
        await self._owner_adjust(ctx, member, amount, "set", reason)

    @economy_admin.group(name="claim", invoke_without_command=True)
    @commands.is_owner()
    async def economy_admin_claim(self, ctx: commands.Context):
        """Manage claim rewards."""
        await ctx.invoke(self.economy_admin_claim_show)

    @economy_admin_claim.command(name="show")
    @commands.is_owner()
    async def economy_admin_claim_show(self, ctx: commands.Context):
        """Show claim reward settings."""
        daily_amount = await self.config.daily_amount()
        daily_cooldown = await self.config.daily_cooldown()
        work_cooldown = await self.config.work_cooldown()
        await ctx.send(
            "Claim rewards:\n"
            f"Daily: {daily_amount:,} {CURRENCY_NAME} {self._format_claim_interval('daily', daily_cooldown)}\n"
            f"Weekly: {await self.config.weekly_amount():,} {CURRENCY_NAME} {self._format_claim_interval('weekly', await self.config.weekly_cooldown())}\n"
            f"Monthly: {await self.config.monthly_amount():,} {CURRENCY_NAME} {self._format_claim_interval('monthly', await self.config.monthly_cooldown())}\n"
            f"Annual: {await self.config.annual_amount():,} {CURRENCY_NAME} {self._format_claim_interval('annual', await self.config.annual_cooldown())}\n"
            f"Work: {await self.config.work_min():,}-{await self.config.work_max():,} {CURRENCY_NAME} every {self._format_duration(work_cooldown)}"
        )

    @economy_admin_claim.command(name="daily")
    @commands.is_owner()
    async def economy_admin_claim_daily(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_DAILY_COOLDOWN):
        """Set daily amount. Positive cooldowns reset each UTC day; 0 disables."""
        await self._set_claim_settings(ctx, "daily", amount, cooldown_seconds)

    @economy_admin_claim.command(name="weekly")
    @commands.is_owner()
    async def economy_admin_claim_weekly(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_WEEKLY_COOLDOWN):
        """Set weekly amount. Positive cooldowns reset each UTC week; 0 disables."""
        await self._set_claim_settings(ctx, "weekly", amount, cooldown_seconds)

    @economy_admin_claim.command(name="monthly")
    @commands.is_owner()
    async def economy_admin_claim_monthly(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_MONTHLY_COOLDOWN):
        """Set monthly amount. Positive cooldowns reset each UTC month; 0 disables."""
        await self._set_claim_settings(ctx, "monthly", amount, cooldown_seconds)

    @economy_admin_claim.command(name="annual", aliases=["yearly"])
    @commands.is_owner()
    async def economy_admin_claim_annual(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_ANNUAL_COOLDOWN):
        """Set annual amount. Positive cooldowns reset each UTC year; 0 disables."""
        await self._set_claim_settings(ctx, "annual", amount, cooldown_seconds)

    @economy_admin_claim.command(name="work")
    @commands.is_owner()
    async def economy_admin_claim_work(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_WORK_COOLDOWN):
        """Set fixed work amount and cooldown."""
        await self._set_work_range(ctx, amount, amount, cooldown_seconds)

    @economy_admin_claim.command(name="workrange")
    @commands.is_owner()
    async def economy_admin_claim_workrange(
        self,
        ctx: commands.Context,
        minimum: int,
        maximum: int,
        cooldown_seconds: int = DEFAULT_WORK_COOLDOWN,
    ):
        """Set random work range and cooldown."""
        await self._set_work_range(ctx, minimum, maximum, cooldown_seconds)

    @economy_admin.command(name="logchannel")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_logchannel(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """Set the economy log channel for this server."""
        channel = channel or ctx.channel
        async with self.config.log_channels() as channels:
            channels[str(ctx.guild.id)] = channel.id
        await ctx.send(f"Economy logs will be sent to {channel.mention}.")

    @economy_admin.command(name="clearlog")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_clearlog(self, ctx: commands.Context):
        """Disable economy logs for this server."""
        async with self.config.log_channels() as channels:
            channels.pop(str(ctx.guild.id), None)
        await ctx.send("Economy logs disabled for this server.")

    @economy_admin.group(name="casino", invoke_without_command=True)
    @commands.is_owner()
    async def economy_admin_casino(self, ctx: commands.Context):
        """Manage casino availability and bet limits."""
        await ctx.invoke(self.economy_admin_casino_show)

    @economy_admin_casino.command(name="show")
    @commands.is_owner()
    async def economy_admin_casino_show(self, ctx: commands.Context):
        """Show casino settings."""
        enabled = bool(await self.config.casino_enabled())
        minimum = int(await self.config.casino_min_bet())
        maximum = int(await self.config.casino_max_bet())
        await ctx.send(
            f"Casino: {'enabled' if enabled else 'disabled'}\n"
            f"Bet limits: {minimum:,}-{maximum:,} {CURRENCY_NAME}"
        )

    @economy_admin_casino.command(name="toggle")
    @commands.is_owner()
    async def economy_admin_casino_toggle(self, ctx: commands.Context):
        """Enable or disable casino games."""
        enabled = not bool(await self.config.casino_enabled())
        await self.config.casino_enabled.set(enabled)
        await ctx.send(f"Casino games are now {'enabled' if enabled else 'disabled'}.")

    @economy_admin_casino.command(name="limits")
    @commands.is_owner()
    async def economy_admin_casino_limits(self, ctx: commands.Context, minimum: int, maximum: int):
        """Set the global minimum and maximum casino wager."""
        if minimum <= 0:
            await ctx.send("Minimum wager must be positive.")
            return
        if maximum < minimum:
            await ctx.send("Maximum wager cannot be lower than the minimum wager.")
            return
        if maximum > MAX_AMOUNT // max(SLOT_TRIPLE_MULTIPLIERS.values()):
            await ctx.send(
                f"Maximum wager cannot exceed "
                f"{MAX_AMOUNT // max(SLOT_TRIPLE_MULTIPLIERS.values()):,}."
            )
            return
        await self.config.casino_min_bet.set(minimum)
        await self.config.casino_max_bet.set(maximum)
        await ctx.send(f"Casino bet limits set to {minimum:,}-{maximum:,} {CURRENCY_NAME}.")

    @economy_admin.group(
        name="lwdmillions",
        aliases=["lwdm", "lottery"],
        invoke_without_command=True,
    )
    @commands.is_owner()
    async def economy_admin_lwdmillions(self, ctx: commands.Context):
        """Manage LWDMillions settings and draws."""
        await ctx.invoke(self.economy_admin_lwdmillions_show)

    @economy_admin_lwdmillions.command(name="show")
    @commands.is_owner()
    async def economy_admin_lwdmillions_show(self, ctx: commands.Context):
        """Show LWDMillions settings."""
        state = await self._lwdmillions_state_snapshot()
        channels = state.get("announcement_channels", {})
        proof_version = int(state.get("proof_version", 1))
        if proof_version == 1:
            proof_summary = "Proof: preserved v1 commitment (drand begins next draw)"
        elif proof_version == 3:
            beacon_round = int(state.get("beacon_round", 0))
            proof_summary = (
                "Proof: paid v1 draw upgraded with its original commitment + "
                f"Quicknet round #{beacon_round:,} at "
                f"<t:{drand_round_timestamp(beacon_round)}:F>"
                if beacon_round > 0
                else "Proof: invalid migrated beacon round"
            )
        else:
            beacon_round = int(state.get("beacon_round", 0))
            proof_summary = (
                f"Locked beacon: Quicknet round #{beacon_round:,} at "
                f"<t:{drand_round_timestamp(beacon_round)}:F>"
                if beacon_round > 0
                else "Proof: invalid v2 beacon round"
            )
        await ctx.send(
            "LWDMillions settings:\n"
            f"Sales: {'enabled' if state['enabled'] else 'disabled'}\n"
            f"Ticket price: {int(state['ticket_price']):,} {CURRENCY_NAME}\n"
            f"Current jackpot: {int(state['jackpot']):,} {CURRENCY_NAME}\n"
            f"Seed jackpot: {int(state['seed_jackpot']):,} {CURRENCY_NAME}\n"
            f"Jackpot contribution: {int(state['jackpot_contribution_percent'])}% per ticket\n"
            f"Upcoming draw: #{int(state['draw_number']):,} at "
            f"<t:{int(state['next_draw'])}:F> (<t:{int(state['next_draw'])}:R>)\n"
            f"{proof_summary}\n"
            f"Tickets sold: {len(state['tickets']):,}/{MAX_LWDMILLIONS_TOTAL_TICKETS:,}\n"
            f"Announcement channels: {len(channels):,}\n"
            f"Commitment: `{state['commitment']}`"
        )

    @economy_admin_lwdmillions.command(name="toggle")
    @commands.is_owner()
    async def economy_admin_lwdmillions_toggle(self, ctx: commands.Context):
        """Open or close LWDMillions ticket sales."""
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state["enabled"] = not bool(state["enabled"])
                enabled = bool(state["enabled"])
        await ctx.send(f"LWDMillions ticket sales are now {'open' if enabled else 'closed'}.")

    @economy_admin_lwdmillions.command(name="ticketprice", aliases=["price"])
    @commands.is_owner()
    async def economy_admin_lwdmillions_ticket_price(
        self,
        ctx: commands.Context,
        amount: int,
    ):
        """Set the price of newly purchased LWDMillions lines."""
        maximum = MAX_AMOUNT // max(LWD_MILLIONS_PRIZE_MULTIPLIERS.values())
        if amount <= 0 or amount > maximum:
            await ctx.send(f"Ticket price must be from 1 to {maximum:,} {CURRENCY_NAME}.")
            return
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state["ticket_price"] = int(amount)
        await ctx.send(f"New LWDMillions lines now cost {amount:,} {CURRENCY_NAME}.")

    @economy_admin_lwdmillions.command(name="contribution", aliases=["jackpotshare"])
    @commands.is_owner()
    async def economy_admin_lwdmillions_contribution(
        self,
        ctx: commands.Context,
        percentage: int,
    ):
        """Set how much of each new line is added to the jackpot."""
        if percentage < 0 or percentage > 100:
            await ctx.send("Jackpot contribution must be from 0 to 100 percent.")
            return
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state["jackpot_contribution_percent"] = int(percentage)
        await ctx.send(f"{percentage}% of each new LWDMillions line will enter the jackpot.")

    @economy_admin_lwdmillions.command(name="seed")
    @commands.is_owner()
    async def economy_admin_lwdmillions_seed(self, ctx: commands.Context, amount: int):
        """Set the jackpot amount used after a jackpot win."""
        if amount <= 0 or amount > MAX_AMOUNT:
            await ctx.send(f"Seed jackpot must be from 1 to {MAX_AMOUNT:,} {CURRENCY_NAME}.")
            return
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state["seed_jackpot"] = int(amount)
        await ctx.send(f"The LWDMillions seed jackpot is now {amount:,} {CURRENCY_NAME}.")

    @economy_admin_lwdmillions.command(name="jackpot", aliases=["pot"])
    @commands.is_owner()
    async def economy_admin_lwdmillions_jackpot(self, ctx: commands.Context, amount: int):
        """Set the current rolling jackpot."""
        if amount <= 0 or amount > MAX_AMOUNT:
            await ctx.send(f"Jackpot must be from 1 to {MAX_AMOUNT:,} {CURRENCY_NAME}.")
            return
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state["jackpot"] = int(amount)
        await ctx.send(f"The current LWDMillions jackpot is now {amount:,} {CURRENCY_NAME}.")

    @economy_admin_lwdmillions.command(name="channel")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_lwdmillions_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ):
        """Set this server's LWDMillions draw announcement channel."""
        channel = channel or ctx.channel
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state["announcement_channels"][str(ctx.guild.id)] = channel.id
        await ctx.send(f"LWDMillions draw results will be announced in {channel.mention}.")

    @economy_admin_lwdmillions.command(name="clearchannel")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_lwdmillions_clear_channel(self, ctx: commands.Context):
        """Disable LWDMillions draw announcements in this server."""
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                removed = state["announcement_channels"].pop(str(ctx.guild.id), None)
        await ctx.send(
            "LWDMillions draw announcements are disabled in this server."
            if removed is not None
            else "This server did not have an LWDMillions announcement channel."
        )

    @economy_admin_lwdmillions.command(name="draw")
    @commands.is_owner()
    @commands.max_concurrency(1, per=commands.BucketType.default, wait=False)
    async def economy_admin_lwdmillions_draw(
        self,
        ctx: commands.Context,
        confirmation: str = "",
    ):
        """Settle the current draw once its committed entropy is available."""
        if confirmation.casefold() != "confirm":
            await ctx.send(
                "This closes the current draw and settles every ticket. A public-beacon draw "
                "can never "
                "settle before its locked public beacon exists. Run "
                f"`{ctx.clean_prefix}eco admin lwdmillions draw confirm` to continue."
            )
            return
        record = await self._run_lwdmillions_draw(
            force=True,
            skip_channel_id=ctx.channel.id,
        )
        if record is None:
            state = await self._lwdmillions_state_snapshot()
            beacon_round = int(state.get("beacon_round", 0))
            if int(state.get("proof_version", 1)) in (2, 3) and beacon_round > 0:
                beacon_time = drand_round_timestamp(beacon_round)
                if int(time.time()) < beacon_time:
                    await ctx.send(
                        "The draw is locked until Quicknet round "
                        f"#{beacon_round:,} is published <t:{beacon_time}:R>."
                    )
                    return
            await ctx.send(
                "The LWDMillions draw could not be completed. Its proof was left unchanged; "
                "check the cog logs for beacon availability or proof validation errors."
            )
            return
        await ctx.send(
            embed=self._lwdmillions_result_embed(record, ctx.clean_prefix),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @economy_admin.group(name="shop", invoke_without_command=True)
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop(self, ctx: commands.Context):
        """Owner-only shop management."""
        await ctx.invoke(self.economy_shop)

    @economy_admin_shop.command(name="add")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_add(
        self,
        ctx: commands.Context,
        name: str,
        price: int,
        stock: int = -1,
        *,
        description: str = "No description.",
    ):
        """Add or replace a shop item. Use stock -1 for unlimited."""
        if price <= 0:
            await ctx.send("Price must be positive.")
            return
        if stock < -1:
            await ctx.send("Stock must be -1 for unlimited, 0, or a positive number.")
            return
        key = self._shop_key(name)
        item = {
            "name": name,
            "price": self._require_amount(price, allow_zero=False),
            "stock": None if stock == -1 else int(stock),
            "description": str(description or "No description.")[:500],
            "role_id": None,
            "redeem_code_enabled": False,
            "max_per_user": None,
            "giftable": False,
        }
        async with self.config.shops() as shops:
            shop = shops.setdefault(str(ctx.guild.id), {})
            shop[key] = item
        await self._send_economy_log(
            ctx.guild,
            "shop item added",
            amount=price,
            actor_id=ctx.author.id,
            target_id=None,
            reason=key,
            item_name=name,
            item_quantity=stock if stock != -1 else None,
        )
        await ctx.send(f"Added **{name}** to the shop for {price:,} {CURRENCY_NAME}.")
        await self._refresh_shop_panel(ctx.guild)

    @economy_admin_shop.command(name="remove")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_remove(self, ctx: commands.Context, name: str):
        """Remove a shop item."""
        key = self._shop_key(name)
        async with self.config.shops() as shops:
            shop = shops.setdefault(str(ctx.guild.id), {})
            item = shop.pop(key, None)
        if not item:
            await ctx.send("That item is not in the shop.")
            return
        await self._send_economy_log(
            ctx.guild,
            "shop item removed",
            amount=None,
            actor_id=ctx.author.id,
            target_id=None,
            reason=key,
            item_name=item.get("name", name),
            item_quantity=None,
        )
        await ctx.send(f"Removed **{item.get('name', name)}** from the shop.")
        await self._refresh_shop_panel(ctx.guild)

    @economy_admin_shop.command(name="role")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_role(self, ctx: commands.Context, name: str, role: discord.Role | None = None):
        """Set or clear the role awarded by a shop item."""
        key = self._shop_key(name)
        async with self.config.shops() as shops:
            shop = shops.setdefault(str(ctx.guild.id), {})
            item = shop.get(key)
            if not item:
                await ctx.send("That item is not in the shop.")
                return
            item["role_id"] = role.id if role else None
            if role:
                item["giftable"] = False
        await ctx.send(f"Role reward for **{item.get('name', name)}** {'set to ' + role.mention if role else 'cleared'}.")
        await self._refresh_shop_panel(ctx.guild)

    @economy_admin_shop.command(name="code")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_code(self, ctx: commands.Context, name: str, enabled: bool = True):
        """Toggle one-time redeem code generation for a shop item."""
        key = self._shop_key(name)
        async with self.config.shops() as shops:
            shop = shops.setdefault(str(ctx.guild.id), {})
            item = shop.get(key)
            if not item:
                await ctx.send("That item is not in the shop.")
                return
            item["redeem_code_enabled"] = bool(enabled)
            if enabled and not item.get("role_id"):
                item["giftable"] = True
        await ctx.send(
            f"Redeem codes for **{item.get('name', name)}** are now {'enabled' if enabled else 'disabled'}."
        )
        await self._refresh_shop_panel(ctx.guild)

    @economy_admin_shop.command(name="stock")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_stock(self, ctx: commands.Context, name: str, stock: int):
        """Set item stock. Use -1 for unlimited."""
        if stock < -1:
            await ctx.send("Stock must be -1 for unlimited, 0, or a positive number.")
            return
        key = self._shop_key(name)
        async with self.config.shops() as shops:
            shop = shops.setdefault(str(ctx.guild.id), {})
            item = shop.get(key)
            if not item:
                await ctx.send("That item is not in the shop.")
                return
            item["stock"] = None if stock == -1 else int(stock)
        await ctx.send(f"Stock for **{item.get('name', name)}** set to {'unlimited' if stock == -1 else f'{stock:,}'}.")
        await self._refresh_shop_panel(ctx.guild)

    @economy_admin_shop.command(name="limit")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_limit(self, ctx: commands.Context, name: str, limit: int):
        """Set the maximum quantity a member can buy. Use -1 for unlimited."""
        if limit < -1 or limit == 0:
            await ctx.send("Limit must be -1 for unlimited or a positive number.")
            return
        key = self._shop_key(name)
        async with self.config.shops() as shops:
            shop = shops.setdefault(str(ctx.guild.id), {})
            item = shop.get(key)
            if not item:
                await ctx.send("That item is not in the shop.")
                return
            item["max_per_user"] = None if limit == -1 else int(limit)
        await ctx.send(
            f"Purchase limit for **{item.get('name', name)}** set to "
            f"{'unlimited' if limit == -1 else f'{limit:,} per member'}."
        )
        await self._refresh_shop_panel(ctx.guild)

    @economy_admin_shop.command(name="giftable")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_giftable(self, ctx: commands.Context, name: str, enabled: bool = True):
        """Set whether an inventory item can be gifted."""
        key = self._shop_key(name)
        async with self.config.shops() as shops:
            shop = shops.setdefault(str(ctx.guild.id), {})
            item = shop.get(key)
            if not item:
                await ctx.send("That item is not in the shop.")
                return
            if enabled and item.get("role_id"):
                await ctx.send("Role reward items cannot be giftable. Use a redeem-code item for giftable VIP.")
                return
            item["giftable"] = bool(enabled)
        await ctx.send(
            f"**{item.get('name', name)}** is now {'giftable' if enabled else 'not giftable'}."
        )
        await self._refresh_shop_panel(ctx.guild)

    @economy_admin_shop.command(name="channel")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ):
        """Set the dedicated shop channel and post item messages."""
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("Shop channel must be a text channel.")
            return

        async with self.config.shop_channels() as channels:
            channels[str(ctx.guild.id)] = channel.id

        try:
            count = await self._post_or_update_shop_panel(ctx.guild, channel, prefix=ctx.clean_prefix)
        except discord.HTTPException:
            await ctx.send(f"I could not post the shop messages in {channel.mention}. Check my permissions there.")
            return

        await ctx.send(f"Shop channel set to {channel.mention}. Synced {count:,} item messages.")

    @economy_admin_shop.command(name="post", aliases=["refresh"])
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_post(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ):
        """Post or refresh the dedicated shop item messages."""
        channel = channel or await self._configured_shop_channel(ctx.guild) or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("Shop channel must be a text channel.")
            return

        async with self.config.shop_channels() as channels:
            channels[str(ctx.guild.id)] = channel.id

        try:
            count = await self._post_or_update_shop_panel(ctx.guild, channel, prefix=ctx.clean_prefix)
        except discord.HTTPException:
            await ctx.send(f"I could not post the shop messages in {channel.mention}. Check my permissions there.")
            return

        await ctx.send(f"Shop messages refreshed in {channel.mention}. Synced {count:,} item messages.")

    @economy_admin_shop.command(name="clearchannel")
    @commands.is_owner()
    @commands.guild_only()
    async def economy_admin_shop_clear_channel(self, ctx: commands.Context):
        """Clear the dedicated shop channel and remove stored item messages if possible."""
        await self._delete_shop_panel(ctx.guild)
        async with self.config.shop_channels() as channels:
            channels.pop(str(ctx.guild.id), None)
        async with self.config.shop_messages() as messages:
            messages.pop(str(ctx.guild.id), None)
        await ctx.send("Dedicated shop channel cleared.")

    @economy.group(name="api", invoke_without_command=True)
    @commands.is_owner()
    async def economy_api(self, ctx: commands.Context):
        """Owner-only API management."""
        await ctx.invoke(self.economy_api_status)

    @economy_api.command(name="status")
    @commands.is_owner()
    async def economy_api_status(self, ctx: commands.Context):
        """Show API status."""
        host = await self.config.api_host()
        port = await self.config.api_port()
        enabled = await self.config.api_enabled()
        tokens = await self.config.api_tokens()
        runtime = "running" if self._runner is not None else "stopped"
        await ctx.send(
            f"API configured: {'enabled' if enabled else 'disabled'}\n"
            f"Runtime: {runtime}\n"
            f"Bind: `{host}:{port}`\n"
            f"Tokens: {len(tokens)}"
        )

    @economy_api.command(name="start")
    @commands.is_owner()
    async def economy_api_start(self, ctx: commands.Context, host: str = DEFAULT_API_HOST, port: int = DEFAULT_API_PORT):
        """Start the HTTP API."""
        if port < 1 or port > 65535:
            await ctx.send("Port must be between 1 and 65535.")
            return

        await self.config.api_host.set(host)
        await self.config.api_port.set(port)
        await self.config.api_enabled.set(True)
        try:
            await self._restart_api(host, port)
        except OSError as error:
            await ctx.send(f"Could not start API: {error}")
            return
        await ctx.send(f"Economy API started on `{host}:{port}`.")

    @economy_api.command(name="stop")
    @commands.is_owner()
    async def economy_api_stop(self, ctx: commands.Context):
        """Stop the HTTP API."""
        await self.config.api_enabled.set(False)
        await self._stop_api()
        await ctx.send("Economy API stopped.")

    @economy_api.group(name="token", invoke_without_command=True)
    @commands.is_owner()
    async def economy_api_token(self, ctx: commands.Context):
        """Manage API tokens."""
        await ctx.send_help()

    @economy_api_token.command(name="create")
    @commands.is_owner()
    async def economy_api_token_create(self, ctx: commands.Context, name: str):
        """Create or replace an API token."""
        token = secrets.token_urlsafe(32)
        async with self.config.api_tokens() as tokens:
            tokens[name] = token
        await ctx.send(f"Token `{name}` created. Copy it now:\n`{token}`")

    @economy_api_token.command(name="list")
    @commands.is_owner()
    async def economy_api_token_list(self, ctx: commands.Context):
        """List configured API token names."""
        tokens = await self.config.api_tokens()
        if not tokens:
            await ctx.send("No API tokens are configured.")
            return

        names = sorted(discord.utils.escape_markdown(name) for name in tokens)
        await ctx.send("Configured API token names:\n" + "\n".join(f"- {name}" for name in names))

    @economy_api_token.command(name="revoke")
    @commands.is_owner()
    async def economy_api_token_revoke(self, ctx: commands.Context, name: str):
        """Revoke an API token."""
        async with self.config.api_tokens() as tokens:
            existed = tokens.pop(name, None) is not None
        await ctx.send(f"Token `{name}` {'revoked' if existed else 'was not found'}.")

    @economy_api_token.command(name="revokeall", aliases=["clear", "revoke-all"])
    @commands.is_owner()
    async def economy_api_token_revoke_all(self, ctx: commands.Context, confirmation: str | None = None):
        """Revoke every API token."""
        if confirmation != "confirm":
            await ctx.send(
                "This revokes every API token. Run "
                f"`{ctx.clean_prefix}eco api token revokeall confirm` to continue."
            )
            return

        async with self.config.api_tokens() as tokens:
            count = len(tokens)
            tokens.clear()

        await ctx.send(f"Revoked {count} API token{'s' if count != 1 else ''}.")

    async def _owner_adjust(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        operation: str,
        reason: str,
    ):
        if amount < 0:
            await ctx.send("Amount cannot be negative.")
            return

        try:
            if operation == "add":
                balances = await self.add_balance(
                    member.id,
                    amount,
                    actor_id=ctx.author.id,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    reason=reason,
                )
            elif operation == "remove":
                balances = await self.remove_balance(
                    member.id,
                    amount,
                    actor_id=ctx.author.id,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    reason=reason,
                )
            else:
                balances = await self.set_balance(
                    member.id,
                    amount,
                    actor_id=ctx.author.id,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    reason=reason,
                )
        except EconomyError as error:
            await ctx.send(str(error))
            return

        await ctx.send(
            f"{member.mention} now has {balances[CASH]:,} {CURRENCY_NAME}.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def _claim_reward(self, ctx: commands.Context, claim_type: str):
        if claim_type == "work":
            minimum = int(await self.config.work_min())
            maximum = int(await self.config.work_max())
            amount = random.randint(minimum, maximum)
        else:
            amount = int(await getattr(self.config, f"{claim_type}_amount")())
        cooldown = int(await getattr(self.config, f"{claim_type}_cooldown")())
        now = int(time.time())
        user_id = str(ctx.author.id)

        async with self.config.claims() as claims:
            user_claims = claims.setdefault(user_id, {})
            last_claimed = int(user_claims.get(claim_type, 0))
            if claim_type in CALENDAR_CLAIM_TYPES:
                next_claim = self._next_calendar_claim_timestamp(claim_type, last_claimed)
            else:
                next_claim = last_claimed + cooldown
            if cooldown and now < next_claim:
                await ctx.send(
                    f"You can claim `{claim_type}` again: <t:{next_claim}:F> (<t:{next_claim}:R>)."
                )
                return
            user_claims[claim_type] = now

        balances = await self.add_balance(
            ctx.author.id,
            amount,
            actor_id=ctx.author.id,
            guild_id=ctx.guild.id if ctx.guild else None,
            reason=f"{claim_type} claim",
        )
        await ctx.send(f"You claimed {amount:,} {CURRENCY_NAME}. Balance: {balances[CASH]:,} {CURRENCY_NAME}.")

    async def _set_claim_settings(self, ctx: commands.Context, claim_type: str, amount: int, cooldown_seconds: int):
        if amount < 0:
            await ctx.send("Amount cannot be negative.")
            return
        if cooldown_seconds < 0:
            await ctx.send("Cooldown cannot be negative.")
            return
        if amount > MAX_AMOUNT:
            await ctx.send(f"Amount cannot exceed {MAX_AMOUNT:,}.")
            return
        await getattr(self.config, f"{claim_type}_amount").set(amount)
        await getattr(self.config, f"{claim_type}_cooldown").set(cooldown_seconds)
        await ctx.send(
            f"{claim_type.title()} claim set to {amount:,} {CURRENCY_NAME} {self._format_claim_interval(claim_type, cooldown_seconds)}."
        )

    async def _set_work_range(self, ctx: commands.Context, minimum: int, maximum: int, cooldown_seconds: int):
        if minimum < 0 or maximum < 0:
            await ctx.send("Work amounts cannot be negative.")
            return
        if minimum > maximum:
            await ctx.send("Minimum cannot be greater than maximum.")
            return
        if maximum > MAX_AMOUNT:
            await ctx.send(f"Amount cannot exceed {MAX_AMOUNT:,}.")
            return
        if cooldown_seconds < 0:
            await ctx.send("Cooldown cannot be negative.")
            return
        await self.config.work_min.set(minimum)
        await self.config.work_max.set(maximum)
        await self.config.work_amount.set(maximum)
        await self.config.work_cooldown.set(cooldown_seconds)
        await ctx.send(
            f"Work claim set to {minimum:,}-{maximum:,} {CURRENCY_NAME} every {self._format_duration(cooldown_seconds)}."
        )

    async def _complete_purchase(
        self,
        guild: discord.Guild,
        member: discord.Member,
        item_name: str,
        quantity: int,
        *,
        reveal_codes_inline: bool,
    ) -> tuple[str, discord.ui.View | None]:
        item, balances = await self.buy_item(guild, member, item_name, quantity)
        item_display = item.get("name", item_name)
        total = int(item.get("price", 0)) * quantity

        notes = []
        role_id = item.get("role_id")
        if role_id:
            role = guild.get_role(int(role_id))
            if role:
                try:
                    await member.add_roles(role, reason=f"Economy shop purchase: {item_display}")
                    notes.append(f"Role added: {role.mention}.")
                except discord.Forbidden:
                    notes.append("I could not add the configured role.")
                except discord.HTTPException:
                    notes.append("Role assignment failed.")

        view = None
        if item.get("redeem_code_enabled"):
            code = await self._create_redeem_code(guild.id, member.id, item_display, quantity)
            code_line = f"`{code}` - **{item_display}** x{quantity:,}"
            if reveal_codes_inline:
                notes.append(f"Redeem code: `{code}`.")
            else:
                try:
                    await member.send(
                        f"Redeem code for **{item_display}** x{quantity:,} in **{guild.name}**:\n`{code}`"
                    )
                    notes.append("Redeem code generated; check your DMs.")
                except discord.HTTPException:
                    notes.append("Redeem code generated. Use the button below to view it privately.")
                    view = CodeRevealView(member.id, f"Redeem code for {guild.name}", [code_line])

        note_text = " " + " ".join(notes) if notes else ""
        content = (
            f"Bought {quantity:,}x **{item_display}** for {total:,} {CURRENCY_NAME}.\n"
            f"Balance: {balances[CASH]:,} {CURRENCY_NAME}.{note_text}"
        )
        return content, view

    async def _shop_button_buy(self, interaction: discord.Interaction, guild_id: int, item_key: str):
        if interaction.guild is None or interaction.guild.id != guild_id:
            await interaction.response.send_message("This shop button is not valid here.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This purchase must be made in a server.", ephemeral=True)
            return

        try:
            content, _ = await self._complete_purchase(
                interaction.guild,
                interaction.user,
                item_key,
                1,
                reveal_codes_inline=True,
            )
        except EconomyError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await self._refresh_shop_panel(interaction.guild)

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
        if len(selections) > MAX_LWDMILLIONS_LINES_PER_PLAYER:
            raise EconomyError(
                f"You can hold at most {MAX_LWDMILLIONS_LINES_PER_PLAYER} lines per draw."
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
                if existing_count + len(validated) > MAX_LWDMILLIONS_LINES_PER_PLAYER:
                    remaining = MAX_LWDMILLIONS_LINES_PER_PLAYER - existing_count
                    raise EconomyError(
                        f"You can hold at most {MAX_LWDMILLIONS_LINES_PER_PLAYER} lines per draw. "
                        f"You have room for {max(0, remaining)} more."
                    )
                if len(state["tickets"]) + len(validated) > MAX_LWDMILLIONS_TOTAL_TICKETS:
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
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

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
            f"Run `{prefix}lwdmillions verify {draw_number}` for the full proof details.",
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

    async def _validate_casino_wager(self, wager: int, user_id: int | None = None):
        if not await self.config.casino_enabled():
            raise EconomyError("The casino is currently closed.")
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
        embed.set_footer(text=f"Balance: {account[CASH]:,} {CURRENCY_NAME}")
        await ctx.send(embed=embed)

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
        embed.set_footer(text=f"Balance: {account[CASH]:,} {CURRENCY_NAME}")

        buffer = None
        file = None
        try:
            dealer = BlackjackCard(*dealer_card)
            hand = BlackjackHand([BlackjackCard(*player_card)], wager, stood=True)
            buffer = await asyncio.to_thread(render_blackjack_table, [dealer], [hand], None)
            filename = f"highcard-{ctx.author.id}-{secrets.token_hex(4)}.png"
            file = discord.File(buffer, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            await ctx.send(
                embed=embed,
                file=file,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            log.exception("Could not render high card table for user %s", ctx.author.id)
            embed.remove_image()
            await ctx.send(embed=embed)
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
        embed.set_footer(text=f"Balance: {account[CASH]:,} {CURRENCY_NAME} | {payout_rule}")

        try:
            async with self._slot_render_semaphore:
                buffer = await asyncio.to_thread(render_slot_spin, stops)
            filename = f"slots-{ctx.author.id}-{secrets.token_hex(4)}.gif"
            file = discord.File(buffer, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            try:
                await ctx.send(
                    embed=embed,
                    file=file,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                file.close()
        except Exception:
            log.exception("Could not render animated slots for user %s", ctx.author.id)
            embed.remove_image()
            await ctx.send(embed=embed)

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
        return account

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
    ) -> dict[str, int]:
        """Credit the total return from a reserved blackjack wager."""
        payout = self._require_amount(payout, allow_zero=False)
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
        return account

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
    ) -> dict[str, int]:
        """Credit the total return from a reserved Mines wager."""
        payout = self._require_amount(payout, allow_zero=False)
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
        return account

    async def get_balance(self, user_id: int) -> dict[str, int]:
        """Public cog API: return a user's balances."""
        balances = await self.config.balances()
        return self._account_from_mapping(balances, user_id)

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

    async def buy_item(
        self,
        guild: discord.Guild,
        member: discord.Member,
        item_name: str,
        quantity: int = 1,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Public cog API: buy a guild shop item for a member."""
        quantity = self._require_amount(quantity, allow_zero=False)
        key = self._shop_key(item_name)
        async with self._lock:
            async with self.config.shops() as shops:
                shop = shops.setdefault(str(guild.id), {})
                item = dict(shop.get(key) or {})
                if not item:
                    raise EconomyError("That item is not in the shop.")
                stock = item.get("stock")
                if stock is not None and int(stock) < quantity:
                    raise EconomyError("That item does not have enough stock.")
                price = self._require_amount(item.get("price"), allow_zero=False)
                total = price * quantity
                if total > MAX_AMOUNT:
                    raise EconomyError(f"Amount cannot exceed {MAX_AMOUNT:,}.")
                item_inventory_name = str(item.get("name", key))
                max_per_user = item.get("max_per_user")
                async with self.config.inventories() as inventories:
                    guild_inventory = inventories.setdefault(str(guild.id), {})
                    inventory = guild_inventory.setdefault(str(member.id), {})
                    current_quantity = int(inventory.get(item_inventory_name, 0))
                    if max_per_user is not None and current_quantity + quantity > int(max_per_user):
                        raise EconomyError(
                            f"You can only buy {int(max_per_user):,}x **{item_inventory_name}**."
                        )
                    async with self.config.balances() as balances:
                        account = self._account_from_mapping(balances, member.id)
                        if account[CASH] < total:
                            raise EconomyError("Insufficient funds.")
                        account[CASH] -= total
                        balances[str(member.id)] = account
                    if stock is not None:
                        item["stock"] = int(stock) - quantity
                        shop[key] = item
                    inventory[item_inventory_name] = current_quantity + quantity
            await self._append_ledger(
                "buy",
                from_user_id=member.id,
                to_user_id=None,
                amount=total,
                actor_id=member.id,
                guild_id=guild.id,
                reason=f"bought {quantity}x {item.get('name', key)}",
                log_to_channel=False,
            )
        await self._send_economy_log(
            guild,
            "item bought",
            amount=total,
            actor_id=member.id,
            target_id=member.id,
            reason="buy",
            item_name=item.get("name", key),
            item_quantity=quantity,
        )
        return item, account

    async def gift_item(
        self,
        guild: discord.Guild,
        sender: discord.Member,
        recipient: discord.Member,
        item_name: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Public cog API: gift a giftable inventory item to another member."""
        quantity = self._require_amount(quantity, allow_zero=False)
        if sender.id == recipient.id:
            raise EconomyError("You cannot gift items to yourself.")

        async with self._lock:
            shops = await self.config.shops()
            item_key, item = self._find_shop_item(shops.get(str(guild.id), {}), item_name)
            if item is None or item_key is None:
                raise EconomyError("That item is not in this server's shop.")
            if item.get("role_id"):
                raise EconomyError("That item is locked and cannot be gifted.")
            if not self._item_is_giftable(item):
                raise EconomyError("That item cannot be gifted.")

            inventory_name = str(item.get("name", item_key))
            codes_transferred = 0
            async with self.config.inventories() as inventories:
                guild_inventory = inventories.setdefault(str(guild.id), {})
                sender_inventory = guild_inventory.setdefault(str(sender.id), {})
                recipient_inventory = guild_inventory.setdefault(str(recipient.id), {})

                current_quantity = int(sender_inventory.get(inventory_name, 0))
                if current_quantity < quantity:
                    raise EconomyError(f"You do not have {quantity:,}x **{inventory_name}**.")

                if item.get("redeem_code_enabled"):
                    async with self.config.redeem_codes() as codes:
                        transferred_codes = self._transfer_unredeemed_codes(
                            codes,
                            guild_id=guild.id,
                            from_user_id=sender.id,
                            to_user_id=recipient.id,
                            item_name=inventory_name,
                            quantity=quantity,
                        )
                    codes_transferred = len(transferred_codes)

                remaining = current_quantity - quantity
                if remaining:
                    sender_inventory[inventory_name] = remaining
                else:
                    sender_inventory.pop(inventory_name, None)
                recipient_inventory[inventory_name] = int(recipient_inventory.get(inventory_name, 0)) + quantity
                if not sender_inventory:
                    guild_inventory.pop(str(sender.id), None)

            await self._append_ledger(
                "gift",
                from_user_id=sender.id,
                to_user_id=recipient.id,
                amount=0,
                actor_id=sender.id,
                guild_id=guild.id,
                reason=f"gifted {quantity}x {inventory_name}",
                log_to_channel=False,
            )

        await self._send_economy_log(
            guild,
            "item gifted",
            amount=None,
            actor_id=sender.id,
            target_id=recipient.id,
            reason="gift",
            item_name=inventory_name,
            item_quantity=quantity,
        )
        return {
            "item_name": inventory_name,
            "quantity": quantity,
            "codes_transferred": codes_transferred,
        }

    async def _get_shop(self, guild_id: int) -> dict[str, Any]:
        shops = await self.config.shops()
        return dict(shops.get(str(guild_id), {}))

    async def _get_inventory(self, guild_id: int, user_id: int) -> dict[str, int]:
        inventories = await self.config.inventories()
        guild_inventory = inventories.get(str(guild_id), {})
        inventory = guild_inventory.get(str(user_id), {})
        return {str(name): int(quantity) for name, quantity in inventory.items()}

    def _find_shop_item(
        self,
        shop: dict[str, Any],
        item_name: str,
    ) -> tuple[str | None, dict[str, Any] | None]:
        key = self._shop_key(item_name)
        direct = shop.get(key)
        if isinstance(direct, dict):
            return key, dict(direct)
        for item_key, item in shop.items():
            if not isinstance(item, dict):
                continue
            if self._shop_key(item.get("name", item_key)) == key:
                return str(item_key), dict(item)
        return None, None

    @staticmethod
    def _item_is_giftable(item: dict[str, Any]) -> bool:
        if item.get("role_id"):
            return False
        if "giftable" in item:
            return bool(item.get("giftable"))
        return bool(item.get("redeem_code_enabled"))

    def _transfer_unredeemed_codes(
        self,
        codes: dict[str, Any],
        *,
        guild_id: int,
        from_user_id: int,
        to_user_id: int,
        item_name: str,
        quantity: int,
    ) -> list[str]:
        candidates: list[tuple[str, dict[str, Any], int]] = []
        for code, entry in codes.items():
            if not isinstance(entry, dict):
                continue
            if int(entry.get("guild_id", 0)) != int(guild_id):
                continue
            if int(entry.get("user_id", 0)) != int(from_user_id):
                continue
            if entry.get("redeemed_at"):
                continue
            if str(entry.get("item_name", "")) != str(item_name):
                continue
            entry_quantity = int(entry.get("quantity", 1))
            if entry_quantity > 0:
                candidates.append((str(code), dict(entry), entry_quantity))

        candidates.sort(key=lambda candidate: int(candidate[1].get("created_at", 0)))
        if sum(entry_quantity for _, _, entry_quantity in candidates) < quantity:
            raise EconomyError(f"You do not have enough unredeemed codes for **{item_name}**.")

        remaining = quantity
        now = int(time.time())
        transferred_codes: list[str] = []
        for code, entry, entry_quantity in candidates:
            if remaining <= 0:
                break
            if entry_quantity <= remaining:
                new_code = self._new_unique_redeem_code(codes)
                gift_entry = dict(entry)
                gift_entry["user_id"] = int(to_user_id)
                gift_entry["created_at"] = now
                gift_entry["gifted_by"] = int(from_user_id)
                gift_entry["gifted_at"] = now
                gift_entry["split_from"] = code
                codes.pop(code, None)
                codes[new_code] = gift_entry
                transferred_codes.append(new_code)
                remaining -= entry_quantity
                continue

            gift_quantity = remaining
            new_code = self._new_unique_redeem_code(codes)
            source_entry = dict(entry)
            source_entry["quantity"] = entry_quantity - gift_quantity
            codes[code] = source_entry

            gift_entry = dict(entry)
            gift_entry["quantity"] = gift_quantity
            gift_entry["user_id"] = int(to_user_id)
            gift_entry["created_at"] = now
            gift_entry["gifted_by"] = int(from_user_id)
            gift_entry["gifted_at"] = now
            gift_entry["split_from"] = code
            codes[new_code] = gift_entry
            transferred_codes.append(new_code)
            remaining = 0

        return transferred_codes

    async def _shop_embeds(
        self,
        guild: discord.Guild,
        *,
        prefix: str | None = None,
        panel: bool,
    ) -> list[discord.Embed]:
        shop = await self._get_shop(guild.id)
        items = sorted(shop.items(), key=lambda item: item[0])
        if not items:
            embed = discord.Embed(
                title="LWD$ Shop",
                description="No shop items are available right now.",
                color=discord.Color.gold(),
            )
            return [embed]

        pages = []
        total_pages = (len(items) + SHOP_PAGE_SIZE - 1) // SHOP_PAGE_SIZE
        for start in range(0, len(items), SHOP_PAGE_SIZE):
            page_items = items[start : start + SHOP_PAGE_SIZE]
            embed = discord.Embed(
                title=f"{guild.name} Shop",
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow(),
            )
            if panel:
                embed.description = (
                    "Use each item's button to buy one item. "
                    "For multiple quantities, use the buy command shown on each item."
                )
            else:
                embed.description = "Available items and purchase details."

            for key, item in page_items:
                embed.add_field(
                    name=self._shop_item_title(item),
                    value=self._shop_item_value(key, item, prefix=prefix),
                    inline=False,
                )

            embed.set_footer(text=f"Page {len(pages) + 1}/{total_pages} | Balance: eco balance")
            pages.append(embed)
        return pages

    def _shop_item_title(self, item: dict[str, Any]) -> str:
        price = int(item.get("price", 0))
        stock = item.get("stock")
        if stock is None:
            stock_text = "Unlimited"
        elif int(stock) <= 0:
            stock_text = "Sold out"
        else:
            stock_text = f"{int(stock):,} left"
        return f"{item.get('name', 'Item')} - {price:,} {CURRENCY_NAME} | {stock_text}"

    def _shop_item_value(self, key: str, item: dict[str, Any], *, prefix: str | None) -> str:
        delivery = ["Inventory"]
        role_id = item.get("role_id")
        if role_id:
            delivery.append(f"Role <@&{role_id}>")
        if item.get("redeem_code_enabled"):
            delivery.append("Redeem code")

        description = str(item.get("description") or "No description.")[:250]
        multi_buy = self._can_multi_buy(item)
        buy_line = (
            f"Multi-Buy: {self._buy_command_text(key, item, prefix=prefix, include_quantity=True)}"
            if multi_buy
            else f"Buy: {self._buy_command_text(key, item, prefix=prefix, include_quantity=False)}"
        )
        return (
            f"{description}\n"
            f"Delivery: {', '.join(delivery)}\n"
            f"Limit: {self._limit_text(item.get('max_per_user'))}\n"
            f"Giftable: {self._giftable_text(item)}\n"
            f"{buy_line}"
        )

    async def _configured_shop_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channels = await self.config.shop_channels()
        channel_id = channels.get(str(guild.id))
        if not channel_id:
            return None
        channel = guild.get_channel(int(channel_id))
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _post_or_update_shop_panel(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        *,
        prefix: str | None = None,
    ) -> int:
        shop = await self._get_shop(guild.id)
        stored_messages = await self._stored_shop_item_messages(guild, channel)
        synced = 0
        next_messages: dict[str, dict[str, int]] = {}

        for item_key, item in sorted(shop.items(), key=lambda entry: entry[0]):
            embed = self._shop_item_embed(guild, item_key, item, prefix=prefix)
            view = ShopItemView(self, guild.id, item_key, item)
            stored_message = stored_messages.pop(item_key, None)
            if stored_message is not None:
                await stored_message.edit(content=None, embed=embed, view=view)
                message = stored_message
            else:
                message = await channel.send(embed=embed, view=view)
            next_messages[item_key] = {"channel_id": channel.id, "message_id": message.id}
            synced += 1

        for stale_message in stored_messages.values():
            try:
                await stale_message.delete()
            except discord.HTTPException:
                pass

        async with self.config.shop_messages() as messages:
            messages[str(guild.id)] = next_messages
        return synced

    async def _refresh_shop_panel(self, guild: discord.Guild):
        channel = await self._configured_shop_channel(guild)
        if channel is None:
            return
        try:
            await self._post_or_update_shop_panel(guild, channel)
        except discord.HTTPException:
            log.exception("Could not refresh shop messages in guild %s", guild.id)

    async def _delete_shop_panel(self, guild: discord.Guild):
        messages = await self.config.shop_messages()
        guild_messages = messages.get(str(guild.id), {})
        for _, entry in self._iter_shop_message_entries(guild_messages):
            channel = guild.get_channel(int(entry.get("channel_id", 0)))
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                message = await channel.fetch_message(int(entry.get("message_id", 0)))
                await message.delete()
            except discord.HTTPException:
                pass

    async def _stored_shop_item_messages(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> dict[str, discord.Message]:
        messages = await self.config.shop_messages()
        guild_messages = messages.get(str(guild.id), {})
        resolved: dict[str, discord.Message] = {}
        for item_key, entry in self._iter_shop_message_entries(guild_messages):
            if int(entry.get("channel_id", 0)) != channel.id:
                continue
            try:
                resolved[item_key] = await channel.fetch_message(int(entry.get("message_id", 0)))
            except discord.HTTPException:
                continue
        return resolved

    def _shop_item_embed(
        self,
        guild: discord.Guild,
        item_key: str,
        item: dict[str, Any],
        *,
        prefix: str | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=str(item.get("name", item_key)),
            description=str(item.get("description") or "No description.")[:1000],
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Price", value=f"{int(item.get('price', 0)):,} {CURRENCY_NAME}", inline=True)
        embed.add_field(name="Stock", value=self._stock_text(item.get("stock")), inline=True)
        embed.add_field(name="Limit", value=self._limit_text(item.get("max_per_user")), inline=True)
        embed.add_field(name="Giftable", value=self._giftable_text(item), inline=True)
        embed.add_field(name="Delivery", value=self._delivery_text(item), inline=True)
        multi_buy = self._can_multi_buy(item)
        embed.add_field(
            name="Multi-Buy" if multi_buy else "Buy",
            value=self._buy_command_text(item_key, item, prefix=prefix, include_quantity=multi_buy),
            inline=False,
        )
        embed.set_footer(
            text="Buttons buy one item per click."
            if multi_buy
            else "Use the button or buy command once."
        )
        return embed

    def _stock_text(self, stock: Any) -> str:
        if stock is None:
            return "Unlimited"
        if int(stock) <= 0:
            return "Sold out"
        return f"{int(stock):,} left"

    def _limit_text(self, limit: Any) -> str:
        if limit is None:
            return "Unlimited"
        limit = int(limit)
        if limit <= 0:
            return "Unlimited"
        if limit == 1:
            return "1 per member"
        return f"{limit:,} per member"

    def _giftable_text(self, item: dict[str, Any]) -> str:
        return "Yes" if self._item_is_giftable(item) else "No"

    def _can_multi_buy(self, item: dict[str, Any]) -> bool:
        stock = item.get("stock")
        if stock is not None and int(stock) <= 1:
            return False
        limit = item.get("max_per_user")
        if limit is not None and int(limit) <= 1:
            return False
        return True

    def _buy_command_text(
        self,
        item_key: str,
        item: dict[str, Any],
        *,
        prefix: str | None,
        include_quantity: bool,
    ) -> str:
        command_prefix = prefix if prefix is not None else "."
        name = str(item.get("name", item_key)).replace('"', '\\"')
        quantity_text = " <quantity>" if include_quantity else ""
        return f"`{command_prefix}buy \"{name}\"{quantity_text}`"

    def _delivery_text(self, item: dict[str, Any]) -> str:
        delivery = ["Inventory"]
        role_id = item.get("role_id")
        if role_id:
            delivery.append(f"Role <@&{role_id}>")
        if item.get("redeem_code_enabled"):
            delivery.append("Redeem code")
        return "\n".join(delivery)

    def _iter_shop_message_entries(self, guild_messages: Any):
        if not isinstance(guild_messages, dict):
            return []
        if "message_id" in guild_messages:
            return [("__legacy_panel__", guild_messages)]
        return [
            (str(item_key), entry)
            for item_key, entry in guild_messages.items()
            if isinstance(entry, dict)
        ]

    @staticmethod
    def _shop_button_custom_id(guild_id: int, item_key: str) -> str:
        digest = hashlib.sha1(item_key.encode("utf-8")).hexdigest()[:16]
        return f"eco:shop:{guild_id}:{digest}"

    async def _create_redeem_code(self, guild_id: int, user_id: int, item_name: str, quantity: int) -> str:
        quantity = self._require_amount(quantity, allow_zero=False)
        item_name = str(item_name or "Unknown item")[:100]
        for _ in range(20):
            code = self._new_redeem_code()
            async with self._lock:
                async with self.config.redeem_codes() as codes:
                    if code in codes:
                        continue
                    codes[code] = {
                        "guild_id": int(guild_id),
                        "user_id": int(user_id),
                        "item_name": item_name,
                        "quantity": quantity,
                        "created_at": int(time.time()),
                        "redeemed_at": None,
                        "redeemed_by": None,
                    }
                    return code
        raise EconomyError("Could not generate a unique redeem code.")

    async def _get_unredeemed_codes(self, guild_id: int, user_id: int) -> list[tuple[str, dict[str, Any]]]:
        codes = await self.config.redeem_codes()
        entries = []
        for code, entry in codes.items():
            if int(entry.get("guild_id", 0)) != int(guild_id):
                continue
            if int(entry.get("user_id", 0)) != int(user_id):
                continue
            if entry.get("redeemed_at"):
                continue
            entries.append((code, dict(entry)))
        entries.sort(key=lambda item: int(item[1].get("created_at", 0)), reverse=True)
        return entries

    async def redeem_code(self, code: str, *, redeemed_by: str = "api") -> dict[str, Any]:
        """Public cog API: redeem a one-time in-game item code."""
        normalized = self._normalize_redeem_code(code)
        if not normalized:
            raise EconomyError("Invalid code.")

        async with self._lock:
            async with self.config.redeem_codes() as codes:
                entry = codes.get(normalized)
                if entry is None:
                    raise EconomyCodeNotFound("Code not found.")
                if entry.get("redeemed_at"):
                    raise EconomyCodeRedeemed("Code has already been redeemed.")

                entry = dict(entry)
                entry["redeemed_at"] = int(time.time())
                entry["redeemed_by"] = str(redeemed_by or "api")[:100]
                codes[normalized] = entry

            await self._consume_inventory_item(
                int(entry["guild_id"]),
                int(entry["user_id"]),
                str(entry["item_name"]),
                int(entry["quantity"]),
            )

        result = dict(entry)
        result["code"] = normalized
        return result

    async def _consume_inventory_item(self, guild_id: int, user_id: int, item_name: str, quantity: int):
        async with self.config.inventories() as inventories:
            guild_inventory = inventories.setdefault(str(guild_id), {})
            inventory = guild_inventory.setdefault(str(user_id), {})
            current = int(inventory.get(item_name, 0))
            remaining = current - int(quantity)
            if remaining > 0:
                inventory[item_name] = remaining
            else:
                inventory.pop(item_name, None)
            if not inventory:
                guild_inventory.pop(str(user_id), None)

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

    async def _start_api_if_enabled(self):
        await self.bot.wait_until_ready()
        await self._restore_shop_panel_views()
        if await self.config.api_enabled():
            await self._restart_api(await self.config.api_host(), int(await self.config.api_port()))

    async def _restore_shop_panel_views(self):
        messages = await self.config.shop_messages()
        shops = await self.config.shops()
        for guild_id, guild_messages in messages.items():
            shop = shops.get(str(guild_id), {})
            if not shop:
                continue
            for item_key, entry in self._iter_shop_message_entries(guild_messages):
                item = shop.get(item_key)
                if not item:
                    continue
                try:
                    message_id = int(entry.get("message_id", 0))
                    view = ShopItemView(self, int(guild_id), item_key, item)
                    self.bot.add_view(view, message_id=message_id)
                except (AttributeError, TypeError, ValueError):
                    log.exception("Could not restore shop item view for guild %s item %s", guild_id, item_key)

    async def _restart_api(self, host: str, port: int):
        await self._stop_api()
        app = web.Application()
        app.add_routes(
            [
                web.get("/health", self._api_health),
                web.get("/balance/{user_id}", self._api_get_balance),
                web.post("/balance/{user_id}/add", self._api_add_balance),
                web.post("/balance/{user_id}/remove", self._api_remove_balance),
                web.post("/balance/{user_id}/set", self._api_set_balance),
                web.post("/transfer", self._api_transfer),
                web.post("/redeem-code", self._api_redeem_code),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        log.info("Economy API started on %s:%s", host, port)

    async def _stop_api(self):
        if self._runner is None:
            return
        runner = self._runner
        self._runner = None
        self._site = None
        await runner.cleanup()

    async def _api_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _api_get_balance(self, request: web.Request) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        user_id = self._parse_user_id(request.match_info["user_id"])
        if user_id is None:
            return web.json_response({"error": "invalid user_id"}, status=400)
        return web.json_response({"user_id": user_id, "balances": await self.get_balance(user_id)})

    async def _api_add_balance(self, request: web.Request) -> web.Response:
        return await self._api_adjust(request, "add")

    async def _api_remove_balance(self, request: web.Request) -> web.Response:
        return await self._api_adjust(request, "remove")

    async def _api_set_balance(self, request: web.Request) -> web.Response:
        return await self._api_adjust(request, "set")

    async def _api_adjust(self, request: web.Request, operation: str) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        user_id = self._parse_user_id(request.match_info["user_id"])
        if user_id is None:
            return web.json_response({"error": "invalid user_id"}, status=400)
        payload = await self._json_payload(request)
        if payload is None:
            return web.json_response({"error": "invalid json"}, status=400)
        try:
            if operation == "add":
                balances = await self.add_balance(user_id, payload.get("amount"), actor_id=None, reason=payload.get("reason", "api add"))
            elif operation == "remove":
                balances = await self.remove_balance(user_id, payload.get("amount"), actor_id=None, reason=payload.get("reason", "api remove"))
            else:
                balances = await self.set_balance(user_id, payload.get("amount"), actor_id=None, reason=payload.get("reason", "api set"))
        except EconomyError as error:
            return web.json_response({"error": str(error)}, status=400)
        return web.json_response({"user_id": user_id, "balances": balances})

    async def _api_transfer(self, request: web.Request) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        payload = await self._json_payload(request)
        if payload is None:
            return web.json_response({"error": "invalid json"}, status=400)
        try:
            result = await self.transfer_balance(
                int(payload.get("from_user_id")),
                int(payload.get("to_user_id")),
                payload.get("amount"),
                actor_id=None,
                reason=payload.get("reason", "api transfer"),
            )
        except (TypeError, ValueError, EconomyError) as error:
            return web.json_response({"error": str(error)}, status=400)
        return web.json_response(result)

    async def _api_redeem_code(self, request: web.Request) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        payload = await self._json_payload(request)
        if payload is None:
            return web.json_response({"error": "invalid json"}, status=400)

        code = payload.get("code")
        if not code:
            return web.json_response({"error": "code is required"}, status=400)
        try:
            entry = await self.redeem_code(
                str(code),
                redeemed_by=str(payload.get("redeemed_by", "api"))[:100],
            )
        except EconomyCodeNotFound as error:
            return web.json_response({"error": str(error)}, status=404)
        except EconomyCodeRedeemed as error:
            return web.json_response({"error": str(error)}, status=409)
        except EconomyError as error:
            return web.json_response({"error": str(error)}, status=400)
        return web.json_response(entry)

    async def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        token = header.removeprefix("Bearer ").strip()
        tokens = await self.config.api_tokens()
        return any(secrets.compare_digest(token, stored) for stored in tokens.values())

    @staticmethod
    def _unauthorized() -> web.Response:
        return web.json_response({"error": "unauthorized"}, status=401)

    @staticmethod
    async def _json_payload(request: web.Request) -> dict[str, Any] | None:
        try:
            payload = await request.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _parse_user_id(value: str) -> int | None:
        try:
            user_id = int(value)
        except (TypeError, ValueError):
            return None
        return user_id if user_id > 0 else None

    @staticmethod
    def _new_redeem_code() -> str:
        groups = []
        for _ in range(REDEEM_CODE_GROUPS):
            groups.append("".join(secrets.choice(REDEEM_CODE_ALPHABET) for _ in range(REDEEM_CODE_GROUP_SIZE)))
        return "-".join(groups)

    def _new_unique_redeem_code(self, existing_codes: dict[str, Any]) -> str:
        for _ in range(20):
            code = self._new_redeem_code()
            if code not in existing_codes:
                return code
        raise EconomyError("Could not generate a unique redeem code.")

    @staticmethod
    def _normalize_redeem_code(code: str) -> str:
        code = str(code or "").strip().upper().replace(" ", "-")
        compact = code.replace("-", "")
        if len(compact) != REDEEM_CODE_GROUPS * REDEEM_CODE_GROUP_SIZE:
            return ""
        if any(character not in REDEEM_CODE_ALPHABET for character in compact):
            return ""
        return "-".join(
            compact[index : index + REDEEM_CODE_GROUP_SIZE]
            for index in range(0, len(compact), REDEEM_CODE_GROUP_SIZE)
        )

    @staticmethod
    def _shop_key(name: str) -> str:
        return str(name or "").strip().lower()

    @staticmethod
    def _account_from_mapping(balances: dict[str, Any], user_id: int) -> dict[str, int]:
        account = balances.get(str(user_id), {})
        return {
            CASH: int(account.get(CASH, 0)),
        }

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
    def _format_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts)

    @staticmethod
    def _format_utc_datetime(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _format_claim_interval(claim_type: str, cooldown_seconds: int) -> str:
        if int(cooldown_seconds) <= 0:
            return "with no cooldown"
        if claim_type in CALENDAR_CLAIM_TYPES:
            return f"once per {CALENDAR_CLAIM_PERIODS[claim_type]}"
        return f"every {Economy._format_duration(cooldown_seconds)}"

    @staticmethod
    def _next_calendar_claim_timestamp(claim_type: str, claimed_at: int) -> int:
        if int(claimed_at) <= 0:
            return 0

        claimed = datetime.fromtimestamp(int(claimed_at), timezone.utc)
        return Economy._next_calendar_reset_timestamp(claim_type, claimed)

    @staticmethod
    def _next_calendar_reset_timestamp(claim_type: str, now: datetime) -> int:
        now = now.astimezone(timezone.utc)
        if claim_type == "daily":
            next_reset = (now + timedelta(days=1)).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        elif claim_type == "weekly":
            week_start = (now - timedelta(days=now.weekday())).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            next_reset = week_start + timedelta(days=7)
        elif claim_type == "monthly":
            year = now.year + int(now.month == 12)
            month = 1 if now.month == 12 else now.month + 1
            next_reset = datetime(year, month, 1, tzinfo=timezone.utc)
        elif claim_type == "annual":
            next_reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_reset = now

        return int(next_reset.timestamp())

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


class EconomyError(Exception):
    pass


class EconomyCodeNotFound(EconomyError):
    pass


class EconomyCodeRedeemed(EconomyError):
    pass
