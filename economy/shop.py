from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any

import discord
from redbot.core import app_commands, commands

from .constants import (
    CASH,
    CURRENCY_NAME,
    MAX_AMOUNT,
    REDEEM_CODE_ALPHABET,
    REDEEM_CODE_GROUPS,
    REDEEM_CODE_GROUP_SIZE,
    SHOP_PAGE_SIZE,
)
from .errors import EconomyCodeNotFound, EconomyCodeRedeemed, EconomyError
from .layouts import send_embed, send_pages

log = logging.getLogger("red.neufox.economy")


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
    def __init__(self, cog: Any, guild_id: int, item_key: str, item: dict[str, Any]):
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
    def __init__(self, cog: Any, guild_id: int, item_key: str, item: dict[str, Any]):
        super().__init__(timeout=None)
        self.add_item(ShopBuyButton(cog, guild_id, item_key, item))

class ShopMixin:
    """Server shop, inventory, gifts and redeem codes."""

    @commands.hybrid_group(name="shop", fallback="view")
    @commands.guild_only()
    async def shop(self, ctx: commands.Context):
        """Spend LWD$: the server shop, perks, rentals, auctions, bounties and more."""
        pages = await self._shop_embeds(ctx.guild, prefix=ctx.clean_prefix, panel=False)
        await send_pages(ctx, pages)

    @commands.command(name="buy")
    @commands.guild_only()
    async def buy_short(self, ctx: commands.Context, item_name: str, quantity: int = 1):
        """Buy an item from this server's shop."""
        await self.economy_buy(ctx, item_name, quantity)

    @shop.command(name="buy")
    @app_commands.describe(item_name="The item to buy.", quantity="How many to buy.")
    async def shop_buy(self, ctx: commands.Context, item_name: str, quantity: int = 1):
        """Buy an item from this server's shop."""
        await self.economy_buy(ctx, item_name, quantity)

    async def economy_buy(self, ctx: commands.Context, item_name: str, quantity: int = 1):
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

    @shop.command(name="gift", aliases=["giveitem"])
    @app_commands.describe(member="Who gets the item.", item_name="The item to gift.", quantity="How many to gift.")
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
            code_note = f" Transferred {result['codes_transferred']:,} redeem code(s) too; they can run `/shop codes`."
        await ctx.send(
            f"Gifted {quantity:,}x **{result['item_name']}** to {member.mention}.{code_note}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @shop.command(name="codes", aliases=["redeemcodes"])
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

    @shop.command(name="inventory", aliases=["inv"])
    @app_commands.describe(member="Whose inventory to show.")
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
        await send_embed(ctx, embed)

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

            embed.set_footer(text=f"Page {len(pages) + 1}/{total_pages} | Balance: /eco")
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

