from __future__ import annotations

import logging
import time
from typing import Any, Literal

import discord
from redbot.core import app_commands, commands

from . import sinks
from .constants import (
    CASH,
    CURRENCY_NAME,
    CUSTOM_ROLE_PERIOD,
    GOLD,
    MAX_BADGE_LENGTH,
    MAX_BOOSTER_HOURS,
    MAX_ROLE_NAME_LENGTH,
    MAX_TITLE_LENGTH,
)
from .errors import EconomyError
from .layouts import send_embed, send_text
from .shop import ShopMixin

log = logging.getLogger("red.neufox.economy")

PerkKind = Literal["title", "badge", "color", "rolecolor", "rolename", "claimbooster", "xpbooster", "streaksaver"]
PERK_LABELS = {
    "title": "Profile title",
    "badge": "Profile badge",
    "color": "Profile colour",
    "rolecolor": "Personal role colour",
    "rolename": "Personal role name",
    "claimbooster": "Claim booster",
    "xpbooster": "XP booster",
    "streaksaver": "Streak saver",
}
BOOSTER_KINDS = {"claimbooster": "claim", "xpbooster": "xp"}
CLEAR_WORDS = {"clear", "none", "remove", "reset"}


class PerksMixin:
    """Cosmetics, personal roles, rented roles, boosters and streak savers."""

    # Public API ---------------------------------------------------------------

    async def get_multiplier(self, user_id: int, kind: str) -> float:
        """Public cog API: the active booster multiplier for ``kind`` ("xp" or "claim").

        Returns 1.0 when no booster is active, for unknown kinds, and on any error.
        """
        if kind not in ("xp", "claim"):
            return 1.0
        try:
            boosters = await self.config.user_from_id(int(user_id)).boosters()
        except Exception:
            log.exception("Could not read boosters for user %s", user_id)
            return 1.0
        return sinks.active_multiplier(boosters, kind, int(time.time()))

    # Display ------------------------------------------------------------------

    async def _profile_embed(self, guild: discord.Guild | None, member: discord.abc.User) -> discord.Embed:
        now = int(time.time())
        balance = (await self.get_balance(member.id))[CASH]
        member_data = await self.config.member(member).all() if guild and isinstance(member, discord.Member) else {}
        user_data = await self.config.user(member).all()
        claims = (await self.config.claims()).get(str(member.id), {})
        badge = member_data.get("badge")
        color = member_data.get("color")
        name = f"{badge} {member.display_name}" if badge else member.display_name
        embed = discord.Embed(
            title=name,
            description=f"*{member_data['title']}*" if member_data.get("title") else None,
            color=discord.Color(int(color)) if color is not None else GOLD,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=CURRENCY_NAME, value=f"{balance:,}", inline=True)
        streak = int(claims.get("streak", 0) or 0)
        missed = sinks.streak_at_risk(int(claims.get("daily", 0) or 0), now)
        streak_text = f"{streak:,} day{'s' if streak != 1 else ''}"
        if missed:
            streak_text += " (at risk)"
        savers = int(user_data.get("streak_savers", 0) or 0)
        if savers:
            streak_text += f" · {savers} saver{'s' if savers != 1 else ''}"
        embed.add_field(name="Daily streak", value=streak_text, inline=True)

        extras = []
        for kind, label in (("claim", "Claim"), ("xp", "XP")):
            multiplier = sinks.active_multiplier(user_data.get("boosters"), kind, now)
            if multiplier > 1.0:
                until = int(user_data["boosters"][kind]["until"])
                extras.append(f"{label} booster x{multiplier:g} until <t:{until}:R>")
        custom = member_data.get("custom_role") or {}
        if custom.get("role_id"):
            renew = "renews" if custom.get("renew") else "ends"
            extras.append(f"Personal role <@&{custom['role_id']}> {renew} <t:{int(custom.get('until', 0))}:R>")
        for role_id, rental in (member_data.get("rentals") or {}).items():
            renew = "renews" if rental.get("renew") else "ends"
            extras.append(f"Rented <@&{role_id}> {renew} <t:{int(rental.get('until', 0))}:R>")
        if extras:
            embed.add_field(name="Perks", value="\n".join(extras), inline=False)
        return embed

    async def _perks_embed(self, guild: discord.Guild) -> discord.Embed:
        settings = await self.config.guild(guild).all()
        lines = []

        def price_line(label: str, price: int, detail: str = "") -> None:
            if int(price) > 0:
                lines.append(f"**{label}** — {int(price):,} {CURRENCY_NAME}{detail}")

        if settings["perks_enabled"]:
            price_line("Profile title", settings["title_price"], " · `/shop perk title <text>`")
            price_line("Profile badge", settings["badge_price"], " · `/shop perk badge <emoji>`")
            price_line("Profile colour", settings["color_price"], " · `/shop perk color #ff8800`")
            if settings["custom_roles_enabled"]:
                price_line(
                    "Personal role",
                    settings["custom_role_price"],
                    f" per week (changes {int(settings['custom_role_edit_price']):,}) · `/shop perk rolecolor` / `rolename`",
                )
        price_line(
            "Claim booster",
            await self.config.claim_booster_price(),
            f" · x{float(await self.config.claim_booster_multiplier()):g} /claim for "
            f"{int(await self.config.claim_booster_hours())}h",
        )
        price_line(
            "XP booster",
            await self.config.xp_booster_price(),
            f" · x{float(await self.config.xp_booster_multiplier()):g} activity XP for "
            f"{int(await self.config.xp_booster_hours())}h",
        )
        price_line(
            "Streak saver",
            await self.config.streak_saver_price(),
            f" · covers one missed day, hold up to {int(await self.config.streak_saver_max())}",
        )
        rentals = []
        for role_id, entry in (settings.get("rentable_roles") or {}).items():
            role = guild.get_role(int(role_id))
            if role is not None:
                rentals.append(f"{role.mention} — {int(entry.get('price', 0)):,} {CURRENCY_NAME} per day")
        embed = discord.Embed(
            title="Perks",
            description="\n".join(lines) or "No perks are on sale here.",
            color=GOLD,
        )
        if rentals:
            embed.add_field(
                name="Rentable roles",
                value="\n".join(rentals) + "\n`/shop rent <role> [days]` · auto-renews until `/shop cancel`",
                inline=False,
            )
        embed.set_footer(text="Perk purchases are burned. `clear` removes a title, badge or colour for free.")
        return embed

    # Commands -----------------------------------------------------------------

    @ShopMixin.shop.command(name="perks")
    async def shop_perks(self, ctx: commands.Context):
        """Show perks, boosters and rentable roles, plus your own."""
        await send_embed(ctx, await self._perks_embed(ctx.guild))
        await send_embed(ctx, await self._profile_embed(ctx.guild, ctx.author))

    @ShopMixin.shop.command(name="perk")
    @app_commands.describe(kind="What to buy.", value="Title text, emoji, #hex colour or role name, if needed.")
    async def shop_perk(self, ctx: commands.Context, kind: PerkKind, *, value: str = ""):
        """Buy a profile title, badge, colour, personal role, booster or streak saver."""
        try:
            message = await self._buy_perk(ctx.author, kind, value)
        except EconomyError as error:
            await ctx.send(str(error))
            return
        except discord.HTTPException:
            await ctx.send("Discord rejected that change. Nothing was charged.")
            return
        await send_text(ctx, message, color=GOLD)

    @ShopMixin.shop.command(name="rent")
    @app_commands.describe(role="A role the server rents out.", days="How many days, renewed automatically.")
    async def shop_rent(self, ctx: commands.Context, role: discord.Role, days: int = 1):
        """Rent a role by the day. It renews automatically until you cancel."""
        try:
            message = await self._rent_role(ctx.author, role, days)
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await send_text(ctx, message, color=GOLD)

    @ShopMixin.shop.command(name="cancel")
    @app_commands.describe(what="customrole, or the name of a rented role.")
    async def shop_cancel(self, ctx: commands.Context, *, what: str):
        """Stop a rented or personal role from renewing. You keep it until it ends."""
        try:
            message = await self._cancel_renewal(ctx.author, what)
        except EconomyError as error:
            await ctx.send(str(error))
            return
        await ctx.send(message)

    # Purchases ----------------------------------------------------------------

    async def _buy_perk(self, member: discord.Member, kind: str, value: str) -> str:
        guild = member.guild
        settings = await self.config.guild(guild).all()
        value = str(value or "").strip()
        if kind in BOOSTER_KINDS:
            return await self._buy_booster(member, BOOSTER_KINDS[kind])
        if kind == "streaksaver":
            return await self._buy_streak_saver(member)
        if not settings["perks_enabled"]:
            raise EconomyError("Perks are disabled in this server.")
        if kind in ("title", "badge", "color"):
            return await self._buy_cosmetic(member, kind, value, settings)
        if kind in ("rolecolor", "rolename"):
            return await self._buy_custom_role(member, kind, value, settings)
        raise EconomyError("Unknown perk.")

    async def _buy_cosmetic(self, member: discord.Member, kind: str, value: str, settings: dict[str, Any]) -> str:
        member_config = self.config.member(member)
        if value.casefold() in CLEAR_WORDS:
            await getattr(member_config, kind).set(None)
            return f"Your {PERK_LABELS[kind].lower()} was removed."
        price = int(settings[f"{kind}_price"])
        if price <= 0:
            raise EconomyError(f"{PERK_LABELS[kind]} is not on sale here.")
        if kind == "title":
            stored: Any = sinks.clean_text(value, MAX_TITLE_LENGTH)
            if not stored:
                raise EconomyError(f"Give a title of up to {MAX_TITLE_LENGTH} characters.")
        elif kind == "badge":
            stored = sinks.clean_text(value, MAX_BADGE_LENGTH)
            if not stored or " " in stored or len(value.strip()) > MAX_BADGE_LENGTH:
                raise EconomyError("Give a single emoji as your badge.")
        else:
            stored = sinks.parse_hex_color(value)
            if stored is None:
                raise EconomyError("Give a colour like `#ff8800`.")
        account = await self._charge(
            member.id, price, tx_type="perk", guild_id=member.guild.id, reason=f"{kind} perk",
        )
        await getattr(member_config, kind).set(stored)
        shown = f"#{stored:06x}" if kind == "color" else stored
        return (
            f"{PERK_LABELS[kind]} set to **{shown}** for {price:,} {CURRENCY_NAME}. "
            f"Balance: {account[CASH]:,} {CURRENCY_NAME}."
        )

    async def _buy_booster(self, member: discord.abc.User, kind: str) -> str:
        price = int(await getattr(self.config, f"{kind}_booster_price")())
        hours = min(MAX_BOOSTER_HOURS, int(await getattr(self.config, f"{kind}_booster_hours")()))
        multiplier = float(await getattr(self.config, f"{kind}_booster_multiplier")())
        label = "Claim" if kind == "claim" else "XP"
        if price <= 0 or hours <= 0 or multiplier <= 1.0:
            raise EconomyError(f"{label} boosters are not on sale.")
        guild_id = member.guild.id if isinstance(member, discord.Member) else None
        account = await self._charge(member.id, price, tx_type="booster", guild_id=guild_id, reason=f"{kind} booster")
        now = int(time.time())
        async with self.config.user(member).boosters() as boosters:
            current = boosters.get(kind) if isinstance(boosters.get(kind), dict) else {}
            until = sinks.extend_until(int(current.get("until", 0) or 0), now, hours * 3600)
            boosters[kind] = {"until": until, "multiplier": multiplier}
        return (
            f"{label} booster x{multiplier:g} active until <t:{until}:F> (<t:{until}:R>). "
            f"Paid {price:,} {CURRENCY_NAME}; balance {account[CASH]:,} {CURRENCY_NAME}."
        )

    async def _buy_streak_saver(self, member: discord.abc.User) -> str:
        price = int(await self.config.streak_saver_price())
        if price <= 0:
            raise EconomyError("Streak savers are not on sale.")
        held = int(await self.config.user(member).streak_savers())
        most = int(await self.config.streak_saver_max())
        if held >= most:
            raise EconomyError(f"You already hold the maximum of {most} streak savers.")
        guild_id = member.guild.id if isinstance(member, discord.Member) else None
        account = await self._charge(member.id, price, tx_type="streak_saver", guild_id=guild_id, reason="streak saver")
        await self.config.user(member).streak_savers.set(held + 1)
        return (
            f"You now hold {held + 1} streak saver{'s' if held else ''}. Each covers one missed day. "
            f"Balance: {account[CASH]:,} {CURRENCY_NAME}."
        )

    def _role_manageable(self, guild: discord.Guild, role: discord.Role | None = None) -> None:
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            raise EconomyError("I need the Manage Roles permission for that.")
        if role is not None and role >= me.top_role:
            raise EconomyError(f"{role.name} is above my highest role, so I cannot manage it.")

    async def _buy_custom_role(self, member: discord.Member, kind: str, value: str, settings: dict[str, Any]) -> str:
        guild = member.guild
        if not settings["custom_roles_enabled"]:
            raise EconomyError("Personal roles are disabled in this server.")
        self._role_manageable(guild)
        if kind == "rolecolor":
            color = sinks.parse_hex_color(value)
            if color is None:
                raise EconomyError("Give a colour like `#ff8800`.")
            name = None
        else:
            name = sinks.clean_text(value, MAX_ROLE_NAME_LENGTH)
            if not name:
                raise EconomyError(f"Give a role name of up to {MAX_ROLE_NAME_LENGTH} characters.")
            color = None

        now = int(time.time())
        data = dict(await self.config.member(member).custom_role() or {})
        role = guild.get_role(int(data.get("role_id", 0) or 0)) if data else None
        active = role is not None and int(data.get("until", 0)) > now
        price = int(settings["custom_role_edit_price"] if active else settings["custom_role_price"])
        if price <= 0 and not active:
            raise EconomyError("Personal roles are not on sale here.")
        account = None
        if price > 0:
            account = await self._charge(
                member.id, price, tx_type="custom_role", guild_id=guild.id,
                reason=f"personal role {'change' if active else 'week'}",
            )
        try:
            if active:
                self._role_manageable(guild, role)
                changes = {"name": name} if name else {"colour": discord.Colour(color)}
                await role.edit(reason=f"Personal role change for {member}", **changes)
            else:
                role = await self._create_custom_role(member, settings, name=name, color=color)
        except (EconomyError, discord.HTTPException):
            if price > 0:
                await self._credit(member.id, price, tx_type="refund", guild_id=guild.id, reason="personal role failed")
            raise
        if not active:
            data = {
                "role_id": role.id,
                "until": now + CUSTOM_ROLE_PERIOD,
                "renew": True,
                "price": int(settings["custom_role_price"]),
            }
            await self.config.member(member).custom_role.set(data)
        balance = account[CASH] if account else (await self.get_balance(member.id))[CASH]
        renew = f"Renews <t:{int(data['until'])}:R> for {int(data.get('price', 0)):,} {CURRENCY_NAME} a week."
        return f"Your personal role {role.mention} is ready. {renew} Balance: {balance:,} {CURRENCY_NAME}."

    async def _create_custom_role(
        self, member: discord.Member, settings: dict[str, Any], *, name: str | None, color: int | None,
    ) -> discord.Role:
        guild = member.guild
        me = guild.me
        role = await guild.create_role(
            name=name or sinks.clean_text(member.display_name, MAX_ROLE_NAME_LENGTH) or "Personal role",
            colour=discord.Colour(color or 0),
            permissions=discord.Permissions.none(),
            reason=f"Personal role bought by {member}",
        )
        anchor = guild.get_role(int(settings.get("custom_role_anchor") or 0))
        target = (anchor.position + 1) if anchor is not None else me.top_role.position - 1
        target = max(1, min(target, me.top_role.position - 1))
        try:
            if role.position != target:
                await role.edit(position=target, reason="Place personal role")
        except discord.HTTPException:
            log.info("Could not move personal role %s in guild %s", role.id, guild.id)
        try:
            await member.add_roles(role, reason="Personal role bought")
        except discord.HTTPException:
            await self._delete_role_quietly(role)
            raise
        return role

    async def _rent_role(self, member: discord.Member, role: discord.Role, days: int) -> str:
        guild = member.guild
        rentable = (await self.config.guild(guild).rentable_roles()) or {}
        entry = rentable.get(str(role.id))
        if not entry:
            raise EconomyError(f"{role.name} is not for rent. See `/shop perks` for rentable roles.")
        max_days = int(await self.config.guild(guild).rental_max_days())
        if days < 1 or days > max_days:
            raise EconomyError(f"Rent for 1 to {max_days} days.")
        self._role_manageable(guild, role)
        price = int(entry.get("price", 0)) * days
        if price <= 0:
            raise EconomyError("That role has no rental price.")
        account = await self._charge(
            member.id, price, tx_type="rental", guild_id=guild.id, reason=f"rent {role.name} for {days}d",
        )
        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"Rented for {days} day(s)")
        except discord.HTTPException:
            await self._credit(member.id, price, tx_type="refund", guild_id=guild.id, reason="rental failed")
            raise EconomyError("I could not give you that role. You were refunded.")
        now = int(time.time())
        async with self.config.member(member).rentals() as rentals:
            current = rentals.get(str(role.id)) or {}
            until = sinks.extend_until(int(current.get("until", 0) or 0), now, days * sinks.DAY)
            rentals[str(role.id)] = {"until": until, "days": days, "price": price, "renew": True}
        return (
            f"You rented {role.mention} until <t:{until}:F>. It renews for {price:,} {CURRENCY_NAME} "
            f"every {days} day(s) until `/shop cancel {role.name}`. Balance: {account[CASH]:,} {CURRENCY_NAME}."
        )

    async def _cancel_renewal(self, member: discord.Member, what: str) -> str:
        text = str(what or "").strip()
        if text.casefold() in {"customrole", "personal", "personalrole", "role"}:
            async with self.config.member(member).custom_role() as data:
                if not data.get("role_id"):
                    raise EconomyError("You do not have a personal role.")
                data["renew"] = False
                until = int(data.get("until", 0))
            return f"Your personal role will not renew and ends <t:{until}:R>."
        target = text.strip("<@&>").casefold()
        async with self.config.member(member).rentals() as rentals:
            for role_id, rental in rentals.items():
                role = member.guild.get_role(int(role_id))
                names = {role_id, role.name.casefold()} if role else {role_id}
                if target in names:
                    rental["renew"] = False
                    return f"That rental will not renew and ends <t:{int(rental.get('until', 0))}:R>."
        raise EconomyError("You have no rental or personal role by that name.")

    # Upkeep -------------------------------------------------------------------

    async def _process_role_upkeep(self, now: int):
        """Renew or end personal roles and rentals whose period is over."""
        for guild_id, members in (await self.config.all_members()).items():
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            for member_id, data in members.items():
                custom = data.get("custom_role") or {}
                if custom.get("role_id") and sinks.is_expired(custom.get("until", 0), now):
                    await self._upkeep_custom_role(guild, int(member_id), custom, now)
                for role_id, rental in dict(data.get("rentals") or {}).items():
                    if sinks.is_expired(rental.get("until", 0), now):
                        await self._upkeep_rental(guild, int(member_id), int(role_id), rental, now)

    async def _try_renew(self, guild: discord.Guild, member: discord.Member | None, record: dict, now: int, what: str) -> bool:
        if member is None:
            return False
        balance = (await self.get_balance(member.id))[CASH]
        price = int(record.get("price", 0))
        decision = sinks.upkeep_decision(int(record.get("until", 0)), now, bool(record.get("renew")), balance, price)
        if decision != sinks.UPKEEP_RENEW:
            return False
        try:
            await self._charge(member.id, price, tx_type="upkeep", guild_id=guild.id, reason=f"{what} renewal")
        except EconomyError:
            return False
        return True

    async def _upkeep_custom_role(self, guild: discord.Guild, member_id: int, record: dict, now: int):
        member = guild.get_member(member_id)
        role = guild.get_role(int(record.get("role_id", 0)))
        member_config = self.config.member_from_ids(guild.id, member_id)
        if role is not None and await self._try_renew(guild, member, record, now, "personal role"):
            record = {**record, "until": sinks.extend_until(record.get("until", 0), now, CUSTOM_ROLE_PERIOD)}
            await member_config.custom_role.set(record)
            return
        await member_config.custom_role.set({})
        if role is not None:
            await self._delete_role_quietly(role)
        if member is not None and record.get("renew"):
            await self._notify(member, f"Your personal role in **{guild.name}** ended because the weekly "
                                       f"{int(record.get('price', 0)):,} {CURRENCY_NAME} could not be paid.")

    async def _upkeep_rental(self, guild: discord.Guild, member_id: int, role_id: int, record: dict, now: int):
        member = guild.get_member(member_id)
        role = guild.get_role(role_id)
        rentable = (await self.config.guild(guild).rentable_roles()) or {}
        member_config = self.config.member_from_ids(guild.id, member_id)
        still_rentable = str(role_id) in rentable and role is not None
        if still_rentable and await self._try_renew(guild, member, record, now, f"{role.name} rental"):
            async with member_config.rentals() as rentals:
                rentals[str(role_id)] = {
                    **record,
                    "until": sinks.extend_until(record.get("until", 0), now, int(record.get("days", 1)) * sinks.DAY),
                }
            return
        async with member_config.rentals() as rentals:
            rentals.pop(str(role_id), None)
        if member is not None and role is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason="Role rental ended")
            except discord.HTTPException:
                log.info("Could not remove rented role %s from %s", role_id, member_id)
        if member is not None and record.get("renew"):
            name = role.name if role else "a rented role"
            await self._notify(member, f"Your rental of **{name}** in **{guild.name}** ended because the renewal "
                                       f"of {int(record.get('price', 0)):,} {CURRENCY_NAME} could not be paid.")

    @staticmethod
    async def _delete_role_quietly(role: discord.Role):
        try:
            await role.delete(reason="Personal role ended")
        except discord.HTTPException:
            log.info("Could not delete personal role %s", role.id)

    @staticmethod
    async def _notify(user: discord.abc.User, text: str):
        try:
            await user.send(text)
        except (discord.HTTPException, AttributeError):
            pass
