from __future__ import annotations

import logging
import secrets
import time
from typing import Any

import discord
from redbot.core import commands

from . import nightlife
from .constants import (
    CASH,
    CASINO_GAMES,
    CURRENCY_NAME,
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    MAX_AMOUNT,
    MAX_BOOSTER_HOURS,
)
from .errors import EconomyError
from .layouts import send_pages
from .lwdmillions import LWD_MILLIONS_PRIZE_MULTIPLIERS, drand_round_timestamp
from .slots import SLOT_TRIPLE_MULTIPLIERS

log = logging.getLogger("red.neufox.economy")

NIGHTLIFE_PRICE_LIMIT = 10**12


class AdminMixin:
    """Control panel settings and the owner's ecoadmin commands."""

    def control_panel_schema(self) -> dict[str, Any]:
        def amount_check(label: str, maximum: int = MAX_AMOUNT, *, at_most=None, at_least=None):
            async def check(guild: discord.Guild | None, value: int) -> str | None:
                if value > maximum:
                    return f"{label} cannot exceed {maximum:,}."
                if at_most is not None and value > int(await at_most()):
                    return f"{label} cannot be above the current maximum."
                if at_least is not None and value < int(await at_least()):
                    return f"{label} cannot be below the current minimum."
                return None

            return check

        def owner_setting(section: str, **fields: Any) -> dict[str, Any]:
            return {"section": section, "scope": "global", **fields}

        def claim_settings(name: str) -> list[dict[str, Any]]:
            return [
                owner_setting(
                    "Claims",
                    key=f"{name}_amount",
                    label=f"{name.title()} amount",
                    type="int",
                    min=0,
                    unit=CURRENCY_NAME,
                    validate=amount_check("Amount"),
                ),
                owner_setting(
                    "Claims",
                    key=f"{name}_cooldown",
                    label=f"{name.title()} cooldown",
                    type="int",
                    min=0,
                    unit="seconds",
                    help="Any positive value resets on the calendar period. 0 disables this claim.",
                ),
            ]

        async def lwdmillions_value(key: str) -> Any:
            return (await self._lwdmillions_state_snapshot())[key]

        def lwdmillions_setting(key: str, **fields: Any) -> dict[str, Any]:
            async def getter(guild: discord.Guild) -> Any:
                return await lwdmillions_value(key)

            async def setter(guild: discord.Guild, value: Any):
                await self._set_lwdmillions_setting(key, value)

            return owner_setting("LWDMillions", key=f"lwdmillions.{key}", get=getter, set=setter, **fields)

        async def set_work_max(guild: discord.Guild, value: int):
            await self.config.work_max.set(value)
            await self.config.work_amount.set(value)

        async def get_nightlife(guild: discord.Guild) -> bool:
            return guild.id not in await self.config.nightlife_disabled_guilds()

        async def set_nightlife(guild: discord.Guild, enabled: bool):
            await self._set_nightlife_enabled(guild.id, enabled)

        async def get_log_channel(guild: discord.Guild) -> int | None:
            return (await self.config.log_channels()).get(str(guild.id))

        async def set_log_channel(guild: discord.Guild, channel_id: int | None):
            await self._set_log_channel(guild.id, channel_id)

        async def get_lottery_channel(guild: discord.Guild) -> int | None:
            return (await lwdmillions_value("announcement_channels")).get(str(guild.id))

        async def set_lottery_channel(guild: discord.Guild, channel_id: int | None):
            await self._set_lwdmillions_channel(guild.id, channel_id)

        def server_setting(section: str, key: str, label: str, kind: str, **fields: Any) -> dict[str, Any]:
            return {"section": section, "key": key, "label": label, "type": kind, "access": "admin", **fields}

        def price_setting(section: str, key: str, label: str) -> dict[str, Any]:
            return server_setting(
                section, key, label, "int", min=0, unit=CURRENCY_NAME, help="0 stops sales.",
                validate=amount_check(label),
            )

        def booster_settings(kind: str, label: str, effect: str) -> list[dict[str, Any]]:
            return [
                owner_setting("Boosters", key=f"{kind}_booster_price", label=f"{label} price", type="int", min=0,
                              unit=CURRENCY_NAME, help="0 stops sales.", validate=amount_check("Price")),
                owner_setting("Boosters", key=f"{kind}_booster_hours", label=f"{label} length", type="int",
                              min=1, max=MAX_BOOSTER_HOURS, unit="hours"),
                owner_setting("Boosters", key=f"{kind}_booster_multiplier", label=f"{label} multiplier",
                              type="float", min=1, max=10, unit="x", help=f"Multiplies {effect}."),
            ]

        def global_game_toggle(game: str, name: str) -> dict[str, Any]:
            return owner_setting("Casino games", key=f"casino_{game}_enabled", label=name, type="bool",
                                 help="Applies to every server.")

        def server_game_toggle(game: str, name: str) -> dict[str, Any]:
            return server_setting("Casino in this server", f"casino_{game}_allowed", name, "bool",
                                  help="Only this server. The bot owner can also close it everywhere.")

        def nightlife_price_setting(section: str, price_key: str, label: str) -> dict[str, Any]:
            default = nightlife.DEFAULT_PRICES[price_key]

            async def getter(guild: discord.Guild) -> int:
                return nightlife.price_of(price_key, await self.config.nightlife_prices())

            async def setter(guild: discord.Guild, value: int):
                async with self.config.nightlife_prices() as prices:
                    if int(value) == default:
                        prices.pop(price_key, None)
                    else:
                        prices[price_key] = int(value)

            return owner_setting(section, key=f"nightlife_price.{price_key}", label=label, type="int", min=0,
                                 max=NIGHTLIFE_PRICE_LIMIT, unit=CURRENCY_NAME, help=f"Default {default:,}.",
                                 get=getter, set=setter)

        nightlife_shop = [
            *(nightlife_price_setting("Nightlife prices", f"escort.{key}", f"Escort: {escort['name']}")
              for key, escort in nightlife.ESCORTS.items()),
            *(nightlife_price_setting("Nightlife prices", f"venue.{key}", f"Venue: {key.title()}")
              for key in nightlife.VENUES),
            *(nightlife_price_setting("Nightlife prices", f"item.{key}", f"Item: {key.title()}")
              for key in nightlife.ITEMS),
        ]
        nightlife_clinic = [
            nightlife_price_setting("Nightlife clinic", "test", "Clinic test"),
            *(nightlife_price_setting("Nightlife clinic", f"treatment.{key}", f"Treat {disease['name']}")
              for key, disease in nightlife.DISEASES.items()),
        ]

        bet_limit = MAX_AMOUNT // max(SLOT_TRIPLE_MULTIPLIERS.values())
        ticket_limit = MAX_AMOUNT // max(LWD_MILLIONS_PRIZE_MULTIPLIERS.values())
        message_channels = ["text", "news"]
        return {
            "title": "Economy",
            "description": (
                "Claims, casino, lottery, boosters, taxes and Nightlife prices (bot owner); "
                "fees, perks, market and casino games in this server (server admins)."
            ),
            "settings": [
                *claim_settings("daily"),
                *claim_settings("weekly"),
                *claim_settings("monthly"),
                *claim_settings("annual"),
                owner_setting(
                    "Work",
                    key="work_min",
                    label="Work minimum",
                    type="int",
                    min=0,
                    unit=CURRENCY_NAME,
                    validate=amount_check("Work minimum", at_most=self.config.work_max),
                ),
                owner_setting(
                    "Work",
                    key="work_max",
                    label="Work maximum",
                    type="int",
                    min=0,
                    unit=CURRENCY_NAME,
                    validate=amount_check("Work maximum", at_least=self.config.work_min),
                    set=set_work_max,
                ),
                owner_setting("Work", key="work_cooldown", label="Work cooldown", type="int", min=0, unit="seconds"),
                owner_setting(
                    "Casino",
                    key="casino_enabled",
                    label="Casino games",
                    type="bool",
                    help="Applies to every server.",
                ),
                owner_setting(
                    "Casino",
                    key="casino_min_bet",
                    label="Minimum wager",
                    type="int",
                    min=1,
                    unit=CURRENCY_NAME,
                    validate=amount_check("Minimum wager", bet_limit, at_most=self.config.casino_max_bet),
                ),
                owner_setting(
                    "Casino",
                    key="casino_max_bet",
                    label="Maximum wager",
                    type="int",
                    min=1,
                    unit=CURRENCY_NAME,
                    validate=amount_check("Maximum wager", bet_limit, at_least=self.config.casino_min_bet),
                ),
                lwdmillions_setting("enabled", label="Ticket sales", type="bool"),
                lwdmillions_setting(
                    "ticket_price",
                    label="Ticket price",
                    type="int",
                    min=1,
                    unit=CURRENCY_NAME,
                    help="Applies to newly purchased lines.",
                    validate=amount_check("Ticket price", ticket_limit),
                ),
                lwdmillions_setting(
                    "jackpot_contribution_percent",
                    label="Jackpot contribution",
                    type="int",
                    min=0,
                    max=100,
                    unit="%",
                    help="Share of each new line added to the jackpot.",
                ),
                lwdmillions_setting(
                    "seed_jackpot",
                    label="Seed jackpot",
                    type="int",
                    min=1,
                    unit=CURRENCY_NAME,
                    help="Jackpot used after a jackpot win.",
                    validate=amount_check("Seed jackpot"),
                ),
                owner_setting(
                    "LWDMillions", key="lwdmillions_max_lines", label="Lines per player", type="int", min=1,
                    max=100, help="Most lines one player can hold in a single draw.",
                ),
                owner_setting(
                    "LWDMillions", key="lwdmillions_max_tickets", label="Lines per draw", type="int", min=100,
                    max=50_000, help="Ticket sales for a draw stop at this many lines in total.",
                ),
                owner_setting(
                    "This server",
                    key="server.lottery_channel",
                    label="Lottery announcements",
                    type="channel",
                    channel_types=message_channels,
                    empty="Off",
                    help="Where draw results are posted in this server.",
                    get=get_lottery_channel,
                    set=set_lottery_channel,
                ),
                owner_setting(
                    "This server",
                    key="server.log_channel",
                    label="Economy log channel",
                    type="channel",
                    channel_types=message_channels,
                    empty="Off",
                    get=get_log_channel,
                    set=set_log_channel,
                ),
                owner_setting(
                    "This server",
                    key="server.nightlife",
                    label="Nightlife",
                    type="bool",
                    help="Allow the Nightlife game in this server.",
                    get=get_nightlife,
                    set=set_nightlife,
                ),
                owner_setting(
                    "Claims", key="streak_bonus_per_day", label="Streak bonus per day", type="int", min=0,
                    unit=CURRENCY_NAME, help="Extra LWD$ per consecutive daily claim after the first.",
                    validate=amount_check("Streak bonus"),
                ),
                owner_setting(
                    "Claims", key="streak_bonus_cap", label="Streak bonus cap", type="int", min=0,
                    unit=CURRENCY_NAME, help="Largest streak bonus. 0 means no cap.", validate=amount_check("Cap"),
                ),
                owner_setting(
                    "Casino", key="casino_rake_percent", label="House rake", type="float", min=0, max=20,
                    unit="%", help="Share of each winning casino result's profit that is burned.",
                ),
                *(global_game_toggle(game, name) for game, name in CASINO_GAMES.items()),
                owner_setting(
                    "Casino tables", key="casino_coinflip_payout_percent", label="Coin flip payout", type="int",
                    min=100, max=200, unit="%", help="Returned on a correct call, wager included. 200 is fair odds.",
                ),
                owner_setting(
                    "Casino tables", key="casino_dice_payout_percent", label="Dice payout", type="int",
                    min=100, max=600, unit="%", help="Returned on a correct guess, wager included. 600 is fair odds.",
                ),
                owner_setting(
                    "Casino tables", key="blackjack_decks", label="Blackjack decks", type="int", min=1, max=8,
                    unit="decks", help="Decks in each player's shoe. A new size applies on the next hand.",
                ),
                owner_setting(
                    "Casino tables", key="holdem_lobby_seconds", label="Hold'em lobby time", type="int", min=30,
                    max=900, unit="seconds", help="How long a table waits for players before refunding.",
                ),
                owner_setting(
                    "Casino tables", key="holdem_turn_seconds", label="Hold'em turn time", type="int", min=15,
                    max=600, unit="seconds", help="How long a player has to act before the table times out.",
                ),
                *booster_settings("claim", "Claim booster", "/claim payouts"),
                *booster_settings("xp", "XP booster", "Activity XP"),
                owner_setting(
                    "Boosters", key="streak_saver_price", label="Streak saver price", type="int", min=0,
                    unit=CURRENCY_NAME, help="Covers one missed daily claim. 0 stops sales.",
                    validate=amount_check("Price"),
                ),
                owner_setting(
                    "Boosters", key="streak_saver_max", label="Streak savers held", type="int", min=1, max=100,
                    help="Most streak savers one person can hold.",
                ),
                owner_setting(
                    "Wealth tax", key="wealth_tax_percent", label="Daily wealth tax", type="float", min=0, max=10,
                    unit="%", help="Charged daily on balances above the threshold. 0 turns it off.",
                ),
                owner_setting(
                    "Wealth tax", key="wealth_tax_threshold", label="Tax-free balance", type="int", min=0,
                    unit=CURRENCY_NAME, validate=amount_check("Threshold"),
                ),
                owner_setting(
                    "Wealth tax", key="wealth_tax_cap", label="Daily tax cap", type="int", min=0,
                    unit=CURRENCY_NAME, help="Most tax per person per day. 0 means no cap.",
                    validate=amount_check("Cap"),
                ),
                server_setting("Fees", "pay_tax_percent", "Transfer tax", "float", min=0, max=50, unit="%",
                               help="Burned on top of every /pay. Shown before the sender confirms."),
                server_setting("Fees", "house_cut_percent", "House cut", "float", min=0, max=50, unit="%",
                               help="Burned from rain, bounty payouts and auction sales."),
                server_setting("Perks", "perks_enabled", "Profile perks", "bool",
                               help="Titles, badges, colours and personal roles."),
                price_setting("Perks", "title_price", "Title price"),
                price_setting("Perks", "badge_price", "Badge price"),
                price_setting("Perks", "color_price", "Profile colour price"),
                server_setting("Perks", "custom_roles_enabled", "Personal roles", "bool",
                               help="Members buy a role with their own name and colour. Needs Manage Roles."),
                price_setting("Perks", "custom_role_price", "Personal role per week"),
                price_setting("Perks", "custom_role_edit_price", "Personal role change"),
                server_setting("Perks", "custom_role_anchor", "Place personal roles above", "role",
                               empty="Just under my top role"),
                server_setting("Perks", "rental_max_days", "Longest role rental", "int", min=1, max=365,
                               unit="days", help="Most days a member can rent a role for at once."),
                price_setting("Rain", "rain_min", "Smallest rain"),
                server_setting("Rain", "rain_max_people", "Rain recipients", "int", min=1, max=50),
                server_setting("Rain", "rain_window_minutes", "Rain activity window", "int", min=1, max=1440,
                               unit="minutes", help="Who counts as recently active."),
                server_setting("Shoutouts & pins", "shoutout_channel", "Shoutout channel", "channel",
                               channel_types=message_channels, empty="Off"),
                price_setting("Shoutouts & pins", "shoutout_price", "Shoutout price"),
                server_setting("Shoutouts & pins", "shoutout_max_length", "Longest shoutout", "int", min=20,
                               max=2000, unit="characters"),
                server_setting("Shoutouts & pins", "pin_channels", "Paid pin channels", "channels",
                               channel_types=message_channels, empty="Off"),
                price_setting("Shoutouts & pins", "pin_price", "Pin price"),
                server_setting("Shoutouts & pins", "pin_hours", "Pin duration", "int", min=1, max=720,
                               unit="hours"),
                server_setting("Auctions & bounties", "auctions_enabled", "Auctions", "bool"),
                server_setting("Auctions & bounties", "auction_max_hours", "Longest auction", "int", min=1,
                               max=336, unit="hours"),
                server_setting("Auctions & bounties", "auction_max_open", "Running auctions limit", "int", min=1,
                               max=100, help="Most auctions running in this server at once."),
                server_setting("Auctions & bounties", "bounty_days", "Bounty lifetime", "int", min=1, max=60,
                               unit="days", help="Unawarded bounties are refunded after this."),
                server_setting("Auctions & bounties", "bounty_max_open", "Open bounties limit", "int", min=1,
                               max=100, help="Most bounties open in this server at once."),
                *(server_game_toggle(game, name) for game, name in CASINO_GAMES.items()),
                *nightlife_shop,
                *nightlife_clinic,
            ],
        }

    @commands.group(name="ecoadmin", aliases=["economyadmin"])
    @commands.admin_or_permissions(manage_guild=True)
    async def ecoadmin(self, ctx: commands.Context):
        """Economy management that the /control panel cannot do.

        Most settings (claims, casino, lottery, taxes, perks and market prices) live in `/control`.
        """

    @ecoadmin.command(name="add")
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

    @ecoadmin.command(name="remove")
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

    @ecoadmin.command(name="set")
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

    @ecoadmin.command(name="jackpot", aliases=["pot"])
    @commands.is_owner()
    async def economy_admin_lwdmillions_jackpot(self, ctx: commands.Context, amount: int):
        """Set the current rolling LWDMillions jackpot."""
        if amount <= 0 or amount > MAX_AMOUNT:
            await ctx.send(f"Jackpot must be from 1 to {MAX_AMOUNT:,} {CURRENCY_NAME}.")
            return
        async with self._lock:
            async with self.config.lwdmillions() as state:
                self._normalize_lwdmillions_state(state)
                state["jackpot"] = int(amount)
        await ctx.send(f"The current LWDMillions jackpot is now {amount:,} {CURRENCY_NAME}.")

    @ecoadmin.command(name="draw")
    @commands.is_owner()
    @commands.max_concurrency(1, per=commands.BucketType.default, wait=False)
    async def economy_admin_lwdmillions_draw(
        self,
        ctx: commands.Context,
        confirmation: str = "",
    ):
        """Force-settle the LWDMillions draw once its committed entropy is available."""
        if confirmation.casefold() != "confirm":
            await ctx.send(
                "This closes the current draw and settles every ticket. A public-beacon draw "
                "can never "
                "settle before its locked public beacon exists. Run "
                f"`{ctx.clean_prefix}ecoadmin draw confirm` to continue."
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

    @ecoadmin.group(name="shop", invoke_without_command=True)
    @commands.is_owner()
    @commands.guild_only()
    async def ecoadmin_shop(self, ctx: commands.Context):
        """Owner-only shop item management."""
        await send_pages(ctx, await self._shop_embeds(ctx.guild, prefix=ctx.clean_prefix, panel=False))

    @ecoadmin_shop.command(name="add")
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

    @ecoadmin_shop.command(name="remove")
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

    @ecoadmin_shop.command(name="role")
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

    @ecoadmin_shop.command(name="code")
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

    @ecoadmin_shop.command(name="stock")
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

    @ecoadmin_shop.command(name="limit")
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

    @ecoadmin_shop.command(name="giftable")
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

    @ecoadmin_shop.command(name="channel")
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

    @ecoadmin_shop.command(name="post", aliases=["refresh"])
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

    @ecoadmin_shop.command(name="clearchannel")
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

    @ecoadmin.group(name="api", invoke_without_command=True)
    @commands.is_owner()
    async def ecoadmin_api(self, ctx: commands.Context):
        """Owner-only API management."""
        await ctx.invoke(self.economy_api_status)

    @ecoadmin_api.command(name="status")
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

    @ecoadmin_api.command(name="start")
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

    @ecoadmin_api.command(name="stop")
    @commands.is_owner()
    async def economy_api_stop(self, ctx: commands.Context):
        """Stop the HTTP API."""
        await self.config.api_enabled.set(False)
        await self._stop_api()
        await ctx.send("Economy API stopped.")

    @ecoadmin_api.group(name="token", invoke_without_command=True)
    @commands.is_owner()
    async def ecoadmin_api_token(self, ctx: commands.Context):
        """Manage API tokens."""
        await ctx.send_help()

    @ecoadmin_api_token.command(name="create")
    @commands.is_owner()
    async def economy_api_token_create(self, ctx: commands.Context, name: str):
        """Create or replace an API token."""
        token = secrets.token_urlsafe(32)
        async with self.config.api_tokens() as tokens:
            tokens[name] = token
        await ctx.send(f"Token `{name}` created. Copy it now:\n`{token}`")

    @ecoadmin_api_token.command(name="list")
    @commands.is_owner()
    async def economy_api_token_list(self, ctx: commands.Context):
        """List configured API token names."""
        tokens = await self.config.api_tokens()
        if not tokens:
            await ctx.send("No API tokens are configured.")
            return

        names = sorted(discord.utils.escape_markdown(name) for name in tokens)
        await ctx.send("Configured API token names:\n" + "\n".join(f"- {name}" for name in names))

    @ecoadmin_api_token.command(name="revoke")
    @commands.is_owner()
    async def economy_api_token_revoke(self, ctx: commands.Context, name: str):
        """Revoke an API token."""
        async with self.config.api_tokens() as tokens:
            existed = tokens.pop(name, None) is not None
        await ctx.send(f"Token `{name}` {'revoked' if existed else 'was not found'}.")

    @ecoadmin_api_token.command(name="revokeall", aliases=["clear", "revoke-all"])
    @commands.is_owner()
    async def economy_api_token_revoke_all(self, ctx: commands.Context, confirmation: str | None = None):
        """Revoke every API token."""
        if confirmation != "confirm":
            await ctx.send(
                "This revokes every API token. Run "
                f"`{ctx.clean_prefix}ecoadmin api token revokeall confirm` to continue."
            )
            return

        async with self.config.api_tokens() as tokens:
            count = len(tokens)
            tokens.clear()

        await ctx.send(f"Revoked {count} API token{'s' if count != 1 else ''}.")

    @ecoadmin.group(name="rentals", aliases=["rental"], invoke_without_command=True)
    @commands.guild_only()
    async def ecoadmin_rentals(self, ctx: commands.Context):
        """List roles members can rent with /shop rent."""
        rentable = await self.config.guild(ctx.guild).rentable_roles()
        lines = [
            f"<@&{role_id}> — {int(entry.get('price', 0)):,} {CURRENCY_NAME} per day"
            for role_id, entry in rentable.items()
        ]
        await ctx.send(
            "\n".join(lines) or f"No rentable roles. Add one with `{ctx.clean_prefix}ecoadmin rentals add <role> <price>`.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ecoadmin_rentals.command(name="add", aliases=["set"])
    async def ecoadmin_rentals_add(self, ctx: commands.Context, role: discord.Role, price_per_day: int):
        """Let members rent a role for a daily price."""
        if price_per_day <= 0 or price_per_day > MAX_AMOUNT // 30:
            await ctx.send("Price per day must be positive.")
            return
        me = ctx.guild.me
        if role.is_default() or role.managed or role >= me.top_role:
            await ctx.send("I cannot hand out that role. Pick a normal role below my highest role.")
            return
        if (
            not await self.bot.is_owner(ctx.author)
            and ctx.author != ctx.guild.owner
            and role >= ctx.author.top_role
        ):
            await ctx.send("You can only offer roles below your own highest role.")
            return
        async with self.config.guild(ctx.guild).rentable_roles() as rentable:
            rentable[str(role.id)] = {"price": int(price_per_day)}
        await ctx.send(
            f"{role.mention} can now be rented for {price_per_day:,} {CURRENCY_NAME} per day.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ecoadmin_rentals.command(name="remove", aliases=["delete"])
    async def ecoadmin_rentals_remove(self, ctx: commands.Context, role: discord.Role):
        """Stop renting out a role. Current renters keep it until their period ends."""
        async with self.config.guild(ctx.guild).rentable_roles() as rentable:
            removed = rentable.pop(str(role.id), None)
        await ctx.send(
            f"{role.mention} is no longer for rent." if removed else "That role was not for rent.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
