from __future__ import annotations

import logging

import discord
from discord import ui
from redbot.core import app_commands, commands

from . import sinks
from .constants import CASH, CURRENCY_NAME, GOLD, TOP_LIMIT
from .errors import EconomyError
from .layouts import NO_PINGS, embed_container, send_embed

log = logging.getLogger("red.neufox.economy")

HUB_TIMEOUT = 300
HUB_PAGES = (
    ("profile", "Balance", discord.ButtonStyle.primary),
    ("claim", "Claim", discord.ButtonStyle.success),
    ("shop", "Shop", discord.ButtonStyle.secondary),
    ("perks", "Perks", discord.ButtonStyle.secondary),
    ("market", "Market", discord.ButtonStyle.secondary),
    ("casino", "Casino", discord.ButtonStyle.secondary),
    ("lottery", "Lottery", discord.ButtonStyle.secondary),
    ("nightlife", "Nightlife", discord.ButtonStyle.secondary),
    ("top", "Leaderboard", discord.ButtonStyle.secondary),
)


class EconomyHub(ui.LayoutView):
    """One panel with a button for every part of the economy."""

    def __init__(self, cog, owner: discord.abc.User, guild: discord.Guild | None, target: discord.abc.User):
        super().__init__(timeout=HUB_TIMEOUT)
        self.cog = cog
        self.owner = owner
        self.guild = guild
        self.target = target
        self.page = "profile"
        self.message: discord.Message | None = None

    def _rows(self, *, closed: bool = False) -> list[ui.ActionRow]:
        buttons = []
        for key, label, style in HUB_PAGES:
            if self.guild is None and key in {"shop", "perks", "market", "nightlife"}:
                continue
            button = ui.Button(
                label=label,
                style=discord.ButtonStyle.primary if key == self.page and key != "claim" else style,
                disabled=closed,
            )
            button.callback = self._callback(key)
            buttons.append(button)
        return [ui.ActionRow(*buttons[index : index + 5]) for index in range(0, len(buttons), 5)]

    def render(self, embed: discord.Embed, *, closed: bool = False):
        self.clear_items()
        self.add_item(embed_container(embed, *self._rows(closed=closed)))

    def _callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            self.page = key
            try:
                embed = await self.cog._hub_page(key, self.owner, self.guild, self.target)
            except EconomyError as error:
                embed = discord.Embed(description=str(error), color=discord.Color.red())
            self.render(embed)
            await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)

        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message("Open your own panel with `/eco`.", ephemeral=True)
        return False

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            embed = await self.cog._hub_page(self.page if self.page != "claim" else "profile",
                                             self.owner, self.guild, self.target)
            self.render(embed, closed=True)
            await self.message.edit(view=self, allowed_mentions=NO_PINGS)
        except (discord.HTTPException, EconomyError):
            pass


class PayConfirmView(discord.ui.View):
    def __init__(self, cog, ctx: commands.Context, member: discord.Member, amount: int, tax: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.ctx = ctx
        self.member = member
        self.amount = amount
        self.tax = tax
        self.message: discord.Message | None = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message("This payment is not yours.", ephemeral=True)
        return False

    @discord.ui.button(label="Send", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return
        self.done = True
        self.stop()
        try:
            text = await self.cog._send_payment(self.ctx, self.member, self.amount, self.tax)
        except EconomyError as error:
            text = str(error)
        await interaction.response.edit_message(content=text, view=None, allowed_mentions=NO_PINGS)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.done = True
        self.stop()
        await interaction.response.edit_message(content="Payment cancelled.", view=None)

    async def on_timeout(self):
        if self.message is not None and not self.done:
            try:
                await self.message.edit(content="Payment expired. Nothing was sent.", view=None)
            except discord.HTTPException:
                pass


class HubMixin:
    """The /eco hub, balances, payments and the leaderboard."""

    @commands.hybrid_command(name="eco", aliases=["economy"])
    @app_commands.describe(member="Whose profile to open the panel on.")
    async def eco(self, ctx: commands.Context, member: discord.Member | None = None):
        """Open the economy panel: balance, claim, shop, perks, market, casino and more."""
        target = member or ctx.author
        view = EconomyHub(self, ctx.author, ctx.guild, target)
        view.render(await self._hub_page("profile", ctx.author, ctx.guild, target))
        view.message = await ctx.send(view=view, allowed_mentions=NO_PINGS)

    @commands.command(name="balance", aliases=["bal"])
    async def balance(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show your LWD$ balance and profile, or another member's."""
        await send_embed(ctx, await self._profile_embed(ctx.guild, member or ctx.author))

    @commands.hybrid_command(name="pay")
    @commands.guild_only()
    @app_commands.describe(member="Who to pay.", amount="How much LWD$ they receive.")
    async def pay(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Pay LWD$ to another member. A server transfer tax may apply."""
        if member.bot:
            await ctx.send("You cannot pay bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("You cannot pay yourself.")
            return
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return
        percent = float(await self.config.guild(ctx.guild).pay_tax_percent())
        tax = sinks.transfer_tax(amount, percent)
        if tax <= 0:
            try:
                text = await self._send_payment(ctx, member, amount, 0)
            except EconomyError as error:
                text = str(error)
            await ctx.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))
            return
        view = PayConfirmView(self, ctx, member, amount, tax)
        view.message = await ctx.send(
            f"Send **{amount:,} {CURRENCY_NAME}** to {member.mention}?\n"
            f"Transfer tax ({percent:g}%): **{tax:,} {CURRENCY_NAME}**, which is burned. "
            f"You pay **{amount + tax:,} {CURRENCY_NAME}** in total.",
            view=view,
            allowed_mentions=NO_PINGS,
        )

    async def _send_payment(self, ctx: commands.Context, member: discord.Member, amount: int, tax: int) -> str:
        result = await self.transfer_with_tax(
            ctx.author.id,
            member.id,
            amount,
            tax,
            guild_id=ctx.guild.id if ctx.guild else None,
            reason=f"Discord pay command in guild {ctx.guild.id if ctx.guild else 'dm'}",
        )
        tax_text = f" Tax: {tax:,} {CURRENCY_NAME}." if tax else ""
        return (
            f"Paid {member.mention} {amount:,} {CURRENCY_NAME}.{tax_text} "
            f"Balance: {result['from'][CASH]:,} {CURRENCY_NAME}."
        )

    async def _top_embed(self, guild: discord.Guild | None) -> discord.Embed:
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
        lines = [
            f"{rank}. **{await self._display_user(guild, user_id)}** - {amount:,} {CURRENCY_NAME}"
            for rank, (user_id, amount) in enumerate(entries, start=1)
        ]
        return discord.Embed(
            title="LWD$ Leaderboard",
            description="\n".join(lines) or "There are no economy balances yet.",
            color=GOLD,
        )

    async def _hub_prefix(self, guild: discord.Guild | None) -> str:
        prefixes = await self.bot.get_valid_prefixes(guild)
        usable = [prefix for prefix in prefixes if not prefix.startswith("<@")]
        return usable[0] if usable else "/"

    async def _hub_page(
        self, key: str, owner: discord.abc.User, guild: discord.Guild | None, target: discord.abc.User,
    ) -> discord.Embed:
        if key == "profile":
            embed = await self._profile_embed(guild, target)
            embed.set_footer(text="Slash: /claim · /pay · /shop · /casino · /nightlife")
            return embed
        if key == "claim":
            return await self._claim_all(owner, guild)
        if key == "top":
            return await self._top_embed(guild)
        if key == "casino":
            return await self._casino_menu_embed(owner.id)
        if key == "lottery":
            return await self._lottery_status_embed()
        if guild is None:
            raise EconomyError("That part of the economy is only available in servers.")
        if key == "shop":
            pages = await self._shop_embeds(guild, prefix=await self._hub_prefix(guild), panel=False)
            embed = pages[0]
            if len(pages) > 1:
                embed.set_footer(text=f"Showing page 1 of {len(pages)}. Use /shop view for every item.")
            return embed
        if key == "perks":
            return await self._perks_embed(guild)
        if key == "market":
            return await self._market_embed(guild)
        if key == "nightlife":
            if guild.id in await self.config.nightlife_disabled_guilds():
                raise EconomyError("Nightlife is disabled in this server.")
            return self._nightlife_help_embed(await self._hub_prefix(guild))
        raise EconomyError("Unknown page.")
