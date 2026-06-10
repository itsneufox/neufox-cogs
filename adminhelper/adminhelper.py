from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.red()
DEFAULT_REASON = "No reason provided."


class AdminHelper(commands.Cog):
    """Manual moderation actions for server staff."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=911223335)
        self.config.register_guild(
            log_channel_id=None,
            dm_users=True,
            softban_delete_message_days=1,
            ban_delete_message_days=0,
        )
        self.config.register_member(warnings=[])

    @commands.group(name="adminhelper", aliases=["ah"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper(self, ctx: commands.Context):
        """Show AdminHelper settings."""
        settings = await self.config.guild(ctx.guild).all()
        log_channel = ctx.guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None

        embed = discord.Embed(title="AdminHelper", color=DEFAULT_COLOR)
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        embed.add_field(name="DM users", value="Yes" if settings["dm_users"] else "No", inline=True)
        embed.add_field(name="Ban cleanup", value=f"{settings['ban_delete_message_days']} day(s)", inline=True)
        embed.add_field(name="Softban cleanup", value=f"{settings['softban_delete_message_days']} day(s)", inline=True)
        embed.add_field(
            name="Actions",
            value="ban, hackban, unban, kick, softban, timeout, untimeout, warn, warnings, clearwarnings, userinfo",
            inline=False,
        )
        await ctx.send(embed=embed)

    @adminhelper.command(name="logchannel")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_logchannel(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """Set or clear the moderation log channel."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id if channel else None)
        if channel is None:
            await ctx.send("AdminHelper log channel cleared.")
            return
        await ctx.send(f"AdminHelper actions will be logged in {channel.mention}.")

    @adminhelper.command(name="dm")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_dm(self, ctx: commands.Context, enabled: bool):
        """Enable or disable DM notices to moderated users."""
        await self.config.guild(ctx.guild).dm_users.set(enabled)
        await ctx.send(f"User DM notices {'enabled' if enabled else 'disabled'}.")

    @adminhelper.command(name="cleanup")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_cleanup(
        self,
        ctx: commands.Context,
        ban_delete_message_days: int | None = None,
        softban_delete_message_days: int | None = None,
    ):
        """View or set message cleanup days for ban and softban actions."""
        guild_cfg = self.config.guild(ctx.guild)
        if ban_delete_message_days is None and softban_delete_message_days is None:
            settings = await guild_cfg.all()
            await ctx.send(
                f"Ban cleanup: {settings['ban_delete_message_days']} day(s). "
                f"Softban cleanup: {settings['softban_delete_message_days']} day(s)."
            )
            return

        if ban_delete_message_days is not None:
            if not 0 <= ban_delete_message_days <= 7:
                await ctx.send("ban_delete_message_days must be between 0 and 7.")
                return
            await guild_cfg.ban_delete_message_days.set(ban_delete_message_days)
        if softban_delete_message_days is not None:
            if not 0 <= softban_delete_message_days <= 7:
                await ctx.send("softban_delete_message_days must be between 0 and 7.")
                return
            await guild_cfg.softban_delete_message_days.set(softban_delete_message_days)

        await ctx.send("Cleanup settings updated.")

    @commands.command(name="userinfo", aliases=["ui", "whois"])
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def userinfo_member(self, ctx: commands.Context, member: discord.Member = None):
        """Show account, server, and moderation stats for a member."""
        member = member or ctx.author
        warnings = await self.config.member(member).warnings()
        timeout_until = getattr(member, "communication_disabled_until", None)
        timeout_text = None
        if timeout_until and timeout_until.timestamp() > datetime.now(timezone.utc).timestamp():
            timeout_text = f"Timed out until {self._format_profile_timestamp(timeout_until)}"

        roles = [role for role in member.roles if role != ctx.guild.default_role]
        sorted_roles = sorted(roles, key=lambda role: role.position, reverse=True)
        role_text = sorted_roles[0].mention if sorted_roles else "No roles"
        if len(sorted_roles) > 1:
            role_text += f" (+{len(sorted_roles) - 1} more)"
        role_mentions = [role.mention for role in sorted_roles]
        displayed_roles = ", ".join(role_mentions[:12]) if role_mentions else "None"
        if len(role_mentions) > 12:
            displayed_roles += f", +{len(role_mentions) - 12} more"

        status_icon = self._status_icon(member.status)
        status_text = self._status_text(member)
        description_lines = [
            f"{status_icon} **{member.display_name}**",
            status_text,
            "",
            "**Joined Discord on**",
            self._format_profile_timestamp(member.created_at),
            "**Joined this server on**",
            self._format_profile_timestamp(member.joined_at),
            "**Role**",
            role_text,
            "**Previous Username**",
            "Not tracked yet",
            "**Previous Global Display Names**",
            "Not tracked yet",
            "**Previous Server Nicknames**",
            "Not tracked yet",
        ]
        if timeout_text:
            description_lines.extend(["**Moderation**", timeout_text])
        if warnings:
            description_lines.extend(["**Warnings**", str(len(warnings))])

        embed = discord.Embed(description="\n".join(description_lines), color=member.color or DEFAULT_COLOR)
        avatar_url = self._avatar_url(member)
        if avatar_url:
            embed.set_author(name=str(member), icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)
        else:
            embed.set_author(name=str(member))
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Administrator", value="Yes" if member.guild_permissions.administrator else "No", inline=True)
        embed.add_field(name="Boosting since", value=self._format_timestamp(member.premium_since), inline=True)
        embed.add_field(name="Top role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Role count", value=str(len(roles)), inline=True)
        embed.add_field(name="Timed out", value=timeout_text or "No", inline=True)
        embed.add_field(name="Warnings", value=str(len(warnings)), inline=True)
        embed.add_field(name="Display name", value=member.display_name, inline=True)
        embed.add_field(name="Roles list", value=displayed_roles[:1024], inline=False)
        member_number = self._member_number(ctx.guild, member)
        embed.set_footer(text=f"Member #{member_number} | User ID: {member.id}")
        await ctx.send(embed=embed)

    @commands.command(name="timeout", aliases=["mute"])
    @commands.admin_or_permissions(moderate_members=True)
    @commands.guild_only()
    async def timeout_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        minutes: int,
        *,
        reason: str = DEFAULT_REASON,
    ):
        """Timeout a member for a number of minutes."""
        if minutes <= 0:
            await ctx.send("Timeout duration must be greater than 0.")
            return
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "timeout", actor=ctx.author)
        if not allowed:
            await ctx.send(f"Unable to timeout {member.mention}: {failure}")
            return

        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        audit_reason = self._audit_reason(ctx.author, reason)
        try:
            await member.edit(communication_disabled_until=until, reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(f"Unable to timeout {member.mention}.")
            return

        await self._maybe_dm(member, ctx.guild, "timeout", reason, duration=f"{minutes} minute(s)")
        await self._log_action(ctx.guild, ctx.author, member, "Timeout", reason, duration=f"{minutes} minute(s)")
        await ctx.send(f"{member.mention} has been timed out for {minutes} minute(s).")

    @commands.command(name="untimeout", aliases=["unmute"])
    @commands.admin_or_permissions(moderate_members=True)
    @commands.guild_only()
    async def untimeout_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = DEFAULT_REASON,
    ):
        """Remove a member timeout."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "timeout", actor=ctx.author)
        if not allowed:
            await ctx.send(f"Unable to remove timeout from {member.mention}: {failure}")
            return

        try:
            await member.edit(communication_disabled_until=None, reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(f"Unable to remove timeout from {member.mention}.")
            return

        await self._log_action(ctx.guild, ctx.author, member, "Untimeout", reason)
        await ctx.send(f"Timeout removed from {member.mention}.")

    @commands.command(name="kick")
    @commands.admin_or_permissions(kick_members=True)
    @commands.guild_only()
    async def kick_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Kick a member."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "kick", actor=ctx.author)
        if not allowed:
            await ctx.send(f"Unable to kick {member.mention}: {failure}")
            return

        await self._maybe_dm(member, ctx.guild, "kick", reason)
        try:
            await member.kick(reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(f"Unable to kick {member.mention}.")
            return

        await self._log_action(ctx.guild, ctx.author, member, "Kick", reason)
        await ctx.send(f"{member.mention} has been kicked.")

    @commands.command(name="ban")
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def ban_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Ban a member."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "ban", actor=ctx.author)
        if not allowed:
            await ctx.send(f"Unable to ban {member.mention}: {failure}")
            return

        delete_days = await self.config.guild(ctx.guild).ban_delete_message_days()
        await self._maybe_dm(member, ctx.guild, "ban", reason)
        try:
            await member.ban(delete_message_days=delete_days, reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(f"Unable to ban {member.mention}.")
            return

        await self._log_action(ctx.guild, ctx.author, member, "Ban", reason, extra=f"Deleted {delete_days} day(s) of messages")
        await ctx.send(f"{member.mention} has been banned.")

    @commands.command(name="hackban", aliases=["forceban"])
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def hackban_user(self, ctx: commands.Context, user: discord.User, *, reason: str = DEFAULT_REASON):
        """Ban a user by ID or mention, even if they are not in the server."""
        member = ctx.guild.get_member(user.id)
        if member is not None:
            allowed, failure = await self._can_moderate_member(ctx.guild, member, "ban", actor=ctx.author)
            if not allowed:
                await ctx.send(f"Unable to ban {member.mention}: {failure}")
                return
        elif ctx.guild.me is None or not ctx.guild.me.guild_permissions.ban_members:
            await ctx.send("Unable to ban: I am missing the `ban_members` permission.")
            return

        delete_days = await self.config.guild(ctx.guild).ban_delete_message_days()
        try:
            await ctx.guild.ban(user, delete_message_days=delete_days, reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(f"Unable to ban {user}.")
            return

        await self._log_action(ctx.guild, ctx.author, user, "Hackban", reason, extra=f"Deleted {delete_days} day(s) of messages")
        await ctx.send(f"{user} has been banned.")

    @commands.command(name="unban")
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def unban_user(self, ctx: commands.Context, user: discord.User, *, reason: str = DEFAULT_REASON):
        """Unban a user."""
        if ctx.guild.me is None or not ctx.guild.me.guild_permissions.ban_members:
            await ctx.send("Unable to unban: I am missing the `ban_members` permission.")
            return
        try:
            await ctx.guild.unban(user, reason=self._audit_reason(ctx.author, reason))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await ctx.send(f"Unable to unban {user}.")
            return

        await self._log_action(ctx.guild, ctx.author, user, "Unban", reason)
        await ctx.send(f"{user} has been unbanned.")

    @commands.command(name="softban")
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def softban_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Ban then immediately unban a member to clean recent messages."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "ban", actor=ctx.author)
        if not allowed:
            await ctx.send(f"Unable to softban {member.mention}: {failure}")
            return

        delete_days = await self.config.guild(ctx.guild).softban_delete_message_days()
        audit_reason = self._audit_reason(ctx.author, f"Softban: {reason}")
        await self._maybe_dm(member, ctx.guild, "softban", reason, extra=f"Deleted {delete_days} day(s) of messages")
        try:
            await member.ban(delete_message_days=delete_days, reason=audit_reason)
            await ctx.guild.unban(member, reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(f"Unable to softban {member.mention}.")
            return

        await self._log_action(ctx.guild, ctx.author, member, "Softban", reason, extra=f"Deleted {delete_days} day(s) of messages")
        await ctx.send(f"{member.mention} has been softbanned.")

    @commands.command(name="warn")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def warn_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Warn a member and store the warning."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "warn", actor=ctx.author)
        if not allowed:
            await ctx.send(f"Unable to warn {member.mention}: {failure}")
            return

        warning = {
            "moderator_id": ctx.author.id,
            "reason": reason,
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }
        async with self.config.member(member).warnings() as warnings:
            warnings.append(warning)
            count = len(warnings)

        await self._maybe_dm(member, ctx.guild, "warn", reason)
        await self._log_action(ctx.guild, ctx.author, member, "Warn", reason, extra=f"Warning #{count}")
        await ctx.send(f"{member.mention} has been warned. This is warning #{count}.")

    @commands.command(name="warnings")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def warnings_member(self, ctx: commands.Context, member: discord.Member):
        """Show stored warnings for a member."""
        warnings = await self.config.member(member).warnings()
        if not warnings:
            await ctx.send(f"{member.mention} has no warnings.")
            return

        lines = []
        for idx, warning in enumerate(warnings[-10:], start=max(1, len(warnings) - 9)):
            moderator = ctx.guild.get_member(warning.get("moderator_id"))
            moderator_text = moderator.mention if moderator else str(warning.get("moderator_id", "unknown"))
            created_at = warning.get("created_at", 0)
            timestamp = f"<t:{created_at}:R>" if created_at else "unknown time"
            lines.append(f"`#{idx}` {timestamp} by {moderator_text}: {warning.get('reason', DEFAULT_REASON)}")

        await ctx.send(f"Warnings for {member.mention} ({len(warnings)} total):\n" + "\n".join(lines))

    @commands.command(name="clearwarnings", aliases=["clearwarns"])
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def clearwarnings_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Clear stored warnings for a member."""
        previous = len(await self.config.member(member).warnings())
        await self.config.member(member).warnings.set([])
        await self._log_action(ctx.guild, ctx.author, member, "Clear Warnings", reason, extra=f"Cleared {previous} warning(s)")
        await ctx.send(f"Cleared {previous} warning(s) for {member.mention}.")

    async def _can_moderate_member(
        self,
        guild: discord.Guild | None,
        member: discord.Member,
        action: str,
        *,
        actor: discord.Member | None = None,
    ) -> tuple[bool, str]:
        if guild is None:
            return False, "this command can only be used in a server."
        if member.guild.id != guild.id:
            return False, "that member is not in this server."
        if member.id == guild.owner_id:
            return False, "I cannot moderate the server owner."

        bot_member = guild.me
        if bot_member is None:
            return False, "I cannot find my server member record."
        if member.id == bot_member.id:
            return False, "I cannot moderate myself."
        if await self._is_bot_owner(member):
            return False, "I cannot moderate a bot owner."

        required_permission = {
            "timeout": "moderate_members",
            "kick": "kick_members",
            "ban": "ban_members",
            "warn": None,
        }.get(action)
        if action != "warn" and required_permission is None:
            return False, "unknown moderation action."
        if required_permission and not getattr(bot_member.guild_permissions, required_permission, False):
            return False, f"I am missing the `{required_permission}` permission."

        if action in {"timeout", "kick", "ban"} and member.top_role >= bot_member.top_role:
            return False, "that member's top role is higher than or equal to mine."

        if actor is not None:
            if member.id == actor.id:
                return False, "you cannot moderate yourself."
            if actor.id != guild.owner_id and member.top_role >= actor.top_role:
                return False, "that member's top role is higher than or equal to yours."

        return True, ""

    async def _is_bot_owner(self, member: discord.Member) -> bool:
        try:
            return await self.bot.is_owner(member)
        except Exception:
            return False

    def _format_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "None"
        return f"<t:{int(value.timestamp())}:F>\n<t:{int(value.timestamp())}:R>"

    def _format_profile_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "Unknown"
        timestamp = int(value.timestamp())
        return f"<t:{timestamp}:f>\n<t:{timestamp}:R>"

    @staticmethod
    def _avatar_url(member: discord.Member) -> str:
        display_avatar = getattr(member, "display_avatar", None)
        if display_avatar is not None:
            return str(display_avatar.url)
        avatar_url = getattr(member, "avatar_url", None)
        if avatar_url is not None:
            return str(avatar_url)
        default_avatar_url = getattr(member, "default_avatar_url", None)
        if default_avatar_url is not None:
            return str(default_avatar_url)
        return ""

    @staticmethod
    def _status_icon(status: discord.Status) -> str:
        return {
            discord.Status.online: "🟢",
            discord.Status.idle: "🟡",
            discord.Status.dnd: "🔴",
            discord.Status.offline: "⚪",
        }.get(status, "⚪")

    @staticmethod
    def _status_text(member: discord.Member) -> str:
        status = str(member.status)
        if status == "offline":
            return "Chilling in offline status"
        return f"Chilling in {status} status"

    @staticmethod
    def _member_number(guild: discord.Guild, member: discord.Member) -> int:
        members = [guild_member for guild_member in guild.members if guild_member.joined_at is not None]
        members.sort(key=lambda guild_member: guild_member.joined_at)
        try:
            return members.index(member) + 1
        except ValueError:
            return 0

    def _audit_reason(self, moderator: discord.abc.User, reason: str) -> str:
        return f"{moderator} ({moderator.id}): {reason or DEFAULT_REASON}"[:512]

    async def _maybe_dm(
        self,
        member: discord.Member,
        guild: discord.Guild,
        action: str,
        reason: str,
        *,
        duration: str | None = None,
        extra: str | None = None,
    ):
        if not await self.config.guild(guild).dm_users():
            return

        lines = [
            f"You received a moderation action in **{guild.name}**.",
            f"Action: **{action}**",
        ]
        if duration:
            lines.append(f"Duration: {duration}")
        if extra:
            lines.append(extra)
        lines.append(f"Reason: {reason or DEFAULT_REASON}")

        try:
            await member.send("\n".join(lines))
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _log_action(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        target: discord.abc.User,
        action: str,
        reason: str,
        *,
        duration: str | None = None,
        extra: str | None = None,
    ):
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if log_channel is None:
            return

        embed = discord.Embed(title=f"AdminHelper: {action}", color=DEFAULT_COLOR)
        embed.add_field(name="Target", value=f"{target} (`{target.id}`)", inline=False)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        if extra:
            embed.add_field(name="Details", value=extra, inline=False)
        embed.add_field(name="Reason", value=reason or DEFAULT_REASON, inline=False)
        embed.timestamp = datetime.now(timezone.utc)

        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return
