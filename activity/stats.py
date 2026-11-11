"""Message, reaction and voice-time stats. Data lives in the old ActivityStats namespace (``self.stats_config``)."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import discord

from .common import (
    TOP_ROLE_TIERS,
    apply_reaction_delta,
    can_manage_role,
    emoji_key,
    entry_author_id,
    entry_channel_id,
)


ROLE_SYNC_INTERVAL = 300


class StatsMixin:
    """Counts messages, received reactions and voice time, and keeps the top-message roles in sync."""

    async def _record_message(self, message: discord.Message):
        conf = self.stats_config.guild(message.guild)
        user_id, channel_id = str(message.author.id), str(message.channel.id)
        async with self._lock_for(message.guild.id):
            await conf.total_messages.set(int(await conf.total_messages()) + 1)
            async with conf.user_messages() as user_messages:
                user_messages[user_id] = int(user_messages.get(user_id, 0)) + 1
            async with conf.channel_messages() as channel_messages:
                channel_messages[channel_id] = int(channel_messages.get(channel_id, 0)) + 1
            async with conf.message_authors() as authors:
                authors[str(message.id)] = {"author_id": user_id, "channel_id": channel_id, "created_at": int(time.time())}
            async with conf.user_display_names() as names:
                if names.get(user_id) != message.author.display_name:
                    names[user_id] = message.author.display_name

    async def _record_reaction(self, payload: discord.RawReactionActionEvent, delta: int):
        if payload.guild_id is None or (self.bot.user and payload.user_id == self.bot.user.id):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not await self._enabled(guild):
            return
        if await self._is_ignored_channel_id(guild, payload.channel_id):
            return
        conf = self.stats_config.guild(guild)
        entry = (await conf.message_authors()).get(str(payload.message_id))
        author_id = str(entry_author_id(entry) or "") if entry else ""
        if not author_id or author_id == str(payload.user_id):
            return
        emoji = emoji_key(payload.emoji)
        async with self._lock_for(guild.id):
            async with conf.received_reactions() as received:
                apply_reaction_delta(received, author_id, emoji, delta)
            async with conf.message_reactions() as snapshots:
                counts = snapshots.setdefault(str(payload.message_id), {})
                count = int(counts.get(emoji, 0)) + delta
                if count <= 0:
                    counts.pop(emoji, None)
                else:
                    counts[emoji] = count
                if not counts:
                    snapshots.pop(str(payload.message_id), None)

    # Voice time

    async def _start_voice_time(self, guild: discord.Guild, user_id: int):
        async with self.stats_config.guild(guild).active_voice_sessions() as sessions:
            sessions[str(user_id)] = int(time.time())

    async def _close_voice_time(self, guild: discord.Guild, user_id: int, ended_at: int):
        key = str(user_id)
        async with self.stats_config.guild(guild).active_voice_sessions() as sessions:
            started = sessions.pop(key, None)
        if started is None:
            return
        elapsed = max(0, int(ended_at) - int(started))
        if elapsed:
            async with self.stats_config.guild(guild).voice_seconds() as totals:
                totals[key] = int(totals.get(key, 0)) + elapsed

    async def _close_all_voice_time(self, guild: discord.Guild, ended_at: int):
        async with self.stats_config.guild(guild).active_voice_sessions() as sessions:
            closed = dict(sessions)
            sessions.clear()
        if not closed:
            return
        async with self.stats_config.guild(guild).voice_seconds() as totals:
            for user_id, started in closed.items():
                elapsed = max(0, int(ended_at) - int(started))
                if elapsed:
                    totals[str(user_id)] = int(totals.get(str(user_id), 0)) + elapsed

    async def _voice_seconds(self, guild: discord.Guild) -> dict[str, int]:
        """Stored voice time plus the running time of sessions that are still open."""
        conf = self.stats_config.guild(guild)
        totals = {str(uid): int(seconds) for uid, seconds in (await conf.voice_seconds()).items()}
        now = int(time.time())
        for user_id, started in (await conf.active_voice_sessions()).items():
            member = guild.get_member(int(user_id))
            if member is None or member.voice is None or member.voice.channel is None:
                continue
            totals[str(user_id)] = totals.get(str(user_id), 0) + max(0, now - int(started))
        return totals

    async def _reaction_totals(self, guild: discord.Guild, emoji: str | None = None) -> dict[str, int]:
        received = await self.stats_config.guild(guild).received_reactions()
        if emoji:
            return {uid: int(counts.get(emoji, 0)) for uid, counts in received.items() if int(counts.get(emoji, 0)) > 0}
        return {uid: sum(int(count) for count in counts.values()) for uid, counts in received.items()}

    # Maintenance

    async def _backfill_channel(self, guild: discord.Guild, channel: discord.TextChannel, limit: int) -> tuple[int, int, int]:
        conf = self.stats_config.guild(guild)
        known = await conf.message_authors()
        snapshots = await conf.message_reactions()
        scanned = added = reaction_updates = 0
        new_authors: dict[str, dict[str, Any]] = {}
        user_deltas: Counter[str] = Counter()
        names: dict[str, str] = {}
        reaction_deltas: list[tuple[str, str, int]] = []
        new_snapshots: dict[str, dict[str, int]] = {}
        touched: set[str] = set()

        async for message in channel.history(limit=limit, oldest_first=True):
            scanned += 1
            if message.author.bot:
                continue
            message_id, author_id = str(message.id), str(message.author.id)
            names[author_id] = str(message.author.display_name)
            if message_id not in known and message_id not in new_authors:
                new_authors[message_id] = {
                    "author_id": author_id,
                    "channel_id": str(channel.id),
                    "created_at": int(message.created_at.timestamp()),
                }
                user_deltas[author_id] += 1
                added += 1
            current = await self._message_reaction_counts(message)
            previous = {emoji: int(count) for emoji, count in snapshots.get(message_id, {}).items()}
            for emoji in set(current) | set(previous):
                delta = current.get(emoji, 0) - previous.get(emoji, 0)
                if delta:
                    reaction_deltas.append((author_id, emoji, delta))
                    reaction_updates += abs(delta)
                    touched.add(message_id)
            if current:
                new_snapshots[message_id] = current

        async with self._lock_for(guild.id):
            if added:
                await conf.total_messages.set(int(await conf.total_messages()) + added)
                async with conf.user_messages() as user_messages:
                    for user_id, delta in user_deltas.items():
                        user_messages[user_id] = int(user_messages.get(user_id, 0)) + delta
                async with conf.channel_messages() as channel_messages:
                    channel_messages[str(channel.id)] = int(channel_messages.get(str(channel.id), 0)) + added
                async with conf.message_authors() as authors:
                    authors.update(new_authors)
            if reaction_deltas:
                async with conf.received_reactions() as received:
                    for author_id, emoji, delta in reaction_deltas:
                        apply_reaction_delta(received, author_id, emoji, delta)
                async with conf.message_reactions() as stored:
                    for message_id in touched:
                        if message_id in new_snapshots:
                            stored[message_id] = new_snapshots[message_id]
                        else:
                            stored.pop(message_id, None)
            if names:
                async with conf.user_display_names() as stored_names:
                    stored_names.update(names)
        return scanned, added, reaction_updates

    @staticmethod
    async def _message_reaction_counts(message: discord.Message) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for reaction in message.reactions:
            emoji = emoji_key(reaction.emoji)
            try:
                async for user in reaction.users(limit=None):
                    if user.id != message.author.id:
                        counts[emoji] += 1
            except discord.HTTPException:
                count = reaction.count - (1 if reaction.me else 0)
                if count > 0:
                    counts[emoji] += count
        return dict(counts)

    async def _purge_channel_stats(self, guild: discord.Guild, target) -> dict[str, int]:
        """Forget known messages (and their reactions) from a channel, thread or category."""
        conf = self.stats_config.guild(guild)
        authors = await conf.message_authors()
        snapshots = await conf.message_reactions()
        removed: set[str] = set()
        reactions_removed = 0
        for message_id, entry in authors.items():
            channel_id = entry_channel_id(entry)
            if channel_id is None or not self._belongs_to(guild, int(channel_id), target):
                continue
            removed.add(str(message_id))
            reactions_removed += sum(max(0, int(count)) for count in snapshots.get(str(message_id), {}).values())
        if not removed:
            return {"messages_removed": 0, "reactions_removed": 0}
        async with self._lock_for(guild.id):
            async with conf.message_authors() as stored:
                for message_id in removed:
                    stored.pop(message_id, None)
            async with conf.message_reactions() as stored:
                for message_id in removed:
                    stored.pop(message_id, None)
        await self._recount_stats(guild)
        return {"messages_removed": len(removed), "reactions_removed": reactions_removed}

    @staticmethod
    def _belongs_to(guild: discord.Guild, channel_id: int, target) -> bool:
        if channel_id == target.id:
            return True
        channel = guild.get_channel_or_thread(channel_id) or guild.get_channel(channel_id)
        if channel is None:
            return False
        if isinstance(target, discord.CategoryChannel):
            if getattr(channel, "category_id", None) == target.id:
                return True
            return getattr(getattr(channel, "parent", None), "category_id", None) == target.id
        if isinstance(target, discord.TextChannel):
            return getattr(channel, "parent_id", None) == target.id
        return False

    async def _recount_stats(self, guild: discord.Guild) -> dict[str, int]:
        """Rebuild message and reaction totals from the known message records."""
        conf = self.stats_config.guild(guild)
        async with self._lock_for(guild.id):
            authors = await conf.message_authors()
            snapshots = await conf.message_reactions()
            user_messages: Counter[str] = Counter()
            channel_messages: Counter[str] = Counter()
            received: dict[str, dict[str, int]] = {}
            valid: set[str] = set()
            for message_id, entry in authors.items():
                author_id = entry_author_id(entry)
                if author_id is None:
                    continue
                valid.add(str(message_id))
                user_messages[str(author_id)] += 1
                channel_id = entry_channel_id(entry)
                if channel_id is not None:
                    channel_messages[str(channel_id)] += 1
                for emoji, count in snapshots.get(str(message_id), {}).items():
                    if int(count) > 0:
                        user_reactions = received.setdefault(str(author_id), {})
                        user_reactions[emoji] = user_reactions.get(emoji, 0) + int(count)
            cleaned = {}
            orphaned = 0
            for message_id, counts in snapshots.items():
                if str(message_id) not in valid:
                    orphaned += 1
                    continue
                kept = {emoji: int(count) for emoji, count in counts.items() if int(count) > 0}
                if kept:
                    cleaned[str(message_id)] = kept
            await conf.total_messages.set(len(valid))
            await conf.user_messages.set(dict(user_messages))
            await conf.channel_messages.set(dict(channel_messages))
            await conf.received_reactions.set(received)
            await conf.message_reactions.set(cleaned)
        return {"messages": len(valid), "users": len(user_messages), "channels": len(channel_messages), "orphaned": orphaned}

    async def _message_sources(self, guild: discord.Guild, user_id: int) -> Counter[str]:
        counts: Counter[str] = Counter()
        for entry in (await self.stats_config.guild(guild).message_authors()).values():
            if str(entry_author_id(entry)) == str(user_id) and entry_channel_id(entry) is not None:
                counts[str(entry_channel_id(entry))] += 1
        return counts

    async def _reset_stats(self, guild: discord.Guild):
        conf = self.stats_config.guild(guild)
        async with self._lock_for(guild.id):
            await conf.total_messages.set(0)
            for key in ("user_messages", "channel_messages", "message_authors", "received_reactions",
                        "message_reactions", "voice_seconds", "active_voice_sessions"):
                await conf.get_attr(key).set({})
        if await self._enabled(guild):
            await self._seed_voice_sessions(guild)

    # Top-message roles

    async def _maybe_sync_top_roles(self, guild: discord.Guild):
        if not any((await self.stats_config.guild(guild).top_message_roles()).values()):
            return
        now = time.monotonic()
        if now - self._last_role_sync.get(guild.id, 0) < ROLE_SYNC_INTERVAL:
            return
        self._last_role_sync[guild.id] = now
        await self._sync_top_roles(guild)

    async def _sync_top_roles(self, guild: discord.Guild) -> dict[str, Any]:
        result: dict[str, Any] = {"configured": 0, "added": 0, "removed": 0, "skipped": []}
        configured = await self.stats_config.guild(guild).top_message_roles()
        roles: dict[str, discord.Role] = {}
        for tier in TOP_ROLE_TIERS:
            role = guild.get_role(int(configured[tier])) if configured.get(tier) else None
            if role is None:
                continue
            result["configured"] += 1
            if can_manage_role(guild, role):
                roles[tier] = role
            else:
                result["skipped"].append(f"{tier}: cannot manage {role.name}")
        if not roles:
            return result

        user_messages = await self.stats_config.guild(guild).user_messages()
        top = [int(uid) for uid, count in sorted(user_messages.items(), key=lambda item: int(item[1]), reverse=True) if int(count) > 0]
        desired = {role.id: top[TOP_ROLE_TIERS.index(tier)] for tier, role in roles.items() if TOP_ROLE_TIERS.index(tier) < len(top)}

        for tier, role in roles.items():
            for member in list(role.members):
                if member.id == desired.get(role.id):
                    continue
                try:
                    await member.remove_roles(role, reason="Activity top-message role sync")
                    result["removed"] += 1
                except discord.HTTPException:
                    result["skipped"].append(f"{tier}: could not remove {role.name} from {member.display_name}")
        for tier, role in roles.items():
            user_id = desired.get(role.id)
            if user_id is None:
                continue
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    result["skipped"].append(f"{tier}: could not find member {user_id}")
                    continue
            if role in member.roles:
                continue
            try:
                await member.add_roles(role, reason="Activity top-message role sync")
                result["added"] += 1
            except discord.HTTPException:
                result["skipped"].append(f"{tier}: could not add {role.name} to {member.display_name}")
        return result

    async def _strip_top_role(self, guild: discord.Guild, role_id: Any):
        role = guild.get_role(int(role_id)) if role_id else None
        if role is None or not can_manage_role(guild, role):
            return
        for member in list(role.members):
            try:
                await member.remove_roles(role, reason="Activity top-message role cleared")
            except discord.HTTPException:
                pass

    @staticmethod
    def _format_role_sync(result: dict[str, Any]) -> str:
        text = f"Synced top-message roles: added {result['added']}, removed {result['removed']}."
        skipped = result.get("skipped") or []
        if skipped:
            text += f" Skipped {len(skipped)}: " + "; ".join(skipped[:3]) + ("; ..." if len(skipped) > 3 else "") + "."
        return text
