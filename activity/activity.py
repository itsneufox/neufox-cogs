from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import discord
from redbot.core import Config, app_commands, commands

from .common import (
    ACCENT,
    BOARDS,
    CURRENCY_NAME,
    DEFAULT_REWARD_AMOUNTS,
    LEVELING_CONFIG,
    MAX_LEVEL,
    PAGE_SIZE,
    RANK_PREFIXES,
    REWARD_CATEGORIES,
    STATS_CONFIG,
    TOP_ROLE_TIERS,
    XP_KINDS,
    can_manage_role,
    channel_reference,
    format_duration,
    normalize_board,
    rank_of,
)
from .layouts import NO_PINGS, LeaderboardView, card
from .rewards import RewardsMixin, reward_amounts
from .stats import ROLE_SYNC_INTERVAL, StatsMixin
from .tracking import TrackingMixin
from .xp import (
    DEFAULT_CURVE,
    DEFAULT_LEVELUP_MESSAGE,
    LEVELUP_PLACEHOLDERS,
    levelup_message_error,
    XPMixin,
    curve_scale,
    level_from_xp,
    next_reward_text,
    normalized_record,
    rank_block,
    xp_rank,
)


KIND_CHOICES = [app_commands.Choice(name="Chat", value="chat"), app_commands.Choice(name="Voice", value="voice")]
BOARD_CHOICES = [app_commands.Choice(name=label, value=key) for key, (label, _) in BOARDS.items()]


class Activity(TrackingMixin, XPMixin, StatsMixin, RewardsMixin, commands.Cog):
    """Chat and voice XP, levels, activity stats, leaderboards and LWD$ rewards."""

    def __init__(self, bot):
        self.bot = bot
        # Both old namespaces are opened by name so existing Leveling and ActivityStats data is reused as is.
        self.config = Config.get_conf(None, force_registration=True, **LEVELING_CONFIG)
        self.config.register_guild(
            enabled=True,
            announce_levelups=True,
            announce_interval=5,
            levelup_channel_id=None,
            chat_xp_min=15,
            chat_xp_max=25,
            chat_cooldown=30,
            voice_xp_min=5,
            voice_xp_max=10,
            level_curve_scale=DEFAULT_CURVE,
            ignored_channel_ids=[],
            ignored_role_ids=[],
            users={},
            level_roles={kind: {} for kind in XP_KINDS},
            active_voice_sessions={},
            level_cash_interval=10,
            level_cash_per_level=100,
            levelup_message=DEFAULT_LEVELUP_MESSAGE,
            levelup_dm=False,
            levelup_ping=False,
            level_roles_stack=True,
            voice_xp_while_muted=False,
            bonus_xp_channel_ids=[],
            bonus_channel_multiplier=2.0,
            bonus_xp_role_ids=[],
            bonus_role_multiplier=1.5,
        )
        self.stats_config = Config.get_conf(None, force_registration=True, **STATS_CONFIG)
        self.stats_config.register_guild(
            enabled=True,
            total_messages=0,
            user_messages={},
            user_display_names={},
            channel_messages={},
            ignored_channel_ids=[],
            message_authors={},
            received_reactions={},
            message_reactions={},
            voice_seconds={},
            active_voice_sessions={},
            top_message_roles={tier: None for tier in TOP_ROLE_TIERS},
            reward_enabled=True,
            reward_cooldown=86400,
            reward_amounts=DEFAULT_REWARD_AMOUNTS,
            reward_last_paid={},
            reward_log_channel_id=None,
            activity_migrated=False,
            top_role_sync_interval=ROLE_SYNC_INTERVAL,
        )
        self._chat_cooldowns: dict[tuple[int, int], float] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_role_sync: dict[int, float] = {}
        self._tasks: list[asyncio.Task] = []

    async def cog_load(self):
        await self._merge_old_settings()
        self._tasks = [asyncio.create_task(self._seed_all_when_ready()), asyncio.create_task(self._reward_loop())]

    async def cog_unload(self):
        for task in self._tasks:
            task.cancel()

    async def _merge_old_settings(self):
        """One-time merge of the ActivityStats switches into the shared ones.

        Ignored channels become one list for everything. Tracking stays on only where both old cogs had it on.
        """
        for guild_id, stats in (await self.stats_config.all_guilds()).items():
            if stats.get("activity_migrated"):
                continue
            leveling = self.config.guild_from_id(guild_id)
            ignored = [str(item) for item in await leveling.ignored_channel_ids()]
            ignored += [str(item) for item in stats.get("ignored_channel_ids", []) if str(item) not in ignored]
            enabled = bool(await leveling.enabled()) and bool(stats.get("enabled", True))
            await leveling.ignored_channel_ids.set(ignored)
            await leveling.enabled.set(enabled)
            stats_conf = self.stats_config.guild_from_id(guild_id)
            await stats_conf.enabled.set(enabled)
            await stats_conf.activity_migrated.set(True)

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    # Control panel

    def control_panel_schema(self) -> dict[str, Any]:
        def stats_attr(key: str, section: str, **extra) -> dict[str, Any]:
            return {
                "key": key,
                "section": section,
                "get": lambda guild: self.stats_config.guild(guild).get_attr(key)(),
                "set": lambda guild, value: self.stats_config.guild(guild).get_attr(key).set(value),
                **extra,
            }

        def xp_range(key: str, label: str, other: str, is_minimum: bool) -> dict[str, Any]:
            async def check(guild, value):
                other_value = await self.config.guild(guild).get_attr(other)()
                if (value > other_value) if is_minimum else (value < other_value):
                    return f"The minimum cannot be above the maximum (the other value is {other_value:,})."
                return None

            return {"key": key, "label": label, "type": "int", "section": "XP", "min": 0, "max": 10000, "unit": "XP", "validate": check}

        def top_role(tier: str) -> dict[str, Any]:
            async def get(guild):
                return (await self.stats_config.guild(guild).top_message_roles()).get(tier)

            async def set_(guild, value):
                async with self.stats_config.guild(guild).top_message_roles() as roles:
                    previous = roles.get(tier)
                    roles[tier] = value
                if previous and previous != value:
                    await self._strip_top_role(guild, previous)
                await self._sync_top_roles(guild)

            def validate(guild, value):
                role = guild.get_role(int(value)) if value else None
                if role is not None and not can_manage_role(guild, role):
                    return f"I cannot manage {role.name}. Move my highest role above it first."
                return None

            return {
                "key": f"top_message_roles.{tier}",
                "label": f"{tier.title()} place role",
                "type": "role",
                "section": "Stats",
                "id_format": "str",
                "help": f"Given to the member in {tier} place on the message leaderboard.",
                "get": get,
                "set": set_,
                "validate": validate,
            }

        def reward_amount(category: str, rank: int) -> dict[str, Any]:
            async def get(guild):
                return reward_amounts(await self.stats_config.guild(guild).reward_amounts())[category].get(str(rank), 0)

            async def set_(guild, value):
                async with self.stats_config.guild(guild).reward_amounts() as stored:
                    amounts = reward_amounts(stored)
                    if value:
                        amounts[category][str(rank)] = int(value)
                    else:
                        amounts[category].pop(str(rank), None)
                    stored.clear()
                    stored.update(amounts)

            place = {1: "1st", 2: "2nd", 3: "3rd"}[rank]
            return {
                "key": f"reward_amounts.{category}.{rank}",
                "label": f"{BOARDS[category][0]} {place}",
                "type": "int",
                "section": "Rewards",
                "min": 0,
                "max": 10_000_000,
                "unit": CURRENCY_NAME,
                "get": get,
                "set": set_,
            }

        return {
            "title": "Activity",
            "description": "Chat and voice XP, activity stats, leaderboards and LWD$ rewards.",
            "settings": [
                {"key": "enabled", "label": "Tracking", "type": "bool", "section": "Tracking",
                 "help": "Track XP and stats in this server.", "set": self._set_enabled},
                {"key": "ignored_channel_ids", "label": "Ignored channels", "type": "channels", "section": "Tracking",
                 "channel_types": ["text", "voice", "category", "forum", "stage", "news"], "id_format": "str",
                 "help": "Channels and categories that earn no XP and count no stats."},
                {"key": "ignored_role_ids", "label": "Ignored roles", "type": "roles", "section": "Tracking",
                 "id_format": "str", "help": "Members with these roles earn no XP and count no stats."},
                {"key": "level_curve_scale", "label": "Level curve", "type": "float", "section": "XP",
                 "min": 0.1, "max": 20, "unit": "x", "help": "Higher values require more XP per level."},
                {"key": "chat_cooldown", "label": "Chat cooldown", "type": "int", "section": "XP",
                 "min": 0, "max": 3600, "unit": "seconds", "help": "How often one member can earn chat XP."},
                xp_range("chat_xp_min", "Chat XP minimum", "chat_xp_max", True),
                xp_range("chat_xp_max", "Chat XP maximum", "chat_xp_min", False),
                xp_range("voice_xp_min", "Voice XP minimum per minute", "voice_xp_max", True),
                xp_range("voice_xp_max", "Voice XP maximum per minute", "voice_xp_min", False),
                {"key": "announce_levelups", "label": "Level-up messages", "type": "bool", "section": "XP",
                 "help": "Post a message when members reach a milestone."},
                {"key": "announce_interval", "label": "Announce every", "type": "int", "section": "XP",
                 "min": 1, "max": 1000, "unit": "levels", "help": "Levels that pay cash or give a role are always announced."},
                {"key": "levelup_channel_id", "label": "Level-up channel", "type": "channel", "section": "XP",
                 "channel_types": ["text"], "empty": "Same channel as the message",
                 "help": "Voice level-ups are only announced when this is set."},
                {"key": "levelup_dm", "label": "Level-ups by DM", "type": "bool", "section": "XP",
                 "help": "Send level-up messages to the member's DMs instead of a channel."},
                {"key": "levelup_ping", "label": "Ping on level-up", "type": "bool", "section": "XP",
                 "help": "Let the level-up message notify the member it mentions."},
                {"key": "levelup_message", "label": "Level-up message", "type": "text", "section": "XP",
                 "min": 1, "max": 1000, "validate": lambda guild, value: levelup_message_error(value),
                 "help": "Placeholders: " + " ".join(f"{{{key}}}" for key in LEVELUP_PLACEHOLDERS) + "."},
                {"key": "level_roles_stack", "label": "Keep lower level roles", "type": "bool", "section": "XP",
                 "help": "On: members keep every level role earned. Off: only the highest one is kept."},
                {"key": "voice_xp_while_muted", "label": "Voice XP while muted", "type": "bool", "section": "XP",
                 "help": "Muted members still earn voice XP. Deafened and AFK members never do."},
                {"key": "bonus_xp_channel_ids", "label": "Bonus XP channels", "type": "channels", "section": "XP",
                 "channel_types": ["text", "category", "forum", "news", "voice"], "id_format": "str",
                 "help": "Chat XP earned in these channels or categories is multiplied."},
                {"key": "bonus_channel_multiplier", "label": "Bonus channel multiplier", "type": "float", "section": "XP",
                 "min": 0, "max": 10, "unit": "x", "help": "XP multiplier for bonus channels. Below 1 reduces XP."},
                {"key": "bonus_xp_role_ids", "label": "Bonus XP roles", "type": "roles", "section": "XP",
                 "id_format": "str", "help": "Members with any of these roles earn multiplied chat and voice XP."},
                {"key": "bonus_role_multiplier", "label": "Bonus role multiplier", "type": "float", "section": "XP",
                 "min": 0, "max": 10, "unit": "x", "help": "XP multiplier for bonus roles. Stacks with the channel bonus."},
                top_role("first"),
                top_role("second"),
                top_role("third"),
                stats_attr("top_role_sync_interval", "Stats", label="Top role refresh", type="int",
                           min=30, max=86400, unit="seconds", help="How often top-message roles are checked and moved."),
                {"key": "level_cash_interval", "label": "Level cash every", "type": "int", "section": "Rewards",
                 "min": 0, "max": 1000, "unit": "levels", "help": "0 turns level cash off."},
                {"key": "level_cash_per_level", "label": "Level cash per level", "type": "int", "section": "Rewards",
                 "min": 0, "max": 1_000_000, "unit": CURRENCY_NAME, "help": "Reaching level 20 pays 20 times this."},
                stats_attr("reward_enabled", "Rewards", label="Leaderboard rewards", type="bool",
                           help="Pay LWD$ to the top of each leaderboard automatically."),
                stats_attr("reward_cooldown", "Rewards", label="Leaderboard payout every", type="int",
                           min=0, max=30 * 86400, unit="seconds", help="Time between automatic payouts."),
                stats_attr("reward_log_channel_id", "Rewards", label="Reward log channel", type="channel",
                           channel_types=["text"], help="Where automatic payouts are logged."),
                *(reward_amount(category, rank) for category in REWARD_CATEGORIES for rank in (1, 2, 3)),
            ],
        }

    # Commands

    @commands.hybrid_command(name="rank", aliases=["level"])
    @commands.guild_only()
    @app_commands.describe(member="Whose card to show. Defaults to you.")
    async def rank(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show a member's levels, activity stats and XP boost."""
        await ctx.send(view=await self._rank_card(ctx.guild, member or ctx.author), allowed_mentions=NO_PINGS)

    @commands.hybrid_command(name="leaderboard", aliases=["lb"])
    @commands.guild_only()
    @app_commands.describe(board="Which leaderboard to open.", emoji="Reactions board: only count this emoji.")
    @app_commands.choices(board=BOARD_CHOICES)
    async def leaderboard(self, ctx: commands.Context, board: str = "xp", emoji: str | None = None):
        """Show the XP, message, voice or reaction leaderboard."""
        key = normalize_board(board)
        if key is None:
            await ctx.send(f"Pick one of: {', '.join(BOARDS)}.", ephemeral=True)
            return
        if emoji and key == "xp":
            key = "reactions"  # An emoji only makes sense on the reactions board.
        guild = ctx.guild

        async def load(selected: str) -> list[discord.Embed]:
            return await self._leaderboard_pages(guild, selected, emoji if selected == "reactions" else None)

        async with ctx.typing():
            view = LeaderboardView(ctx.author.id, key, await load(key), load)
        view.message = await ctx.send(view=view, allowed_mentions=NO_PINGS)

    @commands.hybrid_group(name="activity", invoke_without_command=True)
    @commands.guild_only()
    async def activity(self, ctx: commands.Context):
        """Activity tracking, level roles and maintenance."""
        await ctx.invoke(self.activity_overview)

    @activity.command(name="overview")
    async def activity_overview(self, ctx: commands.Context):
        """Show server totals, level roles and rewards."""
        await ctx.send(view=card(await self._overview_embed(ctx.guild, ctx.clean_prefix)), allowed_mentions=NO_PINGS)

    @activity.command(name="announce")
    @app_commands.describe(enabled="Whether your level-ups can be announced. Leave empty to see the setting.")
    async def activity_announce(self, ctx: commands.Context, enabled: bool | None = None):
        """Opt in or out of public level-up messages for yourself."""
        if enabled is None:
            current = await self._announce_preference(ctx.guild, ctx.author.id)
            await ctx.send(f"Your level-up messages are {'on' if current else 'off'}.", ephemeral=True)
            return
        await self._set_announce_preference(ctx.guild, ctx.author, enabled)
        await ctx.send(f"Your level-up messages are now {'on' if enabled else 'off'}.", ephemeral=True)

    @activity.command(name="levelrole")
    @commands.admin_or_permissions(manage_roles=True)
    @app_commands.describe(kind="Chat or voice levels.", level="The level that awards the role.", role="Role to award. Leave empty to remove.")
    @app_commands.choices(kind=KIND_CHOICES)
    async def activity_levelrole(self, ctx: commands.Context, kind: str, level: int, role: discord.Role | None = None):
        """Set or remove the role awarded at a chat or voice level."""
        kind = kind.lower()
        if kind not in XP_KINDS:
            await ctx.send("Kind must be `chat` or `voice`.")
            return
        if not 1 <= level <= MAX_LEVEL:
            await ctx.send(f"Level must be between 1 and {MAX_LEVEL:,}.")
            return
        if role is not None and not can_manage_role(ctx.guild, role):
            await ctx.send(f"I cannot manage {role.mention}. Move my highest role above it first.", allowed_mentions=NO_PINGS)
            return
        async with self.config.guild(ctx.guild).level_roles() as level_roles:
            kind_roles = level_roles.setdefault(kind, {})
            if role is None:
                removed = kind_roles.pop(str(level), None)
            else:
                kind_roles[str(level)] = str(role.id)
        if role is not None:
            await ctx.send(f"{kind.title()} level {level} now awards {role.mention}.", allowed_mentions=NO_PINGS)
        elif removed:
            await ctx.send(f"Removed the {kind} level {level} role.")
        else:
            await ctx.send(f"No {kind} role is set for level {level}.")

    @activity.command(name="sources")
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(member="Whose messages to look up. Defaults to you.")
    async def activity_sources(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show which channels a member's counted messages came from."""
        member = member or ctx.author
        counts = await self._message_sources(ctx.guild, member.id)
        if not counts:
            await ctx.send(f"No known messages from {member.mention}.", allowed_mentions=NO_PINGS)
            return
        lines = []
        for channel_id, count in counts.most_common(20):
            channel = ctx.guild.get_channel_or_thread(int(channel_id)) or ctx.guild.get_channel(int(channel_id))
            lines.append(f"{channel_reference(channel) if channel else f'`{channel_id}`'}: {count:,}")
        if len(counts) > 20:
            lines.append(f"...and {len(counts) - 20} more")
        embed = discord.Embed(title=f"{member.display_name}'s message sources", description="\n".join(lines), color=ACCENT)
        embed.set_footer(text=f"{sum(counts.values()):,} known messages across {len(counts):,} channels")
        await ctx.send(view=card(embed), allowed_mentions=NO_PINGS)

    @activity.command(name="backfill")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.bot_has_permissions(read_message_history=True)
    @app_commands.describe(
        channel="Channel to import. Defaults to this one.",
        limit="Messages to read per channel.",
        everywhere="Import every readable text channel instead.",
    )
    async def activity_backfill(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
        limit: commands.Range[int, 1, 100000] = 1000,
        everywhere: bool = False,
    ):
        """Import old messages and reactions into the stats."""
        if everywhere:
            channels = list(ctx.guild.text_channels)
        else:
            channel = channel or ctx.channel
            if not isinstance(channel, discord.TextChannel):
                await ctx.send("Pick a text channel to backfill.")
                return
            channels = [channel]
        totals = [0, 0, 0]
        async with ctx.typing():
            for target in channels:
                if await self._is_ignored(ctx.guild, None, target):
                    continue
                permissions = target.permissions_for(ctx.guild.me)
                if not permissions.read_messages or not permissions.read_message_history:
                    continue
                for index, value in enumerate(await self._backfill_channel(ctx.guild, target, limit)):
                    totals[index] += value
            role_result = await self._sync_top_roles(ctx.guild)
        text = f"Backfill done: scanned {totals[0]:,} messages, added {totals[1]:,} new, updated {totals[2]:,} reaction counts."
        if role_result["configured"]:
            text += "\n" + self._format_role_sync(role_result)
        await ctx.send(text)

    @activity.command(name="purge")
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel, thread or category whose stats to forget.")
    async def activity_purge(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | discord.Thread | discord.CategoryChannel,
    ):
        """Forget the message and reaction stats from a channel or category."""
        async with ctx.typing():
            result = await self._purge_channel_stats(ctx.guild, channel)
            role_result = await self._sync_top_roles(ctx.guild)
        text = (
            f"Removed {result['messages_removed']:,} messages and {result['reactions_removed']:,} reactions "
            f"from {channel_reference(channel)}."
        )
        if role_result["configured"]:
            text += "\n" + self._format_role_sync(role_result)
        await ctx.send(text, allowed_mentions=NO_PINGS)

    @activity.command(name="recount")
    @commands.admin_or_permissions(manage_guild=True)
    async def activity_recount(self, ctx: commands.Context):
        """Rebuild message and reaction totals from known messages."""
        async with ctx.typing():
            result = await self._recount_stats(ctx.guild)
            role_result = await self._sync_top_roles(ctx.guild)
        text = (
            f"Recounted {result['messages']:,} messages from {result['users']:,} members in {result['channels']:,} channels. "
            f"Dropped {result['orphaned']:,} orphaned reaction records."
        )
        if role_result["configured"]:
            text += "\n" + self._format_role_sync(role_result)
        await ctx.send(text)

    @activity.command(name="payout")
    @commands.admin_or_permissions(manage_guild=True)
    async def activity_payout(self, ctx: commands.Context):
        """Pay any leaderboard rewards that are due now."""
        if self._economy() is None:
            await ctx.send("The Economy cog is not loaded.")
            return
        paid = await self._pay_due_rewards(ctx.guild)
        await ctx.send("Due leaderboard rewards were paid." if paid else "No leaderboard rewards are due right now.")

    @activity.command(name="reset")
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(confirm="Set to True to really delete the stats.")
    async def activity_reset(self, ctx: commands.Context, confirm: bool = False):
        """Delete all message, reaction and voice-time stats. XP is kept."""
        if not confirm:
            await ctx.send("This deletes every message, reaction and voice-time stat in this server. Run it again with `confirm` set to True.")
            return
        await self._reset_stats(ctx.guild)
        role_result = await self._sync_top_roles(ctx.guild)
        text = "Activity stats were reset. XP and levels were kept."
        if role_result["configured"]:
            text += "\n" + self._format_role_sync(role_result)
        await ctx.send(text)

    # Cards and leaderboards

    async def _rank_card(self, guild: discord.Guild, member: discord.Member) -> discord.ui.LayoutView:
        data = await self.config.guild(guild).all()
        stats = await self.stats_config.guild(guild).all()
        curve = curve_scale(data["level_curve_scale"])
        record = normalized_record(data["users"].get(str(member.id), {}))
        cash_interval, cash_per_level = (
            (int(data.get("level_cash_interval", 10)), int(data.get("level_cash_per_level", 100)))
            if self._economy() is not None
            else (0, 0)
        )
        blocks = []
        for kind in XP_KINDS:
            xp = record[f"{kind}_xp"]
            reward = next_reward_text(guild, level_from_xp(xp, curve), data["level_roles"].get(kind, {}), cash_interval, cash_per_level)
            blocks.append(rank_block(kind, xp, xp_rank(data["users"], member.id, kind), curve, reward))

        voice = await self._voice_seconds(guild)
        reactions = await self._reaction_totals(guild)
        messages = stats["user_messages"]

        def ranked(value: str, scores: dict[str, Any]) -> str:
            position = rank_of(scores, member.id)
            return f"{value} (#{position})" if position else value

        stat_lines = [
            "  ·  ".join(
                (
                    f"**Messages** {ranked(f'{int(messages.get(str(member.id), 0)):,}', messages)}",
                    f"**Voice** {ranked(format_duration(voice.get(str(member.id), 0)), voice)}",
                    f"**Reactions** {ranked(f'{reactions.get(str(member.id), 0):,}', reactions)}",
                )
            )
        ]
        top_emoji = Counter(stats["received_reactions"].get(str(member.id), {})).most_common(5)
        if top_emoji:
            stat_lines.append("-# Top reactions: " + "  ".join(f"{emoji} {int(count):,}" for emoji, count in top_emoji))
        blocks.append("\n".join(stat_lines))

        boost = await self._boost_text(member.id)
        name = discord.utils.escape_markdown(member.display_name)
        items: list[discord.ui.Item] = [
            discord.ui.Section(
                discord.ui.TextDisplay(f"## {name}\n-# {discord.utils.escape_markdown(guild.name)} activity card"),
                accessory=discord.ui.Thumbnail(member.display_avatar.url),
            )
        ]
        for block in blocks:
            items.extend([discord.ui.Separator(), discord.ui.TextDisplay(block)])
        if boost:
            items.append(discord.ui.TextDisplay(boost))
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(*items, accent_color=ACCENT))
        return view

    async def _boost_text(self, user_id: int) -> str | None:
        economy = self.bot.get_cog("Economy")
        if economy is None or not callable(getattr(economy, "get_multiplier", None)):
            return None
        multiplier = await self._xp_multiplier(user_id)
        if multiplier > 1.0:
            return f"⚡ **XP boost active:** {multiplier:g}x XP from chat and voice"
        return "-# Level up faster with an XP booster from the Economy shop."

    async def _board_scores(self, guild: discord.Guild, board: str, emoji: str | None = None) -> dict[str, int]:
        if board in ("xp", "voicexp"):
            key = "chat_xp" if board == "xp" else "voice_xp"
            return {uid: normalized_record(record)[key] for uid, record in (await self.config.guild(guild).users()).items()}
        if board == "messages":
            return {uid: int(count) for uid, count in (await self.stats_config.guild(guild).user_messages()).items()}
        if board == "voice":
            return await self._voice_seconds(guild)
        if board == "reactions":
            return await self._reaction_totals(guild, emoji)
        return {}

    async def _names(self, guild: discord.Guild):
        cached = await self.stats_config.guild(guild).user_display_names()
        users = await self.config.guild(guild).users()

        def name(user_id: int) -> str:
            member = guild.get_member(user_id)
            if member is not None:
                return member.display_name
            stored = cached.get(str(user_id)) or users.get(str(user_id), {}).get("display_name")
            if stored:
                return stored
            user = self.bot.get_user(user_id)
            return user.name if user else f"Unknown user ({user_id})"

        return name

    async def _display_name(self, guild: discord.Guild, user_id: int) -> str:
        return (await self._names(guild))(int(user_id))

    async def _leaderboard_pages(self, guild: discord.Guild, board: str, emoji: str | None = None) -> list[discord.Embed]:
        scores = await self._board_scores(guild, board, emoji)
        entries = sorted(((int(uid), int(score)) for uid, score in scores.items() if int(score) > 0), key=lambda item: item[1], reverse=True)
        title = f"{BOARDS[board][0]} leaderboard" + (f" · {emoji}" if emoji and board == "reactions" else "")
        if not entries:
            return [discord.Embed(title=title, description="No one is on this board yet.", color=ACCENT)]
        name = await self._names(guild)
        curve = curve_scale(await self.config.guild(guild).level_curve_scale()) if board in ("xp", "voicexp") else DEFAULT_CURVE

        def value(score: int) -> str:
            if board in ("xp", "voicexp"):
                return f"Level {level_from_xp(score, curve)} ({score:,} XP)"
            if board == "voice":
                return format_duration(score)
            return f"{score:,} {'messages' if board == 'messages' else 'reactions'}"

        pages = []
        total = (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE
        for start in range(0, len(entries), PAGE_SIZE):
            lines = []
            for position, (user_id, score) in enumerate(entries[start : start + PAGE_SIZE], start=start + 1):
                prefix = RANK_PREFIXES.get(position, f"`#{position}`")
                lines.append(f"{prefix} **{discord.utils.escape_markdown(name(user_id))}** · {value(score)}")
            embed = discord.Embed(title=title, description="\n".join(lines), color=ACCENT)
            embed.set_footer(text=f"Page {len(pages) + 1}/{total}")
            pages.append(embed)
        return pages

    async def _overview_embed(self, guild: discord.Guild, prefix: str) -> discord.Embed:
        data = await self.config.guild(guild).all()
        stats = await self.stats_config.guild(guild).all()
        voice_total = sum((await self._voice_seconds(guild)).values())
        reactions_total = sum((await self._reaction_totals(guild)).values())
        embed = discord.Embed(title="Activity", color=ACCENT)
        embed.description = (
            f"Tracking is **{'on' if data['enabled'] else 'off'}**. "
            f"Settings live in the control panel; use `/rank` and `/leaderboard` to look around."
        )
        embed.add_field(name="Messages", value=f"{int(stats['total_messages']):,}")
        embed.add_field(name="Reactions", value=f"{reactions_total:,}")
        embed.add_field(name="Voice time", value=format_duration(voice_total))
        embed.add_field(name="Members with XP", value=f"{sum(1 for r in data['users'].values() if normalized_record(r)['chat_xp'] or normalized_record(r)['voice_xp']):,}")

        role_lines = []
        for kind in XP_KINDS:
            for level, role_id in sorted(data["level_roles"].get(kind, {}).items(), key=lambda item: int(item[0])):
                role = guild.get_role(int(role_id))
                role_lines.append(f"{kind.title()} {level}: {role.mention if role else f'deleted role `{role_id}`'}")
        embed.add_field(name="Level roles", value="\n".join(role_lines[:20]) or f"None. Add one with `{prefix}activity levelrole`.", inline=False)

        top_roles = stats["top_message_roles"]
        top_lines = [f"{tier.title()}: <@&{top_roles[tier]}>" for tier in TOP_ROLE_TIERS if top_roles.get(tier)]
        if top_lines:
            embed.add_field(name="Top message roles", value="\n".join(top_lines), inline=False)

        if self._economy() is not None:
            interval, per_level = int(data["level_cash_interval"]), int(data["level_cash_per_level"])
            lines = []
            if interval and per_level:
                lines.append(f"Every {interval} levels pays `level x {per_level:,}` {CURRENCY_NAME}.")
            if stats["reward_enabled"]:
                amounts = reward_amounts(stats["reward_amounts"])
                for category in REWARD_CATEGORIES:
                    paid = ", ".join(f"#{rank} {amount:,}" for rank, amount in sorted(amounts[category].items(), key=lambda i: int(i[0])))
                    last = int(stats["reward_last_paid"].get(category, 0))
                    lines.append(f"{BOARDS[category][0]}: {paid or 'none'}" + (f" (last paid <t:{last}:R>)" if last else ""))
                lines.append(f"Leaderboard payouts every {format_duration(stats['reward_cooldown'])}.")
            embed.add_field(name="Rewards", value="\n".join(lines) or "Off", inline=False)
        return embed
