from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Any

import discord
from redbot.core import app_commands, commands

from . import sinks
from .constants import (
    CASH,
    CURRENCY_NAME,
    GOLD,
    MAX_OPEN_AUCTIONS,
    MAX_OPEN_BOUNTIES,
    MAX_SHOUTOUT_LENGTH,
    MAX_TASK_LENGTH,
    SINK_LOOP_SECONDS,
)
from .errors import EconomyError
from .layouts import NO_PINGS, send_embed, send_text
from .shop import ShopMixin

log = logging.getLogger("red.neufox.economy")

MESSAGE_LINK = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")
MAX_TRACKED_CHATTERS = 100


def _is_manager(member: discord.abc.User) -> bool:
    return isinstance(member, discord.Member) and member.guild_permissions.manage_guild


class SocialMixin:
    """Rain, bounties, auctions, paid shoutouts and paid pins, plus the sink loop."""

    # Activity tracking for rain -------------------------------------------------

    @commands.Cog.listener("on_message")
    async def _track_chatter(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        recent = self._recent_chatters.setdefault(message.channel.id, OrderedDict())
        recent[message.author.id] = time.time()
        recent.move_to_end(message.author.id)
        while len(recent) > MAX_TRACKED_CHATTERS:
            recent.popitem(last=False)

    def _recent_active(self, channel_id: int, window_seconds: int, exclude: int) -> list[int]:
        cutoff = time.time() - window_seconds
        recent = self._recent_chatters.get(channel_id, {})
        return [user_id for user_id, seen in reversed(recent.items()) if seen >= cutoff and user_id != exclude]

    # Rain -----------------------------------------------------------------------

    @ShopMixin.shop.command(name="rain")
    @commands.cooldown(1, 30, commands.BucketType.user)
    @app_commands.describe(amount="Total LWD$ to share.", people="How many recent chatters (default: the server maximum).")
    async def shop_rain(self, ctx: commands.Context, amount: int, people: int | None = None):
        """Share LWD$ between people who chatted here recently. The house takes a cut."""
        settings = await self.config.guild(ctx.guild).all()
        if amount < int(settings["rain_min"]):
            ctx.command.reset_cooldown(ctx)
            await ctx.send(f"Rain at least {int(settings['rain_min']):,} {CURRENCY_NAME}.")
            return
        limit = max(1, int(settings["rain_max_people"]))
        people = limit if people is None else max(1, min(int(people), limit))
        window = max(1, int(settings["rain_window_minutes"])) * 60
        recipients = []
        for user_id in self._recent_active(ctx.channel.id, window, ctx.author.id):
            member = ctx.guild.get_member(user_id)
            if member is not None and not member.bot:
                recipients.append(user_id)
            if len(recipients) >= people:
                break
        try:
            plan = sinks.split_rain(amount, recipients, settings["house_cut_percent"])
            account = await self._pay_out(
                ctx.author.id, plan["payouts"], plan["burned"], tx_type="rain", guild_id=ctx.guild.id,
                reason=f"rain of {amount} to {len(recipients)}",
            )
        except (ValueError, EconomyError) as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return
        mentions = ", ".join(f"<@{user_id}>" for user_id in plan["payouts"])
        await send_text(
            ctx,
            f"🌧️ {ctx.author.mention} made it rain **{amount:,} {CURRENCY_NAME}**! {mentions} each got "
            f"**{plan['share']:,} {CURRENCY_NAME}**. House cut: {plan['burned']:,}. "
            f"Balance: {account[CASH]:,} {CURRENCY_NAME}.",
            color=GOLD,
        )

    # Bounties -------------------------------------------------------------------

    @ShopMixin.shop.group(name="bounty", fallback="list")
    async def shop_bounty(self, ctx: commands.Context):
        """Show open bounties in this server."""
        await send_embed(ctx, await self._bounty_embed(ctx.guild))

    async def _bounty_embed(self, guild: discord.Guild) -> discord.Embed:
        bounties = await self.config.guild(guild).bounties()
        lines = []
        for bounty in sorted(bounties.values(), key=lambda entry: -int(entry["amount"]))[:15]:
            lines.append(
                f"`#{bounty['id']}` **{int(bounty['amount']):,} {CURRENCY_NAME}** — {bounty['task']} "
                f"(by <@{bounty['poster']}>, ends <t:{int(bounty['expires'])}:R>)"
            )
        embed = discord.Embed(title="Bounty board", description="\n".join(lines) or "No open bounties.", color=GOLD)
        embed.set_footer(text="/shop bounty post · fund · award · cancel")
        return embed

    @shop_bounty.command(name="post")
    @app_commands.describe(amount="LWD$ to put up.", task="What needs doing (a message link works too).")
    async def shop_bounty_post(self, ctx: commands.Context, amount: int, *, task: str):
        """Put up LWD$ for a task. You choose who earns it."""
        task = sinks.clean_text(task, MAX_TASK_LENGTH)
        if not task:
            await ctx.send("Describe the task.")
            return
        days = max(1, int(await self.config.guild(ctx.guild).bounty_days()))
        try:
            async with self._social_lock:
                bounties = await self.config.guild(ctx.guild).bounties()
                if len(bounties) >= MAX_OPEN_BOUNTIES:
                    raise EconomyError("This server has too many open bounties.")
                await self._charge(ctx.author.id, amount, tx_type="bounty_escrow", guild_id=ctx.guild.id,
                                   reason=f"bounty: {task}")
                bounty_id = int(await self.config.guild(ctx.guild).next_bounty())
                await self.config.guild(ctx.guild).next_bounty.set(bounty_id + 1)
                now = int(time.time())
                async with self.config.guild(ctx.guild).bounties() as stored:
                    stored[str(bounty_id)] = {
                        "id": bounty_id, "poster": ctx.author.id, "task": task, "amount": int(amount),
                        "contributors": {str(ctx.author.id): int(amount)}, "created": now,
                        "expires": now + days * sinks.DAY, "channel_id": ctx.channel.id,
                    }
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await send_text(ctx, f"Bounty `#{bounty_id}` is up: **{int(amount):,} {CURRENCY_NAME}** for {task}. "
                             f"It ends <t:{now + days * sinks.DAY}:R>.", color=GOLD)

    @shop_bounty.command(name="fund")
    @app_commands.describe(bounty_id="The bounty number.", amount="LWD$ to add.")
    async def shop_bounty_fund(self, ctx: commands.Context, bounty_id: int, amount: int):
        """Add LWD$ to someone's bounty."""
        try:
            async with self._social_lock:
                bounty = (await self.config.guild(ctx.guild).bounties()).get(str(bounty_id))
                if not bounty:
                    raise EconomyError("That bounty does not exist.")
                await self._charge(ctx.author.id, amount, tx_type="bounty_escrow", guild_id=ctx.guild.id,
                                   reason=f"fund bounty #{bounty_id}")
                async with self.config.guild(ctx.guild).bounties() as stored:
                    entry = stored[str(bounty_id)]
                    entry["amount"] = int(entry["amount"]) + int(amount)
                    key = str(ctx.author.id)
                    entry["contributors"][key] = int(entry["contributors"].get(key, 0)) + int(amount)
                    total = entry["amount"]
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Bounty `#{bounty_id}` is now worth **{total:,} {CURRENCY_NAME}**.")

    @shop_bounty.command(name="award")
    @app_commands.describe(bounty_id="The bounty number.", member="Who earned it.")
    async def shop_bounty_award(self, ctx: commands.Context, bounty_id: int, member: discord.Member):
        """Pay a bounty to whoever completed it. The house takes a cut."""
        if member.bot:
            await ctx.send("Bots cannot earn bounties.")
            return
        try:
            async with self._social_lock:
                bounty = (await self.config.guild(ctx.guild).bounties()).get(str(bounty_id))
                if not bounty:
                    raise EconomyError("That bounty does not exist.")
                if int(bounty["poster"]) != ctx.author.id and not _is_manager(ctx.author):
                    raise EconomyError("Only the bounty's poster or a server manager can award it.")
                if member.id == int(bounty["poster"]):
                    raise EconomyError("The poster cannot claim their own bounty.")
                amount = int(bounty["amount"])
                cut = sinks.house_cut(amount, await self.config.guild(ctx.guild).house_cut_percent())
                async with self.config.guild(ctx.guild).bounties() as stored:
                    stored.pop(str(bounty_id), None)
                await self._pay_out(None, {member.id: amount - cut}, cut, tx_type="bounty", guild_id=ctx.guild.id,
                                    reason=f"bounty #{bounty_id}")
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await send_text(ctx, f"{member.mention} earned bounty `#{bounty_id}`: **{amount - cut:,} {CURRENCY_NAME}** "
                             f"(house cut {cut:,}).", color=GOLD)

    @shop_bounty.command(name="cancel")
    @app_commands.describe(bounty_id="The bounty number.")
    async def shop_bounty_cancel(self, ctx: commands.Context, bounty_id: int):
        """Cancel a bounty and refund everyone who funded it."""
        try:
            async with self._social_lock:
                bounty = (await self.config.guild(ctx.guild).bounties()).get(str(bounty_id))
                if not bounty:
                    raise EconomyError("That bounty does not exist.")
                if int(bounty["poster"]) != ctx.author.id and not _is_manager(ctx.author):
                    raise EconomyError("Only the bounty's poster or a server manager can cancel it.")
                await self._close_bounty(ctx.guild, bounty, "cancelled")
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Bounty `#{bounty_id}` was cancelled and refunded.")

    async def _close_bounty(self, guild: discord.Guild, bounty: dict[str, Any], why: str):
        async with self.config.guild(guild).bounties() as stored:
            stored.pop(str(bounty["id"]), None)
        refunds = {int(user_id): int(amount) for user_id, amount in bounty["contributors"].items()}
        await self._pay_out(None, refunds, 0, tx_type="bounty_refund", guild_id=guild.id,
                            reason=f"bounty #{bounty['id']} {why}")

    # Auctions -------------------------------------------------------------------

    @ShopMixin.shop.group(name="auction", fallback="list")
    async def shop_auction(self, ctx: commands.Context):
        """Show running auctions in this server."""
        await send_embed(ctx, await self._auction_embed(ctx.guild))

    async def _auction_embed(self, guild: discord.Guild) -> discord.Embed:
        auctions = await self.config.guild(guild).auctions()
        lines = []
        for auction in sorted(auctions.values(), key=lambda entry: int(entry["ends"]))[:15]:
            if auction.get("top_bidder"):
                bid = f"top bid **{int(auction['top_bid']):,}** by <@{auction['top_bidder']}>"
            else:
                bid = f"starts at **{int(auction['start']):,}**"
            seller = f"<@{auction['seller']}>" if auction.get("seller") else "the house"
            lines.append(
                f"`#{auction['id']}` {int(auction['quantity']):,}x **{auction['item']}** from {seller} — {bid}, "
                f"ends <t:{int(auction['ends'])}:R>"
            )
        embed = discord.Embed(title="Auctions", description="\n".join(lines) or "No running auctions.", color=GOLD)
        embed.set_footer(text="/shop auction sell · bid · cancel")
        return embed

    @shop_auction.command(name="sell")
    @app_commands.describe(
        item="An item from your inventory.",
        starting_bid="Lowest acceptable bid.",
        hours="How long the auction runs.",
        quantity="How many to sell.",
        house="Managers only: sell from the shop catalogue; all proceeds are burned.",
    )
    async def shop_auction_sell(
        self, ctx: commands.Context, item: str, starting_bid: int, hours: int = 24, quantity: int = 1,
        house: bool = False,
    ):
        """Auction inventory items. The winner pays you, minus the house cut."""
        settings = await self.config.guild(ctx.guild).all()
        try:
            if not settings["auctions_enabled"]:
                raise EconomyError("Auctions are disabled in this server.")
            if hours < 1 or hours > int(settings["auction_max_hours"]):
                raise EconomyError(f"Auctions run 1 to {int(settings['auction_max_hours'])} hours.")
            if quantity < 1 or quantity > 1000:
                raise EconomyError("Quantity must be 1 to 1,000.")
            starting_bid = self._require_amount(starting_bid, allow_zero=False)
            if house and not _is_manager(ctx.author):
                raise EconomyError("Only server managers can run house auctions.")
            async with self._social_lock:
                if len(await self.config.guild(ctx.guild).auctions()) >= MAX_OPEN_AUCTIONS:
                    raise EconomyError("This server has too many running auctions.")
                shops = await self.config.shops()
                key, shop_item = self._find_shop_item(shops.get(str(ctx.guild.id), {}), item)
                if shop_item is None:
                    raise EconomyError("That item is not in this server's shop.")
                if not self._item_is_giftable(shop_item) or shop_item.get("redeem_code_enabled"):
                    raise EconomyError("Only giftable items without redeem codes can be auctioned.")
                name = str(shop_item.get("name", key))
                if not house:
                    await self._move_inventory(ctx.guild.id, ctx.author.id, None, name, quantity)
                auction_id = int(await self.config.guild(ctx.guild).next_auction())
                await self.config.guild(ctx.guild).next_auction.set(auction_id + 1)
                ends = int(time.time()) + hours * 3600
                async with self.config.guild(ctx.guild).auctions() as auctions:
                    auctions[str(auction_id)] = {
                        "id": auction_id, "seller": None if house else ctx.author.id, "item": name,
                        "quantity": quantity, "start": starting_bid, "top_bid": 0, "top_bidder": None,
                        "ends": ends, "channel_id": ctx.channel.id,
                    }
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await send_text(ctx, f"Auction `#{auction_id}`: {quantity:,}x **{name}** starting at "
                             f"{starting_bid:,} {CURRENCY_NAME}, ends <t:{ends}:R>. Bid with `/shop auction bid`.",
                        color=GOLD)

    @shop_auction.command(name="bid")
    @app_commands.describe(auction_id="The auction number.", amount="Your bid; it is held until you are outbid.")
    async def shop_auction_bid(self, ctx: commands.Context, auction_id: int, amount: int):
        """Bid on an auction. Your bid is held and refunded if someone outbids you."""
        try:
            async with self._social_lock:
                auction = (await self.config.guild(ctx.guild).auctions()).get(str(auction_id))
                if not auction or int(auction["ends"]) <= int(time.time()):
                    raise EconomyError("That auction is not running.")
                if auction.get("seller") == ctx.author.id:
                    raise EconomyError("You cannot bid on your own auction.")
                if auction.get("top_bidder") == ctx.author.id:
                    raise EconomyError("You already hold the top bid.")
                minimum = sinks.min_next_bid(auction["start"], auction["top_bid"], bool(auction.get("top_bidder")))
                if amount < minimum:
                    raise EconomyError(f"Bid at least {minimum:,} {CURRENCY_NAME}.")
                await self._charge(ctx.author.id, amount, tx_type="auction_bid", guild_id=ctx.guild.id,
                                   reason=f"bid on auction #{auction_id}", log_to_channel=False)
                previous, previous_bid = auction.get("top_bidder"), int(auction["top_bid"])
                async with self.config.guild(ctx.guild).auctions() as auctions:
                    auctions[str(auction_id)]["top_bid"] = int(amount)
                    auctions[str(auction_id)]["top_bidder"] = ctx.author.id
                if previous:
                    await self._credit(int(previous), previous_bid, tx_type="auction_refund", guild_id=ctx.guild.id,
                                       reason=f"outbid on auction #{auction_id}", log_to_channel=False)
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"You lead auction `#{auction_id}` with **{amount:,} {CURRENCY_NAME}**.")

    @shop_auction.command(name="cancel")
    @app_commands.describe(auction_id="The auction number.")
    async def shop_auction_cancel(self, ctx: commands.Context, auction_id: int):
        """Cancel your auction before anyone bids. Managers can cancel any auction."""
        try:
            async with self._social_lock:
                auction = (await self.config.guild(ctx.guild).auctions()).get(str(auction_id))
                if not auction:
                    raise EconomyError("That auction does not exist.")
                manager = _is_manager(ctx.author)
                if auction.get("seller") != ctx.author.id and not manager:
                    raise EconomyError("Only the seller or a server manager can cancel it.")
                if auction.get("top_bidder") and not manager:
                    raise EconomyError("Someone has already bid. Only a server manager can cancel it now.")
                await self._close_auction(ctx.guild, auction, cancelled=True)
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Auction `#{auction_id}` was cancelled; items and bids were returned.")

    async def _close_auction(self, guild: discord.Guild, auction: dict[str, Any], *, cancelled: bool) -> str:
        async with self.config.guild(guild).auctions() as auctions:
            auctions.pop(str(auction["id"]), None)
        seller = auction.get("seller")
        bidder = auction.get("top_bidder")
        bid = int(auction.get("top_bid", 0))
        item, quantity = auction["item"], int(auction["quantity"])
        if bidder and not cancelled:
            await self._move_inventory(guild.id, None, int(bidder), item, quantity)
            cut = bid if seller is None else sinks.house_cut(bid, await self.config.guild(guild).house_cut_percent())
            payouts = {int(seller): bid - cut} if seller else {}
            await self._pay_out(None, payouts, cut, tx_type="auction_sale", guild_id=guild.id,
                                reason=f"auction #{auction['id']} {quantity}x {item}")
            return f"Auction `#{auction['id']}` sold {quantity:,}x **{item}** to <@{bidder}> for {bid:,} {CURRENCY_NAME}."
        if bidder:
            await self._credit(int(bidder), bid, tx_type="auction_refund", guild_id=guild.id,
                               reason=f"auction #{auction['id']} cancelled", log_to_channel=False)
        if seller:
            await self._move_inventory(guild.id, None, int(seller), item, quantity)
        return f"Auction `#{auction['id']}` for **{item}** ended without a sale."

    async def _move_inventory(self, guild_id: int, from_id: int | None, to_id: int | None, item: str, quantity: int):
        """Move items between inventories, or into/out of auction escrow when one side is None."""
        async with self._lock:
            async with self.config.inventories() as inventories:
                guild_inventory = inventories.setdefault(str(guild_id), {})
                if from_id is not None:
                    source = guild_inventory.setdefault(str(from_id), {})
                    held = int(source.get(item, 0))
                    if held < quantity:
                        raise EconomyError(f"You do not have {quantity:,}x **{item}**.")
                    if held - quantity:
                        source[item] = held - quantity
                    else:
                        source.pop(item, None)
                    if not source:
                        guild_inventory.pop(str(from_id), None)
                if to_id is not None:
                    target = guild_inventory.setdefault(str(to_id), {})
                    target[item] = int(target.get(item, 0)) + quantity

    # Shoutouts and pins ---------------------------------------------------------

    @ShopMixin.shop.command(name="shoutout")
    @commands.cooldown(1, 300, commands.BucketType.member)
    @app_commands.describe(message="What the bot should post in the shoutout channel.")
    async def shop_shoutout(self, ctx: commands.Context, *, message: str):
        """Pay to have the bot post your message in the shoutout channel."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(int(settings["shoutout_channel"] or 0))
        price = int(settings["shoutout_price"])
        text = sinks.clean_text(message, MAX_SHOUTOUT_LENGTH, single_line=False)
        try:
            if not isinstance(channel, (discord.TextChannel, discord.Thread)) or price <= 0:
                raise EconomyError("Shoutouts are not set up in this server.")
            if not text:
                raise EconomyError("Write a message to shout out.")
            account = await self._charge(ctx.author.id, price, tx_type="shoutout", guild_id=ctx.guild.id,
                                         reason="paid shoutout")
        except EconomyError as error:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(str(error))
            return
        embed = discord.Embed(description=text, color=GOLD)
        embed.set_author(name=f"Shoutout from {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        try:
            await channel.send(embed=embed, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            await self._credit(ctx.author.id, price, tx_type="refund", guild_id=ctx.guild.id, reason="shoutout failed")
            await ctx.send("I could not post there, so you were refunded.")
            return
        await ctx.send(f"Shoutout posted in {channel.mention}. Balance: {account[CASH]:,} {CURRENCY_NAME}.")

    @ShopMixin.shop.command(name="pin")
    @app_commands.describe(message="A link to (or the ID of) your own message.")
    async def shop_pin(self, ctx: commands.Context, message: str):
        """Pay to pin one of your messages for a while in an allowed channel."""
        settings = await self.config.guild(ctx.guild).all()
        price, hours = int(settings["pin_price"]), max(1, int(settings["pin_hours"]))
        allowed = {int(channel_id) for channel_id in settings["pin_channels"]}
        try:
            if not allowed or price <= 0:
                raise EconomyError("Paid pins are not set up in this server.")
            target = await self._resolve_message(ctx, message)
            if target.channel.id not in allowed:
                raise EconomyError("Paid pins are not allowed in that channel.")
            if target.author.id != ctx.author.id:
                raise EconomyError("You can only pin your own messages.")
            if target.pinned:
                raise EconomyError("That message is already pinned.")
            account = await self._charge(ctx.author.id, price, tx_type="pin", guild_id=ctx.guild.id, reason="paid pin")
        except EconomyError as error:
            await ctx.send(str(error))
            return
        try:
            await target.pin(reason=f"Paid pin by {ctx.author}")
        except discord.HTTPException:
            await self._credit(ctx.author.id, price, tx_type="refund", guild_id=ctx.guild.id, reason="pin failed")
            await ctx.send("I could not pin that (missing permission or too many pins), so you were refunded.")
            return
        until = int(time.time()) + hours * 3600
        async with self.config.guild(ctx.guild).pins() as pins:
            pins[str(target.id)] = {"channel_id": target.channel.id, "until": until, "user_id": ctx.author.id}
        await ctx.send(f"Pinned until <t:{until}:R>. Balance: {account[CASH]:,} {CURRENCY_NAME}.")

    async def _resolve_message(self, ctx: commands.Context, text: str) -> discord.Message:
        text = str(text).strip()
        match = MESSAGE_LINK.search(text)
        if match:
            guild_id, channel_id, message_id = (int(part) for part in match.groups())
            if guild_id != ctx.guild.id:
                raise EconomyError("That message is in another server.")
            channel = ctx.guild.get_channel_or_thread(channel_id)
        elif text.isdigit():
            channel, message_id = ctx.channel, int(text)
        else:
            raise EconomyError("Give a message link or ID.")
        if channel is None or not hasattr(channel, "fetch_message"):
            raise EconomyError("I cannot find that channel.")
        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException:
            raise EconomyError("I cannot find that message.") from None

    # Background upkeep ----------------------------------------------------------

    async def _sinks_loop(self):
        await self.bot.wait_until_ready()
        while True:
            now = int(time.time())
            for job in (self._process_role_upkeep, self._process_social_expiry, self._process_wealth_tax):
                try:
                    await job(now)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Economy upkeep job %s failed", job.__name__)
            await asyncio.sleep(SINK_LOOP_SECONDS)

    async def _process_social_expiry(self, now: int):
        """End auctions, refund expired bounties and remove paid pins whose time is up."""
        for guild_id, data in (await self.config.all_guilds()).items():
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            for auction in list((data.get("auctions") or {}).values()):
                if sinks.is_expired(auction["ends"], now):
                    async with self._social_lock:
                        current = (await self.config.guild(guild).auctions()).get(str(auction["id"]))
                        summary = await self._close_auction(guild, current, cancelled=False) if current else None
                    if summary:
                        await self._announce(guild, auction.get("channel_id"), summary)
            for bounty in list((data.get("bounties") or {}).values()):
                if sinks.is_expired(bounty["expires"], now):
                    async with self._social_lock:
                        current = (await self.config.guild(guild).bounties()).get(str(bounty["id"]))
                        if current:
                            await self._close_bounty(guild, current, "expired")
                    await self._announce(guild, bounty.get("channel_id"),
                                         f"Bounty `#{bounty['id']}` expired and was refunded.")
            for message_id, pin in list((data.get("pins") or {}).items()):
                if sinks.is_expired(pin["until"], now):
                    await self._unpin(guild, int(message_id), pin)

    async def _unpin(self, guild: discord.Guild, message_id: int, pin: dict[str, Any]):
        async with self.config.guild(guild).pins() as pins:
            pins.pop(str(message_id), None)
        channel = guild.get_channel_or_thread(int(pin.get("channel_id", 0)))
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.unpin(reason="Paid pin ended")
        except discord.HTTPException:
            pass

    async def _announce(self, guild: discord.Guild, channel_id: Any, text: str):
        channel = guild.get_channel_or_thread(int(channel_id or 0))
        if channel is None:
            return
        try:
            await channel.send(text, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            pass

    async def _process_wealth_tax(self, now: int):
        """Once per UTC day, tax balances above the threshold. Off unless the owner sets a rate."""
        percent = float(await self.config.wealth_tax_percent())
        today = sinks.utc_day(now)
        if percent <= 0 or int(await self.config.wealth_tax_day()) == today:
            return
        await self.config.wealth_tax_day.set(today)
        threshold = int(await self.config.wealth_tax_threshold())
        cap = int(await self.config.wealth_tax_cap())
        charged = []
        async with self._lock:
            async with self.config.balances() as balances:
                for user_id in list(balances):
                    account = self._account_from_mapping(balances, int(user_id))
                    tax = sinks.wealth_tax(account[CASH], threshold, percent, cap)
                    if tax > 0:
                        account[CASH] -= tax
                        balances[user_id] = account
                        charged.append((int(user_id), tax))
            for user_id, tax in charged:
                await self._append_ledger(
                    "wealth_tax", from_user_id=user_id, to_user_id=None, amount=tax, actor_id=None,
                    guild_id=None, reason=f"daily wealth tax above {threshold}", log_to_channel=False,
                )
        if charged:
            log.info("Wealth tax charged %s balance(s)", len(charged))

    async def _market_embed(self, guild: discord.Guild) -> discord.Embed:
        auctions = await self._auction_embed(guild)
        bounties = await self._bounty_embed(guild)
        settings = await self.config.guild(guild).all()
        embed = discord.Embed(title="Market", color=GOLD)
        embed.add_field(name="Auctions", value=auctions.description[:1000], inline=False)
        embed.add_field(name="Bounties", value=bounties.description[:1000], inline=False)
        extras = [
            f"`/shop rain <amount>` — share with up to {int(settings['rain_max_people'])} recent chatters",
            f"House cut on rain, bounties and sales: {float(settings['house_cut_percent']):g}%",
        ]
        if settings["shoutout_channel"] and int(settings["shoutout_price"]) > 0:
            extras.append(f"`/shop shoutout` — {int(settings['shoutout_price']):,} {CURRENCY_NAME} "
                          f"in <#{settings['shoutout_channel']}>")
        if settings["pin_channels"] and int(settings["pin_price"]) > 0:
            extras.append(f"`/shop pin` — {int(settings['pin_price']):,} {CURRENCY_NAME} for "
                          f"{int(settings['pin_hours'])}h")
        embed.add_field(name="Spend", value="\n".join(extras), inline=False)
        return embed
