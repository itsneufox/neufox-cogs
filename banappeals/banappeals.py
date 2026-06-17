from __future__ import annotations

import asyncio
import re
import time
import typing
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp
import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.red()
VALID_STATUSES = {
    "pending": "Pending Review",
    "needs_info": "Needs More Info",
    "accepted": "Accepted",
    "denied": "Denied",
    "final_chance": "Final Chance",
    "fake_evidence": "Fake Evidence",
    "closed": "Closed",
}
FIELD_DEFS: tuple[tuple[str, str], ...] = (
    ("ingame_name", "Your ingame name"),
    ("ban_date", "Date of Ban (DD/MM/YYYY)"),
    ("ban_reason", "Ban reason"),
    ("admin", "Admin who banned you"),
    ("ban_id", "Ban ID"),
    ("why", "Why should you be unbanned?"),
)
FIELD_SHORT_LABELS = {
    "ingame_name": "in-game name",
    "ban_date": "ban date",
    "ban_reason": "ban reason",
    "admin": "banning admin",
    "ban_id": "Ban ID",
    "why": "explanation",
}
LOW_EFFORT_WHY = {
    "i didnt do it",
    "i didn't do it",
    "i did not do it",
    "unban me",
    "please unban me",
    "idk",
    "nothing",
}
CHEAT_WORDS = ("cheat", "hacking", "hack", "aimbot", "wallhack", "cleo", "s0beit")
API_FIELD_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Ban ID", ("ban_id", "id", "banid")),
    ("Player", ("player_name", "player", "name", "username", "ingame_name")),
    ("Reason", ("reason", "ban_reason")),
    ("Admin", ("admin", "admin_name", "banned_by", "staff")),
    ("Banned At", ("banned_at", "date", "created_at", "timestamp")),
    ("Expires", ("expires_at", "expire_at", "expires", "expiration")),
    ("Active", ("active", "is_active")),
)


class AppealActionsView(discord.ui.View):
    def __init__(self, cog: "BanAppeals", log_url: str | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        if log_url:
            self.add_item(
                discord.ui.Button(
                    label="Staff Log",
                    style=discord.ButtonStyle.link,
                    url=log_url,
                    row=2,
                )
            )

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="banappeals:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_staff_action(interaction, "accepted")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="banappeals:deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_staff_action(interaction, "denied")

    @discord.ui.button(label="Need More Info", style=discord.ButtonStyle.primary, custom_id="banappeals:needs_info")
    async def needs_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_staff_action(interaction, "needs_info")

    @discord.ui.button(label="Final Chance", style=discord.ButtonStyle.secondary, row=1, custom_id="banappeals:final_chance")
    async def final_chance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_staff_action(interaction, "final_chance")

    @discord.ui.button(label="Fake Evidence", style=discord.ButtonStyle.danger, row=1, custom_id="banappeals:fake_evidence")
    async def fake_evidence(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_staff_action(interaction, "fake_evidence")

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=1, custom_id="banappeals:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_staff_action(interaction, "closed")


class BanAppeals(commands.Cog):
    """Moderate ban appeals in a forum or text channel."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=911223336)
        self.config.register_guild(
            enabled=False,
            appeal_channel_id=None,
            log_channel_id=None,
            staff_role_ids=[],
            cooldown_days=7,
            base_challenge_count=50,
            api_url_template=None,
            api_bearer_token=None,
            appeals={},
            denied_users={},
            challenge_counts={},
        )
        self._view: AppealActionsView | None = None

    async def cog_load(self) -> None:
        self._view = AppealActionsView(self)
        self.bot.add_view(self._view)

    async def red_delete_data_for_user(self, requester, user_id: int) -> None:
        all_guilds = await self.config.all_guilds()
        for guild_id, data in all_guilds.items():
            appeals = data.get("appeals", {})
            appeals = {
                record_id: record
                for record_id, record in appeals.items()
                if int(record.get("author_id", 0) or 0) != user_id
                and int(record.get("handler_id", 0) or 0) != user_id
            }
            denied_users = data.get("denied_users", {})
            denied_users.pop(str(user_id), None)
            guild_group = self.config.guild_from_id(int(guild_id))
            await guild_group.appeals.set(appeals)
            await guild_group.denied_users.set(denied_users)

    @commands.group(name="appealset", aliases=["banappealset"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset(self, ctx: commands.Context):
        """Show BanAppeals settings."""
        await self._send_settings(ctx)

    @appealset.command(name="toggle")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_toggle(self, ctx: commands.Context, value: bool | None = None):
        """Enable or disable appeal moderation."""
        current = await self.config.guild(ctx.guild).enabled()
        new_value = (not current) if value is None else value
        await self.config.guild(ctx.guild).enabled.set(new_value)
        await ctx.send(f"Ban appeal moderation is now {'enabled' if new_value else 'disabled'}.")

    @appealset.command(name="channel")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_channel(
        self,
        ctx: commands.Context,
        channel: typing.Optional[typing.Union[discord.ForumChannel, discord.TextChannel]] = None,
    ):
        """Set the forum or text channel where appeals are posted. Omit to clear."""
        await self.config.guild(ctx.guild).appeal_channel_id.set(channel.id if channel else None)
        if channel is None:
            await ctx.send("Ban appeal channel cleared.")
            return
        await ctx.send(f"Ban appeals will be monitored in {channel.mention}.")

    @appealset.command(name="log")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_log(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """Set the staff log channel. Omit to clear."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id if channel else None)
        if channel is None:
            await ctx.send("Ban appeal log channel cleared.")
            return
        await ctx.send(f"Ban appeal actions will be logged in {channel.mention}.")

    @appealset.command(name="cooldown")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_cooldown(self, ctx: commands.Context, days: int):
        """Set the cooldown, in days, after an appeal is denied."""
        if days < 0:
            await ctx.send("Cooldown days must be 0 or greater.")
            return
        await self.config.guild(ctx.guild).cooldown_days.set(days)
        await ctx.send(f"Denied appeal cooldown set to {days} day(s).")

    @appealset.command(name="challenge")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_challenge(self, ctx: commands.Context, count: int):
        """Set the base handwritten final-chance sentence count."""
        if count <= 0:
            await ctx.send("Challenge count must be greater than 0.")
            return
        await self.config.guild(ctx.guild).base_challenge_count.set(count)
        await ctx.send(f"Base final-chance challenge count set to {count}.")

    @appealset.group(name="staffrole", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_staffrole(self, ctx: commands.Context):
        """List roles allowed to use appeal staff buttons."""
        settings = await self.config.guild(ctx.guild).all()
        roles = [ctx.guild.get_role(role_id) for role_id in settings["staff_role_ids"]]
        roles = [role for role in roles if role is not None]
        if not roles:
            await ctx.send("No extra appeal staff roles are configured. Manage Messages/Manage Threads can use buttons.")
            return
        await ctx.send("Appeal staff roles: " + ", ".join(role.mention for role in roles))

    @appealset_staffrole.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_staffrole_add(self, ctx: commands.Context, role: discord.Role):
        """Allow a role to use appeal staff buttons."""
        async with self.config.guild(ctx.guild).staff_role_ids() as role_ids:
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"{role.mention} can now use appeal staff buttons.")

    @appealset_staffrole.command(name="remove")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_staffrole_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from appeal staff buttons."""
        async with self.config.guild(ctx.guild).staff_role_ids() as role_ids:
            if role.id in role_ids:
                role_ids.remove(role.id)
                await ctx.send(f"{role.mention} can no longer use appeal staff buttons.")
                return
        await ctx.send(f"{role.mention} was not configured as an appeal staff role.")

    @appealset.command(name="apiurl")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_apiurl(self, ctx: commands.Context, *, url: str | None = None):
        """Set the read-only ban lookup URL. Include {ban_id}. Omit to clear."""
        if not url:
            await self.config.guild(ctx.guild).api_url_template.set(None)
            await ctx.send("Ban ID API lookup disabled.")
            return
        if "{ban_id}" not in url:
            await ctx.send("API URL must include `{ban_id}`, for example `https://example.com/api/bans/{ban_id}`.")
            return
        if not (url.startswith("https://") or url.startswith("http://")):
            await ctx.send("API URL must start with `https://` or `http://`.")
            return
        await self.config.guild(ctx.guild).api_url_template.set(url)
        await ctx.send("Ban ID API lookup URL updated.")

    @appealset.command(name="apitoken")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_apitoken(self, ctx: commands.Context, *, token: str | None = None):
        """Set the optional API bearer token. Omit to clear."""
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        token = (token or "").strip()
        await self.config.guild(ctx.guild).api_bearer_token.set(token or None)
        if token:
            await ctx.send("Ban ID API bearer token updated.", delete_after=10)
        else:
            await ctx.send("Ban ID API bearer token cleared.", delete_after=10)

    @appealset.command(name="testapi")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def appealset_testapi(self, ctx: commands.Context, ban_id: str):
        """Test the configured Ban ID API lookup."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings.get("api_url_template"):
            await ctx.send("No Ban ID API URL is configured.")
            return
        result = await self._lookup_ban(ctx.guild, ban_id, settings)
        embed = discord.Embed(title=f"Ban API Test: {ban_id}", color=DEFAULT_COLOR)
        self._add_api_fields(embed, result)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not await self._message_is_eligible(message):
            return

        try:
            ctx = await self.bot.get_context(message)
        except Exception:
            ctx = None
        if ctx is not None and ctx.valid:
            return

        await self._handle_appeal_message(message, is_edit=False)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not await self._message_is_eligible(after):
            return
        await self._handle_appeal_message(after, is_edit=True)

    async def handle_staff_action(self, interaction: discord.Interaction, status: str) -> None:
        if interaction.guild is None or interaction.message is None:
            await interaction.response.send_message("This button can only be used in a server.", ephemeral=True)
            return

        settings = await self.config.guild(interaction.guild).all()
        if not await self._interaction_is_staff(interaction, settings):
            await interaction.response.send_message("You are not allowed to use appeal staff buttons.", ephemeral=True)
            return

        record_id, record = await self._record_from_interaction(interaction, settings)
        if record is None or record_id is None:
            await interaction.response.send_message("I could not find the appeal record for this button.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        now = self._now()
        record["status"] = status
        record["handler_id"] = interaction.user.id
        record["handler_name"] = str(interaction.user)
        record["handled_at"] = now

        public_message = await self._apply_status_side_effects(interaction, record, settings, status)

        log_message = await self._send_log(
            interaction.guild,
            f"Appeal {VALID_STATUSES.get(status, status)}",
            record,
            moderator=interaction.user,
            extra=public_message,
        )
        self._store_log_reference(record, log_message)

        async with self.config.guild(interaction.guild).appeals() as appeals:
            appeals[record_id] = record

        try:
            await interaction.message.edit(
                embed=self._appeal_panel_embed(interaction.guild, record),
                view=self._appeal_actions_view(record),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.followup.send(f"Appeal marked as {VALID_STATUSES.get(status, status)}.", ephemeral=True)

    async def _apply_status_side_effects(
        self,
        interaction: discord.Interaction,
        record: dict[str, typing.Any],
        settings: dict[str, typing.Any],
        status: str,
    ) -> str:
        guild = interaction.guild
        if guild is None:
            return ""

        channel = interaction.channel
        moderator = interaction.user
        public_message = ""
        if status == "accepted":
            await self._clear_user_cooldown(guild, record.get("author_id"))
            public_message = f"Appeal accepted by {moderator.mention}."
        elif status == "denied":
            cooldown_days = int(settings.get("cooldown_days", 7) or 0)
            cooldown_until = self._now() + cooldown_days * 86400
            record["cooldown_until"] = cooldown_until
            await self._set_user_cooldown(guild, record.get("author_id"), cooldown_until, record)
            public_message = (
                f"Appeal denied by {moderator.mention}."
                if cooldown_days <= 0
                else f"Appeal denied by {moderator.mention}. You may submit another appeal <t:{cooldown_until}:R>."
            )
        elif status == "needs_info":
            public_message = (
                f"{moderator.mention} marked this appeal as needing more information. "
                "Please reply with the missing details or edit the original appeal post."
            )
        elif status == "final_chance":
            count = await self._current_challenge_count(guild, record, settings)
            record["challenge_count"] = count
            public_message = self._challenge_message(record, count, moderator)
        elif status == "fake_evidence":
            count = await self._double_challenge_count(guild, record, settings)
            record["challenge_count"] = count
            public_message = (
                f"{moderator.mention} marked the submitted evidence as fake or invalid.\n\n"
                + self._challenge_message(record, count, moderator, include_intro=False)
            )
        elif status == "closed":
            public_message = f"Appeal closed by {moderator.mention}."

        if public_message and isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                await channel.send(public_message, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            except (discord.Forbidden, discord.HTTPException):
                pass

        if status == "closed" and isinstance(channel, discord.Thread):
            try:
                await channel.edit(locked=True, archived=True, reason=f"Ban appeal closed by {moderator}")
            except TypeError:
                try:
                    await channel.edit(archived=True, reason=f"Ban appeal closed by {moderator}")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            except (discord.Forbidden, discord.HTTPException):
                pass

        return public_message

    async def _message_is_eligible(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        if message.author is None or message.author.bot:
            return False
        settings = await self.config.guild(message.guild).all()
        if not settings.get("enabled") or not settings.get("appeal_channel_id"):
            return False
        return self._record_id_for_message(message, settings, allow_existing=True) is not None

    async def _handle_appeal_message(self, message: discord.Message, *, is_edit: bool) -> None:
        guild = message.guild
        if guild is None:
            return
        settings = await self.config.guild(guild).all()
        record_id = self._record_id_for_message(message, settings, allow_existing=True)
        if record_id is None:
            return

        appeals = settings.get("appeals", {})
        existing = appeals.get(record_id)
        primary_message_id = int(existing.get("message_id", 0) or 0) if existing else message.id
        is_primary_message = existing is None or message.id == primary_message_id
        if existing is not None and not is_edit and is_primary_message:
            return

        parsed_fields = self._parse_appeal_fields(message.content or "")
        if existing is not None and not is_primary_message and not any(parsed_fields.values()):
            return
        if existing is None:
            primary_fields = parsed_fields
            supplemental_fields = {}
            fields = parsed_fields
        elif is_primary_message:
            primary_fields = parsed_fields
            supplemental_fields = existing.get("supplemental_fields", {})
            fields = self._merge_fields(primary_fields, supplemental_fields)
        else:
            primary_fields = existing.get("primary_fields") or existing.get("fields", {})
            supplemental_fields = self._merge_fields(existing.get("supplemental_fields", {}), parsed_fields)
            fields = self._merge_fields(primary_fields, supplemental_fields)

        warnings = self._validate_fields(fields)
        cooldown_warning = await self._cooldown_warning(guild, message.author.id)
        if cooldown_warning:
            warnings.append(cooldown_warning)

        api_result = None
        ban_id = fields.get("ban_id")
        if ban_id and settings.get("api_url_template"):
            api_result = await self._lookup_ban(guild, ban_id, settings)
            if not api_result.get("found", False):
                error = api_result.get("error") or "Ban ID was not found by the configured API."
                warnings.append(error)

        now = self._now()
        record = existing or {}
        record.update(
            {
                "record_id": record_id,
                "guild_id": guild.id,
                "updated_at": now,
                "status": record.get("status", "pending"),
                "primary_fields": primary_fields,
                "supplemental_fields": supplemental_fields,
                "fields": fields,
                "warnings": warnings,
                "api_result": api_result,
            }
        )
        if is_primary_message:
            record.update(
                {
                    "channel_id": message.channel.id,
                    "thread_id": message.channel.id if isinstance(message.channel, discord.Thread) else None,
                    "parent_channel_id": getattr(message.channel, "parent_id", None),
                    "message_id": message.id,
                    "jump_url": message.jump_url,
                    "author_id": message.author.id,
                    "author_name": str(message.author),
                    "created_at": int(message.created_at.replace(tzinfo=timezone.utc).timestamp()),
                }
            )
        else:
            record.update(
                {
                    "last_supplement_message_id": message.id,
                    "last_supplement_url": message.jump_url,
                    "last_supplement_at": int(message.created_at.replace(tzinfo=timezone.utc).timestamp()),
                }
            )

        if existing is None:
            panel_message = await self._send_new_appeal_messages(message, guild, record)
            if panel_message is not None:
                record["panel_message_id"] = panel_message.id
            log_message = await self._send_log(guild, "New Ban Appeal", record)
            self._store_log_reference(record, log_message)
            async with self.config.guild(guild).appeals() as stored_appeals:
                stored_appeals[record_id] = record
            if log_message is not None:
                await self._update_panel_message(guild, record)
            return

        await self._sync_validation_message(guild, record)
        log_message = await self._send_log(guild, "Ban Appeal Updated", record)
        self._store_log_reference(record, log_message)
        async with self.config.guild(guild).appeals() as stored_appeals:
            stored_appeals[record_id] = record
        await self._update_panel_message(guild, record)

    async def _send_new_appeal_messages(
        self,
        message: discord.Message,
        guild: discord.Guild,
        record: dict[str, typing.Any],
    ) -> discord.Message | None:
        warnings = self._display_warnings(record)
        if warnings:
            try:
                validation_message = await message.reply(
                    embed=self._validation_embed(record),
                    mention_author=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                record["validation_message_id"] = validation_message.id
                record["validation_message_url"] = validation_message.jump_url
            except (discord.Forbidden, discord.HTTPException):
                pass

        channel = message.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        try:
            return await channel.send(
                embed=self._appeal_panel_embed(guild, record),
                view=self._appeal_actions_view(record),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _update_panel_message(self, guild: discord.Guild, record: dict[str, typing.Any]) -> None:
        channel_id = record.get("channel_id")
        message_id = record.get("panel_message_id")
        if not channel_id or not message_id:
            return
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            panel_message = await channel.fetch_message(message_id)
            await panel_message.edit(
                embed=self._appeal_panel_embed(guild, record),
                view=self._appeal_actions_view(record),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

    def _record_id_for_message(
        self,
        message: discord.Message,
        settings: dict[str, typing.Any],
        *,
        allow_existing: bool,
    ) -> str | None:
        appeal_channel_id = settings.get("appeal_channel_id")
        if appeal_channel_id is None:
            return None

        channel = message.channel
        if isinstance(channel, discord.Thread) and getattr(channel, "parent_id", None) == appeal_channel_id:
            record_id = str(channel.id)
            if allow_existing and record_id in settings.get("appeals", {}):
                record = settings["appeals"][record_id]
                if int(record.get("message_id", 0) or 0) == message.id:
                    return record_id
                if int(record.get("author_id", 0) or 0) == message.author.id:
                    return record_id
                return None

            owner_id = getattr(channel, "owner_id", None)
            if owner_id is not None and owner_id != message.author.id:
                return None
            created_at = getattr(channel, "created_at", None)
            if created_at is not None:
                created_at = created_at.replace(tzinfo=timezone.utc)
                message_created = message.created_at.replace(tzinfo=timezone.utc)
                if abs((message_created - created_at).total_seconds()) > 300:
                    return None
            else:
                starter_message_id = getattr(channel, "starter_message_id", channel.id)
                if message.id != starter_message_id:
                    return None
            return record_id

        if isinstance(channel, discord.TextChannel) and channel.id == appeal_channel_id:
            if allow_existing:
                for record_id, record in settings.get("appeals", {}).items():
                    if int(record.get("message_id", 0) or 0) == message.id:
                        return record_id
            return str(message.id)

        return None

    async def _sync_validation_message(self, guild: discord.Guild, record: dict[str, typing.Any]) -> None:
        channel_id = record.get("channel_id")
        if not channel_id:
            return
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        warnings = self._display_warnings(record)
        message_id = record.get("validation_message_id")
        validation_message = None
        if message_id:
            try:
                validation_message = await channel.fetch_message(int(message_id))
            except (TypeError, ValueError, discord.Forbidden, discord.NotFound, discord.HTTPException):
                validation_message = None

        if not warnings:
            if validation_message is not None:
                try:
                    await validation_message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass
            record.pop("validation_message_id", None)
            record.pop("validation_message_url", None)
            return

        if validation_message is not None:
            try:
                await validation_message.edit(embed=self._validation_embed(record))
                return
            except (discord.Forbidden, discord.HTTPException):
                return

        try:
            sent = await channel.send(
                embed=self._validation_embed(record),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return
        record["validation_message_id"] = sent.id
        record["validation_message_url"] = sent.jump_url

    async def _record_from_interaction(
        self,
        interaction: discord.Interaction,
        settings: dict[str, typing.Any],
    ) -> tuple[str | None, dict[str, typing.Any] | None]:
        appeals = settings.get("appeals", {})
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            record = appeals.get(str(channel.id))
            if record is not None:
                return str(channel.id), record

        message_id = interaction.message.id if interaction.message else None
        if message_id is not None:
            for record_id, record in appeals.items():
                if int(record.get("panel_message_id", 0) or 0) == message_id:
                    return record_id, record
        return None, None

    def _parse_appeal_fields(self, content: str) -> dict[str, str]:
        fields: dict[str, list[str]] = {key: [] for key, _label in FIELD_DEFS}
        current_key: str | None = None
        label_patterns = [
            (
                key,
                re.compile(rf"^\s*(?:\*\*)?\s*{re.escape(label)}\s*:\s*(?:\*\*)?\s*(.*)$", re.IGNORECASE),
            )
            for key, label in FIELD_DEFS
        ]

        for raw_line in content.splitlines():
            stripped_line = raw_line.strip()
            matched_key = None
            matched_value = None
            for key, pattern in label_patterns:
                match = pattern.match(stripped_line)
                if match is not None:
                    matched_key = key
                    matched_value = match.group(1).strip()
                    break

            if matched_key is not None:
                fields[matched_key] = [matched_value] if matched_value else []
                current_key = matched_key
                continue

            if current_key is not None and stripped_line:
                fields[current_key].append(stripped_line)

        return {key: "\n".join(value).strip() for key, value in fields.items()}

    @staticmethod
    def _merge_fields(base: dict[str, str], override: dict[str, str]) -> dict[str, str]:
        merged = {key: (base.get(key) or "") for key, _label in FIELD_DEFS}
        for key, _label in FIELD_DEFS:
            value = override.get(key)
            if value:
                merged[key] = value
        return merged

    def _validate_fields(self, fields: dict[str, str]) -> list[str]:
        warnings: list[str] = []
        missing = [FIELD_SHORT_LABELS[key] for key, _label in FIELD_DEFS if not fields.get(key)]
        if missing:
            warnings.append("Missing: " + self._join_human_list(missing) + ".")

        ban_date = fields.get("ban_date")
        if ban_date:
            try:
                datetime.strptime(ban_date.strip(), "%d/%m/%Y")
            except ValueError:
                warnings.append("Ban date must use DD/MM/YYYY.")

        why = fields.get("why", "").strip()
        normalized_why = re.sub(r"[^a-z0-9' ]", "", why.lower()).strip()
        normalized_why = re.sub(r"\s+", " ", normalized_why)
        if why and (len(why) < 25 or normalized_why in LOW_EFFORT_WHY):
            warnings.append("Explanation is too short. Add detail about what happened.")

        return warnings

    async def _cooldown_warning(self, guild: discord.Guild, user_id: int) -> str | None:
        now = self._now()
        async with self.config.guild(guild).denied_users() as denied_users:
            entry = denied_users.get(str(user_id))
            if not entry:
                return None
            until = int(entry.get("until", 0) or 0)
            if until <= now:
                denied_users.pop(str(user_id), None)
                return None
            return f"Your previous appeal was denied. You can submit another appeal <t:{until}:R>."

    async def _lookup_ban(
        self,
        guild: discord.Guild,
        ban_id: str,
        settings: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        url_template = settings.get("api_url_template")
        if not url_template:
            return {"found": False, "error": "No API URL is configured."}

        escaped_ban_id = quote(str(ban_id), safe="")
        url = url_template.replace("{ban_id}", escaped_ban_id)
        headers = {"Accept": "application/json"}
        token = settings.get("api_bearer_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    if response.status == 404:
                        return {"found": False, "error": "Ban ID was not found by the configured API."}
                    if response.status < 200 or response.status >= 300:
                        return {"found": False, "error": f"Ban API returned HTTP {response.status}."}
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return {"found": False, "error": "Ban API lookup failed."}

        normalized = self._normalize_api_payload(payload)
        if not normalized:
            return {"found": False, "error": "Ban API returned no usable ban details."}
        return {"found": True, "fields": normalized}

    def _normalize_api_payload(self, payload: typing.Any) -> dict[str, str]:
        if isinstance(payload, dict) and isinstance(payload.get("ban"), dict):
            payload = payload["ban"]
        if not isinstance(payload, dict):
            return {}

        output: dict[str, str] = {}
        lower_payload = {str(key).lower(): value for key, value in payload.items()}
        for label, keys in API_FIELD_MAP:
            for key in keys:
                if key in lower_payload and lower_payload[key] not in (None, ""):
                    value = lower_payload[key]
                    if isinstance(value, bool):
                        output[label] = "Yes" if value else "No"
                    else:
                        output[label] = self._shorten(str(value), 256)
                    break
        return output

    async def _interaction_is_staff(
        self,
        interaction: discord.Interaction,
        settings: dict[str, typing.Any],
    ) -> bool:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        perms = user.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_messages or perms.manage_threads:
            return True
        staff_role_ids = set(settings.get("staff_role_ids", []))
        return any(role.id in staff_role_ids for role in user.roles)

    async def _set_user_cooldown(
        self,
        guild: discord.Guild,
        user_id: int | None,
        until: int,
        record: dict[str, typing.Any],
    ) -> None:
        if user_id is None:
            return
        async with self.config.guild(guild).denied_users() as denied_users:
            denied_users[str(user_id)] = {
                "until": until,
                "ban_id": record.get("fields", {}).get("ban_id"),
                "record_id": record.get("record_id"),
            }

    async def _clear_user_cooldown(self, guild: discord.Guild, user_id: int | None) -> None:
        if user_id is None:
            return
        async with self.config.guild(guild).denied_users() as denied_users:
            denied_users.pop(str(user_id), None)

    async def _current_challenge_count(
        self,
        guild: discord.Guild,
        record: dict[str, typing.Any],
        settings: dict[str, typing.Any],
    ) -> int:
        key = self._challenge_key(record)
        counts = await self.config.guild(guild).challenge_counts()
        return int(counts.get(key, record.get("challenge_count") or settings.get("base_challenge_count") or 50))

    async def _double_challenge_count(
        self,
        guild: discord.Guild,
        record: dict[str, typing.Any],
        settings: dict[str, typing.Any],
    ) -> int:
        key = self._challenge_key(record)
        base = int(settings.get("base_challenge_count") or 50)
        async with self.config.guild(guild).challenge_counts() as counts:
            current = int(counts.get(key, record.get("challenge_count") or base))
            new_count = current * 2
            counts[key] = new_count
            return new_count

    def _challenge_key(self, record: dict[str, typing.Any]) -> str:
        ban_id = record.get("fields", {}).get("ban_id")
        if ban_id:
            return f"ban:{ban_id}"
        return f"user:{record.get('author_id', 'unknown')}"

    def _challenge_message(
        self,
        record: dict[str, typing.Any],
        count: int,
        moderator: discord.abc.User,
        *,
        include_intro: bool = True,
    ) -> str:
        fields = record.get("fields", {})
        name = fields.get("ingame_name") or "your in-game name"
        ban_id = fields.get("ban_id") or "your Ban ID"
        lines = []
        if include_intro:
            lines.append(f"{moderator.mention} offered one final chance for this appeal.")
        lines.extend(
            [
                f'Handwrite `"I WILL NOT CHEAT ANYMORE ON LWD"` **{count} times** on one sheet of paper.',
                f"Include your in-game name, **{name}**, and Ban ID, **{ban_id}**, on the same page.",
                "Attach one clear photo showing the full readable page.",
                "Fake, edited, copied, printed, traced, or AI-generated submissions may be denied.",
            ]
        )
        return "\n".join(lines)

    async def _send_settings(self, ctx: commands.Context) -> None:
        settings = await self.config.guild(ctx.guild).all()
        appeal_channel = ctx.guild.get_channel(settings.get("appeal_channel_id"))
        log_channel = ctx.guild.get_channel(settings.get("log_channel_id"))
        roles = [ctx.guild.get_role(role_id) for role_id in settings.get("staff_role_ids", [])]
        roles = [role for role in roles if role is not None]
        embed = discord.Embed(title="BanAppeals Settings", color=DEFAULT_COLOR)
        embed.add_field(name="Enabled", value="Yes" if settings.get("enabled") else "No", inline=True)
        embed.add_field(name="Appeal channel", value=appeal_channel.mention if appeal_channel else "Not set", inline=True)
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        embed.add_field(name="Cooldown", value=f"{settings.get('cooldown_days', 7)} day(s)", inline=True)
        embed.add_field(name="Challenge", value=str(settings.get("base_challenge_count", 50)), inline=True)
        embed.add_field(
            name="API lookup",
            value=(
                "Enabled"
                if settings.get("api_url_template")
                else "Disabled"
            )
            + (" with token" if settings.get("api_bearer_token") else ""),
            inline=True,
        )
        embed.add_field(
            name="Staff roles",
            value=", ".join(role.mention for role in roles) if roles else "Manage Messages/Threads or Manage Server",
            inline=False,
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{ctx.clean_prefix}appealset channel #forum-or-channel`\n"
                f"`{ctx.clean_prefix}appealset log #staff-logs`\n"
                f"`{ctx.clean_prefix}appealset cooldown 7`\n"
                f"`{ctx.clean_prefix}appealset challenge 50`\n"
                f"`{ctx.clean_prefix}appealset apiurl https://example.com/api/bans/{{ban_id}}`\n"
                f"`{ctx.clean_prefix}appealset testapi <ban id>`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    def _validation_embed(self, record: dict[str, typing.Any]) -> discord.Embed:
        warnings = self._display_warnings(record)
        issue_text = self._format_warning_text(warnings) if warnings else "The appeal is missing required details."
        embed = discord.Embed(
            title="Please Edit Your Appeal",
            description=(
                f"{issue_text}\n\n"
                "Edit the first post or reply with the missing labeled field(s). **Ban ID** must be filled in before review."
            ),
            color=discord.Color.orange(),
        )
        return embed

    def _appeal_actions_view(self, record: dict[str, typing.Any]) -> AppealActionsView:
        return AppealActionsView(self, record.get("last_log_url"))

    @staticmethod
    def _store_log_reference(record: dict[str, typing.Any], message: discord.Message | None) -> None:
        if message is None:
            return
        record["last_log_url"] = message.jump_url
        record["last_log_message_id"] = message.id
        record["last_log_channel_id"] = message.channel.id

    def _appeal_panel_embed(self, guild: discord.Guild, record: dict[str, typing.Any]) -> discord.Embed:
        fields = record.get("fields", {})
        status = record.get("status", "pending")
        color = self._status_color(status)
        author_id = record.get("author_id")
        author_text = f"<@{author_id}>" if author_id else record.get("author_name", "Unknown")
        ban_id = fields.get("ban_id") or "Missing"
        ingame_name = fields.get("ingame_name") or "Missing"

        embed = discord.Embed(
            title="Appeal Review",
            description=(
                f"**{VALID_STATUSES.get(status, status)}**\n"
                f"{author_text} | Ban ID: `{self._shorten(ban_id, 80)}` | IGN: `{self._shorten(ingame_name, 80)}`"
            ),
            color=color,
        )
        embed.add_field(name="Appeal", value=self._compact_appeal_text(record), inline=False)
        embed.add_field(name="Checks", value=self._compact_checks_text(record), inline=False)

        footer_parts = []
        if record.get("challenge_count"):
            footer_parts.append(f"Challenge: {record['challenge_count']}")
        if record.get("handler_name"):
            footer_parts.append(f"Handled by {record['handler_name']}")
        if record.get("jump_url"):
            embed.add_field(name="Post", value=f"[Open appeal]({record['jump_url']})", inline=True)
        embed.set_footer(text=" | ".join(footer_parts) if footer_parts else guild.name)
        return embed

    def _compact_appeal_text(self, record: dict[str, typing.Any]) -> str:
        fields = record.get("fields", {})
        reason = fields.get("ban_reason") or "Missing"
        why = fields.get("why") or "Missing"
        details = []
        if fields.get("ban_date"):
            details.append(f"Date: {self._shorten(fields['ban_date'], 40)}")
        if fields.get("admin"):
            details.append(f"Admin: {self._shorten(fields['admin'], 80)}")

        lines = [
            f"**Reason:** {self._shorten(reason, 180)}",
            f"**Why:** {self._shorten(why, 420)}",
        ]
        if details:
            lines.append(" | ".join(details))
        return self._shorten("\n".join(lines), 1024)

    def _compact_checks_text(self, record: dict[str, typing.Any]) -> str:
        fields = record.get("fields", {})
        warnings = self._display_warnings(record)
        checks = []
        if warnings:
            checks.extend(self._shorten_warning(warning) for warning in warnings)
        else:
            checks.append("Format complete.")

        api_text = self._compact_api_text(record.get("api_result"))
        if api_text:
            checks.append(api_text)

        reason = fields.get("ban_reason", "").lower()
        if any(word in reason for word in CHEAT_WORDS):
            checks.append("Cheating/hacking keyword detected.")

        return self._shorten("\n".join(f"- {check}" for check in checks), 1024)

    def _shorten_warning(self, warning: str) -> str:
        return self._shorten(warning.strip().rstrip("."), 520) + "."

    def _display_warnings(self, record: dict[str, typing.Any]) -> list[str]:
        fields = record.get("fields") or {}
        warnings = self._validate_fields(fields)
        seen = set(warnings)

        for raw_warning in record.get("warnings") or []:
            warning = self._normalize_warning_text(str(raw_warning))
            if warning and warning not in seen:
                warnings.append(warning)
                seen.add(warning)

        return warnings

    def _normalize_warning_text(self, warning: str) -> str | None:
        warning = re.sub(r"\s+", " ", warning).strip()
        if not warning:
            return None

        lowered = warning.lower()
        if lowered.startswith("missing required field(s):"):
            return None
        if lowered.startswith("ban id is mandatory") or "no ban id" in lowered:
            return None
        if lowered.startswith("date of ban must use"):
            return "Ban date must use DD/MM/YYYY."
        if lowered.startswith("the explanation looks low-effort"):
            return "Explanation is too short. Add detail about what happened."

        return warning

    def _compact_api_text(self, api_result: dict[str, typing.Any] | None) -> str | None:
        if not api_result:
            return None
        if not api_result.get("found"):
            return f"Ban API: {api_result.get('error') or 'no usable result'}"
        fields = api_result.get("fields") or {}
        bits = [
            f"{label}: {fields[label]}"
            for label in ("Player", "Reason", "Active")
            if fields.get(label)
        ]
        if not bits:
            return "Ban API: matched."
        return "Ban API: matched - " + self._shorten(" | ".join(bits), 420)

    def _appeal_detail_embed(self, guild: discord.Guild, record: dict[str, typing.Any]) -> discord.Embed:
        fields = record.get("fields", {})
        status = record.get("status", "pending")
        color = self._status_color(status)

        embed = discord.Embed(title="Ban Appeal Details", color=color)
        author_id = record.get("author_id")
        author_text = f"<@{author_id}>" if author_id else record.get("author_name", "Unknown")
        embed.add_field(name="Status", value=VALID_STATUSES.get(status, status), inline=True)
        embed.add_field(name="User", value=author_text, inline=True)
        embed.add_field(name="Ban ID", value=self._value(fields.get("ban_id")), inline=True)
        embed.add_field(name="In-game name", value=self._value(fields.get("ingame_name")), inline=True)
        embed.add_field(name="Ban date", value=self._value(fields.get("ban_date")), inline=True)
        embed.add_field(name="Admin", value=self._value(fields.get("admin")), inline=True)
        embed.add_field(name="Ban reason", value=self._value(fields.get("ban_reason")), inline=False)
        embed.add_field(name="Why unban?", value=self._value(fields.get("why"), limit=900), inline=False)

        reason = fields.get("ban_reason", "").lower()
        if any(word in reason for word in CHEAT_WORDS):
            embed.add_field(name="Appeal type", value="Cheating/hacking keywords detected.", inline=False)

        warnings = self._display_warnings(record)
        embed.add_field(name="Validation", value=self._list_lines(warnings) if warnings else "Format looks complete.", inline=False)
        self._add_api_fields(embed, record.get("api_result"))

        if record.get("challenge_count"):
            embed.add_field(name="Challenge count", value=str(record["challenge_count"]), inline=True)
        if record.get("handler_name"):
            handled_at = record.get("handled_at")
            handled_text = record["handler_name"]
            if handled_at:
                handled_text += f" at <t:{handled_at}:f>"
            embed.add_field(name="Handled by", value=handled_text, inline=True)
        if record.get("jump_url"):
            embed.add_field(name="Post", value=f"[Jump to appeal]({record['jump_url']})", inline=True)

        embed.set_footer(text=f"Guild: {guild.name}")
        return embed

    @staticmethod
    def _status_color(status: str) -> discord.Color:
        return {
            "accepted": discord.Color.green(),
            "denied": discord.Color.red(),
            "needs_info": discord.Color.gold(),
            "final_chance": discord.Color.orange(),
            "fake_evidence": discord.Color.dark_red(),
            "closed": discord.Color.light_grey(),
        }.get(status, DEFAULT_COLOR)

    def _add_api_fields(self, embed: discord.Embed, api_result: dict[str, typing.Any] | None) -> None:
        if not api_result:
            embed.add_field(name="Ban API", value="Not checked.", inline=False)
            return
        if not api_result.get("found"):
            embed.add_field(name="Ban API", value=api_result.get("error") or "No usable result.", inline=False)
            return
        fields = api_result.get("fields") or {}
        if not fields:
            embed.add_field(name="Ban API", value="Found, but no displayable fields were returned.", inline=False)
            return
        lines = [f"**{label}:** {value}" for label, value in fields.items()]
        embed.add_field(name="Ban API", value=self._shorten("\n".join(lines), 1024), inline=False)

    async def _send_log(
        self,
        guild: discord.Guild,
        title: str,
        record: dict[str, typing.Any],
        *,
        moderator: discord.abc.User | None = None,
        extra: str | None = None,
    ) -> discord.Message | None:
        settings = await self.config.guild(guild).all()
        channel_id = settings.get("log_channel_id")
        if not channel_id:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        target = await self._get_or_create_log_thread(guild, channel, record)
        if target is None:
            target = channel
        embed = self._appeal_detail_embed(guild, record)
        embed.title = title
        if moderator is not None:
            embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
        if extra:
            embed.add_field(name="Action note", value=self._shorten(extra, 1024), inline=False)
        try:
            return await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _get_or_create_log_thread(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        record: dict[str, typing.Any],
    ) -> discord.Thread | None:
        thread_id = record.get("log_thread_id")
        if thread_id:
            try:
                thread_id = int(thread_id)
            except (TypeError, ValueError):
                thread_id = None
        if thread_id:
            thread = guild.get_channel_or_thread(thread_id)
            if thread is None:
                thread = channel.get_thread(thread_id)
            if isinstance(thread, discord.Thread):
                try:
                    if thread.archived:
                        await thread.edit(archived=False, reason="Ban appeal log updated")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return thread

        try:
            thread = await channel.create_thread(
                name=self._log_thread_name(record),
                type=discord.ChannelType.public_thread,
                auto_archive_duration=10080,
                reason="Ban appeal staff log",
            )
        except TypeError:
            try:
                thread = await channel.create_thread(
                    name=self._log_thread_name(record),
                    auto_archive_duration=10080,
                    reason="Ban appeal staff log",
                )
            except (discord.Forbidden, discord.HTTPException):
                return None
        except (discord.Forbidden, discord.HTTPException):
            return None

        record["log_thread_id"] = thread.id
        record["log_thread_parent_id"] = channel.id
        return thread

    def _log_thread_name(self, record: dict[str, typing.Any]) -> str:
        fields = record.get("fields", {})
        ban_id = fields.get("ban_id") or record.get("record_id") or "unknown"
        ingame_name = fields.get("ingame_name") or record.get("author_name") or "unknown"
        name = f"Appeal {ban_id} - {ingame_name}"
        name = re.sub(r"[\r\n\t]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
        return self._shorten(name or "Ban appeal log", 95)

    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _shorten(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."

    def _format_warning_text(self, warnings: list[str]) -> str:
        if not warnings:
            return "No issues."
        return self._shorten("\n".join(f"- {self._shorten_warning(warning)}" for warning in warnings), 900)

    @staticmethod
    def _join_human_list(values: list[str]) -> str:
        if not values:
            return ""
        if len(values) == 1:
            return values[0]
        if len(values) == 2:
            return f"{values[0]} and {values[1]}"
        return ", ".join(values[:-1]) + f", and {values[-1]}"

    def _value(self, value: str | None, *, limit: int = 1024) -> str:
        if not value:
            return "Missing"
        return self._shorten(value, limit)

    def _list_lines(self, values: list[str]) -> str:
        if not values:
            return "None"
        return self._shorten("\n".join(f"- {value}" for value in values), 1024)
