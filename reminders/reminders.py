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
DEFAULT_MAX_REMINDERS_PER_PERSON = 2
MENTION_RE = re.compile(r"@(everyone|here)|<@&\d+>")
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
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
        self.config.register_guild(
            reminder_limit_per_person=DEFAULT_MAX_REMINDERS_PER_PERSON,
            reminder_unlimited_role_id=None,
            reminder_protected_role_ids=[],
        )
        self._config_lock = asyncio.Lock()
        self._task = self.bot.loop.create_task(self._reminder_loop())

    def cog_unload(self):
        self._task.cancel()

    @commands.command(name="reminders", aliases=["reminderhelp", "remindhelp"])
    async def reminders_help(self, ctx: commands.Context):
        """Show reminder help."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Reminders Help",
            description="Schedule reminders for yourself or another member. Reminders are delivered by DM first, with channel fallback.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Prefix Commands",
            value="\n".join(
                [
                    f"`{prefix}remindme <when> <message>` - remind yourself",
                    f"`{prefix}remind <member> <when> <message>` - remind another member",
                    f"`{prefix}reminders` - show this help",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands",
            value="\n".join(
                [
                    "`/remindme when message` - remind yourself",
                    "`/remind user when message` - remind another member",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Management",
            value="\n".join(
                [
                    "`[p]remindlist [member]` - list active reminders you created (mods/admins can filter by member)",
                    "`[p]remindcancel <id>` - cancel a pending reminder",
                    "`[p]remindprotectedroles [roles...]` - show or replace roles that are protected from commoners",
                    "`[p]reminderlimit [n]` - show or set per-target cap (0 = unlimited)",
                    "`[p]reminderunlimitedrole [role]` - set role threshold for bypassing cap (omit role to clear)",
                ]
            ),
            inline=False,
        )
        embed.add_field(
            name="Time Examples",
            value="`10s`, `15m`, `2h30m`, `1d`, `1w2d`",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command(name="reminderlimit")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def reminder_limit(self, ctx: commands.Context, max_pending: int | None = None):
        """Get or set the active reminder cap per target."""
        guild_config = self.config.guild(ctx.guild)
        if max_pending is None:
            current_limit = await guild_config.reminder_limit_per_person()
            unlimited_role_id = await guild_config.reminder_unlimited_role_id()
            unlimited_role = ctx.guild.get_role(unlimited_role_id) if unlimited_role_id else None
            role_label = unlimited_role.mention if unlimited_role is not None else "not configured"
            await ctx.send(
                f"Current reminder limit: `{current_limit}` per target member (0 = unlimited).\n"
                f"Unlimited role threshold: {role_label}."
            )
            return

        if max_pending < 0:
            await ctx.send("Reminder limit cannot be negative.")
            return

        await guild_config.reminder_limit_per_person.set(max_pending)
        await ctx.send(f"Reminder limit set to `{max_pending}` per target member.")

    @commands.command(name="reminderunlimitedrole")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def reminder_unlimited_role(self, ctx: commands.Context, role: discord.Role | None = None):
        """Set the role (and roles above it) that bypasses reminder limits."""
        if role is None:
            await self.config.guild(ctx.guild).reminder_unlimited_role_id.set(None)
            await ctx.send("Reminder unlimited role threshold cleared.")
            return

        await self.config.guild(ctx.guild).reminder_unlimited_role_id.set(role.id)
        await ctx.send(f"Reminder unlimited role threshold set to {role.mention} and roles above it.")

    @commands.command(name="remindprotectedroles")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def remind_protected_roles(self, ctx: commands.Context, *, roles: str | None = None):
        """Show or set roles that are protected from commoner reminders."""
        guild_config = self.config.guild(ctx.guild)

        if not roles or not roles.strip():
            protected_role_ids = await guild_config.reminder_protected_role_ids()
            protected_roles = [ctx.guild.get_role(role_id) for role_id in protected_role_ids]
            role_names = [role.mention for role in protected_roles if role is not None]
            if role_names:
                await ctx.send("Protected roles: " + ", ".join(role_names))
            else:
                await ctx.send("No protected roles configured.")
            return

        parsed_roles = self._parse_role_list(ctx.guild, roles)
        if not parsed_roles:
            await ctx.send(
                "I could not parse any roles from that input. "
                "Use role mentions, IDs, or comma-separated role names."
            )
            return

        await guild_config.reminder_protected_role_ids.set([role.id for role in parsed_roles])
        await ctx.send(
            f"Protected roles updated: {', '.join(role.mention for role in parsed_roles)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def _parse_role_list(guild: discord.Guild, raw: str) -> list[discord.Role]:
        role_ids = {int(role_id) for role_id in ROLE_MENTION_RE.findall(raw)}
        remaining = ROLE_MENTION_RE.sub(" ", raw).strip()
        if remaining:
            for chunk in re.split(r"[,\n;]+", remaining):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if chunk.isdigit():
                    role_ids.add(int(chunk))
                    continue

                if chunk.startswith("<@&") and chunk.endswith(">") and chunk[3:-1].isdigit():
                    role_ids.add(int(chunk[3:-1]))
                    continue

                if chunk.startswith("@"):
                    chunk = chunk[1:].strip()
                if chunk.startswith("&"):
                    chunk = chunk[1:].strip()
                if not chunk:
                    continue

                lowered = chunk.lower()
                matched = discord.utils.find(lambda r: r.name.lower() == lowered, guild.roles)
                if matched is None:
                    continue
                role_ids.add(matched.id)

        resolved: list[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                resolved.append(role)
        return resolved

    @commands.command(name="remindclearprotectedroles")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def remind_clear_protected_roles(self, ctx: commands.Context):
        """Clear reminder-protected roles."""
        await self.config.guild(ctx.guild).reminder_protected_role_ids.set([])
        await ctx.send("Reminder-protected roles cleared.")

    @commands.command(name="remindlist", aliases=["listreminders"])
    @commands.guild_only()
    async def remind_list(self, ctx: commands.Context, member: discord.Member | None = None):
        """List active reminders."""
        can_manage = await self._is_reminder_unbounded(ctx.author)
        if member is not None and not can_manage:
            await ctx.send("You can only list reminders you created.")
            return

        creator_id_filter = None if can_manage else ctx.author.id
        if can_manage and member is not None:
            creator_id_filter = member.id

        reminders = await self._get_active_reminders(ctx.guild.id, creator_id=creator_id_filter)
        if not reminders:
            await ctx.send("No active reminders found.")
            return

        lines: list[str] = []
        for reminder_id, reminder in reminders[:20]:
            target = f"<@{reminder['target_id']}>"
            due_at = datetime.fromtimestamp(int(reminder["due_at"]), tz=timezone.utc)
            message = self._sanitize_message(str(reminder["message"]))
            if len(message) > 80:
                message = f"{message[:77]}..."
            if can_manage:
                creator = f"<@{reminder['creator_id']}>"
                line = f"`{reminder_id}` • {target} by {creator} • {message} • due {discord.utils.format_dt(due_at, style='R')}"
            else:
                line = f"`{reminder_id}` • {target} • {message} • due {discord.utils.format_dt(due_at, style='R')}"
            lines.append(line)

        if len(reminders) > 20:
            lines.append(f"+ {len(reminders) - 20} more pending reminder(s).")

        embed = discord.Embed(
            title="Active Reminders",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Showing up to {min(len(reminders), 20)} of {len(reminders)} reminders.")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="remindcancel", aliases=["cancelreminder"])
    @commands.guild_only()
    async def remind_cancel(self, ctx: commands.Context, reminder_id: int):
        """Cancel a pending reminder by ID."""
        async with self.config.reminders() as reminders:
            raw = reminders.get(str(reminder_id))
            if raw is None or int(raw.get("guild_id") or 0) != ctx.guild.id:
                await ctx.send("No active reminder with that ID in this server.")
                return

            if not await self._can_manage_existing_reminder(ctx.author, raw):
                await ctx.send("You can only cancel reminders you created or reminders for you.")
                return

            reminders.pop(str(reminder_id), None)

        await ctx.send(f"Cancelled reminder `{reminder_id}`.")

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
        if (
            isinstance(author, discord.Member)
            and isinstance(target, discord.Member)
            and target.id != author.id
            and not await self._can_send_to_target(source, author, target)
        ):
            return

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

    async def _can_send_to_target(
        self,
        source: commands.Context | discord.Interaction,
        author: discord.Member,
        target: discord.Member,
    ) -> bool:
        if target.guild_permissions.administrator and not await self._is_reminder_unbounded(author):
            await self._send_error(source, "You cannot create reminders for an administrator.")
            return False

        if not await self._is_reminder_unbounded(author) and await self._is_target_protected(target.guild, target):
            await self._send_error(
                source,
                f"{target.mention} is protected from reminders made by common members. "
                "Only staff+ can remind them.",
            )
            return False

        if await self._is_reminder_unbounded(author):
            return True

        guild = target.guild
        if guild is None:
            return True

        limit = await self.config.guild(guild).reminder_limit_per_person()
        if limit <= 0:
            return True

        active_reminders = await self._count_active_reminders_for_target(target.id, guild.id)
        if active_reminders >= limit:
            prefix = await self._command_prefix(source)
            await self._send_error(
                source,
                f"{target.mention} already has {active_reminders} active reminders."
                f" Limit is {limit}. Cancel one with `{prefix}remindcancel` or wait for reminders to fire.",
            )
            return False

        return True

    async def _count_active_reminders_for_target(self, target_id: int, guild_id: int) -> int:
        now = int(time.time())
        count = 0
        async with self.config.reminders() as reminders:
            for reminder in reminders.values():
                if int(reminder.get("target_id", -1)) != target_id:
                    continue
                if int(reminder.get("guild_id", 0)) != guild_id:
                    continue
                if int(reminder.get("due_at", 0)) <= now:
                    continue
                count += 1
        return count

    async def _can_manage_existing_reminder(self, actor: discord.User | discord.Member, reminder: dict[str, Any]) -> bool:
        creator_id = int(reminder.get("creator_id", -1))
        target_id = int(reminder.get("target_id", -1))
        if actor.id in (creator_id, target_id):
            return True
        return await self._is_reminder_unbounded(actor)

    async def _is_reminder_unbounded(self, actor: discord.User | discord.Member | None) -> bool:
        if not isinstance(actor, discord.Member):
            return False

        if actor.guild_permissions.administrator:
            return True

        guild = actor.guild
        if guild is None:
            return False

        role_id = await self.config.guild(guild).reminder_unlimited_role_id()
        if role_id is None:
            return False

        threshold_role = guild.get_role(role_id)
        if threshold_role is None:
            return False

        return actor.top_role >= threshold_role

    async def _is_target_protected(self, guild: discord.Guild, target: discord.Member) -> bool:
        protected_role_ids = set(await self.config.guild(guild).reminder_protected_role_ids())
        if not protected_role_ids:
            return False
        return any(role.id in protected_role_ids for role in target.roles)

    async def _get_active_reminders(
        self,
        guild_id: int,
        creator_id: int | None = None,
        target_id: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        now = int(time.time())
        reminders: list[tuple[str, dict[str, Any]]] = []
        async with self.config.reminders() as storage:
            for reminder_id, reminder in storage.items():
                if int(reminder.get("guild_id", 0)) != guild_id:
                    continue
                if creator_id is not None and int(reminder.get("creator_id", -1)) != creator_id:
                    continue
                if target_id is not None and int(reminder.get("target_id", -1)) != target_id:
                    continue
                if int(reminder.get("due_at", 0)) <= now:
                    continue
                reminders.append((reminder_id, dict(reminder)))
        reminders.sort(key=lambda item: int(item[1].get("due_at", 0)))
        return reminders

    @staticmethod
    async def _command_prefix(source: commands.Context | discord.Interaction) -> str:
        if isinstance(source, commands.Context):
            return source.clean_prefix
        return "[p]"

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
