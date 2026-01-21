from datetime import timedelta
from typing import Optional

import discord
from redbot.core import commands, Config

ACTION_CHOICES = ("ban", "kick", "role")
HONEYPOT_REASON = "Triggered honeypot channel"


class BanReviewView(discord.ui.View):
    def __init__(self, cog: "Honeypot", guild_id: int, target_id: int, target_name: str):
        super().__init__(timeout=86400)
        self.cog = cog
        self.guild_id = guild_id
        self.target_id = target_id
        self.target_name = target_name

    @discord.ui.button(label="Ban User", style=discord.ButtonStyle.danger)
    async def ban_user(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = self.cog.bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "Guild is unavailable. Try again later.", ephemeral=True
            )
            return

        perms = getattr(interaction.user, "guild_permissions", None)
        if not perms or not perms.ban_members:
            await interaction.response.send_message(
                "You need the **Ban Members** permission to use this button.",
                ephemeral=True,
            )
            return

        try:
            await guild.ban(
                discord.Object(id=self.target_id),
                reason=HONEYPOT_REASON,
                delete_message_days=1,
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "I couldn't ban that user. Check my permissions and role hierarchy.",
                ephemeral=True,
            )
            return

        button.disabled = True
        button.label = "User Banned"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"{self.target_name} has been banned.", ephemeral=True
        )
        self.stop()


class Honeypot(commands.Cog):
    """Automatically punishes users who trigger the honeypot channel."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=948372645)
        self.config.register_guild(
            channel_id=None,
            exempt_roles=[],
            log_channel_id=None,
            action="ban",
            punish_role_id=None,
            remove_other_roles=False,
            role_exception_ids=[],
        )

    @commands.group(name="honeypot", invoke_without_command=True)
    @commands.admin()
    async def honeypot(self, ctx: commands.Context):
        """Manage honeypot settings."""
        data = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data.get("channel_id"))
        log_channel = ctx.guild.get_channel(data.get("log_channel_id"))
        exempt_ids = data.get("exempt_roles", [])
        action = (data.get("action") or "ban").lower()
        punish_role_id = data.get("punish_role_id")
        remove_roles = data.get("remove_other_roles", False)
        role_exception_ids = data.get("role_exception_ids", [])

        punish_role = (
            ctx.guild.get_role(punish_role_id) if punish_role_id else None
        )
        role_exceptions = [ctx.guild.get_role(rid) for rid in role_exception_ids]
        role_exceptions = [r for r in role_exceptions if r]

        # Build exempt roles display
        exempt_roles = [ctx.guild.get_role(rid) for rid in exempt_ids]
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

        # Commands section
        prefix = ctx.clean_prefix
        commands_text = (
            f"`{prefix}honeypot set <channel>` - Set the trap channel\n"
            f"`{prefix}honeypot log [channel]` - Set/clear log channel\n"
            f"`{prefix}honeypot action <ban|kick|role>` - Choose the punishment\n"
            f"`{prefix}honeypot punishrole [role]` - Set or clear the punish role\n"
            f"`{prefix}honeypot striproles <true|false>` - Toggle stripping old roles\n"
            f"`{prefix}honeypot stripexception` - Manage strip role exceptions\n"
            f"`{prefix}honeypot exempt` - View exempt roles\n"
            f"`{prefix}honeypot exempt add <role>` - Add exempt role\n"
            f"`{prefix}honeypot exempt remove <role>` - Remove exempt role"
        )
        embed.add_field(name="Commands", value=commands_text, inline=False)

        embed.set_footer(text="Users who message in the trap channel will be punished automatically.")

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @honeypot.command(name="set", aliases=["channel"])
    @commands.admin()
    async def honeypot_set(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set or update the honeypot channel."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        embed = discord.Embed(
            title="Trap Channel Updated",
            description=f"Now monitoring {channel.mention} for intruders.",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Anyone who sends a message there will be punished.")
        await ctx.send(embed=embed)

    @honeypot.command(name="log")
    @commands.admin()
    async def honeypot_log(
        self, ctx: commands.Context, channel: discord.TextChannel = None
    ):
        """Set or clear the honeypot log channel."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            embed = discord.Embed(
                title="Logging Disabled",
                description="Honeypot events will no longer be logged.",
                color=discord.Color.greyple(),
            )
            await ctx.send(embed=embed)
            return

        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        embed = discord.Embed(
            title="Log Channel Updated",
            description=f"Honeypot events will be logged to {channel.mention}.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @honeypot.command(name="action")
    @commands.admin()
    async def honeypot_action(self, ctx: commands.Context, action: str):
        """Choose the punishment applied when the honeypot is triggered."""
        action = action.lower()
        if action not in ACTION_CHOICES:
            embed = discord.Embed(
                title="Invalid Action",
                description=f"Choose one of: {', '.join(ACTION_CHOICES)}.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        await self.config.guild(ctx.guild).action.set(action)
        embed = discord.Embed(
            title="Punishment Updated",
            description=f"The honeypot now uses **{action}**.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @honeypot.command(name="punishrole")
    @commands.admin()
    async def honeypot_punish_role(self, ctx: commands.Context, role: discord.Role = None):
        """Configure the role to apply when the action is set to role."""
        role_id = role.id if role else None
        await self.config.guild(ctx.guild).punish_role_id.set(role_id)

        if role:
            embed = discord.Embed(
                title="Punish Role Set",
                description=f"{role.mention} will be applied to honeypot offenders.",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title="Punish Role Cleared",
                description="No role will be applied until you set one.",
                color=discord.Color.greyple(),
            )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @honeypot.command(name="striproles")
    @commands.admin()
    async def honeypot_strip_roles(self, ctx: commands.Context, toggle: bool):
        """Toggle whether existing roles are removed before applying the punish role."""
        await self.config.guild(ctx.guild).remove_other_roles.set(toggle)
        state = "enabled" if toggle else "disabled"
        embed = discord.Embed(
            title="Role Removal Updated",
            description=f"Role stripping has been **{state}**.",
            color=discord.Color.green() if toggle else discord.Color.greyple(),
        )
        await ctx.send(embed=embed)

    @honeypot.group(name="stripexception", aliases=["stripex"], invoke_without_command=True)
    @commands.admin()
    async def honeypot_strip_exception(self, ctx: commands.Context):
        """Manage exception roles that are kept when stripping roles."""
        await self._send_strip_exception_list(ctx)

    @honeypot_strip_exception.command(name="add")
    @commands.admin()
    async def honeypot_strip_exception_add(self, ctx: commands.Context, role: discord.Role):
        """Add a role to the role stripping exception list."""
        async with self.config.guild(ctx.guild).role_exception_ids() as exception_ids:
            if role.id in exception_ids:
                embed = discord.Embed(
                    title="Already Exception",
                    description=f"{role.mention} is already kept during stripping.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                return
            exception_ids.append(role.id)

        embed = discord.Embed(
            title="Exception Added",
            description=f"{role.mention} will be kept when roles are stripped.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @honeypot_strip_exception.command(name="remove")
    @commands.admin()
    async def honeypot_strip_exception_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the stripping exception list."""
        async with self.config.guild(ctx.guild).role_exception_ids() as exception_ids:
            if role.id not in exception_ids:
                embed = discord.Embed(
                    title="Not Exception",
                    description=f"{role.mention} is not on the exception list.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                return
            exception_ids.remove(role.id)

        embed = discord.Embed(
            title="Exception Removed",
            description=f"{role.mention} will now be removed unless exempted otherwise.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @honeypot_strip_exception.command(name="list")
    @commands.admin()
    async def honeypot_strip_exception_list(self, ctx: commands.Context):
        """List role stripping exceptions."""
        await self._send_strip_exception_list(ctx)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        channel_id = await self.config.guild(message.guild).channel_id()
        if not channel_id or message.channel.id != channel_id:
            return

        exempt = await self._is_exempt(message)
        if exempt:
            await self._send_log(
                message.guild,
                f"{message.author} was exempt from the honeypot in {message.channel.mention}.",
            )
            return

        cleanup_note = None
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        deleted_count = await self._purge_recent_messages_guild(message)
        cleanup_note = self._build_cleanup_note(deleted_count)

        guild_conf = await self.config.guild(message.guild).all()
        await self._apply_punishment(message, guild_conf, cleanup_note)

    async def _is_exempt(self, message: discord.Message) -> bool:
        exempt_role_ids = await self.config.guild(message.guild).exempt_roles()
        if not exempt_role_ids:
            return False

        member = message.guild.get_member(message.author.id)
        if not member:
            return False

        return any(role.id in exempt_role_ids for role in getattr(member, "roles", []))

    async def _apply_punishment(
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
                view = self._build_ban_review_view(guild, member)
                description = self._append_cleanup_note(
                    f"{member} was kicked for tripping the honeypot in {channel_mention}. Review and ban if necessary.",
                    cleanup_note,
                )
                await self._send_log(
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
                await self._send_log(
                    guild,
                    description,
                    target=member,
                    deleted_message=deleted_message,
                )
            return

        if action == "role":
            await self._apply_role_punishment(
                member, config, channel_mention, deleted_message, cleanup_note
            )
            return

        # Default to ban
        try:
            await guild.ban(
                member,
                reason=HONEYPOT_REASON,
                delete_message_days=1,
            )
            description = self._append_cleanup_note(
                f"{member} was banned for tripping the honeypot in {channel_mention}.",
                cleanup_note,
            )
            await self._send_log(
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
            await self._send_log(
                guild,
                description,
                target=member,
                deleted_message=deleted_message,
            )

    async def _apply_role_punishment(
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
            await self._send_log(
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
            view = self._build_ban_review_view(guild, member)
            description = self._append_cleanup_note(
                (
                    f"{member} was assigned {punish_role.mention} for tripping the honeypot in {channel_mention}. "
                    "Review and ban if necessary."
                ),
                cleanup_note,
            )
            await self._send_log(
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
            await self._send_log(
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
            await self._send_log(
                guild,
                description,
                target=member,
            )
            return False

    async def _purge_recent_messages_guild(
        self,
        trigger_message: discord.Message,
    ) -> int:
        guild = trigger_message.guild
        if not guild:
            return 0

        bot_member = guild.me
        member = guild.get_member(trigger_message.author.id)
        if not bot_member or not member:
            return 0

        cutoff = discord.utils.utcnow() - timedelta(hours=1)
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

    def _build_cleanup_note(self, deleted_count: int) -> Optional[str]:
        if not deleted_count:
            return None
        if deleted_count == 1:
            return "Removed 1 other message from the last hour."
        return f"Removed {deleted_count} other messages from the last hour across accessible channels."

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

    async def _send_strip_exception_list(self, ctx: commands.Context):
        exception_ids = await self.config.guild(ctx.guild).role_exception_ids()
        prefix = ctx.clean_prefix

        if not exception_ids:
            embed = discord.Embed(
                title="Role Strip Exceptions",
                description="No roles are currently kept when stripping is enabled.",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="Add Exception",
                value=f"`{prefix}honeypot stripexception add <role>`",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        roles = []
        for rid in exception_ids:
            role = ctx.guild.get_role(rid)
            if role:
                roles.append(role)

        if not roles:
            embed = discord.Embed(
                title="Role Strip Exceptions",
                description="Previously configured roles no longer exist. Consider reconfiguring.",
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)
            return

        role_list = "\n".join(f"- {role.mention}" for role in roles)
        embed = discord.Embed(
            title="Role Strip Exceptions",
            description=f"These roles will be preserved when stripping others:\n\n{role_list}",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Manage",
            value=f"`{prefix}honeypot stripexception add <role>`\n`{prefix}honeypot stripexception remove <role>`",
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _send_exempt_list(self, ctx: commands.Context):
        exempt_ids = await self.config.guild(ctx.guild).exempt_roles()
        prefix = ctx.clean_prefix

        if not exempt_ids:
            embed = discord.Embed(
                title="Exempt Roles",
                description="No roles are exempt from the honeypot.\n\nAll users who message in the trap channel will be punished.",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="Add Exemptions",
                value=f"`{prefix}honeypot exempt add <role>`",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        roles = []
        for rid in exempt_ids:
            role = ctx.guild.get_role(rid)
            if role:
                roles.append(role)

        if not roles:
            embed = discord.Embed(
                title="Exempt Roles",
                description="Previously configured roles no longer exist.\n\nYou may want to reconfigure exemptions.",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Add Exemptions",
                value=f"`{prefix}honeypot exempt add <role>`",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        role_list = "\n".join(f"- {role.mention}" for role in roles)
        embed = discord.Embed(
            title="Exempt Roles",
            description=f"These roles can message in the trap channel without being punished:\n\n{role_list}",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Manage",
            value=f"`{prefix}honeypot exempt add <role>`\n`{prefix}honeypot exempt remove <role>`",
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @honeypot.group(name="exempt", aliases=["ex"], invoke_without_command=True)
    @commands.admin()
    async def honeypot_exempt(self, ctx: commands.Context):
        """Manage honeypot exemption roles."""
        await self._send_exempt_list(ctx)

    @honeypot_exempt.command(name="add")
    @commands.admin()
    async def honeypot_exempt_add(self, ctx: commands.Context, role: discord.Role):
        """Add a role to the honeypot exemption list."""
        async with self.config.guild(ctx.guild).exempt_roles() as exempt_ids:
            if role.id in exempt_ids:
                embed = discord.Embed(
                    title="Already Exempt",
                    description=f"{role.mention} is already on the exemption list.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                return
            exempt_ids.append(role.id)

        embed = discord.Embed(
            title="Role Exempted",
            description=f"{role.mention} can now message in the trap channel without being punished.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @honeypot_exempt.command(name="remove")
    @commands.admin()
    async def honeypot_exempt_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the honeypot exemption list."""
        async with self.config.guild(ctx.guild).exempt_roles() as exempt_ids:
            if role.id not in exempt_ids:
                embed = discord.Embed(
                    title="Not Exempt",
                    description=f"{role.mention} is not on the exemption list.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                return
            exempt_ids.remove(role.id)

        embed = discord.Embed(
            title="Role Removed",
            description=f"{role.mention} is no longer exempt from the honeypot.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @honeypot_exempt.command(name="list")
    @commands.admin()
    async def honeypot_exempt_list(self, ctx: commands.Context):
        """List honeypot exempt roles."""
        await self._send_exempt_list(ctx)

    async def _send_log(
        self,
        guild: discord.Guild,
        description: str,
        *,
        target: discord.abc.User = None,
        view: discord.ui.View = None,
        deleted_message: str = None,
    ):
        log_channel_id = await self.config.guild(guild).log_channel_id()
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


async def setup(bot):
    await bot.add_cog(Honeypot(bot))
