from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import discord
from redbot.core import Config, commands


BADWORDS_DIR = Path(__file__).with_name("badwords")
DEFAULT_COLOR = discord.Color.blurple()
DASHBOARD_CUSTOM_ID = "voicechannels:create"
DEFAULT_NAME_TEMPLATE = "{member}'s Channel"
EMPTY_DELETE_DELAY_SECONDS = 120
CHANNEL_NAME_MAX_LENGTH = 80
CHANNEL_NAME_BLOCKED_CHARS = re.compile(r"[@#:`*_~>|\\/<>\[\]{}]+")
CHANNEL_NAME_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
CHANNEL_NAME_SPACES = re.compile(r"\s+")
CHANNEL_NAME_LEET_TRANSLATION = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "$": "s",
        "!": "i",
    }
)


def _load_channel_name_profanity_terms() -> set[str]:
    fallback_terms = {
        "asshole",
        "bastard",
        "bitch",
        "cunt",
        "fag",
        "faggot",
        "fuck",
        "nigga",
        "nigger",
        "shit",
        "slut",
        "whore",
    }
    terms = set(fallback_terms)
    try:
        wordlist_paths = sorted(BADWORDS_DIR.glob("*.json"))
    except OSError:
        return fallback_terms
    for path in wordlist_paths:
        try:
            wordlist = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        terms.update(str(term).strip().lower() for term in wordlist if str(term).strip())
    return terms


def _compile_channel_name_profanity_pattern(terms: set[str]) -> re.Pattern[str]:
    escaped_terms = [
        re.escape(term).replace(r"\ ", r"\s+")
        for term in sorted(terms, key=len, reverse=True)
    ]
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(escaped_terms) + r")(?![A-Za-z0-9])", re.IGNORECASE)


def _normalize_profanity_term(term: str) -> str:
    return "".join(re.findall(r"[a-z]+", term.lower().translate(CHANNEL_NAME_LEET_TRANSLATION)))


CHANNEL_NAME_PROFANITY_TERMS = _load_channel_name_profanity_terms()
CHANNEL_NAME_PROFANITY = _compile_channel_name_profanity_pattern(CHANNEL_NAME_PROFANITY_TERMS)
CHANNEL_NAME_NORMALIZED_PROFANITY_TERMS = {
    normalized
    for normalized in (_normalize_profanity_term(term) for term in CHANNEL_NAME_PROFANITY_TERMS)
    if len(normalized) >= 4
}


class VoiceDashboardView(discord.ui.View):
    def __init__(self, cog: "VoiceChannels"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Create Channel",
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
        label="Manage Channel",
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


class ChannelSettingsModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "VoiceChannels",
        channel_id: int,
        current_name: str,
        current_limit: int,
    ):
        super().__init__(title="Channel Settings")
        self.cog = cog
        self.channel_id = channel_id
        self.name = discord.ui.TextInput(
            label="Channel name",
            max_length=100,
            placeholder="Study Room",
            default=current_name,
        )
        self.limit = discord.ui.TextInput(
            label="User limit",
            max_length=2,
            placeholder="0-99, 0 means unlimited",
            default=str(current_limit),
            required=False,
        )
        self.add_item(self.name)
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.update_owned_channel_settings(
            interaction,
            self.channel_id,
            str(self.name),
            str(self.limit),
        )


class MemberActionView(discord.ui.View):
    def __init__(self, cog: "VoiceChannels", owner_id: int, channel_id: int, actions: dict[str, str]):
        super().__init__(timeout=60)
        self.add_item(MemberActionSelect(cog, owner_id, channel_id, actions))


class MemberActionSelect(discord.ui.UserSelect):
    def __init__(self, cog: "VoiceChannels", owner_id: int, channel_id: int, actions: dict[str, str]):
        super().__init__(placeholder="Choose a member", min_values=1, max_values=1)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.actions = actions

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This dashboard is not yours.", ephemeral=True)
            return
        member = self.values[0]
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("That member is not available.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"What should I do with {member.mention}?",
            view=MemberActionButtons(self.cog, self.owner_id, self.channel_id, member, self.actions),
            ephemeral=True,
        )


class MemberActionButtons(discord.ui.View):
    def __init__(
        self,
        cog: "VoiceChannels",
        owner_id: int,
        channel_id: int,
        member: discord.Member,
        actions: dict[str, str],
    ):
        super().__init__(timeout=60)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.member = member
        for action, label in actions.items():
            style = discord.ButtonStyle.danger if action in {"kick", "block"} else discord.ButtonStyle.secondary
            if action in {"invite", "trust", "transfer"}:
                style = discord.ButtonStyle.primary
            self.add_item(MemberActionButton(action, label, style))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This dashboard is not yours.", ephemeral=True)
        return False


class MemberActionButton(discord.ui.Button):
    def __init__(self, action: str, label: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, MemberActionButtons):
            await interaction.response.send_message("This control expired.", ephemeral=True)
            return
        await view.cog.apply_member_action(
            interaction,
            view.channel_id,
            self.action,
            view.member,
        )


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

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.secondary, row=0)
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog._validated_owned_channel(interaction, self.channel_id, respond=False)
        if channel is None:
            await interaction.response.send_message("That is not your active channel anymore.", ephemeral=True)
            return
        await interaction.response.send_modal(
            ChannelSettingsModal(self.cog, self.channel_id, channel.name, channel.user_limit)
        )

    @discord.ui.button(label="Access", style=discord.ButtonStyle.primary, row=0)
    async def access_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(
            interaction,
            self.channel_id,
            {
                "invite": "Invite",
                "trust": "Allow",
                "untrust": "Remove Allow",
                "unblock": "Unblock",
            },
            "Choose a member to invite, allow, or unblock.",
        )

    @discord.ui.button(label="Moderation", style=discord.ButtonStyle.danger, row=0)
    async def moderation_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(
            interaction,
            self.channel_id,
            {
                "kick": "Kick",
                "block": "Block",
            },
            "Choose a member to kick or block.",
        )

    @discord.ui.button(label="Privacy", style=discord.ButtonStyle.secondary, row=1)
    async def privacy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.toggle_privacy(interaction, self.channel_id)

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.primary, row=1)
    async def transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_member_picker(
            interaction,
            self.channel_id,
            {"transfer": "Transfer Owner"},
            "Choose the new owner for this temporary channel.",
        )

    @discord.ui.button(label="Refresh Panel", style=discord.ButtonStyle.secondary, row=1)
    async def panel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.post_voice_channel_dashboard(interaction, self.channel_id)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=1)
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
        warnings = self._setup_warnings(ctx.guild, dashboard_channel, category)
        if warnings:
            await ctx.send(
                f"Voice channel dashboard posted in {dashboard_channel.mention}.\n"
                "Permission checks to review:\n" + "\n".join(f"- {warning}" for warning in warnings)
            )
        else:
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
        template = self._clean_channel_name(template, fallback=DEFAULT_NAME_TEMPLATE, allow_member_placeholder=True)
        if len(template) > CHANNEL_NAME_MAX_LENGTH:
            await ctx.send(f"Channel name template must be {CHANNEL_NAME_MAX_LENGTH} characters or less.")
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
                "settings, privacy, invite, allow, remove allow, kick, block, unblock, transfer, refresh panel, and delete."
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

        panel_posted = await self._send_voice_channel_dashboard_message(channel, member.id)

        moved = False
        if member.voice and member.voice.channel:
            try:
                await member.move_to(channel, reason="VoiceChannels dashboard channel created")
                moved = True
            except (discord.Forbidden, discord.HTTPException):
                moved = False

        if moved:
            await interaction.followup.send(
                self._creation_notice(channel, moved=True, panel_posted=panel_posted),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                self._creation_notice(channel, moved=False, panel_posted=panel_posted),
                ephemeral=True,
            )
            self.bot.loop.create_task(self._delete_if_empty_after(guild.id, channel.id, EMPTY_DELETE_DELAY_SECONDS))

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

    async def post_voice_channel_dashboard(self, interaction: discord.Interaction, channel_id: int):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        if await self._send_voice_channel_dashboard_message(channel, interaction.user.id):
            await interaction.response.send_message("Posted a fresh control panel in the voice channel chat.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "I could not post in that voice channel chat. Check my Send Messages permissions.",
                ephemeral=True,
            )

    async def update_owned_channel_settings(
        self,
        interaction: discord.Interaction,
        channel_id: int,
        name: str,
        raw_limit: str,
    ):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        name = name.strip()[:100]
        if not name:
            await interaction.response.send_message("Channel name cannot be empty.", ephemeral=True)
            return
        raw_limit = raw_limit.strip() or "0"
        try:
            limit = int(raw_limit)
        except ValueError:
            await interaction.response.send_message("Limit must be a number from 0 to 99.", ephemeral=True)
            return
        if limit < 0 or limit > 99:
            await interaction.response.send_message("Limit must be between 0 and 99.", ephemeral=True)
            return
        try:
            await channel.edit(name=name, user_limit=limit, reason=f"VoiceChannels settings by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("I could not update that channel.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Updated your channel settings: **{name}**, limit {'none' if limit == 0 else limit}.",
            ephemeral=True,
        )
        await self._send_voice_channel_dashboard_message(channel, interaction.user.id)

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
        await self._send_voice_channel_dashboard_message(channel, interaction.user.id)

    async def send_member_picker(
        self,
        interaction: discord.Interaction,
        channel_id: int,
        actions: dict[str, str],
        prompt: str,
    ):
        channel = await self._validated_owned_channel(interaction, channel_id, respond=False)
        if channel is None:
            await interaction.response.send_message("That is not your active channel anymore.", ephemeral=True)
            return
        await interaction.response.send_message(
            prompt,
            view=MemberActionView(self, interaction.user.id, channel_id, actions),
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
        if action == "transfer":
            if member.bot:
                await interaction.response.send_message("You cannot transfer ownership to a bot.", ephemeral=True)
                return
            existing = self._existing_owned_channel(
                interaction.guild,
                await self.config.guild(interaction.guild).created_channels(),
                member.id,
            )
            if existing is not None and existing.id != channel_id:
                await interaction.response.send_message(
                    f"{member.mention} already owns {existing.mention}.",
                    ephemeral=True,
                )
                return

        async with self.config.guild(interaction.guild).created_channels() as created_channels:
            record = self._normalize_record(created_channels[str(channel_id)])
            old_owner_id = int(record["owner_id"])
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
            elif action == "transfer":
                record["owner_id"] = member.id
                trusted.add(old_owner_id)
                trusted.discard(member.id)
                blocked.discard(member.id)
            record["trusted_ids"] = list(trusted)
            record["blocked_ids"] = list(blocked)
            created_channels[str(channel_id)] = record

        await self._apply_access_overwrites(interaction.guild, channel, record)
        if action == "transfer":
            await self._clear_previous_owner_permissions(interaction.guild, channel, old_owner_id)
        if action in {"kick", "block"} and member.voice and member.voice.channel == channel:
            try:
                await member.move_to(None, reason=f"VoiceChannels {action} by {interaction.user}")
            except (discord.Forbidden, discord.HTTPException):
                pass
        if action == "invite":
            notified = await self._notify_invited_member(interaction.user, member, channel, record)
            suffix = " I sent them a DM invite." if notified else " I could not DM them, but they can join now."
            await interaction.response.send_message(f"Invited {member.mention}.{suffix}", ephemeral=True)
            await self._send_voice_channel_dashboard_message(channel, interaction.user.id)
            return

        responses = {
            "trust": f"Allowed {member.mention} to join your private channel.",
            "untrust": f"Removed {member.mention} from the allowed list.",
            "kick": f"Kicked {member.mention} from your channel if they were inside.",
            "block": f"Blocked {member.mention} from joining your channel.",
            "unblock": f"Unblocked {member.mention}.",
            "transfer": f"Transferred ownership to {member.mention}.",
        }
        await interaction.response.send_message(responses[action], ephemeral=True)
        if action == "transfer":
            await self._send_voice_channel_dashboard_message(channel, member.id)
        else:
            await self._send_voice_channel_dashboard_message(channel, interaction.user.id)

    async def delete_owned_channel(self, interaction: discord.Interaction, channel_id: int):
        channel = await self._validated_owned_channel(interaction, channel_id)
        if channel is None:
            return
        try:
            await channel.delete(reason=f"VoiceChannels deleted by owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("I could not delete your voice channel.", ephemeral=True)
            return
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

        self.bot.loop.create_task(
            self._delete_if_empty_after(member.guild.id, before.channel.id, EMPTY_DELETE_DELAY_SECONDS)
        )

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
            await channel.delete(reason="VoiceChannels temporary channel remained empty")
        except (discord.Forbidden, discord.HTTPException):
            return
        async with self.config.guild(guild).created_channels() as stored_channels:
            stored_channels.pop(str(channel.id), None)

    async def _send_voice_channel_dashboard_message(
        self,
        channel: discord.VoiceChannel,
        owner_id: int,
    ) -> bool:
        record = await self._channel_record(channel.guild, channel.id)
        if record is None:
            return False
        try:
            await channel.send(
                embed=self._owner_dashboard_embed(channel, record),
                view=VoiceOwnerDashboardView(self, owner_id, channel.id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except (AttributeError, discord.Forbidden, discord.HTTPException):
            return False

    def _dashboard_embed(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel | None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Temporary Voice Channels",
            description=(
                "Create a temporary voice channel when you need one.\n"
                "Your controls will appear in the text chat attached to the voice channel."
            ),
            color=DEFAULT_COLOR,
        )
        embed.set_footer(text="Empty temporary channels are deleted after 2 minutes.")
        return embed

    def _creation_notice(
        self,
        channel: discord.VoiceChannel,
        *,
        moved: bool,
        panel_posted: bool,
    ) -> str:
        status = f"Created {channel.mention}"
        if moved:
            status += " and moved you in."
        else:
            status += ". Join it within 2 minutes or it will be deleted."

        if panel_posted:
            return (
                f"{status}\n"
                "Your channel settings are in the text chat attached to that voice channel."
            )

        return (
            f"{status}\n"
            "I could not post the settings panel in the voice channel text chat. "
            "Use **Manage Channel** on the main dashboard to open your controls."
        )

    def _owner_dashboard_embed(
        self,
        channel: discord.VoiceChannel,
        record: dict,
    ) -> discord.Embed:
        owner = channel.guild.get_member(int(record["owner_id"]))
        allowed = len(record.get("trusted_ids", []))
        blocked = len(record.get("blocked_ids", []))
        embed = discord.Embed(
            title="Voice Channel Controls",
            description=(
                f"Managing {channel.mention}\n"
                "**Invite User** sends a DM invite and lets that user join.\n"
                "**Allow User** lets someone join private channels without sending a DM.\n"
                "**Transfer** gives ownership of this channel to another member."
            ),
            color=DEFAULT_COLOR,
        )
        embed.add_field(name="Owner", value=owner.mention if owner else "Unknown", inline=True)
        embed.add_field(name="Privacy", value="Private" if record.get("private") else "Public", inline=True)
        embed.add_field(name="User Limit", value=str(channel.user_limit) if channel.user_limit else "None", inline=True)
        embed.add_field(name="Allowed", value=str(allowed), inline=True)
        embed.add_field(name="Blocked", value=str(blocked), inline=True)
        embed.set_footer(text="This panel is also posted in the voice channel chat.")
        return embed

    async def _notify_invited_member(
        self,
        owner: discord.Member | discord.User,
        member: discord.Member,
        channel: discord.VoiceChannel,
        record: dict,
    ) -> bool:
        try:
            await member.send(
                f"{owner.mention} invited you to join **{channel.name}** in **{channel.guild.name}**.\n"
                f"Join here: {channel.mention}"
            )
            return True
        except discord.HTTPException:
            return False

    def _channel_name(self, template: str, member: discord.Member) -> str:
        clean_member_name = self._clean_channel_name(member.display_name, fallback="Member")
        name = template.replace("{member}", clean_member_name)
        return self._clean_channel_name(
            name,
            fallback=DEFAULT_NAME_TEMPLATE.replace("{member}", clean_member_name),
        )[:CHANNEL_NAME_MAX_LENGTH]

    @classmethod
    def _clean_channel_name(
        cls,
        name: str,
        *,
        fallback: str,
        allow_member_placeholder: bool = False,
    ) -> str:
        placeholder = "MEMBERPLACEHOLDER"
        if allow_member_placeholder:
            name = name.replace("{member}", placeholder)

        name = CHANNEL_NAME_CONTROL_CHARS.sub("", name)
        name = cls._scrub_channel_name_profanity(name)
        name = CHANNEL_NAME_BLOCKED_CHARS.sub("", name)
        name = CHANNEL_NAME_SPACES.sub(" ", name).strip(" .-_")

        if allow_member_placeholder:
            name = name.replace(placeholder, "{member}")

        return name or fallback

    @staticmethod
    def _scrub_channel_name_profanity(name: str) -> str:
        scrubbed = CHANNEL_NAME_PROFANITY.sub("clean", name)
        normalized = scrubbed.lower().translate(CHANNEL_NAME_LEET_TRANSLATION)
        normalized_words = re.findall(r"[a-z]+", normalized)
        compact = "".join(normalized_words)
        has_obfuscated_profanity = any(
            word in CHANNEL_NAME_NORMALIZED_PROFANITY_TERMS
            for word in normalized_words
        ) or any(
            len(word) >= 4 and word in compact
            for word in CHANNEL_NAME_NORMALIZED_PROFANITY_TERMS
        )
        return "clean" if has_obfuscated_profanity else scrubbed

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

    async def _clear_previous_owner_permissions(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        old_owner_id: int,
    ):
        member = guild.get_member(old_owner_id)
        if member is None:
            return
        try:
            await channel.set_permissions(
                member,
                connect=True,
                speak=True,
                manage_channels=None,
                move_members=None,
                reason="VoiceChannels owner transfer",
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    @staticmethod
    def _setup_warnings(
        guild: discord.Guild,
        dashboard_channel: discord.TextChannel,
        category: discord.CategoryChannel | None,
    ) -> list[str]:
        me = guild.me
        if me is None:
            return ["I could not check my server permissions."]

        warnings: list[str] = []
        dashboard_perms = dashboard_channel.permissions_for(me)
        if not dashboard_perms.send_messages:
            warnings.append(f"I cannot send messages in {dashboard_channel.mention}.")
        if not dashboard_perms.embed_links:
            warnings.append(f"I cannot embed links in {dashboard_channel.mention}.")

        if category is None:
            return warnings

        category_perms = category.permissions_for(me)
        checks = {
            "manage_channels": "manage temporary voice channels",
            "move_members": "move members into or out of temporary channels",
            "send_messages": "post the settings panel in voice channel text chats",
            "embed_links": "post embedded settings panels in voice channel text chats",
        }
        for permission, reason in checks.items():
            if not getattr(category_perms, permission, False):
                warnings.append(f"In **{category.name}**, I may not be able to {reason}.")
        return warnings

    @staticmethod
    def _new_channel_record(owner_id: int) -> dict:
        return {
            "owner_id": owner_id,
            "private": False,
            "trusted_ids": [],
            "blocked_ids": [],
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
