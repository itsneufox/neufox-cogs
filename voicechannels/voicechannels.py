from __future__ import annotations

import asyncio

import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.blurple()
DASHBOARD_CUSTOM_ID = "voicechannels:create"
DEFAULT_NAME_TEMPLATE = "{member}'s Channel"


class VoiceDashboardView(discord.ui.View):
    def __init__(self, cog: "VoiceChannels"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Create Voice Channel",
        style=discord.ButtonStyle.primary,
        custom_id=DASHBOARD_CUSTOM_ID,
    )
    async def create_voice_channel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        await self.cog.create_member_channel(interaction)

    @discord.ui.button(
        label="Control My Channel",
        style=discord.ButtonStyle.secondary,
        custom_id="voicechannels:control",
    )
    async def control_voice_channel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        await self.cog.send_owner_dashboard(interaction)


class ChannelNameModal(discord.ui.Modal):
    def __init__(self, cog: "VoiceChannels", channel_id: int):
        super().__init__(title="Rename Voice Channel")
        self.cog = cog
        self.channel_id = channel_id
        self.name = discord.ui.TextInput(
            label="Channel name",
            max_length=100,
            placeholder="Study Room",
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.rename_owned_channel(interaction, self.channel_id, str(self.name))


class ChannelLimitModal(discord.ui.Modal):
    def __init__(self, cog: "VoiceChannels", channel_id: int):
        super().__init__(title="Set User Limit")
        self.cog = cog
        self.channel_id = channel_id
        self.limit = discord.ui.TextInput(
            label="User limit",
            max_length=2,
            placeholder="0-99, 0 means unlimited",
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.set_owned_channel_limit(interaction, self.channel_id, str(self.limit))


class MemberActionView(discord.ui.View):
    def __init__(self, cog: "VoiceChannels", owner_id: int, channel_id: int, action: str):
        super().__init__(timeout=60)
        self.add_item(MemberActionSelect(cog, owner_id, channel_id, action))


class MemberActionSelect(discord.ui.UserSelect):
    def __init__(self, cog: "VoiceChannels", owner_id: int, channel_id: int, action: str):
        super().__init__(placeholder=f"Choose a member to {action}", min_values=1, max_values=1)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This dashboard is not yours.", ephemeral=True)
            return
        member = self.values[0]
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("That member is not available.", ephemeral=True)
            return
        await self.cog.apply_member_action(interaction, self.channel_id, self.action, member)


class VoiceOwnerDashboardView(discord.ui.View):
    def __init__(self, cog: "VoiceChannels", owner_id: int, channel_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This dashboard is not yours.", ephemeral=True)
        return False

    @discord.ui.button(label="Name", style=discord.ButtonStyle.secondary, row=0)
    async def name_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelNameModal(self.cog, self.channel_id))

    @discord.ui.button(label="Limit", style=discord.ButtonStyle.secondary, row=0)
    async def limit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelLimitModal(self.cog, self.channel_id))

    @discord.ui.button(label="Privacy", style=discord.ButtonStyle.secondary, row=0)
    async def privacy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.toggle_privacy(interaction, self.channel_id)

    @discord.ui.button(label="Chat", style=discord.ButtonStyle.secondary, row=0)
    async def chat_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.toggle_chat(interaction, self.channel_id)

    @discord.ui.button(label="Invite", style=discord.ButtonStyle.primary, row=1)
    async def invite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(interaction, self.channel_id, "invite")

    @discord.ui.button(label="Trust", style=discord.ButtonStyle.success, row=1)
    async def trust_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(interaction, self.channel_id, "trust")

    @discord.ui.button(label="Untrust", style=discord.ButtonStyle.secondary, row=1)
    async def untrust_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(interaction, self.channel_id, "untrust")

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.danger, row=2)
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(interaction, self.channel_id, "kick")

    @discord.ui.button(label="Block", style=discord.ButtonStyle.danger, row=2)
    async def block_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(interaction, self.channel_id, "block")

    @discord.ui.button(label="Unblock", style=discord.ButtonStyle.secondary, row=2)
    async def unblock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(interaction, self.channel_id, "unblock")

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=3)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.delete_owned_channel(interaction, self.channel_id)


class VoiceChannels(commands.Cog):
    """Create on-demand temporary voice channels from a dashboard."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=684219730)
        self.config.register_guild(
            dashboard_channel_id=None,
            dashboard_message_id=None,
            category_id=None,
            channel_name_template=DEFAULT_NAME_TEMPLATE,
            user_limit=0,
            created_channels={},
        )
        self.bot.add_view(VoiceDashboardView(self))

    @commands.group(name="voicechannels", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def voicechannels(self, ctx: commands.Context):
        """Manage on-demand voice channels."""
        await ctx.invoke(self.voicechannels_show)

    @voicechannels.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True, embed_links=True, manage_channels=True, move_members=True)
    async def voicechannels_setup(
        self,
        ctx: commands.Context,
        dashboard_channel: discord.TextChannel,
        category: discord.CategoryChannel | None = None,
    ):
        """Configure and post the voice channel dashboard."""
        category = category or dashboard_channel.category
        await self.config.guild(ctx.guild).dashboard_channel_id.set(dashboard_channel.id)
        await self.config.guild(ctx.guild).category_id.set(category.id if category else None)

        message = await dashboard_channel.send(
            embed=self._dashboard_embed(ctx.guild, category),
            view=VoiceDashboardView(self),
        )
        await self.config.guild(ctx.guild).dashboard_message_id.set(message.id)
        await ctx.send(f"Voice channel dashboard posted in {dashboard_channel.mention}.")

    @voicechannels.command(name="dashboard")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def voicechannels_dashboard(self, ctx: commands.Context):
        """Repost the dashboard using the current configuration."""
        data = await self.config.guild(ctx.guild).all()
        dashboard_channel = self._text_channel(ctx.guild, data["dashboard_channel_id"])
        category = self._category(ctx.guild, data["category_id"])
        if dashboard_channel is None:
            await ctx.send("Set a dashboard channel first with `voicechannels setup <channel> [category]`.")
            return

        message = await dashboard_channel.send(
            embed=self._dashboard_embed(ctx.guild, category),
            view=VoiceDashboardView(self),
        )
        await self.config.guild(ctx.guild).dashboard_message_id.set(message.id)
        await ctx.send(f"Voice channel dashboard posted in {dashboard_channel.mention}.")

    @voicechannels.command(name="category")
    @commands.admin_or_permissions(manage_guild=True)
    async def voicechannels_category(
        self,
        ctx: commands.Context,
        category: discord.CategoryChannel | None = None,
    ):
        """Set or clear the category used for created voice channels."""
        await self.config.guild(ctx.guild).category_id.set(category.id if category else None)
        if category:
            await ctx.send(f"New voice channels will be created under **{category.name}**.")
        else:
            await ctx.send("New voice channels will use Discord's default placement.")

    @voicechannels.command(name="name")
    @commands.admin_or_permissions(manage_guild=True)
    async def voicechannels_name(self, ctx: commands.Context, *, template: str):
        """Set the created channel name. Use {member} for the creator name."""
        if len(template) > 80:
            await ctx.send("Channel name template must be 80 characters or less.")
            return
        await self.config.guild(ctx.guild).channel_name_template.set(template)
        await ctx.send(f"Voice channel name template set to `{template}`.")

    @voicechannels.command(name="limit")
    @commands.admin_or_permissions(manage_guild=True)
    async def voicechannels_limit(self, ctx: commands.Context, limit: int = 0):
        """Set the user limit for created channels. Use 0 for no limit."""
        if limit < 0 or limit > 99:
            await ctx.send("User limit must be between 0 and 99.")
            return
        await self.config.guild(ctx.guild).user_limit.set(limit)
        await ctx.send("Created voice channels will have no user limit." if limit == 0 else f"Created voice channels will have a user limit of {limit}.")

    @voicechannels.command(name="show")
    @commands.admin_or_permissions(manage_guild=True)
    async def voicechannels_show(self, ctx: commands.Context):
        """Show current VoiceChannels configuration."""
        data = await self.config.guild(ctx.guild).all()
        dashboard_channel = self._text_channel(ctx.guild, data["dashboard_channel_id"])
        category = self._category(ctx.guild, data["category_id"])
        active_count = len(data["created_channels"])

        embed = discord.Embed(title="VoiceChannels Configuration", color=DEFAULT_COLOR)
        embed.add_field(
            name="Dashboard Channel",
            value=dashboard_channel.mention if dashboard_channel else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Category",
            value=category.name if category else "Default placement",
            inline=True,
        )
        embed.add_field(
            name="User Limit",
            value=str(data["user_limit"]) if data["user_limit"] else "None",
            inline=True,
        )
        embed.add_field(name="Name Template", value=f"`{data['channel_name_template']}`", inline=False)
        embed.add_field(name="Tracked Channels", value=str(active_count), inline=True)

        prefix = ctx.clean_prefix
        embed.add_field(
            name="Commands",
            value=(
                f"`{prefix}voicechannels setup <dashboard_channel> [category]`\n"
                f"`{prefix}voicechannels dashboard`\n"
                f"`{prefix}voicechannels category [category]`\n"
                f"`{prefix}voicechannels name <template>`\n"
                f"`{prefix}voicechannels limit [0-99]`\n"
                f"`{prefix}voicechannels cleanup`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Member Dashboard",
            value=(
                "Members use the dashboard buttons to create and control their own temporary channels: "
                "name, limit, privacy, chat, invite, trust, untrust, kick, block, unblock, and delete."
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @voicechannels.command(name="cleanup")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def voicechannels_cleanup(self, ctx: commands.Context):
        """Delete empty tracked channels and forget missing ones."""
        deleted, forgotten = await self.cleanup_guild_channels(ctx.guild)
        await ctx.send(f"Cleanup complete: deleted {deleted}, forgot {forgotten}.")

    async def create_member_channel(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await self.config.guild(guild).all()

        existing = self._existing_owned_channel(guild, data["created_channels"], member.id)
        if existing is not None:
            await interaction.followup.send(
                f"You already have {existing.mention}.",
                ephemeral=True,
            )
            return

        category = self._category(guild, data["category_id"])
        name = self._channel_name(data["channel_name_template"], member)
        overwrites = {
            member: discord.PermissionOverwrite(
                manage_channels=True,
                move_members=True,
                connect=True,
                speak=True,
            )
        }

        try:
            channel = await guild.create_voice_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                user_limit=int(data["user_limit"]) or 0,
                reason=f"VoiceChannels dashboard used by {member} ({member.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to create voice channels here.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord rejected the voice channel creation request. Try again later.",
                ephemeral=True,
            )
            return

        async with self.config.guild(guild).created_channels() as created_channels:
            created_channels[str(channel.id)] = self._new_channel_record(member.id)

        moved = False
        if member.voice and member.voice.channel:
            try:
                await member.move_to(channel, reason="VoiceChannels dashboard channel created")
                moved = True
            except (discord.Forbidden, discord.HTTPException):
                moved = False

        if moved:
            record = await self._channel_record(guild, channel.id)
            await interaction.followup.send(
                f"Created {channel.mention} and moved you in.",
                embed=self._owner_dashboard_embed(channel, record),
                view=VoiceOwnerDashboardView(self, member.id, channel.id),
                ephemeral=True,
            )
        else:
            record = await self._channel_record(guild, channel.id)
            await interaction.followup.send(
                f"Created {channel.mention}. Join it within 5 minutes or it will be deleted.",
                embed=self._owner_dashboard_embed(channel, record),
                view=VoiceOwnerDashboardView(self, member.id, channel.id),
                ephemeral=True,
            )
            self.bot.loop.create_task(self._delete_if_empty_after(guild.id, channel.id, 300))

    async def send_owner_dashboard(self, interaction: discord.Interaction):
        channel, record = await self._owned_channel_and_record(interaction.guild, interaction.user.id)
        if channel is None or record is None:
            await interaction.response.send_message("You do not have an active voice channel.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self._owner_dashboard_embed(channel, record),
            view=VoiceOwnerDashboardView(self, interaction.user.id, channel.id),
            ephemeral=True,
        )

    async def rename_owned_channel(self, interaction: discord.Interaction, channel_id: int, name: str):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        name = name.strip()[:100]
        if not name:
            await interaction.response.send_message("Channel name cannot be empty.", ephemeral=True)
            return
        try:
            await channel.edit(name=name, reason=f"VoiceChannels rename by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("I could not rename that channel.", ephemeral=True)
            return
        await interaction.response.send_message(f"Renamed your channel to **{name}**.", ephemeral=True)

    async def set_owned_channel_limit(self, interaction: discord.Interaction, channel_id: int, raw_limit: str):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        try:
            limit = int(raw_limit)
        except ValueError:
            await interaction.response.send_message("Limit must be a number from 0 to 99.", ephemeral=True)
            return
        if limit < 0 or limit > 99:
            await interaction.response.send_message("Limit must be between 0 and 99.", ephemeral=True)
            return
        try:
            await channel.edit(user_limit=limit, reason=f"VoiceChannels limit by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("I could not update that channel limit.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Removed the user limit." if limit == 0 else f"Set user limit to {limit}.",
            ephemeral=True,
        )

    async def toggle_privacy(self, interaction: discord.Interaction, channel_id: int):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        async with self.config.guild(interaction.guild).created_channels() as created_channels:
            record = self._normalize_record(created_channels[str(channel_id)])
            record["private"] = not bool(record.get("private"))
            created_channels[str(channel_id)] = record
        await self._apply_access_overwrites(interaction.guild, channel, record)
        await interaction.response.send_message(
            "Your channel is now private." if record["private"] else "Your channel is now public.",
            ephemeral=True,
        )

    async def toggle_chat(self, interaction: discord.Interaction, channel_id: int):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        record = await self._channel_record(interaction.guild, channel_id)
        text_channel_id = record.get("text_channel_id")
        text_channel = interaction.guild.get_channel(int(text_channel_id)) if text_channel_id else None
        if isinstance(text_channel, discord.TextChannel):
            try:
                await text_channel.delete(reason="VoiceChannels chat disabled")
            except (discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message("I could not delete that chat channel.", ephemeral=True)
                return
            async with self.config.guild(interaction.guild).created_channels() as created_channels:
                record = self._normalize_record(created_channels[str(channel_id)])
                record["text_channel_id"] = None
                created_channels[str(channel_id)] = record
            await interaction.response.send_message("Deleted your linked chat channel.", ephemeral=True)
            return

        try:
            text_channel = await interaction.guild.create_text_channel(
                name=channel.name,
                category=channel.category,
                overwrites=dict(channel.overwrites),
                reason=f"VoiceChannels chat created by {interaction.user}",
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("I could not create a chat channel.", ephemeral=True)
            return
        async with self.config.guild(interaction.guild).created_channels() as created_channels:
            record = self._normalize_record(created_channels[str(channel_id)])
            record["text_channel_id"] = text_channel.id
            created_channels[str(channel_id)] = record
        await interaction.response.send_message(f"Created linked chat channel {text_channel.mention}.", ephemeral=True)

    async def send_member_picker(self, interaction: discord.Interaction, channel_id: int, action: str):
        channel = await self._validated_owned_channel(interaction, channel_id, respond=False)
        if channel is None:
            await interaction.response.send_message("That is not your active channel anymore.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Choose a member to {action}.",
            view=MemberActionView(self, interaction.user.id, channel_id, action),
            ephemeral=True,
        )

    async def apply_member_action(
        self,
        interaction: discord.Interaction,
        channel_id: int,
        action: str,
        member: discord.Member,
    ):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message("You cannot use that action on yourself.", ephemeral=True)
            return

        async with self.config.guild(interaction.guild).created_channels() as created_channels:
            record = self._normalize_record(created_channels[str(channel_id)])
            trusted = set(int(user_id) for user_id in record.get("trusted_ids", []))
            blocked = set(int(user_id) for user_id in record.get("blocked_ids", []))
            if action in {"invite", "trust"}:
                trusted.add(member.id)
                blocked.discard(member.id)
            elif action == "untrust":
                trusted.discard(member.id)
            elif action == "block":
                blocked.add(member.id)
                trusted.discard(member.id)
            elif action == "unblock":
                blocked.discard(member.id)
            record["trusted_ids"] = list(trusted)
            record["blocked_ids"] = list(blocked)
            created_channels[str(channel_id)] = record

        await self._apply_access_overwrites(interaction.guild, channel, record)
        if action in {"kick", "block"} and member.voice and member.voice.channel == channel:
            try:
                await member.move_to(None, reason=f"VoiceChannels {action} by {interaction.user}")
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.response.send_message(f"Applied `{action}` to {member.mention}.", ephemeral=True)

    async def delete_owned_channel(self, interaction: discord.Interaction, channel_id: int):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        record = await self._channel_record(interaction.guild, channel_id)
        text_channel_id = record.get("text_channel_id")
        text_channel = interaction.guild.get_channel(int(text_channel_id)) if text_channel_id else None
        try:
            await channel.delete(reason=f"VoiceChannels deleted by owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("I could not delete your voice channel.", ephemeral=True)
            return
        if isinstance(text_channel, discord.TextChannel):
            try:
                await text_channel.delete(reason="VoiceChannels linked chat cleanup")
            except (discord.Forbidden, discord.HTTPException):
                pass
        async with self.config.guild(interaction.guild).created_channels() as created_channels:
            created_channels.pop(str(channel_id), None)
        await interaction.response.send_message("Deleted your voice channel.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if before.channel is None or before.channel == after.channel:
            return
        if not isinstance(before.channel, discord.VoiceChannel):
            return

        created_channels = await self.config.guild(member.guild).created_channels()
        if str(before.channel.id) not in created_channels:
            return
        if before.channel.members:
            return

        try:
            await before.channel.delete(reason="VoiceChannels temporary channel empty")
            deleted = True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            deleted = False

        if deleted:
            async with self.config.guild(member.guild).created_channels() as stored_channels:
                record = self._normalize_record(stored_channels.pop(str(before.channel.id), {}))
            text_channel_id = record.get("text_channel_id")
            text_channel = member.guild.get_channel(int(text_channel_id)) if text_channel_id else None
            if isinstance(text_channel, discord.TextChannel):
                try:
                    await text_channel.delete(reason="VoiceChannels linked chat cleanup")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def cleanup_guild_channels(self, guild: discord.Guild) -> tuple[int, int]:
        deleted = 0
        forgotten = 0
        async with self.config.guild(guild).created_channels() as created_channels:
            for channel_id in list(created_channels):
                channel = guild.get_channel(int(channel_id))
                if channel is None:
                    created_channels.pop(channel_id, None)
                    forgotten += 1
                    continue
                if not isinstance(channel, discord.VoiceChannel):
                    continue
                if channel.members:
                    continue
                try:
                    await channel.delete(reason="VoiceChannels cleanup")
                    deleted += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue
                record = self._normalize_record(created_channels.get(channel_id))
                text_channel_id = record.get("text_channel_id")
                text_channel = guild.get_channel(int(text_channel_id)) if text_channel_id else None
                if isinstance(text_channel, discord.TextChannel):
                    try:
                        await text_channel.delete(reason="VoiceChannels linked chat cleanup")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                created_channels.pop(channel_id, None)
        return deleted, forgotten

    async def _delete_if_empty_after(self, guild_id: int, channel_id: int, delay: int):
        await asyncio.sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel) or channel.members:
            return
        created_channels = await self.config.guild(guild).created_channels()
        if str(channel.id) not in created_channels:
            return
        try:
            await channel.delete(reason="VoiceChannels created channel remained empty")
        except (discord.Forbidden, discord.HTTPException):
            return
        async with self.config.guild(guild).created_channels() as stored_channels:
            record = self._normalize_record(stored_channels.pop(str(channel.id), {}))
        text_channel_id = record.get("text_channel_id")
        text_channel = guild.get_channel(int(text_channel_id)) if text_channel_id else None
        if isinstance(text_channel, discord.TextChannel):
            try:
                await text_channel.delete(reason="VoiceChannels linked chat cleanup")
            except (discord.Forbidden, discord.HTTPException):
                pass

    def _dashboard_embed(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel | None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Create a Voice Channel",
            description="Press the button below to create a temporary voice channel.",
            color=DEFAULT_COLOR,
        )
        embed.add_field(
            name="Category",
            value=category.name if category else "Default placement",
            inline=True,
        )
        embed.set_footer(text="Use Control My Channel after creating one. Empty channels are deleted automatically.")
        return embed

    def _owner_dashboard_embed(
        self,
        channel: discord.VoiceChannel,
        record: dict,
    ) -> discord.Embed:
        trusted = len(record.get("trusted_ids", []))
        blocked = len(record.get("blocked_ids", []))
        embed = discord.Embed(
            title="Voice Channel Controls",
            description=f"Managing {channel.mention}",
            color=DEFAULT_COLOR,
        )
        embed.add_field(name="Privacy", value="Private" if record.get("private") else "Public", inline=True)
        embed.add_field(name="User Limit", value=str(channel.user_limit) if channel.user_limit else "None", inline=True)
        embed.add_field(name="Trusted", value=str(trusted), inline=True)
        embed.add_field(name="Blocked", value=str(blocked), inline=True)
        text_channel_id = record.get("text_channel_id")
        text = channel.guild.get_channel(int(text_channel_id)) if text_channel_id else None
        embed.add_field(
            name="Chat",
            value=text.mention if isinstance(text, discord.TextChannel) else "Disabled",
            inline=True,
        )
        return embed

    def _channel_name(self, template: str, member: discord.Member) -> str:
        name = template.replace("{member}", member.display_name)
        return name[:100] or DEFAULT_NAME_TEMPLATE.replace("{member}", member.display_name)

    def _existing_owned_channel(
        self,
        guild: discord.Guild,
        created_channels: dict[str, int | dict],
        owner_id: int,
    ) -> discord.VoiceChannel | None:
        for channel_id, raw_record in created_channels.items():
            record = self._normalize_record(raw_record)
            if int(record["owner_id"]) != owner_id:
                continue
            channel = guild.get_channel(int(channel_id))
            if isinstance(channel, discord.VoiceChannel):
                return channel
        return None

    async def _owned_channel_and_record(
        self,
        guild: discord.Guild,
        owner_id: int,
    ) -> tuple[discord.VoiceChannel | None, dict | None]:
        created_channels = await self.config.guild(guild).created_channels()
        for channel_id, raw_record in created_channels.items():
            record = self._normalize_record(raw_record)
            if int(record["owner_id"]) != owner_id:
                continue
            channel = guild.get_channel(int(channel_id))
            if isinstance(channel, discord.VoiceChannel):
                return channel, record
        return None, None

    async def _validated_owned_channel(
        self,
        interaction: discord.Interaction,
        channel_id: int,
        respond: bool = True,
    ) -> discord.VoiceChannel | None:
        if interaction.guild is None:
            if respond:
                await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return None
        record = await self._channel_record(interaction.guild, channel_id)
        channel = interaction.guild.get_channel(channel_id)
        if (
            record is None
            or not isinstance(channel, discord.VoiceChannel)
            or int(record["owner_id"]) != interaction.user.id
        ):
            if respond:
                await interaction.response.send_message("That is not your active channel anymore.", ephemeral=True)
            return None
        return channel

    async def _channel_record(self, guild: discord.Guild, channel_id: int) -> dict | None:
        created_channels = await self.config.guild(guild).created_channels()
        raw_record = created_channels.get(str(channel_id))
        if raw_record is None:
            return None
        return self._normalize_record(raw_record)

    async def _apply_access_overwrites(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        record: dict,
    ):
        everyone = guild.default_role
        try:
            if record.get("private"):
                await channel.set_permissions(everyone, connect=False, reason="VoiceChannels privacy update")
            else:
                await channel.set_permissions(everyone, connect=None, reason="VoiceChannels privacy update")
        except (discord.Forbidden, discord.HTTPException):
            pass

        owner = guild.get_member(int(record["owner_id"]))
        if owner:
            try:
                await channel.set_permissions(
                    owner,
                    connect=True,
                    speak=True,
                    manage_channels=True,
                    move_members=True,
                    reason="VoiceChannels owner permissions",
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        for user_id in record.get("trusted_ids", []):
            member = guild.get_member(int(user_id))
            if member:
                try:
                    await channel.set_permissions(member, connect=True, speak=True, reason="VoiceChannels trusted user")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        for user_id in record.get("blocked_ids", []):
            member = guild.get_member(int(user_id))
            if member:
                try:
                    await channel.set_permissions(member, connect=False, speak=False, reason="VoiceChannels blocked user")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        text_channel_id = record.get("text_channel_id")
        text_channel = guild.get_channel(int(text_channel_id)) if text_channel_id else None
        if isinstance(text_channel, discord.TextChannel):
            try:
                await text_channel.edit(overwrites=dict(channel.overwrites), reason="VoiceChannels chat permissions sync")
            except (discord.Forbidden, discord.HTTPException):
                pass

    @staticmethod
    def _new_channel_record(owner_id: int) -> dict:
        return {
            "owner_id": owner_id,
            "private": False,
            "trusted_ids": [],
            "blocked_ids": [],
            "text_channel_id": None,
        }

    def _normalize_record(self, raw_record: int | dict | None) -> dict:
        if isinstance(raw_record, dict):
            record = self._new_channel_record(int(raw_record.get("owner_id", 0)))
            record.update(raw_record)
            record["trusted_ids"] = [int(user_id) for user_id in record.get("trusted_ids", [])]
            record["blocked_ids"] = [int(user_id) for user_id in record.get("blocked_ids", [])]
            return record
        return self._new_channel_record(int(raw_record or 0))

    @staticmethod
    def _text_channel(guild: discord.Guild, channel_id: int | None) -> discord.TextChannel | None:
        if not channel_id:
            return None
        channel = guild.get_channel(int(channel_id))
        return channel if isinstance(channel, discord.TextChannel) else None

    @staticmethod
    def _category(guild: discord.Guild, category_id: int | None) -> discord.CategoryChannel | None:
        if not category_id:
            return None
        category = guild.get_channel(int(category_id))
        return category if isinstance(category, discord.CategoryChannel) else None
