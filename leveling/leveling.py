from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import discord
from redbot.core import Config, app_commands, commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu


DEFAULT_COLOR = discord.Color.blurple()
DEFAULT_CHAT_XP_MIN = 15
DEFAULT_CHAT_XP_MAX = 25
DEFAULT_CHAT_COOLDOWN = 30
DEFAULT_VOICE_XP_MIN = 5
DEFAULT_VOICE_XP_MAX = 10
LEADERBOARD_PAGE_SIZE = 10
PAGINATION_TIMEOUT = 120
PROGRESS_BAR_WIDTH = 26
PROGRESS_BAR_GAP = "\u00a0" * 10
LEVEL_KINDS = ("chat", "voice")
LEVEL_CASH_REWARD_LIMIT = 10**12
CURRENCY_NAME = "LWD$"
KIND_ALIASES = {
    "chat": "chat",
    "text": "chat",
    "message": "chat",
    "messages": "chat",
    "voice": "voice",
    "vc": "voice",
}

log = logging.getLogger("red.neufox.leveling")


class LevelingLeaderboardView(discord.ui.View):
    def __init__(self, owner_id: int, embeds: list[discord.Embed]):
        super().__init__(timeout=PAGINATION_TIMEOUT)
        self.owner_id = owner_id
        self.embeds = embeds
        self.message: discord.Message | None = None
        self.page = 0
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This leaderboard is not yours to control.", ephemeral=True)
        return False

    @discord.ui.button(label="First", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(self.embeds) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label="Last", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = len(self.embeds) - 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _update_buttons(self):
        disabled = len(self.embeds) <= 1
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = disabled
        if disabled:
            return
        self.first_page.disabled = self.page == 0
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= len(self.embeds) - 1
        self.last_page.disabled = self.page >= len(self.embeds) - 1


class Leveling(commands.Cog):
    """Chat and voice XP leveling."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=902410375)
        self.config.register_guild(
            enabled=True,
            announce_levelups=True,
            levelup_channel_id=None,
            chat_xp_min=DEFAULT_CHAT_XP_MIN,
            chat_xp_max=DEFAULT_CHAT_XP_MAX,
            chat_cooldown=DEFAULT_CHAT_COOLDOWN,
            voice_xp_min=DEFAULT_VOICE_XP_MIN,
            voice_xp_max=DEFAULT_VOICE_XP_MAX,
            ignored_channel_ids=[],
            ignored_role_ids=[],
            users={},
            level_roles={kind: {} for kind in LEVEL_KINDS},
            level_cash_rewards={kind: {} for kind in LEVEL_KINDS},
            active_voice_sessions={},
        )
        self._chat_cooldowns: dict[tuple[int, int], float] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._startup_task = self.bot.loop.create_task(self._seed_voice_sessions_when_ready())

    def cog_unload(self):
        self._startup_task.cancel()

    @commands.command(name="level", aliases=["lvl", "xp", "rank"])
    @commands.guild_only()
    async def level_command(self, ctx: commands.Context, *, query: str = ""):
        """Show your level, or another member's level."""
        kind, member = await self._parse_level_query(ctx, query)
        if kind is None:
            return
        member = member or ctx.author
        await ctx.send(embed=await self._rank_embed(ctx.guild, member, kind))

    @app_commands.command(name="xp", description="Show your XP level and rank.")
    @app_commands.describe(
        member="The member to show XP for.",
        kind="Choose chat or voice XP.",
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Chat", value="chat"),
            app_commands.Choice(name="Voice", value="voice"),
        ]
    )
    @app_commands.guild_only()
    async def xp(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        kind: str = "chat",
    ):
        """Show your XP level and rank."""
        await interaction.response.defer()
        normalized = self._normalize_kind(kind)
        if normalized is None:
            await self._send_interaction_message(interaction, "Type must be `chat` or `voice`.", ephemeral=True)
            return
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            await self._send_interaction_message(interaction, "This command must be used in a server.", ephemeral=True)
            return
        await self._send_interaction_embed(interaction, await self._rank_embed(interaction.guild, target, normalized))

    @commands.command(name="leaderboard", aliases=["lb", "levels"])
    @commands.guild_only()
    async def leveling_leaderboard(self, ctx: commands.Context, kind: str = "chat"):
        """Show the chat or voice XP leaderboard."""
        normalized = self._normalize_kind(kind)
        if normalized is None:
            await ctx.send("Type must be `chat` or `voice`.")
            return

        embeds = await self._leaderboard_embeds(ctx.guild, normalized)
        if len(embeds) == 1:
            await ctx.send(embed=embeds[0])
        else:
            await menu(ctx, embeds, DEFAULT_CONTROLS)

    @app_commands.command(name="topxp", description="Show the XP leaderboard.")
    @app_commands.describe(kind="Choose chat or voice XP.")
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Chat", value="chat"),
            app_commands.Choice(name="Voice", value="voice"),
        ]
    )
    @app_commands.guild_only()
    async def topxp(self, interaction: discord.Interaction, kind: str = "chat"):
        """Show the chat or voice XP leaderboard."""
        await interaction.response.defer()
        normalized = self._normalize_kind(kind)
        if normalized is None:
            await self._send_interaction_message(interaction, "Type must be `chat` or `voice`.", ephemeral=True)
            return
        embeds = await self._leaderboard_embeds(interaction.guild, normalized)
        await self._send_paginated_interaction(interaction, embeds)

    @commands.group(name="levelrole", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def levelrole(self, ctx: commands.Context):
        """Manage level role rewards."""
        await ctx.invoke(self.levelrole_list)

    @levelrole.command(name="add")
    @commands.admin_or_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def levelrole_add(self, ctx: commands.Context, kind: str, level: int, role: discord.Role):
        """Add or replace a role reward for a level."""
        normalized = self._normalize_kind(kind)
        if normalized is None:
            await ctx.send("Type must be `chat` or `voice`.")
            return
        if level < 1:
            await ctx.send("Level must be at least 1.")
            return
        if not self._can_manage_role(ctx.guild, role):
            await ctx.send(f"I cannot manage {role.mention}. Move my highest role above it first.")
            return

        async with self.config.guild(ctx.guild).level_roles() as level_roles:
            kind_roles = level_roles.setdefault(normalized, {})
            kind_roles[str(level)] = str(role.id)

        await ctx.send(f"{normalized.title()} level {level} will now award {role.mention}.")

    @levelrole.command(name="remove", aliases=["delete"])
    @commands.admin_or_permissions(manage_roles=True)
    async def levelrole_remove(
        self,
        ctx: commands.Context,
        kind: str,
        level: int,
        role: discord.Role | None = None,
    ):
        """Remove a role reward for a level."""
        normalized = self._normalize_kind(kind)
        if normalized is None:
            await ctx.send("Type must be `chat` or `voice`.")
            return
        if level < 1:
            await ctx.send("Level must be at least 1.")
            return

        removed_role_id = None
        async with self.config.guild(ctx.guild).level_roles() as level_roles:
            kind_roles = level_roles.setdefault(normalized, {})
            current_role_id = kind_roles.get(str(level))
            if current_role_id is None:
                await ctx.send(f"No {normalized} role is configured for level {level}.")
                return
            if role is not None and str(role.id) != str(current_role_id):
                await ctx.send(f"Level {level} is not configured for {role.mention}.")
                return
            removed_role_id = kind_roles.pop(str(level), None)

        removed_role = ctx.guild.get_role(int(removed_role_id)) if removed_role_id else None
        role_text = removed_role.mention if removed_role else f"`{removed_role_id}`"
        await ctx.send(f"Removed {role_text} from {normalized} level {level}.")

    @levelrole.command(name="list")
    @commands.admin_or_permissions(manage_roles=True)
    async def levelrole_list(self, ctx: commands.Context):
        """List configured level role rewards."""
        level_roles = await self.config.guild(ctx.guild).level_roles()
        embed = discord.Embed(title="Level Roles", color=DEFAULT_COLOR)
        for kind in LEVEL_KINDS:
            lines = []
            kind_roles = level_roles.get(kind, {})
            for level, role_id in sorted(kind_roles.items(), key=lambda item: int(item[0])):
                role = ctx.guild.get_role(int(role_id))
                lines.append(f"Level {level}: {role.mention if role else f'Unknown role `{role_id}`'}")
            embed.add_field(
                name=kind.title(),
                value="\n".join(lines) if lines else "No roles configured.",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.group(name="levelcoins", aliases=["levelcash", "levelmoney"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def levelcash(self, ctx: commands.Context):
        """Manage Economy LWD$ rewards for levels."""
        await ctx.invoke(self.levelcash_list)

    @levelcash.command(name="add", aliases=["set"])
    @commands.admin_or_permissions(manage_guild=True)
    async def levelcash_add(self, ctx: commands.Context, kind: str, level: int, amount: int):
        """Add or replace an LWD$ reward for a level."""
        normalized = self._normalize_kind(kind)
        if normalized is None:
            await ctx.send("Type must be `chat` or `voice`.")
            return
        if level < 1:
            await ctx.send("Level must be at least 1.")
            return
        if amount < 0 or amount > LEVEL_CASH_REWARD_LIMIT:
            await ctx.send(f"Amount must be between 0 and {LEVEL_CASH_REWARD_LIMIT:,}.")
            return

        async with self.config.guild(ctx.guild).level_cash_rewards() as rewards:
            kind_rewards = rewards.setdefault(normalized, {})
            if amount:
                kind_rewards[str(level)] = int(amount)
            else:
                kind_rewards.pop(str(level), None)

        economy_note = "" if self._economy_cog() is not None else " Load Economy before members level up for payouts."
        await ctx.send(f"{normalized.title()} level {level} LWD$ reward set to {amount:,}.{economy_note}")

    @levelcash.command(name="remove", aliases=["delete"])
    @commands.admin_or_permissions(manage_guild=True)
    async def levelcash_remove(self, ctx: commands.Context, kind: str, level: int):
        """Remove an LWD$ reward for a level."""
        normalized = self._normalize_kind(kind)
        if normalized is None:
            await ctx.send("Type must be `chat` or `voice`.")
            return
        if level < 1:
            await ctx.send("Level must be at least 1.")
            return

        async with self.config.guild(ctx.guild).level_cash_rewards() as rewards:
            kind_rewards = rewards.setdefault(normalized, {})
            removed = kind_rewards.pop(str(level), None)

        if removed is None:
            await ctx.send(f"No {normalized} LWD$ reward is configured for level {level}.")
        else:
            await ctx.send(f"Removed the {removed:,} LWD$ reward from {normalized} level {level}.")

    @levelcash.command(name="list")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelcash_list(self, ctx: commands.Context):
        """List configured level LWD$ rewards."""
        rewards = await self.config.guild(ctx.guild).level_cash_rewards()
        embed = discord.Embed(title="Level LWD Coin Rewards", color=DEFAULT_COLOR)
        embed.description = "Economy cog: loaded" if self._economy_cog() is not None else "Economy cog: not loaded"
        for kind in LEVEL_KINDS:
            lines = []
            kind_rewards = rewards.get(kind, {})
            for level, amount in sorted(kind_rewards.items(), key=lambda item: int(item[0])):
                lines.append(f"Level {level}: {int(amount):,} {CURRENCY_NAME}")
            embed.add_field(
                name=kind.title(),
                value="\n".join(lines) if lines else "No LWD$ rewards configured.",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.group(name="levelset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset(self, ctx: commands.Context):
        """Manage leveling settings."""
        await ctx.invoke(self.levelset_show)

    @levelset.command(name="show")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_show(self, ctx: commands.Context):
        """Show leveling settings."""
        data = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data["levelup_channel_id"]) if data["levelup_channel_id"] else None
        ignored_channels = self._format_ignored_channels(ctx.guild, data["ignored_channel_ids"])
        ignored_roles = self._format_ignored_roles(ctx.guild, data["ignored_role_ids"])
        cash_rewards = data.get("level_cash_rewards", {})
        cash_reward_count = sum(len(cash_rewards.get(kind, {})) for kind in LEVEL_KINDS)

        embed = discord.Embed(title="Leveling Settings", color=DEFAULT_COLOR)
        embed.add_field(name="Status", value="Enabled" if data["enabled"] else "Disabled", inline=True)
        embed.add_field(
            name="Announcements",
            value="Enabled" if data["announce_levelups"] else "Disabled",
            inline=True,
        )
        embed.add_field(
            name="Level-Up Channel",
            value=channel.mention if isinstance(channel, discord.TextChannel) else "Source channel",
            inline=True,
        )
        embed.add_field(
            name="Chat XP",
            value=(
                f"{data['chat_xp_min']}-{data['chat_xp_max']} XP "
                f"every {self._format_duration(data['chat_cooldown'])}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Voice XP",
            value=f"{data['voice_xp_min']}-{data['voice_xp_max']} XP per eligible minute",
            inline=False,
        )
        embed.add_field(
            name="Economy Rewards",
            value=(
                f"{cash_reward_count} configured | "
                f"Economy cog {'loaded' if self._economy_cog() is not None else 'not loaded'}"
            ),
            inline=False,
        )
        embed.add_field(name="Ignored Channels", value=ignored_channels, inline=False)
        embed.add_field(name="Ignored Roles", value=ignored_roles, inline=False)
        await ctx.send(embed=embed)

    @levelset.command(name="toggle")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_toggle(self, ctx: commands.Context):
        """Toggle leveling in this server."""
        enabled = await self.config.guild(ctx.guild).enabled()
        new_enabled = not enabled
        await self.config.guild(ctx.guild).enabled.set(new_enabled)
        if new_enabled:
            await self._seed_voice_sessions(ctx.guild)
        else:
            await self.config.guild(ctx.guild).active_voice_sessions.set({})
        await ctx.send(f"Leveling is now {'enabled' if new_enabled else 'disabled'}.")

    @levelset.command(name="announce")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_announce(self, ctx: commands.Context):
        """Toggle level-up announcements."""
        announce = await self.config.guild(ctx.guild).announce_levelups()
        await self.config.guild(ctx.guild).announce_levelups.set(not announce)
        await ctx.send(f"Level-up announcements are now {'enabled' if not announce else 'disabled'}.")

    @levelset.command(name="channel")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ):
        """Set the channel for level-up announcements."""
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("Level-up announcements must go to a text channel.")
            return
        await self.config.guild(ctx.guild).levelup_channel_id.set(channel.id)
        await ctx.send(f"Level-up announcements will be sent to {channel.mention}.")

    @levelset.command(name="clearchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_clear_channel(self, ctx: commands.Context):
        """Send chat level-up announcements in the source channel."""
        await self.config.guild(ctx.guild).levelup_channel_id.set(None)
        await ctx.send("Level-up announcements will use the source channel.")

    @levelset.command(name="cooldown")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_cooldown(self, ctx: commands.Context, seconds: int):
        """Set the chat XP cooldown in seconds."""
        if seconds < 0 or seconds > 3600:
            await ctx.send("Cooldown must be between 0 and 3,600 seconds.")
            return
        await self.config.guild(ctx.guild).chat_cooldown.set(seconds)
        await ctx.send(f"Chat XP cooldown set to {self._format_duration(seconds)}.")

    @levelset.command(name="chatxp")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_chat_xp(self, ctx: commands.Context, minimum: int, maximum: int):
        """Set the chat XP reward range."""
        if not self._valid_xp_range(minimum, maximum):
            await ctx.send("XP range must be 0-10,000 and minimum cannot exceed maximum.")
            return
        await self.config.guild(ctx.guild).chat_xp_min.set(minimum)
        await self.config.guild(ctx.guild).chat_xp_max.set(maximum)
        await ctx.send(f"Chat XP range set to {minimum}-{maximum}.")

    @levelset.command(name="voicexp")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_voice_xp(self, ctx: commands.Context, minimum: int, maximum: int):
        """Set the per-minute voice XP reward range."""
        if not self._valid_xp_range(minimum, maximum):
            await ctx.send("XP range must be 0-10,000 and minimum cannot exceed maximum.")
            return
        await self.config.guild(ctx.guild).voice_xp_min.set(minimum)
        await self.config.guild(ctx.guild).voice_xp_max.set(maximum)
        await ctx.send(f"Voice XP range set to {minimum}-{maximum} per eligible minute.")

    @levelset.command(name="ignorechannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_ignore_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | discord.VoiceChannel | discord.CategoryChannel | None = None,
    ):
        """Ignore a channel or category for XP."""
        channel = channel or ctx.channel
        await self._add_ignored_channel(ctx.guild, channel.id)
        await ctx.send(f"Leveling will ignore {channel.mention if hasattr(channel, 'mention') else channel.name}.")

    @levelset.command(name="allowchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_allow_channel(self, ctx: commands.Context, channel_or_category_id: int):
        """Stop ignoring a channel or category by ID."""
        removed = await self._remove_ignored_channel(ctx.guild, channel_or_category_id)
        if removed:
            await ctx.send(f"Channel/category `{channel_or_category_id}` is no longer ignored.")
        else:
            await ctx.send(f"Channel/category `{channel_or_category_id}` was not ignored.")

    @levelset.command(name="ignorerole")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_ignore_role(self, ctx: commands.Context, role: discord.Role):
        """Ignore members with a role for XP."""
        async with self.config.guild(ctx.guild).ignored_role_ids() as ignored_role_ids:
            role_id = str(role.id)
            if role_id not in ignored_role_ids:
                ignored_role_ids.append(role_id)
        await ctx.send(f"Members with {role.mention} will not earn XP.")

    @levelset.command(name="allowrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset_allow_role(self, ctx: commands.Context, role: discord.Role):
        """Stop ignoring members with a role."""
        async with self.config.guild(ctx.guild).ignored_role_ids() as ignored_role_ids:
            role_id = str(role.id)
            if role_id in ignored_role_ids:
                ignored_role_ids.remove(role_id)
                removed = True
            else:
                removed = False
        await ctx.send(f"{role.mention} is {'no longer ignored' if removed else 'not ignored'}.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not await self._message_can_earn_xp(message):
            return

        try:
            ctx = await self.bot.get_context(message)
        except Exception:
            ctx = None
        if ctx is not None and ctx.valid:
            return

        guild_conf = self.config.guild(message.guild)
        cooldown = int(await guild_conf.chat_cooldown())
        cooldown_key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if cooldown > 0 and now - self._chat_cooldowns.get(cooldown_key, 0) < cooldown:
            return

        minimum = int(await guild_conf.chat_xp_min())
        maximum = int(await guild_conf.chat_xp_max())
        if maximum <= 0:
            return

        self._chat_cooldowns[cooldown_key] = now
        xp = random.randint(minimum, maximum)
        level_up = await self._add_xp(message.guild, message.author, "chat", xp)
        if level_up is not None:
            old_level, new_level = level_up
            await self._apply_level_roles(message.guild, message.author, "chat", new_level)
            cash_paid = await self._pay_level_cash_rewards(
                message.guild,
                message.author,
                "chat",
                old_level,
                new_level,
            )
            await self._announce_level_up(message.guild, message.author, "chat", new_level, cash_paid, message.channel)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot or member.guild is None:
            return
        if not await self.config.guild(member.guild).enabled():
            return

        was_eligible = await self._voice_state_can_earn_xp(member, before)
        is_eligible = await self._voice_state_can_earn_xp(member, after)
        if was_eligible and not is_eligible:
            await self._close_voice_session(member.guild, member, int(time.time()))
        elif not was_eligible and is_eligible:
            await self._start_voice_session(member.guild, member.id)

    async def _parse_level_query(
        self,
        ctx: commands.Context,
        query: str,
    ) -> tuple[str | None, discord.Member | None]:
        kind = "chat"
        member: discord.Member | None = None
        query = query.strip()
        if not query:
            return kind, member

        parts = query.split()
        member_parts = []
        for part in parts:
            normalized = self._normalize_kind(part)
            if normalized is not None:
                kind = normalized
            else:
                member_parts.append(part)

        if member_parts:
            member_query = " ".join(member_parts)
            try:
                member = await commands.MemberConverter().convert(ctx, member_query)
            except commands.BadArgument:
                await ctx.send("I could not find that member. Try `level chat @member` or `level voice @member`.")
                return None, None
        elif ctx.message.mentions:
            member = ctx.message.mentions[0]

        return kind, member

    async def _rank_embed(self, guild: discord.Guild, member: discord.Member, kind: str) -> discord.Embed:
        users = await self.config.guild(guild).users()
        record = self._normalized_record(users.get(str(member.id), {}))
        xp = int(record[f"{kind}_xp"])
        level = self._level_from_xp(xp)
        rank = self._rank_for_user(users, member.id, kind)
        current_level_xp = self._total_xp_for_level(level)
        next_level_xp = self._total_xp_for_level(level + 1)
        into_level = max(0, xp - current_level_xp)
        needed = max(1, next_level_xp - current_level_xp)

        embed = discord.Embed(title=f"{member.display_name}'s {kind.title()} Level", color=DEFAULT_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Rank", value=f"#{rank}" if rank else "Unranked", inline=True)
        embed.add_field(name="Total XP", value=f"{xp:,}", inline=True)
        embed.add_field(
            name="Progress",
            value=f"`{self._progress_bar(into_level, needed)}`{PROGRESS_BAR_GAP}{into_level:,}/{needed:,} XP",
            inline=False,
        )
        return embed

    async def _leaderboard_embeds(self, guild: discord.Guild, kind: str) -> list[discord.Embed]:
        users = await self.config.guild(guild).users()
        key = f"{kind}_xp"
        entries = []
        for user_id, raw_record in users.items():
            record = self._normalized_record(raw_record)
            xp = int(record[key])
            if xp <= 0:
                continue
            member = guild.get_member(int(user_id))
            display_name = member.display_name if member else record.get("display_name") or f"User {user_id}"
            entries.append((int(user_id), display_name, xp))

        entries.sort(key=lambda item: item[2], reverse=True)
        if not entries:
            embed = discord.Embed(
                title=f"{kind.title()} XP Leaderboard",
                description="No one has earned XP yet.",
                color=DEFAULT_COLOR,
            )
            return [embed]

        pages = []
        total_pages = (len(entries) + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE
        for start in range(0, len(entries), LEADERBOARD_PAGE_SIZE):
            page_entries = entries[start : start + LEADERBOARD_PAGE_SIZE]
            lines = []
            for offset, (_, display_name, xp) in enumerate(page_entries, start=start + 1):
                level = self._level_from_xp(xp)
                safe_name = discord.utils.escape_markdown(display_name)
                lines.append(f"`#{offset}` **{safe_name}** - Level {level} ({xp:,} XP)")

            embed = discord.Embed(
                title=f"{kind.title()} XP Leaderboard",
                description="\n".join(lines),
                color=DEFAULT_COLOR,
            )
            embed.set_footer(text=f"Page {len(pages) + 1}/{total_pages}")
            pages.append(embed)
        return pages

    async def _send_paginated_interaction(
        self,
        interaction: discord.Interaction,
        embeds: list[discord.Embed],
    ):
        if len(embeds) <= 1:
            await self._send_interaction_embed(interaction, embeds[0])
            return

        view = LevelingLeaderboardView(interaction.user.id, embeds)
        if interaction.response.is_done():
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            return
        await interaction.response.send_message(embed=embeds[0], view=view)
        view.message = await interaction.original_response()

    async def _send_interaction_embed(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
    ):
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

    async def _send_interaction_message(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = False,
    ):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    async def _message_can_earn_xp(self, message: discord.Message) -> bool:
        if message.author.bot or message.guild is None:
            return False
        if not isinstance(message.author, discord.Member):
            return False
        if not await self.config.guild(message.guild).enabled():
            return False
        if await self._is_ignored(message.guild, message.author, message.channel):
            return False
        return True

    async def _voice_state_can_earn_xp(
        self,
        member: discord.Member,
        state: discord.VoiceState,
    ) -> bool:
        if state.channel is None:
            return False
        if (
            state.self_deaf
            or state.deaf
            or getattr(state, "self_mute", False)
            or getattr(state, "mute", False)
            or getattr(state, "afk", False)
        ):
            return False
        if member.guild.afk_channel and state.channel.id == member.guild.afk_channel.id:
            return False
        if await self._is_ignored(member.guild, member, state.channel):
            return False
        return True

    async def _is_ignored(
        self,
        guild: discord.Guild,
        member: discord.Member,
        channel: discord.abc.GuildChannel,
    ) -> bool:
        ignored_role_ids = set(await self.config.guild(guild).ignored_role_ids())
        if ignored_role_ids and any(str(role.id) in ignored_role_ids for role in member.roles):
            return True

        ignored_channel_ids = set(await self.config.guild(guild).ignored_channel_ids())
        channel_ids = {str(channel.id)}
        category = getattr(channel, "category", None)
        if category is not None:
            channel_ids.add(str(category.id))
        return bool(channel_ids & ignored_channel_ids)

    async def _add_xp(
        self,
        guild: discord.Guild,
        member: discord.Member,
        kind: str,
        amount: int,
    ) -> tuple[int, int] | None:
        if amount <= 0:
            return None

        lock = self._lock_for(guild.id)
        async with lock:
            async with self.config.guild(guild).users() as users:
                user_id = str(member.id)
                record = self._normalized_record(users.get(user_id, {}))
                old_xp = int(record[f"{kind}_xp"])
                old_level = self._level_from_xp(old_xp)
                record[f"{kind}_xp"] = old_xp + int(amount)
                record["display_name"] = member.display_name
                users[user_id] = record
                new_level = self._level_from_xp(int(record[f"{kind}_xp"]))

        if new_level > old_level:
            return old_level, new_level
        return None

    async def _start_voice_session(self, guild: discord.Guild, user_id: int):
        async with self.config.guild(guild).active_voice_sessions() as active_sessions:
            active_sessions[str(user_id)] = int(time.time())

    async def _close_voice_session(
        self,
        guild: discord.Guild,
        member: discord.Member,
        ended_at: int,
    ):
        user_id = str(member.id)
        async with self.config.guild(guild).active_voice_sessions() as active_sessions:
            started_at = active_sessions.pop(user_id, None)
        if started_at is None:
            return

        elapsed = max(0, int(ended_at) - int(started_at))
        minutes = elapsed // 60
        if minutes <= 0:
            return

        guild_conf = self.config.guild(guild)
        minimum = int(await guild_conf.voice_xp_min())
        maximum = int(await guild_conf.voice_xp_max())
        if maximum <= 0:
            return

        xp = sum(random.randint(minimum, maximum) for _ in range(minutes))
        level_up = await self._add_xp(guild, member, "voice", xp)
        if level_up is not None:
            old_level, new_level = level_up
            await self._apply_level_roles(guild, member, "voice", new_level)
            cash_paid = await self._pay_level_cash_rewards(guild, member, "voice", old_level, new_level)
            await self._announce_level_up(guild, member, "voice", new_level, cash_paid, None)

    async def _apply_level_roles(
        self,
        guild: discord.Guild,
        member: discord.Member,
        kind: str,
        level: int,
    ):
        if member.id == guild.owner_id:
            return
        if guild.me is None or not guild.me.guild_permissions.manage_roles:
            return
        if guild.me.top_role <= member.top_role:
            return

        changed = False
        level_roles = await self.config.guild(guild).level_roles()
        kind_roles = level_roles.get(kind, {})
        for level_key, role_id in list(kind_roles.items()):
            try:
                required_level = int(level_key)
                role = guild.get_role(int(role_id))
            except (TypeError, ValueError):
                role = None
                required_level = 0

            if role is None:
                kind_roles.pop(level_key, None)
                changed = True
                continue
            if required_level > level or role in member.roles or not self._can_manage_role(guild, role):
                continue
            try:
                await member.add_roles(role, reason=f"Leveling {kind} level {level}")
            except discord.HTTPException:
                continue

        if changed:
            level_roles[kind] = kind_roles
            await self.config.guild(guild).level_roles.set(level_roles)

    async def _pay_level_cash_rewards(
        self,
        guild: discord.Guild,
        member: discord.Member,
        kind: str,
        old_level: int,
        new_level: int,
    ) -> int:
        economy = self._economy_cog()
        if economy is None or not hasattr(economy, "add_balance"):
            return 0

        rewards = await self.config.guild(guild).level_cash_rewards()
        total = 0
        for level_key, amount in rewards.get(kind, {}).items():
            try:
                reward_level = int(level_key)
                reward_amount = int(amount)
            except (TypeError, ValueError):
                continue
            if old_level < reward_level <= new_level and reward_amount > 0:
                total += reward_amount

        if total <= 0:
            return 0

        try:
            await economy.add_balance(
                member.id,
                total,
                actor_id=None,
                guild_id=guild.id,
                reason=f"leveling {kind} level {new_level} reward",
            )
        except Exception:
            log.exception("Failed to pay Economy reward for %s in guild %s.", member.id, guild.id)
            return 0
        return total

    async def _announce_level_up(
        self,
        guild: discord.Guild,
        member: discord.Member,
        kind: str,
        level: int,
        cash_paid: int,
        source_channel: discord.abc.Messageable | None,
    ):
        if not await self.config.guild(guild).announce_levelups():
            return

        channel = None
        channel_id = await self.config.guild(guild).levelup_channel_id()
        if channel_id:
            channel = guild.get_channel(channel_id)
        elif kind == "chat":
            channel = source_channel

        if channel is None or not hasattr(channel, "send"):
            return

        try:
            cash_text = f" and earned **{cash_paid:,}** {CURRENCY_NAME}" if cash_paid else ""
            await channel.send(f"{member.mention} reached {kind} level **{level}**{cash_text}!")
        except discord.HTTPException:
            pass

    async def _seed_voice_sessions_when_ready(self):
        await self.bot.wait_until_ready()
        for guild in list(self.bot.guilds):
            if await self.config.guild(guild).enabled():
                await self._seed_voice_sessions(guild)

    async def _seed_voice_sessions(self, guild: discord.Guild):
        now = int(time.time())
        active_user_ids = set()
        for channel in guild.voice_channels:
            for member in channel.members:
                if not member.bot and member.voice and await self._voice_state_can_earn_xp(member, member.voice):
                    active_user_ids.add(str(member.id))

        async with self.config.guild(guild).active_voice_sessions() as active_sessions:
            active_sessions.clear()
            for user_id in active_user_ids:
                active_sessions[user_id] = now

    async def _add_ignored_channel(self, guild: discord.Guild, channel_id: int):
        async with self.config.guild(guild).ignored_channel_ids() as ignored_channel_ids:
            value = str(channel_id)
            if value not in ignored_channel_ids:
                ignored_channel_ids.append(value)

    async def _remove_ignored_channel(self, guild: discord.Guild, channel_id: int) -> bool:
        async with self.config.guild(guild).ignored_channel_ids() as ignored_channel_ids:
            value = str(channel_id)
            if value not in ignored_channel_ids:
                return False
            ignored_channel_ids.remove(value)
            return True

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        return lock

    def _normalize_kind(self, kind: str | None) -> str | None:
        if kind is None:
            return None
        return KIND_ALIASES.get(kind.lower())

    def _normalized_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "chat_xp": int(record.get("chat_xp", 0)),
            "voice_xp": int(record.get("voice_xp", 0)),
            "display_name": str(record.get("display_name", "")),
        }

    def _rank_for_user(self, users: dict[str, Any], user_id: int, kind: str) -> int | None:
        key = f"{kind}_xp"
        ranked = sorted(
            (
                (int(stored_user_id), int(self._normalized_record(record)[key]))
                for stored_user_id, record in users.items()
                if int(self._normalized_record(record)[key]) > 0
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        for index, (stored_user_id, _) in enumerate(ranked, start=1):
            if stored_user_id == user_id:
                return index
        return None

    def _can_manage_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        if guild.me is None or role.is_default() or role.managed:
            return False
        return guild.me.guild_permissions.manage_roles and guild.me.top_role > role

    def _economy_cog(self):
        return self.bot.get_cog("Economy")

    def _format_ignored_channels(self, guild: discord.Guild, channel_ids: list[str]) -> str:
        if not channel_ids:
            return "None"
        lines = []
        for channel_id in channel_ids[:20]:
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                lines.append(f"`{channel_id}`")
            elif hasattr(channel, "mention"):
                lines.append(channel.mention)
            else:
                lines.append(f"{channel.name} (`{channel_id}`)")
        if len(channel_ids) > 20:
            lines.append(f"...and {len(channel_ids) - 20} more")
        return "\n".join(lines)

    def _format_ignored_roles(self, guild: discord.Guild, role_ids: list[str]) -> str:
        if not role_ids:
            return "None"
        lines = []
        for role_id in role_ids[:20]:
            role = guild.get_role(int(role_id))
            lines.append(role.mention if role else f"`{role_id}`")
        if len(role_ids) > 20:
            lines.append(f"...and {len(role_ids) - 20} more")
        return "\n".join(lines)

    def _valid_xp_range(self, minimum: int, maximum: int) -> bool:
        return 0 <= minimum <= maximum <= 10000

    def _progress_bar(self, current: int, needed: int) -> str:
        filled = int(PROGRESS_BAR_WIDTH * min(current, needed) / max(needed, 1))
        return "[" + "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled) + "]"

    def _format_duration(self, seconds: int) -> str:
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"

    def _total_xp_for_level(self, level: int) -> int:
        if level <= 0:
            return 0
        return 5 * level * (2 * level * level + 27 * level + 91) // 6

    def _level_from_xp(self, xp: int) -> int:
        xp = max(0, int(xp))
        low = 0
        high = 1
        while self._total_xp_for_level(high) <= xp:
            high *= 2
        while low + 1 < high:
            mid = (low + high) // 2
            if self._total_xp_for_level(mid) <= xp:
                low = mid
            else:
                high = mid
        return low
