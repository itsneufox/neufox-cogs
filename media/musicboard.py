"""MusicBoard: members nominate music links to a channel with a reaction."""

from __future__ import annotations

import asyncio
import re

import discord
from redbot.core import Config, commands

MUSICBOARD_ID = 728463910
MUSIC_PATTERN = re.compile(
    r"https?://"
    r"(?:"
    r"(?:www\.)?(?:youtube\.com/watch\?(?:[^&\s]*&)*v=|youtu\.be/)[\w-]+"  # YouTube
    r"|music\.youtube\.com/watch\?(?:[^&\s]*&)*v=[\w-]+"  # YouTube Music
    r"|open\.spotify\.com/(?:track|album|playlist|episode)/[\w]+"  # Spotify
    r"|(?:www\.)?soundcloud\.com/[\w-]+/[\w-]+"  # SoundCloud
    r"|music\.apple\.com/\S+"  # Apple Music
    r"|(?:www\.)?tidal\.com/browse/(?:track|album|playlist)/\d+"  # Tidal
    r"|(?:www\.)?deezer\.com/(?:\w+/)?(?:track|album|playlist)/\d+"  # Deezer
    r"|[\w-]+\.bandcamp\.com/track/[\w-]+"  # Bandcamp
    r"|music\.amazon\.com/\S+"  # Amazon Music
    r")"
)

LINK_EMOJI = "\U0001f517"  # link
CHECK_EMOJI = "✅"  # check mark
BROKEN_EMOJI = "⛓️‍\U0001f4a5"  # broken chain
BLOCKED_EMOJI = "❌"
PROMPT_EMOJIS = (LINK_EMOJI, BROKEN_EMOJI)
DEFAULT_REACT_TIMEOUT_SECONDS = 10 * 60
BLOCKED_REACT_TIMEOUT_SECONDS = 60


def _norm(emoji: str) -> str:
    return emoji.replace("️", "")


def extract_music_url(content: str) -> str | None:
    match = MUSIC_PATTERN.search(content or "")
    return match.group(0) if match else None


def format_timeout(seconds: int) -> str:
    if not seconds:
        return "never"
    minutes = seconds // 60
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


class MusicBoardMixin:
    """Config, listeners and helpers for MusicBoard. Composed into the Media cog."""

    def _musicboard_init(self):
        # Same identifier and cog name as the old standalone cog, so its data is reused.
        self.musicboard_config = Config.get_conf(
            None, identifier=MUSICBOARD_ID, cog_name="MusicBoard", force_registration=True
        )
        self.musicboard_config.register_guild(
            enabled=True,
            music_channel_id=None,
            posted_message_ids=[],
            react_timeout_seconds=DEFAULT_REACT_TIMEOUT_SECONDS,
        )
        self._musicboard_processing: set[int] = set()
        self._musicboard_cleanup: dict[tuple[int, int], asyncio.Task] = {}

    def _musicboard_unload(self):
        for task in self._musicboard_cleanup.values():
            task.cancel()
        self._musicboard_cleanup.clear()

    async def _musicboard_status(self, guild: discord.Guild) -> str:
        data = await self.musicboard_config.guild(guild).all()
        channel = guild.get_channel(data["music_channel_id"]) if data["music_channel_id"] else None
        return "### MusicBoard\n" + "\n".join(
            [
                f"**Enabled:** {'Yes' if data['enabled'] else 'No'}",
                f"**Music channel:** {channel.mention if channel else 'Not set'}",
                f"**Tracks posted:** {len(data['posted_message_ids'])}",
                f"**Reaction timeout:** {format_timeout(data['react_timeout_seconds'])}",
                f"-# React with {LINK_EMOJI} on a music link to nominate it.",
            ]
        )

    async def _musicboard_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """The music channel, or None when the feature is off or not set up."""
        data = await self.musicboard_config.guild(guild).all()
        if not data["enabled"] or not data["music_channel_id"]:
            return None
        if await self.bot.cog_disabled_in_guild(self, guild):
            return None
        channel = guild.get_channel(int(data["music_channel_id"]))
        return channel if isinstance(channel, discord.TextChannel) else None

    @commands.Cog.listener("on_message")
    async def _musicboard_on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not extract_music_url(message.content):
            return
        if await self._musicboard_channel(message.guild) is None:
            return
        existing = {_norm(str(reaction.emoji)) for reaction in message.reactions if reaction.me}
        for emoji in PROMPT_EMOJIS:
            if _norm(emoji) not in existing:
                try:
                    await message.add_reaction(emoji)
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await self._musicboard_schedule_cleanup(message)

    @commands.Cog.listener("on_raw_reaction_add")
    async def _musicboard_on_reaction(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        emoji = _norm(str(payload.emoji))
        if emoji not in (_norm(LINK_EMOJI), _norm(BROKEN_EMOJI)):
            return
        if payload.message_id in self._musicboard_processing:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        music_channel = await self._musicboard_channel(guild)
        if music_channel is None:
            return
        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return

        self._musicboard_processing.add(payload.message_id)
        try:
            try:
                message = await channel.fetch_message(payload.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
            # Only act on messages the bot prompted and that are still open.
            if not any(_norm(str(r.emoji)) == _norm(LINK_EMOJI) and r.me for r in message.reactions):
                return
            if emoji == _norm(BROKEN_EMOJI):
                await self._musicboard_block(message)
            else:
                await self._musicboard_nominate(guild, music_channel, message, payload.user_id)
        finally:
            self._musicboard_processing.discard(payload.message_id)

    async def _musicboard_block(self, message: discord.Message):
        self._musicboard_cancel_cleanup(message.channel.id, message.id)
        await self._musicboard_clear_prompts(message)
        try:
            await message.add_reaction(BLOCKED_EMOJI)
        except (discord.Forbidden, discord.HTTPException):
            return
        await self._musicboard_schedule_cleanup(message, (BLOCKED_EMOJI,), BLOCKED_REACT_TIMEOUT_SECONDS)

    async def _musicboard_nominate(self, guild, music_channel, message: discord.Message, user_id: int):
        if message.id in await self.musicboard_config.guild(guild).posted_message_ids():
            return
        url = extract_music_url(message.content)
        if not url:
            return
        nominator = guild.get_member(user_id)
        if nominator is None:
            try:
                nominator = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException):
                return
        try:
            await music_channel.send(
                content=f"\U0001f3b5 Nominated by **{nominator.display_name}** • [Jump to message]({message.jump_url})\n{url}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return
        async with self.musicboard_config.guild(guild).posted_message_ids() as posted:
            posted.append(message.id)
        self._musicboard_cancel_cleanup(message.channel.id, message.id)
        await self._musicboard_clear_prompts(message)
        try:
            await message.add_reaction(CHECK_EMOJI)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @staticmethod
    async def _musicboard_clear_prompts(message: discord.Message):
        for emoji in PROMPT_EMOJIS:
            try:
                await message.clear_reaction(emoji)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _musicboard_schedule_cleanup(
        self, message: discord.Message, emojis: tuple[str, ...] = PROMPT_EMOJIS, timeout: int | None = None
    ):
        if timeout is None:
            timeout = await self.musicboard_config.guild(message.guild).react_timeout_seconds()
        if not timeout:
            return
        key = (message.channel.id, message.id)
        self._musicboard_cancel_cleanup(*key)
        task = asyncio.create_task(self._musicboard_cleanup_after(message.channel.id, message.id, timeout, emojis))
        self._musicboard_cleanup[key] = task
        task.add_done_callback(lambda done: self._musicboard_cleanup.get(key) is done and self._musicboard_cleanup.pop(key))

    async def _musicboard_cleanup_after(self, channel_id: int, message_id: int, delay: int, emojis: tuple[str, ...]):
        try:
            await asyncio.sleep(delay)
            channel = self.bot.get_channel(channel_id)
            if channel is None or not hasattr(channel, "fetch_message") or self.bot.user is None:
                return
            message = await channel.fetch_message(message_id)
            for emoji in emojis:
                try:
                    await message.remove_reaction(emoji, self.bot.user)
                except (discord.Forbidden, discord.HTTPException):
                    pass
        except (asyncio.CancelledError, discord.Forbidden, discord.HTTPException):
            pass

    def _musicboard_cancel_cleanup(self, channel_id: int, message_id: int):
        task = self._musicboard_cleanup.pop((channel_id, message_id), None)
        if task is not None:
            task.cancel()
