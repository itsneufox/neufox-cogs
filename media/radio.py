"""Radio: stream a configured URL into voice, with auto join/leave and now-playing status."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
import discord
from discord.http import Route
from redbot.core import Config, commands

log = logging.getLogger("red.neufox.media.radio")

RADIO_ID = 938475610
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
STATUS_POLL_SECONDS = 30
AUTO_SYNC_DELAY_SECONDS = 3
AUTO_LEAVE_GRACE_SECONDS = 10
RECONNECT_DELAY_SECONDS = 5
RADIO_SHOWS = (
    ("Early Haul", 6, 12),
    ("Midday Cruise", 12, 16),
    ("Heavy Miles", 16, 22),
    ("Night Drive", 22, 6),
)


def valid_http_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def current_show_name(hour: int | None = None) -> str:
    hour = datetime.now(timezone.utc).hour if hour is None else hour
    for name, start, end in RADIO_SHOWS:
        if start < end and start <= hour < end:
            return name
        if start > end and (hour >= start or hour < end):
            return name
    return "LongWayFM"


def status_api_url(data: dict) -> str | None:
    if data.get("status_api_url"):
        return data["status_api_url"]
    stream_url = data.get("stream_url")
    if not valid_http_url(stream_url):
        return None
    parsed = urlparse(stream_url)
    return f"{parsed.scheme}://{parsed.netloc}/status-json.xsl"


def select_source(sources: list, stream_url: str | None) -> dict | None:
    if not stream_url:
        return None
    stream_path = urlparse(stream_url).path or "/stream"
    for source in sources:
        if isinstance(source, dict):
            listenurl = str(source.get("listenurl") or "")
            if stream_url in listenurl or stream_path in listenurl:
                return source
    return None


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _has_human(channel: discord.VoiceChannel) -> bool:
    return any(not member.bot for member in channel.members)


class RadioMixin:
    """Config, voice listener and playback for Radio. Composed into the Media cog."""

    def _radio_init(self):
        # Same identifier and cog name as the old standalone cog, so its data is reused.
        self.radio_config = Config.get_conf(None, identifier=RADIO_ID, cog_name="Radio", force_registration=True)
        self.radio_config.register_guild(
            enabled=True,
            stream_url=None,
            voice_channel_id=None,
            reconnect=True,
            status_channel_id=None,
            status_message_id=None,
            status_api_url=None,
            voice_status_enabled=True,
            auto_play=True,
        )
        self._radio_stopping: set[int] = set()  # guilds where the next stream end is intentional
        self._radio_sync_tasks: dict[int, asyncio.Task] = {}
        self._radio_reconnect_tasks: dict[int, asyncio.Task] = {}
        self._radio_status_task: asyncio.Task | None = None

    def _radio_start(self):
        self._radio_status_task = asyncio.create_task(self._radio_status_loop())

    def _radio_unload(self):
        for task in [*self._radio_sync_tasks.values(), *self._radio_reconnect_tasks.values()]:
            task.cancel()
        if self._radio_status_task is not None:
            self._radio_status_task.cancel()

    # ----- settings used by the panel -----

    @staticmethod
    def _radio_url_error(guild, url) -> str | None:
        return None if valid_http_url(url) else "That does not look like a valid HTTP/HTTPS URL."

    @staticmethod
    def _radio_optional_url_error(guild, url) -> str | None:
        return None if not url or valid_http_url(url) else "That does not look like a valid HTTP/HTTPS URL."

    async def _radio_settings_changed(self, guild: discord.Guild):
        """Bring voice in line with new settings without blocking the panel interaction."""
        data = await self.radio_config.guild(guild).all()
        voice_client = guild.voice_client
        if not data["enabled"] and voice_client is not None:
            await self._radio_disconnect(guild)
        elif not data["auto_play"] and voice_client and voice_client.channel and voice_client.channel.id == data["voice_channel_id"]:
            await self._radio_disconnect(guild)
        else:
            self._radio_schedule_sync(guild.id)

    async def _radio_status(self, guild: discord.Guild) -> str:
        data = await self.radio_config.guild(guild).all()
        channel = guild.get_channel(data["voice_channel_id"]) if data["voice_channel_id"] else None
        voice_client = guild.voice_client
        return "### Radio\n" + "\n".join(
            [
                f"**Enabled:** {'Yes' if data['enabled'] else 'No'}",
                f"**Stream URL:** {(data['stream_url'] or 'Not set')[:1000]}",
                f"**Auto channel:** {channel.mention if isinstance(channel, discord.VoiceChannel) else 'Not set'}",
                f"**Auto play:** {'On' if data['auto_play'] else 'Off'}",
                f"**Playing:** {'Yes' if voice_client and voice_client.is_playing() else 'No'}",
            ]
        )

    # ----- manual control -----

    async def _radio_play(self, guild: discord.Guild, author, channel: discord.VoiceChannel | None) -> str:
        """Join voice and start the stream. Returns the message to show the user."""
        data = await self.radio_config.guild(guild).all()
        if not data["enabled"]:
            return "Radio is turned off in this server. Turn it on in `/control`."
        if not data["stream_url"]:
            return "No stream URL is set yet. Set one in `/control`."
        if shutil.which("ffmpeg") is None:
            return "`ffmpeg` is not installed or is not available in the bot's PATH."

        if channel is None and isinstance(author, discord.Member) and author.voice:
            if isinstance(author.voice.channel, discord.VoiceChannel):
                channel = author.voice.channel
        if channel is None:
            channel = self._radio_configured_channel(guild, data)
        if channel is None:
            return "Join a voice channel or pick one."

        self._radio_stopping.discard(guild.id)
        self._radio_cancel_reconnect(guild.id)
        try:
            voice_client = await self._radio_connect(guild, channel)
            if voice_client.is_playing() or voice_client.is_paused():
                self._radio_stopping.add(guild.id)
                voice_client.stop()
        except discord.Forbidden:
            return "I do not have permission to join or speak in that voice channel."
        except asyncio.TimeoutError:
            return "Discord voice connection timed out. Try again in a moment."
        except discord.HTTPException:
            return "Discord rejected the voice connection request."
        except RuntimeError as error:
            if "PyNaCl" in str(error):
                return "Voice support is not installed. Install `PyNaCl` in the bot environment."
            return f"Could not start Discord voice: {error}"
        except discord.ClientException as error:
            return f"Could not connect to voice: {error}"

        if not self._radio_stream(guild, voice_client, data["stream_url"]):
            return "I could not start the stream. Check that ffmpeg is installed and the URL is reachable."
        await self.radio_config.guild(guild).voice_channel_id.set(channel.id)
        await self._radio_refresh_voice_status(guild)
        return f"Streaming radio in {channel.mention}."

    async def _radio_stop(self, guild: discord.Guild) -> str:
        if guild.voice_client is None:
            return "I am not connected to voice."
        await self._radio_disconnect(guild)
        return "Radio stopped and left voice."

    async def _radio_disconnect(self, guild: discord.Guild):
        voice_client = guild.voice_client
        if voice_client is None:
            return
        self._radio_stopping.add(guild.id)
        self._radio_cancel_reconnect(guild.id)
        await self._radio_clear_voice_status(guild)
        await voice_client.disconnect(force=True)

    # ----- voice plumbing -----

    @staticmethod
    def _radio_configured_channel(guild: discord.Guild, data: dict) -> discord.VoiceChannel | None:
        channel = guild.get_channel(int(data["voice_channel_id"])) if data.get("voice_channel_id") else None
        return channel if isinstance(channel, discord.VoiceChannel) else None

    @staticmethod
    async def _radio_connect(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
        voice_client = guild.voice_client
        if voice_client is None:
            return await channel.connect()
        if voice_client.channel != channel:
            await voice_client.move_to(channel)
        return voice_client

    def _radio_stream(self, guild: discord.Guild, voice_client: discord.VoiceClient, stream_url: str) -> bool:
        def after(error: Exception | None):
            self.bot.loop.call_soon_threadsafe(self._radio_stream_ended, guild.id)

        try:
            voice_client.play(discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS), after=after)
        except (discord.ClientException, OSError):
            log.exception("Could not start FFmpeg playback for guild %s", guild.id)
            return False
        return True

    def _radio_stream_ended(self, guild_id: int):
        if guild_id in self._radio_stopping:
            self._radio_stopping.discard(guild_id)
            return
        if guild_id not in self._radio_reconnect_tasks:
            self._radio_reconnect_tasks[guild_id] = asyncio.create_task(self._radio_reconnect_later(guild_id))

    async def _radio_reconnect_later(self, guild_id: int):
        try:
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            data = await self.radio_config.guild(guild).all()
            if not data["enabled"] or not data["reconnect"] or not data["stream_url"]:
                return
            channel = self._radio_configured_channel(guild, data)
            if data["auto_play"] and channel is not None and not _has_human(channel):
                await self._radio_clear_voice_status(guild)
                return
            voice_client = guild.voice_client
            if voice_client is None:
                if channel is None:
                    return
                voice_client = await channel.connect()
            if not voice_client.is_playing() and not voice_client.is_paused():
                self._radio_stream(guild, voice_client, data["stream_url"])
        except (discord.HTTPException, discord.ClientException, asyncio.TimeoutError):
            log.warning("Radio reconnect failed for guild %s", guild_id, exc_info=True)
        finally:
            self._radio_reconnect_tasks.pop(guild_id, None)

    def _radio_cancel_reconnect(self, guild_id: int):
        task = self._radio_reconnect_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

    # ----- auto play -----

    @commands.Cog.listener("on_voice_state_update")
    async def _radio_on_voice_state(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        data = await self.radio_config.guild(member.guild).all()
        if not data["enabled"] or not data["auto_play"] or not data["voice_channel_id"]:
            return
        channel_id = int(data["voice_channel_id"])
        if (before.channel and before.channel.id == channel_id) or (after.channel and after.channel.id == channel_id):
            self._radio_schedule_sync(member.guild.id)

    def _radio_schedule_sync(self, guild_id: int):
        task = self._radio_sync_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()
        self._radio_sync_tasks[guild_id] = asyncio.create_task(self._radio_sync_soon(guild_id))

    async def _radio_sync_soon(self, guild_id: int):
        current = asyncio.current_task()
        try:
            await asyncio.sleep(AUTO_SYNC_DELAY_SECONDS)
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                await self._radio_sync(guild)
        finally:
            if self._radio_sync_tasks.get(guild_id) is current:
                self._radio_sync_tasks.pop(guild_id, None)

    async def _radio_auto_channel(self, guild: discord.Guild) -> discord.VoiceChannel | None:
        data = await self.radio_config.guild(guild).all()
        if not data["enabled"] or not data["auto_play"] or not data["stream_url"]:
            return None
        if await self.bot.cog_disabled_in_guild(self, guild):
            return None
        return self._radio_configured_channel(guild, data)

    async def _radio_sync(self, guild: discord.Guild):
        """Join the auto channel when someone is listening, leave it after a grace period when empty."""
        channel = await self._radio_auto_channel(guild)
        if channel is None:
            return
        if not _has_human(channel):
            await asyncio.sleep(AUTO_LEAVE_GRACE_SECONDS)
            channel = await self._radio_auto_channel(guild)
            voice_client = guild.voice_client
            if channel is None or _has_human(channel):
                return
            if voice_client and voice_client.channel and voice_client.channel.id == channel.id:
                await self._radio_disconnect(guild)
            return

        voice_client = guild.voice_client
        if voice_client and voice_client.channel and voice_client.channel.id != channel.id:
            return  # Someone moved the bot on purpose.
        if (voice_client and voice_client.is_playing()) or shutil.which("ffmpeg") is None:
            return
        try:
            self._radio_stopping.discard(guild.id)
            self._radio_cancel_reconnect(guild.id)
            voice_client = await self._radio_connect(guild, channel)
            if voice_client.is_playing() or voice_client.is_paused():
                return
            stream_url = await self.radio_config.guild(guild).stream_url()
            if self._radio_stream(guild, voice_client, stream_url):
                await self._radio_refresh_voice_status(guild)
            else:
                await voice_client.disconnect(force=True)
        except (discord.HTTPException, asyncio.TimeoutError, discord.ClientException, RuntimeError):
            log.exception("Radio auto play failed for guild %s", guild.id)

    # ----- now playing status -----

    async def _radio_status_loop(self):
        await self.bot.wait_until_red_ready()
        while True:
            try:
                for guild_id, data in (await self.radio_config.all_guilds()).items():
                    guild = self.bot.get_guild(int(guild_id))
                    if guild is None or not data.get("enabled", True):
                        continue
                    if data.get("status_channel_id"):
                        await self._radio_refresh_status_message(guild, data)
                    if data.get("voice_status_enabled", True) and guild.voice_client is not None:
                        await self._radio_refresh_voice_status(guild, data)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Radio status update failed")
            await asyncio.sleep(STATUS_POLL_SECONDS)

    async def _radio_refresh_status_message(self, guild: discord.Guild, data: dict) -> bool:
        channel = guild.get_channel(int(data["status_channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return False
        embed = self._radio_status_embed(guild, data, await self._radio_fetch_status(data))
        content = "**LongWayFM Status**"
        if data.get("status_message_id"):
            try:
                message = await channel.fetch_message(int(data["status_message_id"]))
                await message.edit(content=content, embed=embed)
                return True
            except discord.NotFound:
                await self.radio_config.guild(guild).status_message_id.clear()
            except discord.HTTPException:
                return False
        try:
            message = await channel.send(content=content, embed=embed)
        except discord.HTTPException:
            return False
        await self.radio_config.guild(guild).status_message_id.set(message.id)
        return True

    async def _radio_refresh_voice_status(self, guild: discord.Guild, data: dict | None = None) -> bool:
        data = data or await self.radio_config.guild(guild).all()
        if not data.get("voice_status_enabled"):
            return False
        channel = self._radio_status_channel(guild, data)
        if channel is None:
            return False
        status = await self._radio_fetch_status(data)
        if status.get("online"):
            title = (status.get("title") or "").strip()
            text = f"{current_show_name()}: {title}" if title else current_show_name()
        else:
            text = f"{current_show_name()}: offline"
        return await self._radio_set_voice_status(channel, text[:500])

    async def _radio_clear_voice_status(self, guild: discord.Guild) -> bool:
        channel = self._radio_status_channel(guild, await self.radio_config.guild(guild).all())
        return channel is not None and await self._radio_set_voice_status(channel, None)

    def _radio_status_channel(self, guild: discord.Guild, data: dict) -> discord.VoiceChannel | None:
        voice_client = guild.voice_client
        if voice_client and isinstance(voice_client.channel, discord.VoiceChannel):
            return voice_client.channel
        return self._radio_configured_channel(guild, data)

    async def _radio_set_voice_status(self, channel: discord.VoiceChannel, status: str | None) -> bool:
        try:
            await self.bot.http.request(
                Route("PUT", "/channels/{channel_id}/voice-status", channel_id=channel.id),
                json={"status": status},
                reason="Radio now playing status update",
            )
        except discord.Forbidden:
            log.warning("Missing permission to set voice status for channel %s", channel.id)
            return False
        except discord.HTTPException:
            log.exception("Discord rejected voice status update for channel %s", channel.id)
            return False
        return True

    @staticmethod
    async def _radio_fetch_status(data: dict) -> dict:
        url = status_api_url(data)
        if not url:
            return {"online": False, "error": "No status API configured."}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status >= 400:
                        return {"online": False, "error": f"Status API returned HTTP {response.status}."}
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return {"online": False, "error": "Could not reach the status API."}

        source = payload.get("icestats", {}).get("source") if isinstance(payload, dict) else None
        if isinstance(source, list) and source:
            source = select_source(source, data.get("stream_url")) or source[0]
        if not isinstance(source, dict):
            return {"online": False, "error": None}
        raw_title = str(source.get("title") or source.get("server_name") or "LongWayFM - Live")
        return {
            "online": True,
            "title": "Commercial Break" if "commercial" in raw_title.lower() else raw_title,
            "listeners": _int(source.get("listeners")),
            "bitrate": _int(source.get("bitrate")),
            "error": None,
        }

    @staticmethod
    def _radio_status_embed(guild: discord.Guild, data: dict, status: dict) -> discord.Embed:
        # Kept as an embed: the status message already exists in servers and cannot switch to components.
        voice_client = guild.voice_client
        if status.get("online"):
            embed = discord.Embed(
                title=current_show_name(), description=f"Now playing: **{status['title']}**", color=discord.Color.green()
            )
            embed.add_field(name="Listeners", value=str(status.get("listeners", 0)), inline=True)
            if status.get("bitrate"):
                embed.add_field(name="Bitrate", value=f"{status['bitrate']} kbps", inline=True)
        else:
            embed = discord.Embed(
                title=current_show_name(),
                description="The stream is currently offline or metadata is unavailable.",
                color=discord.Color.dark_grey(),
            )
            if status.get("error"):
                embed.add_field(name="Status", value=status["error"], inline=False)
        playing = bool(voice_client and voice_client.is_playing())
        embed.add_field(name="Discord Playback", value="Playing" if playing else "Stopped", inline=True)
        if data.get("stream_url"):
            embed.add_field(name="Stream", value=f"[Open stream]({data['stream_url']})", inline=False)
        embed.set_footer(text=f"Updates every {STATUS_POLL_SECONDS} seconds")
        return embed
