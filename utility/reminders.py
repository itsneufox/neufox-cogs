"""/remind: reminders for yourself or another member, delivered by DM with channel fallback."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import discord
from redbot.core import Config, app_commands, commands

from .layouts import NO_PINGS, card, channel_allowed


CONFIG_ID = 591743820
CONFIG_NAME = "Reminders"  # The old standalone cog's class name, which keys its stored data.
CHECK_INTERVAL_SECONDS = 30
MAX_REMINDER_SECONDS = 366 * 24 * 60 * 60
MAX_MESSAGE_LENGTH = 1000
MAX_LISTED = 20
DEFAULT_MAX_REMINDERS_PER_PERSON = 2
MENTION_RE = re.compile(r"@(everyone|here)|<@&\d+>")
TIME_PART_RE = re.compile(r"(\d+)\s*([wdhms])", re.IGNORECASE)
TIME_UNITS = {"w": 7 * 24 * 60 * 60, "d": 24 * 60 * 60, "h": 60 * 60, "m": 60, "s": 1}
WHEN_HELP = "When, like 10m, 2h30m, 1d, or 1w2d."
REMINDER_GUILD_DEFAULTS: dict[str, Any] = {
    "reminder_limit_per_person": DEFAULT_MAX_REMINDERS_PER_PERSON,
    "reminder_unlimited_role_id": None,
    "reminder_protected_role_ids": [],
    "reminders_enabled": True,
    "reminder_channels": [],
    "reminder_max_days": MAX_REMINDER_SECONDS // 86400,
    "reminder_min_seconds": 0,
    "reminder_max_length": MAX_MESSAGE_LENGTH,
    "reminder_allow_others": True,
    "reminder_creator_limit": 0,
    "reminder_delivery": "dm",
}
log = logging.getLogger("red.neufox.utility.reminders")


def parse_duration(value: str) -> int | None:
    cleaned = value.lower().strip()
    if cleaned.startswith("in "):
        cleaned = cleaned[3:].strip()
    if not cleaned:
        return None
    total, consumed = 0, ""
    for amount, unit in TIME_PART_RE.findall(cleaned):
        total += int(amount) * TIME_UNITS[unit.lower()]
        consumed += f"{amount}{unit}"
    if not total or consumed.lower() != re.sub(r"\s+", "", cleaned):
        return None
    return total


def format_duration(seconds: int) -> str:
    remaining, parts = int(seconds), []
    for label, unit in (("week", "w"), ("day", "d"), ("hour", "h"), ("minute", "m"), ("second", "s")):
        amount, remaining = divmod(remaining, TIME_UNITS[unit])
        if amount:
            parts.append(f"{amount} {label}{'s' if amount != 1 else ''}")
        if len(parts) == 2:
            break
    return " ".join(parts) if parts else "0 seconds"


def sanitize_message(message: str) -> str:
    def replace(match: re.Match) -> str:
        if match.group(1):
            return f"@​{match.group(1)}"
        return match.group(0).replace("<@&", "<@​&")

    return MENTION_RE.sub(replace, message)


def reminder_text(reminder: dict[str, Any], target: discord.abc.User | None) -> str:
    target_text = target.mention if target else f"<@{reminder['target_id']}>"
    created_at = datetime.fromtimestamp(int(reminder["created_at"]), tz=timezone.utc)
    ago = format_duration(max(1, int(reminder["due_at"]) - int(reminder["created_at"])))
    location = reminder.get("source_location") or "dms"
    source_url = reminder.get("source_url")
    source = f"[original message]({source_url})" if source_url else "original message"
    return (
        f":bell: Reminder! :bell:\n"
        f"From {ago} ago:\n\n"
        f"{target_text} {sanitize_message(str(reminder['message']))}\n\n"
        f"{source} • {created_at.strftime('%-d %B %Y at %H:%M')} on {location}"
    )


class RemindersMixin:
    def _reminders_init(self):
        self.reminders_config = Config.get_conf(
            None, identifier=CONFIG_ID, cog_name=CONFIG_NAME, force_registration=True
        )
        self.reminders_config.register_global(reminders={}, next_id=1)
        self.reminders_config.register_guild(**REMINDER_GUILD_DEFAULTS)
        self._reminders_lock = asyncio.Lock()
        self._reminders_task = asyncio.create_task(self._reminder_loop())

    def _reminders_unload(self):
        self._reminders_task.cancel()

    def _reminders_settings(self) -> list[dict[str, Any]]:
        def bind(key):
            return {
                "get": lambda guild: self.reminders_config.guild(guild).get_attr(key)(),
                "set": lambda guild, value: self.reminders_config.guild(guild).get_attr(key).set(value),
            }

        return [
            {
                "key": "reminders_enabled",
                "label": "Enabled",
                "type": "bool",
                "section": "Reminders",
                "help": "Allow new reminders in this server. Pending ones are still delivered.",
                **bind("reminders_enabled"),
            },
            {
                "key": "reminder_channels",
                "label": "Allowed channels",
                "type": "channels",
                "section": "Reminders",
                "channel_types": ["text", "news", "voice", "forum", "stage", "category"],
                "empty": "Every channel",
                "help": "Only allow new reminders in these channels or categories. Empty allows every channel.",
                **bind("reminder_channels"),
            },
            {
                "key": "reminder_max_days",
                "label": "Longest reminder",
                "type": "int",
                "min": 1,
                "max": 366,
                "unit": "days",
                "section": "Reminders",
                "help": "How far ahead a reminder can be set.",
                **bind("reminder_max_days"),
            },
            {
                "key": "reminder_min_seconds",
                "label": "Shortest reminder",
                "type": "int",
                "min": 0,
                "max": 86400,
                "unit": "seconds",
                "section": "Reminders",
                "help": "Reminders must be at least this far ahead. 0 means no minimum.",
                **bind("reminder_min_seconds"),
            },
            {
                "key": "reminder_max_length",
                "label": "Longest message",
                "type": "int",
                "min": 10,
                "max": MAX_MESSAGE_LENGTH,
                "unit": "characters",
                "section": "Reminders",
                "help": "Maximum length of a reminder's text.",
                **bind("reminder_max_length"),
            },
            {
                "key": "reminder_allow_others",
                "label": "Members can remind others",
                "type": "bool",
                "section": "Reminders",
                "help": "Let non-staff use /remind member. Staff always can.",
                **bind("reminder_allow_others"),
            },
            {
                "key": "reminder_creator_limit",
                "label": "Limit per creator",
                "type": "int",
                "min": 0,
                "max": 1000,
                "section": "Reminders",
                "help": "Active reminders a non-staff member can create. 0 means unlimited.",
                **bind("reminder_creator_limit"),
            },
            {
                "key": "reminder_delivery",
                "label": "Delivery",
                "type": "choice",
                "choices": [
                    {"value": "dm", "label": "DM, then the original channel"},
                    {"value": "channel", "label": "Original channel, then DM"},
                ],
                "section": "Reminders",
                "help": "Where reminders are delivered first.",
                **bind("reminder_delivery"),
            },
            {
                "key": "reminder_limit_per_person",
                "label": "Limit per person",
                "type": "int",
                "min": 0,
                "max": 1000,
                "section": "Reminders",
                "help": "Active reminders allowed per target member. 0 means unlimited.",
                **bind("reminder_limit_per_person"),
            },
            {
                "key": "reminder_unlimited_role_id",
                "label": "Unlimited role",
                "type": "role",
                "section": "Reminders",
                "help": "This role and the roles above it bypass the limit and protections.",
                **bind("reminder_unlimited_role_id"),
            },
            {
                "key": "reminder_protected_role_ids",
                "label": "Protected roles",
                "type": "roles",
                "section": "Reminders",
                "help": "Members with these roles cannot be reminded by non-staff.",
                **bind("reminder_protected_role_ids"),
            },
        ]

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Delete every reminder the user created or is the target of."""
        async with self._reminders_lock:
            async with self.reminders_config.reminders() as reminders:
                for reminder_id, reminder in list(reminders.items()):
                    if user_id in (int(reminder.get("creator_id", -1)), int(reminder.get("target_id", -1))):
                        reminders.pop(reminder_id, None)

    # Commands

    @commands.hybrid_group(name="remind")
    async def remind(self, ctx: commands.Context):
        """Schedule reminders for yourself or another member."""

    @remind.command(name="me")
    @app_commands.describe(when=WHEN_HELP, message="What to remind you about.")
    async def remind_me(self, ctx: commands.Context, when: str, *, message: str):
        """Remind yourself about something."""
        await self._create_reminder(ctx, ctx.author, when, message)

    @remind.command(name="member")
    @commands.guild_only()
    @app_commands.describe(member="The member to remind.", when=WHEN_HELP, message="What to remind them about.")
    async def remind_member(self, ctx: commands.Context, member: discord.Member, when: str, *, message: str):
        """Remind another member about something."""
        await self._create_reminder(ctx, member, when, message)

    @remind.command(name="list")
    @commands.guild_only()
    @app_commands.describe(member="Staff only: list reminders created by this member.")
    async def remind_list(self, ctx: commands.Context, member: discord.Member | None = None):
        """List active reminders you created, or anyone's if you are staff."""
        error, view = await self._reminder_list(ctx.guild, ctx.author, member)
        if view is None:
            await ctx.send(error, ephemeral=True)
        else:
            await ctx.send(view=view, ephemeral=True, allowed_mentions=NO_PINGS)

    @remind.command(name="cancel")
    @commands.guild_only()
    @app_commands.describe(reminder_id="The ID shown by /remind list.")
    async def remind_cancel(self, ctx: commands.Context, reminder_id: int):
        """Cancel a pending reminder by ID."""
        await ctx.send(await self._cancel_reminder(ctx.guild, ctx.author, reminder_id), ephemeral=True)

    # Listing and cancelling

    async def _reminder_list(
        self,
        guild: discord.Guild,
        actor: discord.abc.User,
        member: discord.Member | None,
    ) -> tuple[str, discord.ui.LayoutView | None]:
        can_manage = await self._is_reminder_unbounded(actor)
        if member is not None and not can_manage:
            return "You can only list reminders you created.", None

        creator_id = member.id if can_manage and member is not None else None if can_manage else actor.id
        reminders = await self._get_active_reminders(guild.id, creator_id=creator_id)
        if not reminders:
            return "No active reminders found.", None

        lines = []
        for reminder_id, reminder in reminders[:MAX_LISTED]:
            due = discord.utils.format_dt(datetime.fromtimestamp(int(reminder["due_at"]), tz=timezone.utc), style="R")
            message = sanitize_message(str(reminder["message"]))
            if len(message) > 80:
                message = f"{message[:77]}..."
            who = f"<@{reminder['target_id']}>"
            if can_manage:
                who += f" by <@{reminder['creator_id']}>"
            lines.append(f"`{reminder_id}` • {who} • {message} • due {due}")
        if len(reminders) > MAX_LISTED:
            lines.append(f"+ {len(reminders) - MAX_LISTED} more pending reminder(s).")

        embed = discord.Embed(title="Active Reminders", description="\n".join(lines), color=discord.Color.blurple())
        embed.set_footer(text=f"Showing up to {min(len(reminders), MAX_LISTED)} of {len(reminders)} reminders.")
        return "", card(embed)

    async def _cancel_reminder(self, guild: discord.Guild, actor: discord.abc.User, reminder_id: int) -> str:
        async with self.reminders_config.reminders() as reminders:
            raw = reminders.get(str(reminder_id))
            if raw is None or int(raw.get("guild_id") or 0) != guild.id:
                return "No active reminder with that ID in this server."
            if not await self._can_manage_existing_reminder(actor, raw):
                return "You can only cancel reminders you created or reminders for you."
            reminders.pop(str(reminder_id), None)
        return f"Cancelled reminder `{reminder_id}`."

    # Creating

    async def _create_reminder(self, ctx: commands.Context, target: discord.abc.User, when: str, message: str):
        error = await self._reminder_error(ctx, target, when, message)
        if error:
            await ctx.send(error, ephemeral=True)
            return

        duration = parse_duration(when)
        now = int(time.time())
        channel = ctx.channel
        reminder = {
            "guild_id": ctx.guild.id if ctx.guild else None,
            "channel_id": channel.id,
            "creator_id": ctx.author.id,
            "target_id": target.id,
            "message": message.strip(),
            "created_at": now,
            "due_at": now + duration,
            "source_url": None,
            "source_location": f"#{channel.name}" if isinstance(channel, discord.abc.GuildChannel) else "dms",
        }
        reminder_id = await self._store_reminder(reminder)

        due = discord.utils.format_dt(datetime.fromtimestamp(reminder["due_at"], tz=timezone.utc), style="f")
        who = "you" if target.id == ctx.author.id else target.mention
        sent = await ctx.send(
            f"I will remind {who} of that in {format_duration(duration)} ({due}).",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        source = sent if ctx.interaction else ctx.message
        if source is not None:
            await self._set_reminder_source_url(reminder_id, source.jump_url)

    async def _reminder_guild_settings(self, guild: discord.Guild | None) -> dict[str, Any]:
        if guild is None:
            return dict(REMINDER_GUILD_DEFAULTS)
        return {**REMINDER_GUILD_DEFAULTS, **await self.reminders_config.guild(guild).all()}

    async def _reminder_error(self, ctx: commands.Context, target: discord.abc.User, when: str, message: str) -> str | None:
        settings = await self._reminder_guild_settings(ctx.guild)
        if ctx.guild is not None:
            if not settings["reminders_enabled"]:
                return "Reminders are turned off in this server."
            allowed = settings["reminder_channels"]
            if not channel_allowed(ctx.channel, allowed):
                return "Reminders can only be set in " + ", ".join(f"<#{int(item)}>" for item in allowed[:10]) + "."
        duration = parse_duration(when)
        if duration is None:
            return "Use a duration like `10m`, `2h30m`, `1d`, or `1w2d`."
        max_days = max(1, min(366, int(settings["reminder_max_days"])))
        if duration > max_days * 24 * 60 * 60:
            return f"Reminder time cannot be more than {max_days} day{'s' if max_days != 1 else ''}."
        min_seconds = int(settings["reminder_min_seconds"])
        if duration < min_seconds:
            return f"Reminder time must be at least {format_duration(min_seconds)}."
        message = message.strip()
        if not message:
            return "Reminder message cannot be empty."
        max_length = max(1, min(MAX_MESSAGE_LENGTH, int(settings["reminder_max_length"])))
        if len(message) > max_length:
            return f"Reminder message cannot be longer than {max_length} characters."

        author = ctx.author
        if not isinstance(author, discord.Member):
            return None
        if await self._is_reminder_unbounded(author):
            return None
        creator_limit = int(settings["reminder_creator_limit"])
        if creator_limit > 0:
            created = len(await self._get_active_reminders(author.guild.id, creator_id=author.id))
            if created >= creator_limit:
                return f"You already have {created} active reminders. Limit is {creator_limit}."
        if not isinstance(target, discord.Member) or target.id == author.id:
            return None
        if not settings["reminder_allow_others"]:
            return "Only staff can remind other members in this server."
        if target.guild_permissions.administrator:
            return "You cannot create reminders for an administrator."
        protected = set(await self.reminders_config.guild(target.guild).reminder_protected_role_ids())
        if any(role.id in protected for role in target.roles):
            return f"{target.mention} is protected from reminders made by common members. Only staff+ can remind them."
        limit = await self.reminders_config.guild(target.guild).reminder_limit_per_person()
        if limit > 0:
            active = len(await self._get_active_reminders(target.guild.id, target_id=target.id))
            if active >= limit:
                return (
                    f"{target.mention} already has {active} active reminders. Limit is {limit}. "
                    "Cancel one with `/remind cancel` or wait for reminders to fire."
                )
        return None

    async def _store_reminder(self, reminder: dict[str, Any]) -> int:
        async with self._reminders_lock:
            reminder_id = int(await self.reminders_config.next_id())
            await self.reminders_config.next_id.set(reminder_id + 1)
            async with self.reminders_config.reminders() as reminders:
                reminders[str(reminder_id)] = reminder
        return reminder_id

    async def _set_reminder_source_url(self, reminder_id: int, source_url: str):
        async with self.reminders_config.reminders() as reminders:
            reminder = reminders.get(str(reminder_id))
            if reminder is not None:
                reminder["source_url"] = source_url

    # Permissions

    async def _can_manage_existing_reminder(self, actor: discord.abc.User, reminder: dict[str, Any]) -> bool:
        if actor.id in (int(reminder.get("creator_id", -1)), int(reminder.get("target_id", -1))):
            return True
        return await self._is_reminder_unbounded(actor)

    async def _is_reminder_unbounded(self, actor: discord.abc.User | None) -> bool:
        """Administrators and members at or above the unlimited role are staff for reminders."""
        if not isinstance(actor, discord.Member):
            return False
        if actor.guild_permissions.administrator:
            return True
        role_id = await self.reminders_config.guild(actor.guild).reminder_unlimited_role_id()
        threshold = actor.guild.get_role(role_id) if role_id else None
        return threshold is not None and actor.top_role >= threshold

    async def _get_active_reminders(
        self,
        guild_id: int,
        creator_id: int | None = None,
        target_id: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        now = int(time.time())
        found = []
        for reminder_id, reminder in (await self.reminders_config.reminders()).items():
            if int(reminder.get("guild_id") or 0) != guild_id or int(reminder.get("due_at", 0)) <= now:
                continue
            if creator_id is not None and int(reminder.get("creator_id", -1)) != creator_id:
                continue
            if target_id is not None and int(reminder.get("target_id", -1)) != target_id:
                continue
            found.append((reminder_id, dict(reminder)))
        found.sort(key=lambda item: int(item[1].get("due_at", 0)))
        return found

    # Delivery

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
        due = []
        async with self.reminders_config.reminders() as reminders:
            for reminder_id, reminder in list(reminders.items()):
                if int(reminder.get("due_at", 0)) <= now:
                    due.append(dict(reminder))
                    reminders.pop(reminder_id, None)
        for reminder in due:
            await self._deliver_reminder(reminder)

    async def _deliver_reminder(self, reminder: dict[str, Any]):
        target = self.bot.get_user(int(reminder["target_id"]))
        if target is None:
            try:
                target = await self.bot.fetch_user(int(reminder["target_id"]))
            except discord.HTTPException:
                target = None
        content = reminder_text(reminder, target)
        allowed_mentions = discord.AllowedMentions(users=True)

        async def via_dm() -> bool:
            if target is None:
                return False
            try:
                await target.send(content, allowed_mentions=allowed_mentions)
                return True
            except discord.HTTPException:
                return False

        async def via_channel() -> bool:
            channel = self.bot.get_channel(int(reminder["channel_id"]))
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(int(reminder["channel_id"]))
                except discord.HTTPException:
                    channel = None
            if not isinstance(channel, discord.abc.Messageable):
                return False
            try:
                await channel.send(content, allowed_mentions=allowed_mentions)
                return True
            except discord.HTTPException:
                return False

        order = (via_dm, via_channel)
        if await self._reminder_delivery_mode(reminder) == "channel":
            order = (via_channel, via_dm)
        for attempt in order:
            if await attempt():
                return

    async def _reminder_delivery_mode(self, reminder: dict[str, Any]) -> str:
        guild_id = reminder.get("guild_id")
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None:
            return "dm"
        return await self.reminders_config.guild(guild).reminder_delivery()
