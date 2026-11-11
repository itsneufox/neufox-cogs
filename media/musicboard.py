"""MusicBoard: members nominate music links to a channel with a reaction."""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache

import discord
from redbot.core import Config, commands

from .layouts import channel_listed, config_field

MUSICBOARD_ID = 728463910
# key: (label, URL pattern after the scheme)
MUSIC_SOURCES: dict[str, tuple[str, str]] = {
    "youtube": ("YouTube", r"(?:www\.)?(?:youtube\.com/watch\?(?:[^&\s]*&)*v=|youtu\.be/)[\w-]+"),
    "youtube_music": ("YouTube Music", r"music\.youtube\.com/watch\?(?:[^&\s]*&)*v=[\w-]+"),
    "spotify": ("Spotify", r"open\.spotify\.com/(?:track|album|playlist|episode)/[\w]+"),
    "soundcloud": ("SoundCloud", r"(?:www\.)?soundcloud\.com/[\w-]+/[\w-]+"),
    "apple_music": ("Apple Music", r"music\.apple\.com/\S+"),
    "tidal": ("Tidal", r"(?:www\.)?tidal\.com/browse/(?:track|album|playlist)/\d+"),
    "deezer": ("Deezer", r"(?:www\.)?deezer\.com/(?:\w+/)?(?:track|album|playlist)/\d+"),
    "bandcamp": ("Bandcamp", r"[\w-]+\.bandcamp\.com/track/[\w-]+"),
    "amazon_music": ("Amazon Music", r"music\.amazon\.com/\S+"),
}
MUSIC_PATTERN = re.compile(r"https?://(?:" + "|".join(pattern for _, pattern in MUSIC_SOURCES.values()) + ")")
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w{2,32}:(\d{15,21})>")

LINK_EMOJI = "\U0001f517"  # link
CHECK_EMOJI = "✅"  # check mark
BROKEN_EMOJI = "⛓️‍\U0001f4a5"  # broken chain
BLOCKED_EMOJI = "❌"
PROMPT_EMOJIS = (LINK_EMOJI, BROKEN_EMOJI)
DEFAULT_REACT_TIMEOUT_SECONDS = 10 * 60
BLOCKED_REACT_TIMEOUT_SECONDS = 60
DEFAULT_NOMINATION_FORMAT = "\U0001f3b5 Nominated by **{user}** • [Jump to message]({jump})\n{url}"
MUSICBOARD_GUILD_DEFAULTS = {
    "enabled": True,
    "music_channel_id": None,
    "posted_message_ids": [],
    "react_timeout_seconds": DEFAULT_REACT_TIMEOUT_SECONDS,
    "nominate_emoji": LINK_EMOJI,
    "block_emoji": BROKEN_EMOJI,
    "posted_emoji": CHECK_EMOJI,
    "blocked_emoji": BLOCKED_EMOJI,
    "block_enabled": True,
    "source_channels": [],
    "disabled_sources": [],
    "votes_needed": 1,
    "blocked_timeout_seconds": BLOCKED_REACT_TIMEOUT_SECONDS,
    "nomination_format": None,
}


def _norm(emoji: str) -> str:
    return emoji.replace("️", "")


@lru_cache(maxsize=64)
def _pattern_for(disabled: frozenset[str]) -> re.Pattern | None:
    if not disabled:
        return MUSIC_PATTERN
    parts = [pattern for key, (_, pattern) in MUSIC_SOURCES.items() if key not in disabled]
    return re.compile(r"https?://(?:" + "|".join(parts) + ")") if parts else None


def extract_music_url(content: str, disabled=()) -> str | None:
    """The first music link in ``content`` from a source not listed in ``disabled``."""
    pattern = _pattern_for(frozenset(disabled))
    match = pattern.search(content or "") if pattern is not None else None
    return match.group(0) if match else None


def nomination_text(template: str | None, *, user: str, jump: str, url: str) -> str:
    template = template or DEFAULT_NOMINATION_FORMAT
    try:
        text = template.format(user=user, jump=jump, url=url)
    except (KeyError, IndexError, ValueError):
        text = DEFAULT_NOMINATION_FORMAT.format(user=user, jump=jump, url=url)
    return text[:2000]


def validate_nomination_format(guild, value) -> str | None:
    if not value:
        return None
    try:
        value.format(user="u", jump="j", url="l")
    except (KeyError, IndexError, ValueError):
        return "Only {user}, {jump} and {url} can go in braces. Write {{ or }} for a literal brace."
    if "{url}" not in value:
        return "The format needs {url} so the music channel gets the link."
    return None


def validate_emoji(guild, value) -> str | None:
    value = (value or "").strip()
    custom = CUSTOM_EMOJI_RE.fullmatch(value)
    if custom:
        ids = {emoji.id for emoji in getattr(guild, "emojis", ())}
        return None if int(custom.group(1)) in ids else "That custom emoji is not from this server."
    if not value or len(value) > 16 or any(char.isspace() or char.isascii() for char in value):
        return "Use one emoji, like 🔗, or a custom emoji from this server, like <:name:123>."
    return None


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
        self.musicboard_config.register_guild(**MUSICBOARD_GUILD_DEFAULTS)
        self._musicboard_processing: set[int] = set()
        self._musicboard_cleanup: dict[tuple[int, int], asyncio.Task] = {}

    def _musicboard_unload(self):
        for task in self._musicboard_cleanup.values():
            task.cancel()
        self._musicboard_cleanup.clear()

    async def _musicboard_data(self, guild: discord.Guild) -> dict:
        return {**MUSICBOARD_GUILD_DEFAULTS, **await self.musicboard_config.guild(guild).all()}

    @staticmethod
    def _musicboard_prompts(data: dict) -> tuple[str, ...]:
        if data["block_enabled"]:
            return (data["nominate_emoji"], data["block_emoji"])
        return (data["nominate_emoji"],)

    def _musicboard_settings(self) -> dict[str, list[dict]]:
        """Panel settings added on top of the basic ones, keyed by section."""
        config = self.musicboard_config

        def field(name, **extra):
            return {**config_field(config, name), **extra}

        def emoji(key, label, help_text):
            return field(key, key=f"musicboard.{key}", label=label, type="text", section="MusicBoard",
                         min=1, max=64, help=help_text, validate=validate_emoji)

        def source(key, label):
            async def get(guild):
                return key not in await config.guild(guild).disabled_sources()

            async def set_(guild, value):
                async with config.guild(guild).disabled_sources() as disabled:
                    if value and key in disabled:
                        disabled.remove(key)
                    elif not value and key not in disabled:
                        disabled.append(key)

            return {"key": f"musicboard.source.{key}", "label": label, "type": "bool",
                    "section": "MusicBoard links", "help": f"Offer {label} links for nomination.",
                    "get": get, "set": set_}

        general = [
            field("source_channels", key="musicboard.source_channels", label="Watched channels", type="channels",
                  section="MusicBoard", channel_types=["text", "news", "voice", "forum", "stage", "category"],
                  empty="Every channel", help="Only prompt on links posted here. Empty watches every channel."),
            field("votes_needed", key="musicboard.votes_needed", label="Votes needed", type="int",
                  section="MusicBoard", min=1, max=50, unit="votes",
                  help="Nominate reactions needed before a link is posted."),
            field("block_enabled", key="musicboard.block_enabled", label="Block reaction", type="bool",
                  section="MusicBoard", help="Also offer the block reaction that stops a link being nominated."),
            emoji("nominate_emoji", "Nominate emoji", "Reaction members press to nominate a link."),
            emoji("block_emoji", "Block emoji", "Reaction members press to block a link."),
            emoji("posted_emoji", "Posted emoji", "Mark left on a message after its link is posted."),
            emoji("blocked_emoji", "Blocked emoji", "Mark left on a message after its link is blocked."),
            field("blocked_timeout_seconds", key="musicboard.blocked_timeout", label="Blocked mark timeout",
                  type="int", section="MusicBoard", min=0, max=86400, unit="seconds",
                  help="How long the blocked mark stays. 0 keeps it forever."),
            field("nomination_format", key="musicboard.nomination_format", label="Nomination message",
                  type="text", section="MusicBoard", max=500, clearable=True, empty="Default",
                  help="Placeholders: {user}, {jump} (message link), {url}. Clear for the default.",
                  validate=validate_nomination_format),
        ]
        return {
            "MusicBoard": general,
            "MusicBoard links": [source(key, label) for key, (label, _) in MUSIC_SOURCES.items()],
        }

    async def _musicboard_status(self, guild: discord.Guild) -> str:
        data = await self._musicboard_data(guild)
        channel = guild.get_channel(data["music_channel_id"]) if data["music_channel_id"] else None
        return "### MusicBoard\n" + "\n".join(
            [
                f"**Enabled:** {'Yes' if data['enabled'] else 'No'}",
                f"**Music channel:** {channel.mention if channel else 'Not set'}",
                f"**Tracks posted:** {len(data['posted_message_ids'])}",
                f"**Reaction timeout:** {format_timeout(data['react_timeout_seconds'])}",
                f"-# React with {data['nominate_emoji']} on a music link to nominate it.",
            ]
        )

    async def _musicboard_channel(self, guild: discord.Guild, data: dict | None = None) -> discord.TextChannel | None:
        """The music channel, or None when the feature is off or not set up."""
        data = data or await self._musicboard_data(guild)
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
        data = await self._musicboard_data(message.guild)
        if not extract_music_url(message.content, data["disabled_sources"]):
            return
        if data["source_channels"] and not channel_listed(message.channel, data["source_channels"]):
            return
        if await self._musicboard_channel(message.guild, data) is None:
            return
        prompts = self._musicboard_prompts(data)
        existing = {_norm(str(reaction.emoji)) for reaction in message.reactions if reaction.me}
        for emoji in prompts:
            if _norm(emoji) not in existing:
                try:
                    await message.add_reaction(emoji)
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await self._musicboard_schedule_cleanup(message, prompts, data["react_timeout_seconds"])

    @commands.Cog.listener("on_raw_reaction_add")
    async def _musicboard_on_reaction(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        if payload.message_id in self._musicboard_processing:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        data = await self._musicboard_data(guild)
        emoji = _norm(str(payload.emoji))
        nominate = _norm(data["nominate_emoji"])
        block = _norm(data["block_emoji"]) if data["block_enabled"] else None
        if emoji not in (nominate, block):
            return
        music_channel = await self._musicboard_channel(guild, data)
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
            prompt = next((r for r in message.reactions if _norm(str(r.emoji)) == nominate and r.me), None)
            if prompt is None:
                return
            if emoji == block:
                await self._musicboard_block(message, data)
            elif prompt.count - 1 >= max(1, int(data["votes_needed"])):
                await self._musicboard_nominate(guild, music_channel, message, payload.user_id, data)
        finally:
            self._musicboard_processing.discard(payload.message_id)

    async def _musicboard_block(self, message: discord.Message, data: dict):
        self._musicboard_cancel_cleanup(message.channel.id, message.id)
        await self._musicboard_clear_prompts(message, self._musicboard_prompts(data))
        try:
            await message.add_reaction(data["blocked_emoji"])
        except (discord.Forbidden, discord.HTTPException):
            return
        await self._musicboard_schedule_cleanup(message, (data["blocked_emoji"],), data["blocked_timeout_seconds"])

    async def _musicboard_nominate(self, guild, music_channel, message: discord.Message, user_id: int, data: dict):
        if message.id in await self.musicboard_config.guild(guild).posted_message_ids():
            return
        url = extract_music_url(message.content, data["disabled_sources"])
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
                content=nomination_text(
                    data["nomination_format"], user=nominator.display_name, jump=message.jump_url, url=url
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return
        async with self.musicboard_config.guild(guild).posted_message_ids() as posted:
            posted.append(message.id)
        self._musicboard_cancel_cleanup(message.channel.id, message.id)
        await self._musicboard_clear_prompts(message, self._musicboard_prompts(data))
        try:
            await message.add_reaction(data["posted_emoji"])
        except (discord.Forbidden, discord.HTTPException):
            pass

    @staticmethod
    async def _musicboard_clear_prompts(message: discord.Message, emojis: tuple[str, ...] = PROMPT_EMOJIS):
        for emoji in emojis:
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
