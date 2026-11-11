"""The Media cog: Radio, MusicBoard and EmbedFixer behind one command group and one settings panel."""

from __future__ import annotations

import discord
from redbot.core import app_commands, commands

from .embedfixer import EmbedFixerMixin
from .fixers import TRANSLATE_LANGUAGES, normalize_language
from .layouts import config_field, reply
from .musicboard import MusicBoardMixin
from .radio import RadioMixin


def _validate_language(guild, value):
    if value is None or normalize_language(value) is not None:
        return None
    return "Unknown language. Use a code like en, pt or ja, or a name like Portuguese."


_field = config_field


class Media(RadioMixin, MusicBoardMixin, EmbedFixerMixin, commands.Cog):
    """Radio streaming, music link nominations and social embed fixing."""

    def __init__(self, bot):
        self.bot = bot
        self._radio_init()
        self._musicboard_init()
        self._embedfixer_init()

    async def cog_load(self):
        self._radio_start()

    def cog_unload(self):
        self._radio_unload()
        self._musicboard_unload()

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        await self._embedfixer_delete_user(user_id)

    # ----- settings -----

    def control_panel_schema(self) -> dict:
        radio, board, fixer = self.radio_config, self.musicboard_config, self.embedfixer_config
        radio_sync = self._radio_settings_changed

        async def status_channel_changed(guild):
            await radio.guild(guild).status_message_id.clear()

        async def get_timeout(guild):
            return await board.guild(guild).react_timeout_seconds() // 60

        async def set_timeout(guild, minutes):
            await board.guild(guild).react_timeout_seconds.set(int(minutes) * 60)

        async def get_translate(guild):
            code = await fixer.guild(guild).translate_to()
            return f"{TRANSLATE_LANGUAGES.get(code, code)} ({code})" if code else None

        async def set_translate(guild, value):
            await fixer.guild(guild).translate_to.set(normalize_language(value))

        board_more, fixer_more = self._musicboard_settings(), self._embedfixer_settings()
        settings = [
            {"key": "radio.enabled", "label": "Radio", "type": "bool", "section": "Radio",
             "help": "Turn the whole radio feature on or off.", **_field(radio, "enabled", set_hook=radio_sync)},
            {"key": "radio.stream_url", "label": "Stream URL", "type": "text", "section": "Radio",
             "help": "The HTTP or HTTPS address of the stream.", "min": 1, "max": 500,
             "validate": self._radio_url_error, **_field(radio, "stream_url", set_hook=radio_sync)},
            {"key": "radio.voice_channel_id", "label": "Auto channel", "type": "channel", "section": "Radio",
             "channel_types": ["voice"], "clearable": False,
             "help": "Voice channel used for automatic join and leave.",
             **_field(radio, "voice_channel_id", set_hook=radio_sync)},
            {"key": "radio.auto_play", "label": "Auto play", "type": "bool", "section": "Radio",
             "help": "Join when people are in the auto channel and leave when it empties.",
             **_field(radio, "auto_play", set_hook=radio_sync)},
            {"key": "radio.reconnect", "label": "Reconnect", "type": "bool", "section": "Radio",
             "help": "Restart the stream if it drops.", **_field(radio, "reconnect")},
            {"key": "radio.voice_status_enabled", "label": "Voice channel status", "type": "bool",
             "section": "Radio", "help": "Show the current show and track as the voice channel status.",
             **_field(radio, "voice_status_enabled")},
            {"key": "radio.status_channel_id", "label": "Status channel", "type": "channel", "section": "Radio",
             "channel_types": ["text"], "help": "Text channel with a now-playing message kept up to date.",
             **_field(radio, "status_channel_id", set_hook=status_channel_changed)},
            {"key": "radio.status_api_url", "label": "Status API URL", "type": "text", "section": "Radio",
             "max": 500, "clearable": True, "empty": "From stream URL",
             "help": "Icecast status-json.xsl address. Empty uses the stream's server.",
             "validate": self._radio_optional_url_error, **_field(radio, "status_api_url")},
            *self._radio_settings(),
            {"key": "musicboard.enabled", "label": "MusicBoard", "type": "bool", "section": "MusicBoard",
             "help": "Turn music link nominations on or off.", **_field(board, "enabled")},
            {"key": "musicboard.music_channel_id", "label": "Music channel", "type": "channel",
             "section": "MusicBoard", "channel_types": ["text"], "clearable": False,
             "help": "Where nominated links are posted.", **_field(board, "music_channel_id")},
            {"key": "musicboard.react_timeout", "label": "Reaction timeout", "type": "int",
             "section": "MusicBoard", "min": 0, "max": 10080, "unit": "minutes",
             "help": "How long prompt reactions stay up. 0 keeps them forever.",
             "get": get_timeout, "set": set_timeout},
            *board_more["MusicBoard"],
            *board_more["MusicBoard links"],
            {"key": "embedfixer.enabled", "label": "Automatic fixing", "type": "bool", "section": "EmbedFixer",
             "help": "Repost supported social links with working embeds.",
             **_field(fixer, "enabled")},
            {"key": "embedfixer.youtube", "label": "Fix YouTube links", "type": "bool", "section": "EmbedFixer",
             "help": "Also repost YouTube links through Koutube. Off by default.",
             **_field(fixer, "youtube")},
            {"key": "embedfixer.translate_to", "label": "Translate Twitter/X posts", "type": "text",
             "section": "EmbedFixer", "max": 30, "empty": "Off", "clearable": True,
             "help": "Language code or name, e.g. en, pt or Portuguese. FxEmbed links only. Clear to turn off.",
             "validate": _validate_language, "get": get_translate, "set": set_translate},
            *fixer_more["EmbedFixer"],
            *fixer_more["EmbedFixer sites"],
            *fixer_more["EmbedFixer services"],
        ]
        return {
            "title": "Media",
            "description": "Radio streaming, music link nominations and social embed fixing.",
            "settings": settings,
        }

    # ----- commands -----

    @commands.hybrid_group(name="media", invoke_without_command=True, fallback="status")
    @commands.guild_only()
    async def media(self, ctx: commands.Context):
        """Show Radio, MusicBoard and EmbedFixer status."""
        blocks = [
            "## Media",
            await self._radio_status(ctx.guild),
            await self._musicboard_status(ctx.guild),
            await self._embedfixer_status(ctx.guild, ctx.author),
            "-# Staff change settings in `/control`.",
        ]
        await reply(ctx, *blocks, ephemeral=True)

    @media.command(name="play")
    @commands.guild_only()
    @commands.bot_has_permissions(connect=True, speak=True)
    @app_commands.describe(channel="Voice channel to stream into. Defaults to yours, then the auto channel.")
    async def media_play(self, ctx: commands.Context, channel: discord.VoiceChannel | None = None):
        """Join voice and start the radio stream."""
        await ctx.defer()
        await reply(ctx, await self._radio_play(ctx.guild, ctx.author, channel))

    @media.command(name="stop")
    @commands.guild_only()
    async def media_stop(self, ctx: commands.Context):
        """Stop the radio and leave voice."""
        await ctx.defer()
        await reply(ctx, await self._radio_stop(ctx.guild))

    @media.command(name="fixlinks")
    @commands.guild_only()
    @app_commands.describe(enabled="Whether EmbedFixer reposts your links in this server.")
    async def media_fixlinks(self, ctx: commands.Context, enabled: bool):
        """Choose whether EmbedFixer fixes your links here."""
        await self.embedfixer_config.member(ctx.author).opted_out.set(not enabled)
        text = (
            "EmbedFixer will fix your supported links again in this server."
            if enabled
            else "EmbedFixer will leave your messages alone in this server."
        )
        await reply(ctx, text, ephemeral=True)

    @media.command(name="clearboard")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def media_clearboard(self, ctx: commands.Context):
        """Let already-posted MusicBoard links be nominated again."""
        await self.musicboard_config.guild(ctx.guild).posted_message_ids.set([])
        await reply(
            ctx,
            "## MusicBoard\nHistory cleared. Previously posted links can be nominated again.",
            accent=discord.Color.orange(),
            ephemeral=True,
        )
