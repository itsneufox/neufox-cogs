"""Honeypot: punish anyone who posts in a trap channel."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

import discord
from redbot.core import commands

from .views import HONEYPOT_REASON, BanReviewView


ACTION_CHOICES = ("ban", "kick", "timeout", "role")
DEFAULT_HONEYPOT_DM = "You posted in a trap channel in {server} and were {action}."
HONEYPOT_DM_VERBS = {"ban": "banned", "kick": "kicked", "timeout": "timed out", "role": "restricted"}


def honeypot_dm_text(template: str, server: str, action: str) -> str:
    """Fill the honeypot DM. A template with broken braces falls back to the default text."""
    values = {"server": server, "action": HONEYPOT_DM_VERBS.get(action, action)}
    try:
        return (template or DEFAULT_HONEYPOT_DM).format_map(values)
    except (KeyError, IndexError, ValueError):
        return DEFAULT_HONEYPOT_DM.format_map(values)


def honeypot_dm_error(template: str) -> str | None:
    try:
        (template or "").format_map({"server": "", "action": ""})
    except (KeyError, IndexError, ValueError, AttributeError):
        return "Use only the {server} and {action} placeholders."
    return None


class HoneypotMixin:
    """Deletes trap-channel messages and punishes their author."""

    async def _honeypot_status_embed(self, guild: discord.Guild) -> discord.Embed:
        data = await self.honeypot_config.guild(guild).all()
        channel = guild.get_channel(data.get("channel_id"))
        log_channel = guild.get_channel(data.get("log_channel_id"))
        exempt_ids = data.get("exempt_roles", [])
        action = (data.get("action") or "ban").lower()
        punish_role_id = data.get("punish_role_id")
        remove_roles = data.get("remove_other_roles", False)
        role_exception_ids = data.get("role_exception_ids", [])

        punish_role = (
            guild.get_role(punish_role_id) if punish_role_id else None
        )
        role_exceptions = [guild.get_role(rid) for rid in role_exception_ids]
        role_exceptions = [r for r in role_exceptions if r]

        # Build exempt roles display
        exempt_roles = [guild.get_role(rid) for rid in exempt_ids]
        exempt_roles = [r for r in exempt_roles if r]

        embed = discord.Embed(
            title="Honeypot Configuration",
            color=discord.Color.orange(),
        )

        # Status section
        if channel:
            status = f"**Active** - Monitoring {channel.mention}"
            embed.color = discord.Color.green()
        else:
            status = "**Inactive** - No trap channel configured"
            embed.color = discord.Color.red()

        embed.add_field(name="Status", value=status, inline=False)

        embed.add_field(
            name="Trap Channel",
            value=channel.mention if channel else "*Not set*",
            inline=True,
        )
        embed.add_field(
            name="Log Channel",
            value=log_channel.mention if log_channel else "*Not set*",
            inline=True,
        )

        punishment_lines = [f"Action: **{action.title()}**"]
        if action == "timeout":
            punishment_lines.append(f"Length: {data.get('timeout_minutes', 60)} minute(s)")
        elif action == "ban":
            punishment_lines.append(f"Message cleanup: {data.get('ban_delete_message_days', 1)} day(s)")
        if action == "role":
            punishment_lines.append(
                f"Punish Role: {punish_role.mention if punish_role else '*Not set*'}"
            )
            strip_text = "Yes" if remove_roles else "No"
            punishment_lines.append(f"Strip Existing Roles: {strip_text}")
            if remove_roles:
                if role_exceptions:
                    exceptions_display = ", ".join(r.mention for r in role_exceptions[:5])
                    if len(role_exceptions) > 5:
                        exceptions_display += f" *+{len(role_exceptions) - 5} more*"
                else:
                    exceptions_display = "*None*"
                punishment_lines.append(f"Keep Roles: {exceptions_display}")
        embed.add_field(
            name="Punishment",
            value="\n".join(punishment_lines),
            inline=False,
        )

        if exempt_roles:
            role_list = ", ".join(r.mention for r in exempt_roles[:5])
            if len(exempt_roles) > 5:
                role_list += f" *+{len(exempt_roles) - 5} more*"
            embed.add_field(name="Exempt Roles", value=role_list, inline=False)
        else:
            embed.add_field(name="Exempt Roles", value="*None configured*", inline=False)

        embed.set_footer(text="Users who message in the trap channel will be punished automatically.")
        return embed

    @commands.Cog.listener("on_message")
    async def _honeypot_on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        channel_id = await self.honeypot_config.guild(message.guild).channel_id()
        if not channel_id or message.channel.id != channel_id:
            return

        exempt = await self._honeypot_exempt(message)
        if exempt:
            if not await self.honeypot_config.guild(message.guild).log_exempt():
                return
            await self._honeypot_log(
                message.guild,
                f"{message.author} was exempt from the honeypot in {message.channel.mention}.",
            )
            return

        cleanup_note = None
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        guild_conf = await self.honeypot_config.guild(message.guild).all()
        cleanup_hours = int(guild_conf.get("cleanup_hours", 1))
        deleted_count = await self._purge_recent_messages_guild(message, cleanup_hours) if cleanup_hours > 0 else 0
        cleanup_note = self._build_cleanup_note(deleted_count, cleanup_hours)

        if guild_conf.get("dm_member"):
            await self._honeypot_dm(message, guild_conf)
        await self._honeypot_punish(message, guild_conf, cleanup_note)

    async def _honeypot_dm(self, message: discord.Message, config: dict):
        """DM the member before punishing them, while the bot still shares a server with them."""
        action = (config.get("action") or "ban").lower()
        text = honeypot_dm_text(config.get("dm_message", ""), message.guild.name, action)
        try:
            await message.author.send(text[:2000])
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _honeypot_exempt(self, message: discord.Message) -> bool:
        exempt_role_ids = await self.honeypot_config.guild(message.guild).exempt_roles()
        if not exempt_role_ids:
            return False

        member = message.guild.get_member(message.author.id)
        if not member:
            return False

        return any(role.id in exempt_role_ids for role in getattr(member, "roles", []))

    async def _honeypot_punish(
        self,
        message: discord.Message,
        config: dict,
        cleanup_note: Optional[str] = None,
    ):
        guild = message.guild
        member = guild.get_member(message.author.id)
        if not member:
            return

        action = (config.get("action") or "ban").lower()
        channel_mention = message.channel.mention
        deleted_message = self._extract_deleted_message_details(message)

        if action == "kick":
            try:
                await guild.kick(member, reason=HONEYPOT_REASON)
                await self._honeypot_case(guild, member, "Honeypot Kick", channel_mention)
                view = self._build_ban_review_view(guild, member)
                description = self._append_cleanup_note(
                    f"{member} was kicked for tripping the honeypot in {channel_mention}. Review and ban if necessary.",
                    cleanup_note,
                )
                await self._honeypot_log(
                    guild,
                    description,
                    target=member,
                    view=view,
                    deleted_message=deleted_message,
                )
            except discord.HTTPException:
                description = self._append_cleanup_note(
                    f"Failed to kick {member} after they tripped the honeypot in {channel_mention}. Check permissions and role hierarchy.",
                    cleanup_note,
                )
                await self._honeypot_log(
                    guild,
                    description,
                    target=member,
                    deleted_message=deleted_message,
                )
            return

        if action == "timeout":
            await self._honeypot_timeout(member, config, channel_mention, deleted_message, cleanup_note)
            return

        if action == "role":
            await self._honeypot_role_punish(
                member, config, channel_mention, deleted_message, cleanup_note
            )
            return

        # Default to ban
        try:
            await guild.ban(
                member,
                reason=HONEYPOT_REASON,
                delete_message_days=max(0, min(7, int(config.get("ban_delete_message_days", 1)))),
            )
            await self._honeypot_case(guild, member, "Honeypot Ban", channel_mention)
            description = self._append_cleanup_note(
                f"{member} was banned for tripping the honeypot in {channel_mention}.",
                cleanup_note,
            )
            await self._honeypot_log(
                guild,
                description,
                target=member,
                deleted_message=deleted_message,
            )
        except discord.HTTPException:
            description = self._append_cleanup_note(
                f"Failed to ban {member} after they tripped the honeypot in {channel_mention}. Check permissions and role hierarchy.",
                cleanup_note,
            )
            await self._honeypot_log(
                guild,
                description,
                target=member,
                deleted_message=deleted_message,
            )

    async def _honeypot_timeout(
        self,
        member: discord.Member,
        config: dict,
        channel_mention: str,
        deleted_message: Optional[str] = None,
        cleanup_note: Optional[str] = None,
    ):
        guild = member.guild
        minutes = max(1, min(40320, int(config.get("timeout_minutes", 60))))
        try:
            await member.edit(communication_disabled_until=discord.utils.utcnow() + timedelta(minutes=minutes), reason=HONEYPOT_REASON)
        except discord.HTTPException:
            description = self._append_cleanup_note(
                f"Failed to timeout {member} after they tripped the honeypot in {channel_mention}. Check permissions and role hierarchy.",
                cleanup_note,
            )
            await self._honeypot_log(guild, description, target=member, deleted_message=deleted_message)
            return
        await self._honeypot_case(guild, member, "Honeypot Timeout", channel_mention, extra=f"{minutes} minute(s)")
        description = self._append_cleanup_note(
            f"{member} was timed out for {minutes} minute(s) for tripping the honeypot in {channel_mention}. Review and ban if necessary.",
            cleanup_note,
        )
        await self._honeypot_log(
            guild,
            description,
            target=member,
            view=self._build_ban_review_view(guild, member),
            deleted_message=deleted_message,
        )

    async def _honeypot_role_punish(
        self,
        member: discord.Member,
        config: dict,
        channel_mention: str,
        deleted_message: str = None,
        cleanup_note: Optional[str] = None,
    ):
        guild = member.guild
        punish_role_id = config.get("punish_role_id")
        punish_role = guild.get_role(punish_role_id) if punish_role_id else None

        if not punish_role:
            description = self._append_cleanup_note(
                f"{member} tripped the honeypot in {channel_mention}, but no punish role is configured.",
                cleanup_note,
            )
            await self._honeypot_log(
                guild,
                description,
                target=member,
                deleted_message=deleted_message,
            )
            return

        remove_roles = config.get("remove_other_roles", False)
        exceptions = set(config.get("role_exception_ids", []))
        exceptions.add(punish_role.id)

        if remove_roles:
            stripped = await self._strip_roles_from_member(
                member, exceptions, channel_mention, cleanup_note
            )
            if not stripped:
                return

        try:
            if punish_role not in member.roles:
                await member.add_roles(punish_role, reason=HONEYPOT_REASON)
                await self._honeypot_case(guild, member, "Honeypot Role", channel_mention, extra=f"Role: {punish_role.name}")
            view = self._build_ban_review_view(guild, member)
            description = self._append_cleanup_note(
                (
                    f"{member} was assigned {punish_role.mention} for tripping the honeypot in {channel_mention}. "
                    "Review and ban if necessary."
                ),
                cleanup_note,
            )
            await self._honeypot_log(
                guild,
                description,
                target=member,
                view=view,
                deleted_message=deleted_message,
            )
        except discord.HTTPException:
            description = self._append_cleanup_note(
                f"Failed to assign {punish_role.name} to {member} after they tripped the honeypot in {channel_mention}. Check permissions and role hierarchy.",
                cleanup_note,
            )
            await self._honeypot_log(
                guild,
                description,
                target=member,
                deleted_message=deleted_message,
            )

    async def _strip_roles_from_member(
        self,
        member: discord.Member,
        keep_ids: set,
        channel_mention: str,
        cleanup_note: Optional[str] = None,
    ) -> bool:
        guild = member.guild
        default_role = guild.default_role
        roles_to_remove = [
            role
            for role in member.roles
            if role != default_role and role.id not in keep_ids
        ]

        if not roles_to_remove:
            return True

        try:
            await member.remove_roles(*roles_to_remove, reason=HONEYPOT_REASON)
            return True
        except discord.HTTPException:
            description = self._append_cleanup_note(
                f"Failed to strip roles from {member} after they tripped the honeypot in {channel_mention}. Check permissions and role hierarchy.",
                cleanup_note,
            )
            await self._honeypot_log(
                guild,
                description,
                target=member,
            )
            return False

    async def _purge_recent_messages_guild(
        self,
        trigger_message: discord.Message,
        hours: int = 1,
    ) -> int:
        guild = trigger_message.guild
        if not guild:
            return 0

        bot_member = guild.me
        member = guild.get_member(trigger_message.author.id)
        if not bot_member or not member:
            return 0

        cutoff = discord.utils.utcnow() - timedelta(hours=hours)
        total_deleted = 0

        for channel in guild.text_channels:
            perms = channel.permissions_for(bot_member)
            if not (perms.manage_messages and perms.read_message_history):
                continue

            member_perms = channel.permissions_for(member)
            if not (member_perms.view_channel and member_perms.send_messages):
                continue

            deleted = await self._purge_channel_messages(
                channel,
                author_id=member.id,
                skip_message_id=trigger_message.id,
                cutoff=cutoff,
            )
            total_deleted += deleted

        return total_deleted

    async def _purge_channel_messages(
        self,
        channel: discord.TextChannel,
        *,
        author_id: int,
        skip_message_id: int,
        cutoff,
    ) -> int:
        purge = getattr(channel, "purge", None)
        if purge is None:
            return 0

        def should_delete(msg: discord.Message) -> bool:
            if msg.author.id != author_id or msg.id == skip_message_id:
                return False
            return True

        try:
            deleted = await purge(
                after=cutoff,
                check=should_delete,
                limit=200,
                bulk=True,
                reason=HONEYPOT_REASON,
            )
        except (discord.Forbidden, discord.HTTPException):
            return 0

        return len(deleted)

    async def _honeypot_case(self, guild, member, action: str, channel_mention: str, extra: str | None = None):
        if guild.me is None:
            return
        details = f"Posted in {channel_mention}" + (f"; {extra}" if extra else "")
        await self._create_case(guild, guild.me, member, action, HONEYPOT_REASON, extra=details)

    def _build_cleanup_note(self, deleted_count: int, hours: int = 1) -> Optional[str]:
        if not deleted_count:
            return None
        window = "the last hour" if hours == 1 else f"the last {hours} hours"
        if deleted_count == 1:
            return f"Removed 1 other message from {window}."
        return f"Removed {deleted_count} other messages from {window} across accessible channels."

    def _append_cleanup_note(
        self, description: str, cleanup_note: Optional[str]
    ) -> str:
        if cleanup_note:
            return f"{description} {cleanup_note}"
        return description

    def _extract_deleted_message_details(
        self, message: discord.Message
    ) -> Optional[str]:
        parts = []
        clean_content = getattr(message, "clean_content", "") or ""
        clean_content = clean_content.strip()
        if clean_content:
            parts.append(clean_content)

        attachments = getattr(message, "attachments", None) or []
        if attachments:
            attachment_names = ", ".join(
                attachment.filename or attachment.url for attachment in attachments
            )
            if attachment_names:
                parts.append(f"Attachments: {attachment_names}")

        return "\n".join(parts) if parts else None

    def _build_ban_review_view(
        self, guild: discord.Guild, target: discord.abc.User
    ) -> BanReviewView:
        return BanReviewView(self, guild.id, target.id, str(target))

    async def _honeypot_log(
        self,
        guild: discord.Guild,
        description: str,
        *,
        target: discord.abc.User = None,
        view: discord.ui.View = None,
        deleted_message: str = None,
    ):
        log_channel_id = await self.honeypot_config.guild(guild).log_channel_id()
        if not log_channel_id:
            return

        channel = guild.get_channel(log_channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(description=description, color=discord.Color.red())
        embed.timestamp = discord.utils.utcnow()
        author_name = str(target) if target else "Honeypot Alert"
        icon_url = None
        if target and hasattr(target, "display_avatar"):
            icon_url = target.display_avatar.url
        elif guild.me and guild.me.display_avatar:
            icon_url = guild.me.display_avatar.url

        embed.set_author(name=author_name, icon_url=icon_url)
        embed.set_footer(text=f"Guild: {guild.name}")
        if deleted_message:
            trimmed_message = deleted_message.strip()
            if trimmed_message:
                safe_text = discord.utils.escape_mentions(trimmed_message)
                safe_text = discord.utils.escape_markdown(safe_text)
                if len(safe_text) > 1024:
                    safe_text = f"{safe_text[:1021]}..."
                embed.add_field(
                    name="Deleted Message",
                    value=safe_text or "*No message content*",
                    inline=False,
                )

        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            return

