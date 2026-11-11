"""One listener pipeline feeding both stats and XP."""

from __future__ import annotations

import time

import discord
from redbot.core import commands

from .common import channel_ignore_ids


class TrackingMixin:
    async def _enabled(self, guild: discord.Guild) -> bool:
        return bool(await self.config.guild(guild).enabled())

    async def _is_ignored(self, guild: discord.Guild, member: discord.Member | None, channel) -> bool:
        """Ignored channels and roles apply to every kind of tracking."""
        conf = self.config.guild(guild)
        if member is not None:
            ignored_roles = set(map(str, await conf.ignored_role_ids()))
            if ignored_roles and any(str(role.id) in ignored_roles for role in member.roles):
                return True
        if channel is None:
            return False
        return bool(channel_ignore_ids(channel) & set(map(str, await conf.ignored_channel_ids())))

    async def _is_ignored_channel_id(self, guild: discord.Guild, channel_id: int | None) -> bool:
        if channel_id is None:
            return False
        channel = guild.get_channel_or_thread(int(channel_id)) or guild.get_channel(int(channel_id))
        if channel is None:
            return str(channel_id) in set(map(str, await self.config.guild(guild).ignored_channel_ids()))
        return await self._is_ignored(guild, None, channel)

    async def _voice_tracked(self, member: discord.Member, state: discord.VoiceState | None) -> bool:
        """Counts towards voice time: connected to a channel that is not ignored."""
        if state is None or state.channel is None:
            return False
        return not await self._is_ignored(member.guild, member, state.channel)

    async def _voice_earns_xp(self, member: discord.Member, state: discord.VoiceState | None) -> bool:
        """Earns voice XP: tracked, not muted or deafened, and not in the AFK channel."""
        if not await self._voice_tracked(member, state):
            return False
        if state.self_deaf or state.deaf or getattr(state, "afk", False):
            return False
        if (state.self_mute or state.mute) and not await self.config.guild(member.guild).voice_xp_while_muted():
            return False
        afk = member.guild.afk_channel
        return not (afk and state.channel.id == afk.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        if not await self._enabled(guild) or await self._is_ignored(guild, message.author, message.channel):
            return
        await self._record_message(message)
        await self._maybe_sync_top_roles(guild)
        try:
            ctx = await self.bot.get_context(message)
        except Exception:
            ctx = None
        if ctx is not None and ctx.valid:
            return  # Commands count as messages but never earn XP.
        await self._award_chat_xp(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._record_reaction(payload, 1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._record_reaction(payload, -1)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or member.guild is None or not await self._enabled(member.guild):
            return
        guild, now = member.guild, int(time.time())

        was, is_ = await self._voice_tracked(member, before), await self._voice_tracked(member, after)
        if was and not is_:
            await self._close_voice_time(guild, member.id, now)
        elif is_ and not was:
            await self._start_voice_time(guild, member.id)

        was, is_ = await self._voice_earns_xp(member, before), await self._voice_earns_xp(member, after)
        if was and not is_:
            await self._close_voice_xp(guild, member, now)
        elif is_ and not was:
            async with self.config.guild(guild).active_voice_sessions() as sessions:
                sessions[str(member.id)] = now

    async def _close_voice_xp(self, guild: discord.Guild, member: discord.Member, ended_at: int):
        async with self.config.guild(guild).active_voice_sessions() as sessions:
            started = sessions.pop(str(member.id), None)
        if started is not None:
            await self._award_voice_xp(guild, member, max(0, int(ended_at) - int(started)) // 60)

    async def _seed_voice_sessions(self, guild: discord.Guild):
        """Match open sessions to who is in voice right now.

        XP sessions restart from now. Voice-time sessions keep their start time for members still connected.
        """
        now = int(time.time())
        tracked, earning = set(), set()
        for channel in guild.voice_channels:
            for member in channel.members:
                if member.bot or member.voice is None:
                    continue
                if await self._voice_tracked(member, member.voice):
                    tracked.add(str(member.id))
                if await self._voice_earns_xp(member, member.voice):
                    earning.add(str(member.id))
        async with self.config.guild(guild).active_voice_sessions() as sessions:
            sessions.clear()
            sessions.update({user_id: now for user_id in earning})
        async with self.stats_config.guild(guild).active_voice_sessions() as sessions:
            for user_id in [uid for uid in sessions if uid not in tracked]:
                sessions.pop(user_id)
            for user_id in tracked:
                sessions.setdefault(user_id, now)

    async def _seed_all_when_ready(self):
        await self.bot.wait_until_ready()
        for guild in list(self.bot.guilds):
            if await self._enabled(guild):
                await self._seed_voice_sessions(guild)

    async def _set_enabled(self, guild: discord.Guild, enabled: bool):
        await self.config.guild(guild).enabled.set(enabled)
        await self.stats_config.guild(guild).enabled.set(enabled)
        if enabled:
            await self._seed_voice_sessions(guild)
        else:
            await self._close_all_voice_time(guild, int(time.time()))
            await self.config.guild(guild).active_voice_sessions.set({})
