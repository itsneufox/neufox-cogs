from __future__ import annotations

import time
from collections import Counter
from typing import Any

import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.blue()
MAX_LIMIT = 25
ROLE_SYNC_INTERVAL = 300
TOP_MESSAGE_ROLE_TIERS = ("first", "second", "third")
RANK_PREFIXES = {
    1: "🥇",
    2: "🥈",
    3: "🥉",
}


class ActivityStats(commands.Cog):
    """Track message and reaction activity stats."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=248935701)
        self.config.register_guild(
            enabled=True,
            total_messages=0,
            user_messages={},
            channel_messages={},
            message_authors={},
            received_reactions={},
            message_reactions={},
            top_message_roles={tier: None for tier in TOP_MESSAGE_ROLE_TIERS},
        )
        self._last_role_sync: dict[int, float] = {}

    @commands.group(name="activitystats", invoke_without_command=True)
    async def activitystats(self, ctx: commands.Context):
        """View message and reaction activity statistics."""
        await ctx.invoke(self.activitystats_server)

    @activitystats.command(name="server")
    async def activitystats_server(self, ctx: commands.Context):
        """Show server-wide tracked totals."""
        data = await self.config.guild(ctx.guild).all()
        users_tracked = len(data["user_messages"])
        messages_tracked = len(data["message_authors"])
        reactions = sum(
            int(count)
            for user_reactions in data["received_reactions"].values()
            for count in user_reactions.values()
        )

        embed = discord.Embed(title="ActivityStats", color=DEFAULT_COLOR)
        embed.add_field(name="Status", value="Enabled" if data["enabled"] else "Disabled", inline=True)
        embed.add_field(name="Messages", value=str(data["total_messages"]), inline=True)
        embed.add_field(name="Received Reactions", value=str(reactions), inline=True)
        embed.add_field(name="Users Tracked", value=str(users_tracked), inline=True)
        embed.add_field(name="Known Messages", value=str(messages_tracked), inline=True)
        await ctx.send(embed=embed)

    @activitystats.command(name="help")
    async def activitystats_help(self, ctx: commands.Context):
        """Show ActivityStats commands."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="ActivityStats Help",
            description="Tracks message counts and received reactions without storing message content.",
            color=DEFAULT_COLOR,
        )
        embed.add_field(
            name="Slash Commands",
            value=(
                "`/messages [member]` - show your message count and rank\n"
                "`/reactions [member]` - show received reaction counts\n"
                "`/topmessages` - show the message leaderboard\n"
                "`/topreacts [emoji]` - show the received reaction leaderboard"
            ),
            inline=False,
        )
        embed.add_field(
            name="Prefix Commands",
            value=(
                f"`{prefix}activitystats server` - show tracked totals\n"
                f"`{prefix}activitystats messages` - show the message leaderboard\n"
                f"`{prefix}activitystats me [member]` - show a member's message rank\n"
                f"`{prefix}activitystats reactions [member]` - show a member's received reactions\n"
                f"`{prefix}activitystats reactiontop [emoji]` - show reaction rankings"
            ),
            inline=False,
        )
        embed.add_field(
            name="Admin Commands",
            value=(
                f"`{prefix}activitystats backfill [limit] [channel]` - import channel history\n"
                f"`{prefix}activitystats backfillall [limit_per_channel]` - import readable channels\n"
                f"`{prefix}activitystats toggle` - toggle tracking\n"
                f"`{prefix}activitystats reset` - clear tracked stats\n"
                f"`{prefix}activitystats roles` - show top-message role config\n"
                f"`{prefix}activitystats roles set <first|second|third> <role>` - configure rank roles\n"
                f"`{prefix}activitystats roles clear [first|second|third]` - clear rank role config\n"
                f"`{prefix}activitystats roles sync` - apply rank roles now"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @activitystats.command(name="backfill")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.bot_has_permissions(read_message_history=True)
    async def activitystats_backfill(
        self,
        ctx: commands.Context,
        limit: int = 1000,
        channel: discord.TextChannel | None = None,
    ):
        """Import old message and reaction stats from one channel."""
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("Backfill must be run in, or pointed at, a text channel.")
            return

        limit = max(1, min(int(limit), 100000))
        async with ctx.typing():
            scanned, added, reaction_updates = await self._backfill_channel(ctx.guild, channel, limit)

        await ctx.send(
            f"Backfilled {channel.mention}: scanned {scanned} messages, "
            f"added {added} new message records, updated {reaction_updates} reaction counters."
        )
        result = await self._sync_top_message_roles(ctx.guild)
        if result["configured"]:
            await ctx.send(self._format_role_sync_result(result))

    @activitystats.command(name="backfillall")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.bot_has_permissions(read_message_history=True)
    async def activitystats_backfillall(self, ctx: commands.Context, limit_per_channel: int = 1000):
        """Import old message and reaction stats from every readable text channel."""
        limit_per_channel = max(1, min(int(limit_per_channel), 100000))
        totals = [0, 0, 0]

        async with ctx.typing():
            for channel in ctx.guild.text_channels:
                permissions = channel.permissions_for(ctx.guild.me)
                if not permissions.read_messages or not permissions.read_message_history:
                    continue
                scanned, added, reaction_updates = await self._backfill_channel(
                    ctx.guild,
                    channel,
                    limit_per_channel,
                )
                totals[0] += scanned
                totals[1] += added
                totals[2] += reaction_updates

        await ctx.send(
            f"Backfill complete: scanned {totals[0]} messages, "
            f"added {totals[1]} new message records, updated {totals[2]} reaction counters."
        )
        result = await self._sync_top_message_roles(ctx.guild)
        if result["configured"]:
            await ctx.send(self._format_role_sync_result(result))

    @activitystats.command(name="messages")
    async def activitystats_messages(self, ctx: commands.Context):
        """Show users with the most tracked messages."""
        await self._send_message_leaderboard(ctx)

    @commands.guild_only()
    @commands.hybrid_command(name="topmessages")
    async def topmessages(self, ctx: commands.Context):
        """Show users with the most tracked messages."""
        await self._send_message_leaderboard(ctx)

    async def _send_message_leaderboard(self, ctx: commands.Context, limit: int = 10):
        limit = self._clean_limit(limit)
        user_messages = await self.config.guild(ctx.guild).user_messages()
        entries = sorted(
            ((int(user_id), int(count)) for user_id, count in user_messages.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:limit]

        embed = discord.Embed(
            title="Message Rankings",
            description=await self._format_user_count_lines(ctx.guild, entries, "messages"),
            color=DEFAULT_COLOR,
        )
        await ctx.send(embed=embed)

    @activitystats.command(name="me")
    async def activitystats_me(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show your message rank and count, or another member's."""
        await self._send_message_rank(ctx, member)

    @commands.guild_only()
    @commands.hybrid_command(name="messages")
    async def messages(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show your message rank and count, or another member's."""
        await self._send_message_rank(ctx, member)

    async def _send_message_rank(self, ctx: commands.Context, member: discord.Member | None = None):
        member = member or ctx.author
        user_messages = await self.config.guild(ctx.guild).user_messages()
        count = int(user_messages.get(str(member.id), 0))
        rank = self._rank_for_user(user_messages, member.id)

        embed = discord.Embed(title="Message Stats", color=DEFAULT_COLOR)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Rank", value=f"#{rank}" if rank else "Unranked", inline=True)
        embed.add_field(name="Messages", value=str(count), inline=True)
        await ctx.send(embed=embed)

    @activitystats.command(name="reactions")
    async def activitystats_reactions(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show received reaction counts for a member."""
        await self._send_reaction_summary(ctx, member)

    @commands.guild_only()
    @commands.hybrid_command(name="reactions")
    async def reactions(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show received reaction counts for a member."""
        await self._send_reaction_summary(ctx, member)

    async def _send_reaction_summary(self, ctx: commands.Context, member: discord.Member | None = None):
        member = member or ctx.author
        all_reactions = await self.config.guild(ctx.guild).received_reactions()
        reactions = {
            emoji: int(count)
            for emoji, count in all_reactions.get(str(member.id), {}).items()
            if int(count) > 0
        }

        embed = discord.Embed(
            title=f"{member.display_name}'s Reactions",
            color=DEFAULT_COLOR,
        )
        if not reactions:
            embed.description = "There are no reactions to display here."
        else:
            for emoji, count in Counter(reactions).most_common(24):
                embed.add_field(name=f"{emoji}\u200b", value=f"{count}x", inline=True)
        await ctx.send(embed=embed)

    @activitystats.command(name="reactiontop")
    async def activitystats_reactiontop(
        self,
        ctx: commands.Context,
        emoji: str | None = None,
    ):
        """Show users with the most received reactions, optionally for one emoji."""
        await self._send_reaction_leaderboard(ctx, emoji)

    @commands.guild_only()
    @commands.hybrid_command(name="topreacts")
    async def topreacts(
        self,
        ctx: commands.Context,
        emoji: str | None = None,
    ):
        """Show users with the most received reactions, optionally for one emoji."""
        await self._send_reaction_leaderboard(ctx, emoji)

    async def _send_reaction_leaderboard(
        self,
        ctx: commands.Context,
        emoji: str | None = None,
        limit: int = 10,
    ):
        limit = self._clean_limit(limit)

        all_reactions = await self.config.guild(ctx.guild).received_reactions()
        entries: list[tuple[int, int, str]] = []
        for user_id, reactions in all_reactions.items():
            if emoji:
                count = int(reactions.get(emoji, 0))
                if count > 0:
                    entries.append((int(user_id), count, emoji))
            else:
                for reaction, count in reactions.items():
                    if int(count) > 0:
                        entries.append((int(user_id), int(count), reaction))

        entries.sort(key=lambda item: item[1], reverse=True)
        embed = discord.Embed(
            title="Reaction Rankings",
            description=await self._format_reaction_lines(ctx.guild, entries[:limit], emoji),
            color=DEFAULT_COLOR,
        )
        await ctx.send(embed=embed)

    @activitystats.command(name="toggle")
    @commands.admin_or_permissions(manage_guild=True)
    async def activitystats_toggle(self, ctx: commands.Context):
        """Toggle stat tracking in this server."""
        enabled = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not enabled)
        await ctx.send(f"ActivityStats tracking is now {'enabled' if not enabled else 'disabled'}.")

    @activitystats.command(name="reset")
    @commands.admin_or_permissions(manage_guild=True)
    async def activitystats_reset(self, ctx: commands.Context):
        """Clear all tracked stats for this server."""
        guild_conf = self.config.guild(ctx.guild)
        await guild_conf.total_messages.set(0)
        await guild_conf.user_messages.set({})
        await guild_conf.channel_messages.set({})
        await guild_conf.message_authors.set({})
        await guild_conf.received_reactions.set({})
        await guild_conf.message_reactions.set({})
        result = await self._sync_top_message_roles(ctx.guild)
        await ctx.send("ActivityStats data has been reset for this server.")
        if result["configured"]:
            await ctx.send(self._format_role_sync_result(result))

    @activitystats.group(name="roles", invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def activitystats_roles(self, ctx: commands.Context):
        """Manage top-message rank roles."""
        await ctx.invoke(self.activitystats_roles_show)

    @activitystats_roles.command(name="set")
    @commands.admin_or_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def activitystats_roles_set(self, ctx: commands.Context, tier: str, role: discord.Role):
        """Set the role for first, second, or third place."""
        tier = tier.lower()
        if tier not in TOP_MESSAGE_ROLE_TIERS:
            await ctx.send("Tier must be one of: first, second, third.")
            return
        if not self._can_manage_role(ctx.guild, role):
            await ctx.send(f"I cannot manage {role.mention}. Move my highest role above it first.")
            return

        role_id = str(role.id)
        configured_roles = await self.config.guild(ctx.guild).top_message_roles()
        for configured_tier, configured_role_id in configured_roles.items():
            if configured_tier != tier and configured_role_id == role_id:
                await ctx.send(f"{role.mention} is already configured for {configured_tier} place.")
                return

        configured_roles[tier] = role_id
        await self.config.guild(ctx.guild).top_message_roles.set(configured_roles)
        result = await self._sync_top_message_roles(ctx.guild)
        await ctx.send(f"{tier.title()} place role set to {role.mention}. {self._format_role_sync_result(result)}")

    @activitystats_roles.command(name="clear")
    @commands.admin_or_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def activitystats_roles_clear(self, ctx: commands.Context, tier: str | None = None):
        """Clear one top-message role, or all of them."""
        configured_roles = await self.config.guild(ctx.guild).top_message_roles()
        if tier is None:
            result = await self._remove_configured_top_message_roles(ctx.guild, configured_roles)
            configured_roles = {configured_tier: None for configured_tier in TOP_MESSAGE_ROLE_TIERS}
            cleared = "all top-message roles"
        else:
            tier = tier.lower()
            if tier not in TOP_MESSAGE_ROLE_TIERS:
                await ctx.send("Tier must be one of: first, second, third.")
                return
            result = await self._remove_configured_top_message_roles(
                ctx.guild,
                {tier: configured_roles.get(tier)},
            )
            configured_roles[tier] = None
            cleared = f"{tier} place role"

        await self.config.guild(ctx.guild).top_message_roles.set(configured_roles)
        await ctx.send(f"Cleared {cleared}. {self._format_role_sync_result(result)}")

    @activitystats_roles.command(name="show")
    @commands.admin_or_permissions(manage_roles=True)
    async def activitystats_roles_show(self, ctx: commands.Context):
        """Show configured top-message roles."""
        configured_roles = await self.config.guild(ctx.guild).top_message_roles()
        lines = []
        for tier in TOP_MESSAGE_ROLE_TIERS:
            role = self._configured_role(ctx.guild, configured_roles.get(tier))
            lines.append(f"**{tier.title()}**: {role.mention if role else 'Not set'}")

        embed = discord.Embed(
            title="Top Message Roles",
            description="\n".join(lines),
            color=DEFAULT_COLOR,
        )
        await ctx.send(embed=embed)

    @activitystats_roles.command(name="sync")
    @commands.admin_or_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def activitystats_roles_sync(self, ctx: commands.Context):
        """Apply top-message roles to the current rankings."""
        result = await self._sync_top_message_roles(ctx.guild)
        await ctx.send(self._format_role_sync_result(result))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not await self.config.guild(message.guild).enabled():
            return

        guild_conf = self.config.guild(message.guild)
        user_id = str(message.author.id)
        channel_id = str(message.channel.id)
        message_id = str(message.id)

        await guild_conf.total_messages.set(await guild_conf.total_messages() + 1)
        async with guild_conf.user_messages() as user_messages:
            user_messages[user_id] = int(user_messages.get(user_id, 0)) + 1
        async with guild_conf.channel_messages() as channel_messages:
            channel_messages[channel_id] = int(channel_messages.get(channel_id, 0)) + 1
        async with guild_conf.message_authors() as message_authors:
            message_authors[message_id] = {
                "author_id": user_id,
                "channel_id": channel_id,
                "created_at": int(time.time()),
            }
        await self._maybe_sync_top_message_roles(message.guild)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._record_reaction(payload, delta=1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._record_reaction(payload, delta=-1)

    async def _record_reaction(self, payload: discord.RawReactionActionEvent, delta: int):
        if payload.guild_id is None:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        if not await self.config.guild(guild).enabled():
            return

        message_authors = await self.config.guild(guild).message_authors()
        entry = message_authors.get(str(payload.message_id))
        if not entry:
            return

        author_id = str(self._entry_author_id(entry))
        if not author_id or author_id == str(payload.user_id):
            return

        emoji = self._emoji_key(payload.emoji)
        async with self.config.guild(guild).received_reactions() as received_reactions:
            self._apply_reaction_delta(received_reactions, author_id, emoji, delta)
        async with self.config.guild(guild).message_reactions() as message_reactions:
            message_counts = message_reactions.setdefault(str(payload.message_id), {})
            new_count = int(message_counts.get(emoji, 0)) + delta
            if new_count <= 0:
                message_counts.pop(emoji, None)
            else:
                message_counts[emoji] = new_count
            if not message_counts:
                message_reactions.pop(str(payload.message_id), None)

    async def _backfill_channel(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        limit: int,
    ) -> tuple[int, int, int]:
        guild_conf = self.config.guild(guild)
        message_authors = await guild_conf.message_authors()
        message_reactions = await guild_conf.message_reactions()

        scanned = 0
        added = 0
        reaction_updates = 0
        new_authors: dict[str, dict[str, str | int]] = {}
        user_message_deltas: Counter[str] = Counter()
        channel_message_delta = 0
        reaction_deltas: list[tuple[str, str, int]] = []
        reaction_snapshots: dict[str, dict[str, int]] = {}
        touched_reaction_messages: set[str] = set()

        async for message in channel.history(limit=limit, oldest_first=True):
            scanned += 1
            if message.author.bot or not message.guild:
                continue

            message_id = str(message.id)
            author_id = str(message.author.id)
            if message_id not in message_authors and message_id not in new_authors:
                new_authors[message_id] = {
                    "author_id": author_id,
                    "channel_id": str(channel.id),
                    "created_at": int(message.created_at.timestamp()),
                }
                user_message_deltas[author_id] += 1
                channel_message_delta += 1
                added += 1

            current_reactions = await self._message_reaction_counts(message)
            previous_reactions = {
                emoji: int(count)
                for emoji, count in message_reactions.get(message_id, {}).items()
            }
            all_emoji = set(current_reactions) | set(previous_reactions)
            for emoji in all_emoji:
                delta = int(current_reactions.get(emoji, 0)) - int(previous_reactions.get(emoji, 0))
                if delta:
                    reaction_deltas.append((author_id, emoji, delta))
                    reaction_updates += abs(delta)
                    touched_reaction_messages.add(message_id)
            if current_reactions:
                reaction_snapshots[message_id] = current_reactions

        if added:
            await guild_conf.total_messages.set(await guild_conf.total_messages() + added)
            async with guild_conf.user_messages() as user_messages:
                for user_id, delta in user_message_deltas.items():
                    user_messages[user_id] = int(user_messages.get(user_id, 0)) + delta
            async with guild_conf.channel_messages() as channel_messages:
                channel_id = str(channel.id)
                channel_messages[channel_id] = int(channel_messages.get(channel_id, 0)) + channel_message_delta
            async with guild_conf.message_authors() as stored_authors:
                stored_authors.update(new_authors)

        if reaction_deltas:
            async with guild_conf.received_reactions() as received_reactions:
                for author_id, emoji, delta in reaction_deltas:
                    self._apply_reaction_delta(received_reactions, author_id, emoji, delta)
            async with guild_conf.message_reactions() as stored_reactions:
                for message_id in touched_reaction_messages:
                    if message_id in reaction_snapshots:
                        stored_reactions[message_id] = reaction_snapshots[message_id]
                    else:
                        stored_reactions.pop(message_id, None)

        return scanned, added, reaction_updates

    async def _message_reaction_counts(self, message: discord.Message) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for reaction in message.reactions:
            emoji = self._emoji_key(reaction.emoji)
            try:
                async for user in reaction.users(limit=None):
                    if user.id != message.author.id:
                        counts[emoji] += 1
            except (discord.Forbidden, discord.HTTPException):
                count = reaction.count
                if reaction.me:
                    count -= 1
                if count > 0:
                    counts[emoji] += count
        return dict(counts)

    async def _maybe_sync_top_message_roles(self, guild: discord.Guild):
        configured_roles = await self.config.guild(guild).top_message_roles()
        if not any(configured_roles.values()):
            return

        now = time.monotonic()
        last_sync = self._last_role_sync.get(guild.id, 0)
        if now - last_sync < ROLE_SYNC_INTERVAL:
            return

        self._last_role_sync[guild.id] = now
        await self._sync_top_message_roles(guild)

    async def _sync_top_message_roles(self, guild: discord.Guild) -> dict[str, Any]:
        result: dict[str, Any] = {
            "configured": 0,
            "added": 0,
            "removed": 0,
            "skipped": [],
        }
        configured_roles = await self.config.guild(guild).top_message_roles()
        manageable_roles: dict[str, discord.Role] = {}
        for tier in TOP_MESSAGE_ROLE_TIERS:
            role = self._configured_role(guild, configured_roles.get(tier))
            if role is None:
                continue
            result["configured"] += 1
            if not self._can_manage_role(guild, role):
                result["skipped"].append(f"{tier}: cannot manage {role.name}")
                continue
            manageable_roles[tier] = role

        if not manageable_roles:
            return result

        top_user_ids = await self._top_message_user_ids(guild, len(TOP_MESSAGE_ROLE_TIERS))
        desired_by_role: dict[int, int] = {}
        for index, tier in enumerate(TOP_MESSAGE_ROLE_TIERS):
            role = manageable_roles.get(tier)
            if role is not None and index < len(top_user_ids):
                desired_by_role[role.id] = top_user_ids[index]

        for tier, role in manageable_roles.items():
            desired_user_id = desired_by_role.get(role.id)
            for member in list(role.members):
                if member.id == desired_user_id:
                    continue
                try:
                    await member.remove_roles(role, reason="ActivityStats top-message role sync")
                    result["removed"] += 1
                except (discord.Forbidden, discord.HTTPException):
                    result["skipped"].append(f"{tier}: could not remove {role.name} from {member.display_name}")

        for tier, role in manageable_roles.items():
            desired_user_id = desired_by_role.get(role.id)
            if desired_user_id is None:
                continue
            member = guild.get_member(desired_user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(desired_user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    result["skipped"].append(f"{tier}: could not find member {desired_user_id}")
                    continue
            if role in member.roles:
                continue
            try:
                await member.add_roles(role, reason="ActivityStats top-message role sync")
                result["added"] += 1
            except (discord.Forbidden, discord.HTTPException):
                result["skipped"].append(f"{tier}: could not add {role.name} to {member.display_name}")

        return result

    async def _remove_configured_top_message_roles(
        self,
        guild: discord.Guild,
        configured_roles: dict[str, str | int | None],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "configured": 0,
            "added": 0,
            "removed": 0,
            "skipped": [],
        }
        for tier, role_id in configured_roles.items():
            role = self._configured_role(guild, role_id)
            if role is None:
                continue
            result["configured"] += 1
            if not self._can_manage_role(guild, role):
                result["skipped"].append(f"{tier}: cannot manage {role.name}")
                continue
            for member in list(role.members):
                try:
                    await member.remove_roles(role, reason="ActivityStats top-message role config cleared")
                    result["removed"] += 1
                except (discord.Forbidden, discord.HTTPException):
                    result["skipped"].append(f"{tier}: could not remove {role.name} from {member.display_name}")
        return result

    async def _top_message_user_ids(self, guild: discord.Guild, limit: int) -> list[int]:
        user_messages = await self.config.guild(guild).user_messages()
        entries = sorted(
            ((int(user_id), int(count)) for user_id, count in user_messages.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        return [user_id for user_id, count in entries[:limit] if count > 0]

    def _configured_role(self, guild: discord.Guild, role_id: str | int | None) -> discord.Role | None:
        if not role_id:
            return None
        return guild.get_role(int(role_id))

    def _can_manage_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        if role.managed:
            return False
        me = guild.me
        return me is not None and me.guild_permissions.manage_roles and role < me.top_role

    @staticmethod
    def _format_role_sync_result(result: dict[str, Any]) -> str:
        message = f"Synced top-message roles: added {result['added']}, removed {result['removed']}."
        skipped = result.get("skipped") or []
        if skipped:
            message += f" Skipped {len(skipped)} update(s): " + "; ".join(skipped[:3])
            if len(skipped) > 3:
                message += f"; and {len(skipped) - 3} more"
            message += "."
        return message

    @staticmethod
    def _apply_reaction_delta(
        received_reactions: dict[str, dict[str, int]],
        author_id: str,
        emoji: str,
        delta: int,
    ):
        user_reactions = received_reactions.setdefault(author_id, {})
        new_count = int(user_reactions.get(emoji, 0)) + delta
        if new_count <= 0:
            user_reactions.pop(emoji, None)
        else:
            user_reactions[emoji] = new_count
        if not user_reactions:
            received_reactions.pop(author_id, None)

    async def _format_user_count_lines(
        self,
        guild: discord.Guild,
        entries: list[tuple[int, int]],
        label: str,
    ) -> str:
        if not entries:
            return "There are no entries to display here."

        lines = []
        for index, (user_id, count) in enumerate(entries, start=1):
            rank = RANK_PREFIXES.get(index, f"{index}.")
            lines.append(f"{rank} **{await self._display_user(guild, user_id)}** - {count} {label}")
        return "\n".join(lines)

    async def _format_reaction_lines(
        self,
        guild: discord.Guild,
        entries: list[tuple[int, int, str]],
        emoji: str | None,
    ) -> str:
        if not entries:
            prefix = f"{emoji}: " if emoji else ""
            return f"{prefix}There are no entries to display here."

        lines = []
        for index, (user_id, count, reaction) in enumerate(entries, start=1):
            user = await self._display_user(guild, user_id)
            rank = RANK_PREFIXES.get(index, f"{index}.")
            lines.append(f"{rank} **{user}** - {reaction} x {count}")
        return "\n".join(lines)

    async def _display_user(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member:
            return member.display_name
        user = self.bot.get_user(user_id)
        if user:
            return user.name
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return str(user_id)
        return user.name

    @staticmethod
    def _clean_limit(limit: int) -> int:
        return max(1, min(int(limit), MAX_LIMIT))

    @staticmethod
    def _entry_author_id(entry: Any) -> str | int | None:
        if isinstance(entry, dict):
            return entry.get("author_id")
        return entry

    @staticmethod
    def _emoji_key(emoji: discord.PartialEmoji | discord.Emoji | str) -> str:
        if isinstance(emoji, str):
            return emoji
        if getattr(emoji, "id", None):
            return str(emoji)
        return getattr(emoji, "name", None) or str(emoji)

    @staticmethod
    def _rank_for_user(user_messages: dict[str, int], user_id: int) -> int | None:
        count = int(user_messages.get(str(user_id), 0))
        if count <= 0:
            return None
        return sum(1 for other_count in user_messages.values() if int(other_count) > count) + 1
