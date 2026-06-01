from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import discord
from redbot.core import Config, app_commands, commands


CHECK_INTERVAL_SECONDS = 30
MAX_REMINDER_SECONDS = 366 * 24 * 60 * 60
MAX_MESSAGE_LENGTH = 1000
MENTION_RE = re.compile(r"@(everyone|here)|<@&\d+>")
TIME_PART_RE = re.compile(r"(\d+)\s*([wdhms])", re.IGNORECASE)
TIME_UNITS = {
    "w": 7 * 24 * 60 * 60,
    "d": 24 * 60 * 60,
    "h": 60 * 60,
    "m": 60,
    "s": 1,
}
log = logging.getLogger("red.neufox.reminders")


class Reminders(commands.Cog):
    """Schedule reminders for yourself or another member."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=591743820)
        self.config.register_global(reminders={}, next_id=1)
        self._config_lock = asyncio.Lock()
        self._task = self.bot.loop.create_task(self._reminder_loop())

    def cog_unload(self):
        self._task.cancel()

    @commands.command(name="remindme")
    async def remindme_prefix(self, ctx: commands.Context, when: str, *, message: str):
        """Schedule a reminder for yourself."""
        result = await self._prepare_reminder(ctx, ctx.author, when, message)
        if result is None:
            return

        reminder_id, reminder, duration = result
        await ctx.send(
            self._confirmation_text(ctx.author, ctx.author, reminder, duration),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await self._set_reminder_source_url(reminder_id, ctx.message.jump_url)

    @commands.command(name="remind")
    @commands.guild_only()
    async def remind_prefix(
        self,
        ctx: commands.Context,
        user: discord.Member,
        when: str,
        *,
        message: str,
    ):
        """Schedule a reminder for another member."""
        result = await self._prepare_reminder(ctx, user, when, message)
        if result is None:
            return

        reminder_id, reminder, duration = result
        await ctx.send(
            self._confirmation_text(ctx.author, user, reminder, duration),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await self._set_reminder_source_url(reminder_id, ctx.message.jump_url)

    @app_commands.command(name="remindme", description="Schedule a reminder for yourself.")
    @app_commands.describe(
        when="When to remind you, like 10m, 2h30m, 1d, or 1w2d.",
        message="What to remind you about.",
    )
    async def remindme(
        self,
        interaction: discord.Interaction,
        when: str,
        message: str,
    ):
        """Schedule a reminder for yourself."""
        await self._create_interaction_reminder(interaction, interaction.user, when, message)

    @app_commands.command(name="remind", description="Schedule a reminder for another member.")
    @app_commands.describe(
        user="The member to remind.",
        when="When to remind them, like 10m, 2h30m, 1d, or 1w2d.",
        message="What to remind them about.",
    )
    @app_commands.guild_only()
    async def remind(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        when: str,
        message: str,
    ):
        """Schedule a reminder for another member."""
        await self._create_interaction_reminder(interaction, user, when, message)

    async def _create_interaction_reminder(
        self,
        interaction: discord.Interaction,
        target: discord.User | discord.Member,
        when: str,
        message: str,
    ):
        if interaction.channel is None:
            await interaction.response.send_message("I cannot create reminders from this channel.", ephemeral=True)
            return

        result = await self._prepare_reminder(interaction, target, when, message)
        if result is None:
            return

        reminder_id, reminder, duration = result
        await interaction.response.send_message(
            self._confirmation_text(interaction.user, target, reminder, duration),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        try:
            response_message = await interaction.original_response()
        except discord.HTTPException:
            response_message = None
        if response_message is not None:
            await self._set_reminder_source_url(reminder_id, response_message.jump_url)

    async def _prepare_reminder(
        self,
        source: commands.Context | discord.Interaction,
        target: discord.User | discord.Member,
        when: str,
        message: str,
    ) -> tuple[int, dict[str, Any], int] | None:
        duration = self._parse_duration(when)
        if duration is None:
            await self._send_error(source, "Use a duration like `10m`, `2h30m`, `1d`, or `1w2d`.")
            return
        if duration <= 0:
            await self._send_error(source, "Reminder time must be in the future.")
            return
        if duration > MAX_REMINDER_SECONDS:
            await self._send_error(source, "Reminder time cannot be more than 366 days.")
            return

        message = message.strip()
        if not message:
            await self._send_error(source, "Reminder message cannot be empty.")
            return
        if len(message) > MAX_MESSAGE_LENGTH:
            await self._send_error(source, f"Reminder message cannot be longer than {MAX_MESSAGE_LENGTH} characters.")
            return

        guild = source.guild
        channel = source.channel
        author = source.user if isinstance(source, discord.Interaction) else source.author
        now = int(time.time())
        reminder = {
            "guild_id": guild.id if guild else None,
            "channel_id": channel.id,
            "creator_id": author.id,
            "target_id": target.id,
            "message": message,
            "created_at": now,
            "due_at": now + duration,
            "source_url": None,
            "source_location": self._format_source_location(channel),
        }
        reminder_id = await self._store_reminder(reminder)
        return reminder_id, reminder, duration

    async def _send_error(self, source: commands.Context | discord.Interaction, message: str):
        if isinstance(source, discord.Interaction):
            await source.response.send_message(message, ephemeral=True)
        else:
            await source.send(message)

    def _confirmation_text(
        self,
        author: discord.User | discord.Member,
        target: discord.User | discord.Member,
        reminder: dict[str, Any],
        duration: int,
    ) -> str:
        due_at = datetime.fromtimestamp(reminder["due_at"], tz=timezone.utc)
        duration_text = self._format_duration(duration)
        target_text = "you" if target.id == author.id else target.mention
        return f"I will remind {target_text} of that in {duration_text} ({discord.utils.format_dt(due_at, style='f')})."

    async def _store_reminder(self, reminder: dict[str, Any]) -> int:
        async with self._config_lock:
            reminder_id = int(await self.config.next_id())
            await self.config.next_id.set(reminder_id + 1)
            async with self.config.reminders() as reminders:
                reminders[str(reminder_id)] = reminder
        return reminder_id

    async def _set_reminder_source_url(self, reminder_id: int, source_url: str):
        async with self.config.reminders() as reminders:
            reminder = reminders.get(str(reminder_id))
            if reminder is not None:
                reminder["source_url"] = source_url

    async def _reminder_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._send_due_reminders()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Unhandled error while processing reminders")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _send_due_reminders(self):
        now = int(time.time())
        due_reminders: list[tuple[str, dict[str, Any]]] = []
        async with self.config.reminders() as reminders:
            for reminder_id, reminder in list(reminders.items()):
                if int(reminder.get("due_at", 0)) <= now:
                    due_reminders.append((reminder_id, dict(reminder)))
                    reminders.pop(reminder_id, None)

        for _, reminder in due_reminders:
            await self._deliver_reminder(reminder)

    async def _deliver_reminder(self, reminder: dict[str, Any]):
        target = await self._get_user(int(reminder["target_id"]))
        message = self._sanitize_message(str(reminder["message"]))
        content = self._build_reminder_message(reminder, message, target)
        allowed_mentions = discord.AllowedMentions(users=True)

        if target:
            try:
                await target.send(content, allowed_mentions=allowed_mentions)
                return
            except discord.HTTPException:
                pass

        channel = self.bot.get_channel(int(reminder["channel_id"]))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(reminder["channel_id"]))
            except (discord.HTTPException, discord.NotFound):
                channel = None

        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(content, allowed_mentions=allowed_mentions)
                return
            except discord.HTTPException:
                pass

    def _build_reminder_message(
        self,
        reminder: dict[str, Any],
        message: str,
        target: discord.User | discord.Member | None,
    ) -> str:
        target_text = target.mention if target else f"<@{reminder['target_id']}>"
        created_at = datetime.fromtimestamp(int(reminder["created_at"]), tz=timezone.utc)
        due_at = datetime.fromtimestamp(int(reminder["due_at"]), tz=timezone.utc)
        ago_text = self._format_duration(max(1, int((due_at - created_at).total_seconds())))
        source_label = self._format_absolute_time(created_at)
        location = reminder.get("source_location") or "dms"
        source_url = reminder.get("source_url")
        source_text = f"[original message]({source_url})" if source_url else "original message"

        return (
            f":bell: Reminder! :bell:\n"
            f"From {ago_text} ago:\n\n"
            f"{target_text} {message}\n\n"
            f"{source_text} • {source_label} on {location}"
        )

    async def _get_user(self, user_id: int) -> discord.User | discord.Member | None:
        user = self.bot.get_user(user_id)
        if user is not None:
            return user
        try:
            return await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return None

    @staticmethod
    def _parse_duration(value: str) -> int | None:
        cleaned = value.lower().strip()
        if cleaned.startswith("in "):
            cleaned = cleaned[3:].strip()
        if not cleaned:
            return None

        total = 0
        consumed = ""
        for amount, unit in TIME_PART_RE.findall(cleaned):
            total += int(amount) * TIME_UNITS[unit.lower()]
            consumed += f"{amount}{unit}"

        compact = re.sub(r"\s+", "", cleaned)
        if not total or consumed.lower() != compact:
            return None
        return total

    @staticmethod
    def _format_duration(seconds: int) -> str:
        remaining = int(seconds)
        parts: list[str] = []
        for label, unit_seconds in (
            ("week", TIME_UNITS["w"]),
            ("day", TIME_UNITS["d"]),
            ("hour", TIME_UNITS["h"]),
            ("minute", TIME_UNITS["m"]),
            ("second", TIME_UNITS["s"]),
        ):
            amount, remaining = divmod(remaining, unit_seconds)
            if amount:
                plural = "s" if amount != 1 else ""
                parts.append(f"{amount} {label}{plural}")
            if len(parts) == 2:
                break
        return " ".join(parts) if parts else "0 seconds"

    @staticmethod
    def _format_absolute_time(value: datetime) -> str:
        return value.strftime("%-d %B %Y at %H:%M")

    @staticmethod
    def _format_source_location(channel: discord.abc.GuildChannel | discord.abc.PrivateChannel) -> str:
        if isinstance(channel, discord.abc.GuildChannel):
            return f"#{channel.name}"
        return "dms"

    @staticmethod
    def _sanitize_message(message: str) -> str:
        def replace(match: re.Match) -> str:
            if match.group(1):
                return f"@\u200b{match.group(1)}"
            return match.group(0).replace("<@&", "<@\u200b&")

        return MENTION_RE.sub(replace, message)
