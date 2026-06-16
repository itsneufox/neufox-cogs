from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import secrets
import time
from typing import Any

from aiohttp import web
import discord
from redbot.core import Config, commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu


log = logging.getLogger("red.neufox.economy")

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
MAX_LEDGER_ENTRIES = 500
MAX_AMOUNT = 10**15
TOP_LIMIT = 10
SHOP_PAGE_SIZE = 8
REDEEM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REDEEM_CODE_GROUPS = 3
REDEEM_CODE_GROUP_SIZE = 4


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
            claims={},
            shops={},
            inventories={},
            redeem_codes={},
            shop_channels={},
            shop_messages={},
            log_channels={},
        )
        self._lock = asyncio.Lock()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._startup_task = self.bot.loop.create_task(self._start_api_if_enabled())

    def cog_unload(self):
        self._startup_task.cancel()
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
            description="Global LWD$ balances with claims, transfers, shop items, and API access.",
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
                    f"`{prefix}eco top` - show the leaderboard",
                    f"`{prefix}eco shop` - view the server shop",
                    f"`{prefix}eco buy <item> [quantity]` - buy a shop item",
                    f"`{prefix}eco gift <member> <item> [quantity]` - gift an inventory item",
                    f"`{prefix}eco inventory [member]` - show inventory",
                    f"`{prefix}eco codes` - DM your unredeemed in-game item codes",
                ]
            ),
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

    @economy.group(name="admin", invoke_without_command=True)
    @commands.is_owner()
    async def economy_admin(self, ctx: commands.Context):
        """Owner-only economy management."""
        await ctx.invoke(self.economy_admin_help)

    @economy_admin.command(name="help", aliases=["commands"])
    @commands.is_owner()
    async def economy_admin_help(self, ctx: commands.Context):
        """Show owner economy help."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Economy Admin Help",
            description="Owner-only balance, claim, shop, log, and API commands.",
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
                    f"`{prefix}eco admin claim daily <amount> [cooldown_seconds]`",
                    f"`{prefix}eco admin claim weekly <amount> [cooldown_seconds]`",
                    f"`{prefix}eco admin claim monthly <amount> [cooldown_seconds]`",
                    f"`{prefix}eco admin claim annual <amount> [cooldown_seconds]`",
                    f"`{prefix}eco admin claim work <amount> [cooldown_seconds]`",
                    f"`{prefix}eco admin claim workrange <min> <max> [cooldown_seconds]`",
                    f"`{prefix}eco admin logchannel [channel]`",
                    f"`{prefix}eco admin clearlog`",
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
                    f"`{prefix}eco api token create <name>`",
                    f"`{prefix}eco api token revoke <name>`",
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
            f"Daily: {daily_amount:,} {CURRENCY_NAME} every {self._format_duration(daily_cooldown)}\n"
            f"Weekly: {await self.config.weekly_amount():,} {CURRENCY_NAME} every {self._format_duration(await self.config.weekly_cooldown())}\n"
            f"Monthly: {await self.config.monthly_amount():,} {CURRENCY_NAME} every {self._format_duration(await self.config.monthly_cooldown())}\n"
            f"Annual: {await self.config.annual_amount():,} {CURRENCY_NAME} every {self._format_duration(await self.config.annual_cooldown())}\n"
            f"Work: {await self.config.work_min():,}-{await self.config.work_max():,} {CURRENCY_NAME} every {self._format_duration(work_cooldown)}"
        )

    @economy_admin_claim.command(name="daily")
    @commands.is_owner()
    async def economy_admin_claim_daily(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_DAILY_COOLDOWN):
        """Set daily amount and cooldown."""
        await self._set_claim_settings(ctx, "daily", amount, cooldown_seconds)

    @economy_admin_claim.command(name="weekly")
    @commands.is_owner()
    async def economy_admin_claim_weekly(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_WEEKLY_COOLDOWN):
        """Set weekly amount and cooldown."""
        await self._set_claim_settings(ctx, "weekly", amount, cooldown_seconds)

    @economy_admin_claim.command(name="monthly")
    @commands.is_owner()
    async def economy_admin_claim_monthly(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_MONTHLY_COOLDOWN):
        """Set monthly amount and cooldown."""
        await self._set_claim_settings(ctx, "monthly", amount, cooldown_seconds)

    @economy_admin_claim.command(name="annual", aliases=["yearly"])
    @commands.is_owner()
    async def economy_admin_claim_annual(self, ctx: commands.Context, amount: int, cooldown_seconds: int = DEFAULT_ANNUAL_COOLDOWN):
        """Set annual amount and cooldown."""
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

    @economy_api_token.command(name="revoke")
    @commands.is_owner()
    async def economy_api_token_revoke(self, ctx: commands.Context, name: str):
        """Revoke an API token."""
        async with self.config.api_tokens() as tokens:
            existed = tokens.pop(name, None) is not None
        await ctx.send(f"Token `{name}` {'revoked' if existed else 'was not found'}.")

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
            next_claim = last_claimed + cooldown
            if cooldown and now < next_claim:
                await ctx.send(f"You can claim `{claim_type}` again <t:{next_claim}:R>.")
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
            f"{claim_type.title()} claim set to {amount:,} {CURRENCY_NAME} every {self._format_duration(cooldown_seconds)}."
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
