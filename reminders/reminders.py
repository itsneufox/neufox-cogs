from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import discord
from redbot.core import Config, app_commands, commands


DEFAULT_COLOR = discord.Color.gold()
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

    @app_commands.command(name="remindme", description="Schedule a reminder for yourself.")
    @app_commands.describe(
        when="When to remind you, like 10m, 2h30m, 1d, or 1w2d.",
        message="What to remind you about.",
    )
    @app_commands.guild_only()
    async def remindme(
        self,
        interaction: discord.Interaction,
        when: str,
        message: str,
    ):
        """Schedule a reminder for yourself."""
        await self._create_reminder(interaction, interaction.user, when, message)

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
        await self._create_reminder(interaction, user, when, message)

    async def _create_reminder(
        self,
        interaction: discord.Interaction,
        target: discord.User | discord.Member,
        when: str,
        message: str,
    ):
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Reminders can only be created in a server.", ephemeral=True)
            return

        duration = self._parse_duration(when)
        if duration is None:
            await interaction.response.send_message(
                "Use a duration like `10m`, `2h30m`, `1d`, or `1w2d`.",
                ephemeral=True,
            )
            return
        if duration <= 0:
            await interaction.response.send_message("Reminder time must be in the future.", ephemeral=True)
            return
        if duration > MAX_REMINDER_SECONDS:
            await interaction.response.send_message("Reminder time cannot be more than 366 days.", ephemeral=True)
            return

        message = message.strip()
        if not message:
            await interaction.response.send_message("Reminder message cannot be empty.", ephemeral=True)
            return
        if len(message) > MAX_MESSAGE_LENGTH:
            await interaction.response.send_message(
                f"Reminder message cannot be longer than {MAX_MESSAGE_LENGTH} characters.",
                ephemeral=True,
            )
            return

        now = int(time.time())
        reminder = {
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "creator_id": interaction.user.id,
            "target_id": target.id,
            "message": message,
            "created_at": now,
            "due_at": now + duration,
        }
        reminder_id = await self._store_reminder(reminder)
        due_at = datetime.fromtimestamp(reminder["due_at"], tz=timezone.utc)
        due_text = discord.utils.format_dt(due_at, style="R")
        target_text = "you" if target.id == interaction.user.id else target.mention

        await interaction.response.send_message(
            f"Reminder #{reminder_id} set for {target_text} {due_text}.",
            allowed_mentions=discord.AllowedMentions(users=True),
            ephemeral=True,
        )

    async def _store_reminder(self, reminder: dict[str, Any]) -> int:
        async with self._config_lock:
            reminder_id = int(await self.config.next_id())
            await self.config.next_id.set(reminder_id + 1)
            async with self.config.reminders() as reminders:
                reminders[str(reminder_id)] = reminder
        return reminder_id

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

        for reminder_id, reminder in due_reminders:
            await self._deliver_reminder(reminder_id, reminder)

    async def _deliver_reminder(self, reminder_id: str, reminder: dict[str, Any]):
        target = await self._get_user(int(reminder["target_id"]))
        creator = await self._get_user(int(reminder["creator_id"]))
        message = self._sanitize_message(str(reminder["message"]))
        embed = self._build_reminder_embed(reminder_id, reminder, message, creator)
        content = target.mention if target else f"<@{reminder['target_id']}>"
        allowed_mentions = discord.AllowedMentions(users=True)

        channel = self.bot.get_channel(int(reminder["channel_id"]))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(reminder["channel_id"]))
            except (discord.HTTPException, discord.NotFound):
                channel = None

        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(content, embed=embed, allowed_mentions=allowed_mentions)
                return
            except discord.HTTPException:
                pass

        if target:
            try:
                await target.send(embed=embed)
            except discord.HTTPException:
                pass

    def _build_reminder_embed(
        self,
        reminder_id: str,
        reminder: dict[str, Any],
        message: str,
        creator: discord.User | discord.Member | None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"Reminder #{reminder_id}",
            description=message,
            color=DEFAULT_COLOR,
        )
        if creator:
            embed.set_footer(text=f"Set by {creator}")
        return embed

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
    def _sanitize_message(message: str) -> str:
        def replace(match: re.Match) -> str:
            if match.group(1):
                return f"@\u200b{match.group(1)}"
            return match.group(0).replace("<@&", "<@\u200b&")

        return MENTION_RE.sub(replace, message)
