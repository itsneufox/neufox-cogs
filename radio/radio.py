from __future__ import annotations

import asyncio
import logging
import shutil
from urllib.parse import urlparse

import aiohttp
import discord
from discord.http import Route
from redbot.core import Config, commands


log = logging.getLogger("red.neufox.radio")
DEFAULT_COLOR = discord.Color.blurple()
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
STATUS_POLL_SECONDS = 30


class Radio(commands.Cog):
    """Stream a configured radio URL into voice."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=938475610)
        self.config.register_guild(
            stream_url=None,
            voice_channel_id=None,
            reconnect=True,
            status_channel_id=None,
            status_message_id=None,
            status_api_url=None,
            voice_status_enabled=True,
            auto_play=True,
        )
        self._manual_stop: set[int] = set()
        self._reconnect_tasks: dict[int, asyncio.Task] = {}
        self._status_task = self.bot.loop.create_task(self._status_loop())

    def cog_unload(self):
        for task in self._reconnect_tasks.values():
            task.cancel()
        self._status_task.cancel()

    @commands.group(name="radio", invoke_without_command=True)
    @commands.guild_only()
    async def radio(self, ctx: commands.Context):
        """Manage the server radio stream."""
        data = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data["voice_channel_id"]) if data["voice_channel_id"] else None
        voice_client = ctx.guild.voice_client

        embed = discord.Embed(title="Radio", color=DEFAULT_COLOR)
        embed.add_field(name="Stream URL", value=data["stream_url"] or "Not set", inline=False)
        embed.add_field(
            name="Auto Channel",
            value=channel.mention if isinstance(channel, discord.VoiceChannel) else "Not set",
            inline=True,
        )
        embed.add_field(name="Auto Play", value="Enabled" if data["auto_play"] else "Disabled", inline=True)
        embed.add_field(name="Connected", value="Yes" if voice_client else "No", inline=True)
        embed.add_field(
            name="Playing",
            value="Yes" if voice_client and voice_client.is_playing() else "No",
            inline=True,
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{ctx.clean_prefix}radio url <stream_url>`\n"
                f"`{ctx.clean_prefix}radio autoplay <voice_channel> [true/false]`\n"
                f"`{ctx.clean_prefix}radio forcejoin [voice_channel]`\n"
                f"`{ctx.clean_prefix}radio forcestop`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @radio.command(name="url")
    @commands.admin_or_permissions(manage_guild=True)
    async def radio_url(self, ctx: commands.Context, *, url: str):
        """Set the radio stream URL."""
        if not self._valid_stream_url(url):
            await ctx.send("That does not look like a valid HTTP/HTTPS stream URL.")
            return
        await self.config.guild(ctx.guild).stream_url.set(url.strip())
        await self._sync_auto_play(ctx.guild)
        await ctx.send("Radio stream URL updated.")

    @radio.command(name="autoplay")
    @commands.admin_or_permissions(manage_guild=True)
    async def radio_auto_play(
        self,
        ctx: commands.Context,
        channel: discord.VoiceChannel,
        enabled: bool = True,
    ):
        """Set the radio channel and enable or disable automatic join/leave."""
        await self.config.guild(ctx.guild).voice_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).auto_play.set(enabled)
        if enabled:
            await self._sync_auto_play(ctx.guild)
        else:
            voice_client = ctx.guild.voice_client
            if voice_client and voice_client.channel and voice_client.channel.id == channel.id:
                self._manual_stop.add(ctx.guild.id)
                self._cancel_reconnect(ctx.guild.id)
                await self._clear_configured_voice_status(ctx.guild)
                await voice_client.disconnect(force=True)
        await ctx.send(
            f"Radio auto play is now {'enabled' if enabled else 'disabled'} for {channel.mention}."
        )

    @radio.command(name="forcejoin")
    @commands.guild_only()
    @commands.bot_has_permissions(connect=True, speak=True)
    async def radio_force_join(self, ctx: commands.Context, channel: discord.VoiceChannel | None = None):
        """Force the bot to join and start the stream."""
        try:
            await self._play(ctx, channel, sync_auto=False)
        except Exception:
            log.exception("Unhandled error in radio forcejoin for guild %s", ctx.guild.id if ctx.guild else None)
            await ctx.send("Radio failed before playback could start. Check the bot logs for the exact traceback.")

    @radio.command(name="forcestop")
    @commands.guild_only()
    async def radio_force_stop(self, ctx: commands.Context):
        """Force the bot to stop playback and leave voice."""
        voice_client = ctx.guild.voice_client
        if voice_client is None:
            await ctx.send("I am not connected to voice.")
            return
        self._manual_stop.add(ctx.guild.id)
        self._cancel_reconnect(ctx.guild.id)
        await self._clear_configured_voice_status(ctx.guild)
        await voice_client.disconnect(force=True)
        await ctx.send("Radio stopped and left voice.")

    async def _play(
        self,
        ctx: commands.Context,
        channel: discord.VoiceChannel | None,
        *,
        sync_auto: bool = True,
    ):
        data = await self.config.guild(ctx.guild).all()
        stream_url = data["stream_url"]
        if not stream_url:
            await ctx.send("Set a stream URL first with `radio url <stream_url>`.")
            return
        if shutil.which("ffmpeg") is None:
            await ctx.send("`ffmpeg` is not installed or is not available in the bot's PATH.")
            return

        channel = channel or self._author_voice_channel(ctx.author)
        if channel is None and data["voice_channel_id"]:
            configured_channel = ctx.guild.get_channel(int(data["voice_channel_id"]))
            if isinstance(configured_channel, discord.VoiceChannel):
                channel = configured_channel
        if channel is None:
            await ctx.send("Join a voice channel or provide one with `radio forcejoin <voice_channel>`.")
            return

        self._manual_stop.discard(ctx.guild.id)
        self._cancel_reconnect(ctx.guild.id)

        try:
            voice_client = await self._ensure_voice_client(ctx.guild, channel)
        except discord.Forbidden:
            await ctx.send("I do not have permission to join or speak in that voice channel.")
            return
        except discord.HTTPException:
            await ctx.send("Discord rejected the voice connection request.")
            return
        except asyncio.TimeoutError:
            await ctx.send("Discord voice connection timed out. Try again in a moment.")
            return
        except RuntimeError as exc:
            if "PyNaCl" in str(exc):
                await ctx.send("Voice support is not installed. Install `PyNaCl` in the bot environment.")
            else:
                await ctx.send(f"Could not start Discord voice: {exc}")
            return
        except discord.ClientException as exc:
            await ctx.send(f"Could not connect to voice: {exc}")
            return
        except Exception:
            log.exception("Unexpected error while connecting radio voice in guild %s", ctx.guild.id)
            await ctx.send("I hit an unexpected error while connecting to voice. Check the bot logs for details.")
            return

        try:
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
        except Exception:
            log.exception("Unexpected error while stopping previous radio source in guild %s", ctx.guild.id)
            await ctx.send("I could not stop the existing voice source cleanly. Try `.radio forcestop` and then join again.")
            return

        if not self._start_stream(ctx.guild, voice_client, stream_url):
            await ctx.send("I could not start the stream. Check that ffmpeg is installed and the URL is reachable.")
            return

        await self.config.guild(ctx.guild).voice_channel_id.set(channel.id)
        await self._refresh_voice_status(ctx.guild)
        if sync_auto:
            await self._sync_auto_play(ctx.guild)
        await ctx.send(f"Streaming radio in {channel.mention}.")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        data = await self.config.guild(member.guild).all()
        if not data.get("auto_play") or not data.get("voice_channel_id"):
            return
        channel_id = int(data["voice_channel_id"])
        if before.channel and before.channel.id == channel_id:
            await self._sync_auto_play(member.guild, data)
            return
        if after.channel and after.channel.id == channel_id:
            await self._sync_auto_play(member.guild, data)
            return

    async def _ensure_voice_client(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
    ) -> discord.VoiceClient:
        voice_client = guild.voice_client
        if voice_client is None:
            return await channel.connect()
        if voice_client.channel != channel:
            await voice_client.move_to(channel)
        return voice_client

    def _start_stream(
        self,
        guild: discord.Guild,
        voice_client: discord.VoiceClient,
        stream_url: str,
    ) -> bool:
        try:
            source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
        except (discord.ClientException, OSError):
            log.exception("Could not create FFmpeg source for guild %s", guild.id)
            return False

        def after(error: Exception | None):
            self.bot.loop.call_soon_threadsafe(self._handle_stream_end, guild.id, error)

        try:
            voice_client.play(source, after=after)
        except (discord.ClientException, OSError):
            log.exception("Could not start FFmpeg playback for guild %s", guild.id)
            return False
        return True

    def _handle_stream_end(self, guild_id: int, error: Exception | None):
        if guild_id in self._manual_stop:
            self._manual_stop.discard(guild_id)
            return
        if guild_id in self._reconnect_tasks:
            return
        self._reconnect_tasks[guild_id] = self.bot.loop.create_task(self._reconnect_later(guild_id))

    async def _reconnect_later(self, guild_id: int):
        try:
            await asyncio.sleep(5)
            guild = self.bot.get_guild(guild_id)
            if guild is None or not await self.config.guild(guild).reconnect():
                return
            data = await self.config.guild(guild).all()
            stream_url = data["stream_url"]
            if not stream_url:
                return
            channel = None
            if data["voice_channel_id"]:
                configured_channel = guild.get_channel(int(data["voice_channel_id"]))
                if isinstance(configured_channel, discord.VoiceChannel):
                    channel = configured_channel
            if data.get("auto_play") and channel is not None and not self._has_human_listener(channel):
                await self._clear_configured_voice_status(guild, data)
                return
            voice_client = guild.voice_client
            if voice_client is None:
                if channel is None:
                    return
                voice_client = await channel.connect()
            if voice_client.is_playing() or voice_client.is_paused():
                return
            self._start_stream(guild, voice_client, stream_url)
        except (discord.Forbidden, discord.HTTPException):
            return
        finally:
            self._reconnect_tasks.pop(guild_id, None)

    def _cancel_reconnect(self, guild_id: int):
        task = self._reconnect_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

    async def _sync_auto_play(self, guild: discord.Guild, data: dict | None = None):
        data = data or await self.config.guild(guild).all()
        if not data.get("auto_play") or not data.get("stream_url") or not data.get("voice_channel_id"):
            return

        channel = guild.get_channel(int(data["voice_channel_id"]))
        if not isinstance(channel, discord.VoiceChannel):
            return

        voice_client = guild.voice_client
        has_listener = self._has_human_listener(channel)

        if not has_listener:
            if voice_client and voice_client.channel and voice_client.channel.id == channel.id:
                self._manual_stop.add(guild.id)
                self._cancel_reconnect(guild.id)
                await self._clear_configured_voice_status(guild, data)
                await voice_client.disconnect(force=True)
            return

        if voice_client and voice_client.channel and voice_client.channel.id != channel.id:
            return
        if voice_client and voice_client.is_playing():
            return
        if shutil.which("ffmpeg") is None:
            return

        try:
            self._manual_stop.discard(guild.id)
            self._cancel_reconnect(guild.id)
            voice_client = await self._ensure_voice_client(guild, channel)
            if not voice_client.is_playing() and not voice_client.is_paused():
                self._start_stream(guild, voice_client, data["stream_url"])
                await self._refresh_voice_status(guild, data)
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError, discord.ClientException, RuntimeError):
            return

    async def _status_loop(self):
        await self.bot.wait_until_red_ready()
        while True:
            try:
                guild_data = await self.config.all_guilds()
                for guild_id, data in guild_data.items():
                    if not data.get("status_channel_id") and not data.get("voice_status_enabled"):
                        continue
                    guild = self.bot.get_guild(int(guild_id))
                    if guild is None:
                        continue
                    if data.get("status_channel_id"):
                        await self._refresh_status_message(guild, data)
                    if data.get("voice_status_enabled"):
                        await self._refresh_voice_status(guild, data)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(STATUS_POLL_SECONDS)

    async def _refresh_status_message(self, guild: discord.Guild, data: dict | None = None) -> bool:
        data = data or await self.config.guild(guild).all()
        channel_id = data.get("status_channel_id")
        if not channel_id:
            return False

        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return False

        status = await self._fetch_status(data)
        embed = self._status_embed(guild, data, status)
        content = "**LongWayFM Status**"
        message_id = data.get("status_message_id")

        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(content=content, embed=embed)
                return True
            except discord.NotFound:
                await self.config.guild(guild).status_message_id.clear()
            except (discord.Forbidden, discord.HTTPException):
                return False

        try:
            message = await channel.send(content=content, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return False

        await self.config.guild(guild).status_message_id.set(message.id)
        return True

    async def _refresh_voice_status(self, guild: discord.Guild, data: dict | None = None) -> bool:
        data = data or await self.config.guild(guild).all()
        if not data.get("voice_status_enabled"):
            return False

        channel = self._configured_voice_channel(guild, data)
        if channel is None:
            return False

        status = await self._fetch_status(data)
        if status.get("online"):
            text = self._voice_status_text(status["title"])
        else:
            text = "LongWayFM: offline"

        return await self._set_voice_channel_status(channel, text)

    async def _clear_configured_voice_status(self, guild: discord.Guild, data: dict | None = None) -> bool:
        data = data or await self.config.guild(guild).all()
        channel = self._configured_voice_channel(guild, data)
        if channel is None:
            return False
        return await self._set_voice_channel_status(channel, None)

    async def _set_voice_channel_status(
        self,
        channel: discord.VoiceChannel,
        status: str | None,
    ) -> bool:
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

    async def _fetch_status(self, data: dict) -> dict:
        status_url = self._status_api_url(data)
        if not status_url:
            return {"online": False, "error": "No status API configured."}

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(status_url) as response:
                    if response.status >= 400:
                        return {"online": False, "error": f"Status API returned HTTP {response.status}."}
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return {"online": False, "error": "Could not reach the status API."}

        source = payload.get("icestats", {}).get("source")
        if not source:
            return {"online": False, "error": None}

        if isinstance(source, list):
            source = self._select_source(source, data.get("stream_url")) or source[0]
        if not isinstance(source, dict):
            return {"online": False, "error": None}

        raw_title = source.get("title") or source.get("server_name") or "LongWayFM - Live"
        title = "Commercial Break" if "commercial" in str(raw_title).lower() else str(raw_title)
        listeners = self._parse_int(source.get("listeners"))
        bitrate = self._parse_int(source.get("bitrate"))

        return {
            "online": True,
            "title": title,
            "listeners": listeners,
            "bitrate": bitrate,
            "listenurl": source.get("listenurl"),
            "server_name": source.get("server_name"),
            "error": None,
        }

    def _status_embed(self, guild: discord.Guild, data: dict, status: dict) -> discord.Embed:
        voice_client = guild.voice_client
        playing = bool(voice_client and voice_client.is_playing())
        stream_url = data.get("stream_url")

        if status.get("online"):
            embed = discord.Embed(
                title="LongWayFM",
                description=f"Now playing: **{status['title']}**",
                color=discord.Color.green(),
            )
            embed.add_field(name="Listeners", value=str(status.get("listeners", 0)), inline=True)
            if status.get("bitrate"):
                embed.add_field(name="Bitrate", value=f"{status['bitrate']} kbps", inline=True)
        else:
            embed = discord.Embed(
                title="LongWayFM",
                description="The stream is currently offline or metadata is unavailable.",
                color=discord.Color.dark_grey(),
            )
            if status.get("error"):
                embed.add_field(name="Status", value=status["error"], inline=False)

        embed.add_field(name="Discord Playback", value="Playing" if playing else "Stopped", inline=True)
        if stream_url:
            embed.add_field(name="Stream", value=f"[Open stream]({stream_url})", inline=False)
        embed.set_footer(text=f"Updates every {STATUS_POLL_SECONDS} seconds")
        return embed

    def _status_api_url(self, data: dict) -> str | None:
        configured = data.get("status_api_url")
        if configured:
            return configured
        stream_url = data.get("stream_url")
        if not stream_url:
            return None
        parsed = urlparse(stream_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}/status-json.xsl"

    def _configured_voice_channel(self, guild: discord.Guild, data: dict) -> discord.VoiceChannel | None:
        voice_client = guild.voice_client
        if voice_client and isinstance(voice_client.channel, discord.VoiceChannel):
            return voice_client.channel
        channel_id = data.get("voice_channel_id")
        if not channel_id:
            return None
        channel = guild.get_channel(int(channel_id))
        return channel if isinstance(channel, discord.VoiceChannel) else None

    @staticmethod
    def _has_human_listener(channel: discord.VoiceChannel) -> bool:
        return any(not member.bot for member in channel.members)

    @staticmethod
    def _voice_status_text(title: str) -> str:
        text = f"LongWayFM: {title}".strip()
        return text[:500]

    @staticmethod
    def _select_source(sources: list, stream_url: str | None) -> dict | None:
        if not stream_url:
            return None
        parsed = urlparse(stream_url)
        stream_path = parsed.path or "/stream"
        for source in sources:
            if not isinstance(source, dict):
                continue
            listenurl = str(source.get("listenurl") or "")
            if stream_url in listenurl or stream_path in listenurl:
                return source
        return None

    @staticmethod
    def _parse_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _author_voice_channel(author: discord.Member | discord.User) -> discord.VoiceChannel | None:
        if not isinstance(author, discord.Member) or author.voice is None:
            return None
        channel = author.voice.channel
        return channel if isinstance(channel, discord.VoiceChannel) else None

    @staticmethod
    def _valid_stream_url(url: str) -> bool:
        parsed = urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
