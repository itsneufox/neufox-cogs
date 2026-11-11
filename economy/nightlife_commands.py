from __future__ import annotations

import logging
import time

import discord
from redbot.core import app_commands, commands

from .constants import (
    CASH,
    CURRENCY_NAME,
    NIGHTLIFE_COLOR,
)
from .layouts import send_embed, send_text
from . import nightlife
from .nightlife_invite import NightlifeInviteView

log = logging.getLogger("red.neufox.economy")


class NightlifeMixin:
    """Commands for the Nightlife game."""

    async def _set_nightlife_enabled(self, guild_id: int, enabled: bool):
        async with self._lock:
            async with self.config.nightlife_disabled_guilds() as disabled:
                if enabled and guild_id in disabled:
                    disabled.remove(guild_id)
                elif not enabled and guild_id not in disabled:
                    disabled.append(guild_id)

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
                prices = await self.config.nightlife_prices()
                async with self.config.nightlife_players() as players:
                    async with self.config.balances() as balances:
                        account = self._account_from_mapping(balances, ctx.author.id)
                        player, remaining, message, cost = nightlife.apply_action(
                            players.get(str(ctx.author.id)), account[CASH], action,
                            now=int(time.time()), prices=prices, **kwargs,
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
        await send_text(ctx, message, color=NIGHTLIFE_COLOR)

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
                    prices=await self.config.nightlife_prices(),
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
            prices = await self.config.nightlife_prices()
            async with self.config.nightlife_players() as players:
                async with self.config.balances() as balances:
                    account = self._account_from_mapping(balances, first_id)
                    first, second, remaining, cost, protection, first_after, second_after = nightlife.apply_player_encounter(
                        players.get(str(first_id)), players.get(str(second_id)), account[CASH],
                        now=int(time.time()), venue=view.venue, protected=view.protected, prices=prices,
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

    @commands.hybrid_group(name="nightlife", aliases=["sex"], fallback="start", ignore_extra=False)
    @commands.guild_only()
    @app_commands.describe(member="Who to invite. Leave empty for an NPC.", protected="Whether to use a condom.")
    async def nightlife(
        self, ctx: commands.Context, member: discord.Member | None = None, protected: bool = True,
    ):
        """Start an encounter, or invite a member. Add false for no condom."""
        if member is not None:
            await self._invite_nightlife_partner(ctx, member, protected=protected)
        else:
            await self._nightlife_run(ctx, "encounter", protected=protected)

    @staticmethod
    def _nightlife_help_embed(prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title="Nightlife",
            description="Slash: `/nightlife start`, `/nightlife shop`, `/nightlife clinic` and friends.",
            color=NIGHTLIFE_COLOR,
        )
        embed.add_field(name="Commands", value="\n".join([
            f"`{prefix}sex` — have sex",
            f"`{prefix}sex @member [true|false]` — invite; condom on/off",
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
        return embed

    @nightlife.command(name="help")
    async def nightlife_help(self, ctx: commands.Context):
        """Show Nightlife commands."""
        if not await self._nightlife_allowed(ctx):
            return
        await send_embed(ctx, self._nightlife_help_embed(ctx.clean_prefix))

    @nightlife.command(name="escorts", aliases=["hookers", "npcs"])
    async def nightlife_escorts(self, ctx: commands.Context):
        """Browse escorts and venues."""
        if not await self._nightlife_allowed(ctx):
            return
        prices = await self.config.nightlife_prices()
        lines = ["**Escorts**"]
        for key, escort in nightlife.ESCORTS.items():
            lines.append(f"`{key}` — {escort['name']}: {nightlife.price_of(f'escort.{key}', prices):,} LWD$")
        lines.append("\n**Venues** (added to the escort fee)")
        for key in nightlife.VENUES:
            lines.append(f"`{key}` — {nightlife.price_of(f'venue.{key}', prices):,} LWD$")
        await send_text(ctx, "\n".join(lines), color=NIGHTLIFE_COLOR)

    @nightlife.command(name="shop")
    async def nightlife_shop(self, ctx: commands.Context):
        """Browse supplies."""
        if not await self._nightlife_allowed(ctx):
            return
        prices = await self.config.nightlife_prices()
        lines = ["**Nightlife shop**"]
        for key, item in nightlife.ITEMS.items():
            lines.append(f"`{key}` — {nightlife.price_of(f'item.{key}', prices):,} LWD$: {item['description']}")
        lines.append(f"`{ctx.clean_prefix}sex buy <item> [quantity]`")
        await send_text(ctx, "\n".join(lines), color=NIGHTLIFE_COLOR)

    @nightlife.command(name="buy")
    @app_commands.describe(item="The item to buy.", quantity="How many to buy.")
    async def nightlife_buy(self, ctx: commands.Context, item: str, quantity: int = 1):
        """Buy Nightlife items with LWD$."""
        await self._nightlife_run(ctx, "buy", item=item, quantity=quantity)

    @nightlife.command(name="use")
    @app_commands.describe(item="The item to use.")
    async def nightlife_use(self, ctx: commands.Context, item: str):
        """Use an item."""
        await self._nightlife_run(ctx, "use", item=item)

    @nightlife.command(name="encounter", aliases=["book"])
    @app_commands.describe(escort="Escort name, or a member mention.", venue="motel, hotel or penthouse.", protected="Whether to use a condom.")
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

    @nightlife.command(name="status", aliases=["profile", "inventory", "inv"])
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
        await send_text(ctx, "\n".join(lines), color=NIGHTLIFE_COLOR)

    @nightlife.group(name="clinic", fallback="prices")
    async def nightlife_clinic(self, ctx: commands.Context):
        """View clinic prices."""
        if not await self._nightlife_allowed(ctx):
            return
        prices = await self.config.nightlife_prices()
        lines = [f"**Nightlife clinic** — a test costs {nightlife.price_of('test', prices):,} LWD$."]
        for key, disease in nightlife.DISEASES.items():
            lines.append(f"{disease['name']}: {nightlife.price_of(f'treatment.{key}', prices):,} LWD$ to treat")
        lines.append(f"`{ctx.clean_prefix}sex clinic test` — get tested\n"
                     f"`{ctx.clean_prefix}sex clinic cure` — get treated\n"
                     f"`{ctx.clean_prefix}sex clinic pay` — pay hospital bills")
        await send_text(ctx, "\n".join(lines), color=NIGHTLIFE_COLOR)

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

