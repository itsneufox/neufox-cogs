"""AutoMod: spam detection, strike escalation and server lockdowns."""

from __future__ import annotations

import time
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from redbot.core import commands

from .common import DEFAULT_COLOR


URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
# Each filter has its own on switch (command spam uses its threshold) and its own exempt channels and roles.
FILTER_NAMES = ("rate", "command", "mention", "caps", "link")
PROFILE_KEYS = (
    "warn_threshold",
    "timeout_threshold",
    "long_timeout_threshold",
    "short_timeout_minutes",
    "long_timeout_minutes",
    "kick_threshold",
    "kick_enabled",
    "ban_threshold",
    "ban_enabled",
    "ban_delete_message_days",
)


def parse_domains(text: str) -> tuple[str, ...]:
    """Turn "example.com, youtu.be" into lowercase domains, ignoring any scheme or leading dot."""
    domains = []
    for part in re.split(r"[\s,]+", text or ""):
        part = part.strip().lower()
        part = re.sub(r"^(https?://)?(www\.)?", "", part).strip("./")
        if part:
            domains.append(part.split("/")[0])
    return tuple(domains)


def link_domain(url: str) -> str:
    host = re.sub(r"^(https?://)?", "", url.lower()).split("/")[0].split("?")[0].split("#")[0]
    host = host.rsplit("@", 1)[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


class AutoModMixin:
    """Watches messages for spam and escalates strikes into punishments."""

    async def _set_command_spam_threshold(self, guild: discord.Guild, threshold: int):
        guild_cfg = self.automod_config.guild(guild)
        await guild_cfg.command_spam_threshold.set(threshold)
        await guild_cfg.command_spam_enabled.set(threshold > 0)

    async def _caps_ratio_percent(self, guild: discord.Guild) -> float:
        return round(await self.automod_config.guild(guild).caps_ratio() * 100, 4)

    @staticmethod
    def _validate_caps_percent(guild: discord.Guild, percent: float) -> str | None:
        return "Caps ratio must be above 0." if percent <= 0 else None

    async def _set_caps_ratio_percent(self, guild: discord.Guild, percent: float):
        await self.automod_config.guild(guild).caps_ratio.set((percent / 100) or 0.01)

    @commands.Cog.listener("on_message")
    async def _automod_on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if message.author is None or not isinstance(message.author, discord.Member):
            return
        if message.author.bot:
            return
        if message.author.guild_permissions.administrator:
            return

        settings = await self.automod_config.guild(message.guild).all()
        if not settings["antiabuse_enabled"]:
            return
        if self._is_ignored_location(message, settings):
            return
        if message.channel.id == await self.honeypot_config.guild(message.guild).channel_id():
            return  # The honeypot handles its own channel.

        await self._ensure_lockdown_expired(message.guild)

        exempt_ids = set(settings["exempt_role_ids"])
        if any(role.id in exempt_ids for role in message.author.roles):
            return

        violations = await self._collect_violations(message, settings)
        if not violations:
            return

        if settings.get("delete_violations"):
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        await self._apply_penalty(message, violations)

    async def _automod_status_embed(self, guild: discord.Guild) -> discord.Embed:
        settings = await self.automod_config.guild(guild).all()
        lockdown_until = settings.get("lockdown_until", 0)
        now = time.time()

        embed = discord.Embed(title="AutoMod Protection", color=DEFAULT_COLOR)
        embed.add_field(name="Enabled", value="Yes" if settings["antiabuse_enabled"] else "No", inline=True)
        embed.add_field(name="Rate limit", value=f"{settings['rate_threshold']} per {settings['rate_window_seconds']}s", inline=True)
        embed.add_field(
            name="Command spam",
            value=(
                f"{'on' if settings['command_spam_enabled'] else 'off'} "
                f"({settings['command_spam_threshold']} per {settings['command_spam_window_seconds']}s)"
            ),
            inline=True,
        )
        embed.add_field(name="Mentions", value=f">{settings['mention_threshold']}", inline=True)
        embed.add_field(
            name="Caps",
            value=f">{settings['caps_ratio'] * 100:.0f}% uppercase ({settings['caps_min_length']} letters min; stretched-aware)",
            inline=True,
        )
        embed.add_field(name="Links", value=f">{settings['link_threshold']}", inline=True)
        embed.add_field(
            name="Punishment",
            value=(
                f"warn at {settings['warn_threshold']}, "
                f"timeout {settings['short_timeout_minutes']}m @ {settings['timeout_threshold']}, "
                f"timeout {settings['long_timeout_minutes']}m @ {settings['long_timeout_threshold']}, "
                f"kick {'on' if settings['kick_enabled'] else 'off'} @ {settings['kick_threshold']}, "
                f"ban {'on' if settings['ban_enabled'] else 'off'} @ {settings['ban_threshold']}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Auto Lockdown",
            value=(
                f"{'enabled' if settings['auto_lockdown_enabled'] else 'disabled'} • "
                f"trigger {settings['lockdown_threshold']} • "
                f"{settings['lockdown_minutes']}m"
            ),
            inline=False,
        )

        if lockdown_until and lockdown_until > now:
            embed.add_field(name="Lockdown active", value=f"ends <t:{int(lockdown_until)}:R>", inline=True)
        else:
            embed.add_field(name="Lockdown active", value="No", inline=True)

        log_channel = guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "Not set", inline=True)

        exempt_ids = settings["exempt_role_ids"]
        roles = [guild.get_role(role_id) for role_id in exempt_ids]
        roles = [role for role in roles if role is not None]
        exempt_text = ", ".join(role.mention for role in roles) if roles else "None"
        embed.add_field(name="Exempt roles", value=exempt_text, inline=False)
        ignored_channels = [guild.get_channel(channel_id) for channel_id in settings["ignored_channel_ids"]]
        ignored_channels = [channel for channel in ignored_channels if channel is not None]
        ignored_categories = [guild.get_channel(category_id) for category_id in settings["ignored_category_ids"]]
        ignored_categories = [category for category in ignored_categories if category is not None]
        ignored_text = []
        if ignored_channels:
            ignored_text.append("Channels: " + ", ".join(channel.mention for channel in ignored_channels))
        if ignored_categories:
            ignored_text.append("Categories: " + ", ".join(category.name for category in ignored_categories))
        embed.add_field(name="Ignored locations", value="\n".join(ignored_text) if ignored_text else "None", inline=False)
        return embed

    async def _send_lockdown_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return
        settings = await self.automod_config.guild(ctx.guild).all()
        lockdown_until = settings.get("lockdown_until", 0)
        now = int(time.time())

        if lockdown_until and lockdown_until > now:
            await ctx.send(f"Lockdown is active. Restores <t:{int(lockdown_until)}:R>.")
            return

        await ctx.send(
            "No lockdown currently active. "
            f"Auto-lockdown is {'enabled' if settings['auto_lockdown_enabled'] else 'disabled'}."
        )

    async def _collect_violations(self, message: discord.Message, settings: dict[str, Any]) -> list[str]:
        violations: list[str] = []

        if settings.get("rate_enabled", True) and not self._filter_exempt(message, settings, "rate"):
            if self._is_rate_violation(message, settings["rate_window_seconds"], settings["rate_threshold"]):
                violations.append("message rate")

        if not self._filter_exempt(message, settings, "command") and await self._is_command_spam_violation(message, settings):
            violations.append("command spam")

        if settings.get("mention_enabled", True) and not self._filter_exempt(message, settings, "mention"):
            mentions = self._count_mentions(message)
            if mentions > settings["mention_threshold"]:
                violations.append(f"mentions ({mentions})")

        if settings.get("caps_enabled", True) and not self._filter_exempt(message, settings, "caps"):
            if self._is_caps_spam(message.content, settings["caps_ratio"], settings["caps_min_length"]):
                violations.append("caps spam")

        if settings.get("link_enabled", True) and not self._filter_exempt(message, settings, "link"):
            links = self._count_links(message.content, parse_domains(settings.get("allowed_link_domains", "")))
            if links > settings["link_threshold"]:
                violations.append(f"links ({links})")

        return violations

    @staticmethod
    def _filter_exempt(message: discord.Message, settings: dict[str, Any], name: str) -> bool:
        """Whether this filter skips the message because of its channel, category or the author's roles."""
        channel_ids = set(settings.get(f"{name}_exempt_channel_ids") or [])
        if channel_ids:
            channel = message.channel
            parent = getattr(channel, "parent", None)
            candidates = {
                getattr(channel, "id", None),
                getattr(channel, "parent_id", None),
                getattr(getattr(channel, "category", None), "id", None),
                getattr(getattr(parent, "category", None), "id", None),
            }
            if candidates & channel_ids:
                return True
        role_ids = set(settings.get(f"{name}_exempt_role_ids") or [])
        return bool(role_ids) and any(role.id in role_ids for role in getattr(message.author, "roles", []))

    def _is_ignored_location(self, message: discord.Message, settings: dict[str, Any]) -> bool:
        channel_id = getattr(message.channel, "id", None)
        if channel_id in set(settings["ignored_channel_ids"]):
            return True

        category = getattr(message.channel, "category", None)
        if category is not None and category.id in set(settings["ignored_category_ids"]):
            return True

        parent = getattr(message.channel, "parent", None)
        parent_category = getattr(parent, "category", None)
        if parent_category is not None and parent_category.id in set(settings["ignored_category_ids"]):
            return True

        return False

    def _is_rate_violation(self, message: discord.Message, window_seconds: int, threshold: int) -> bool:
        now = time.time()
        key = (message.guild.id if message.guild else 0, message.author.id)
        bucket = self._message_buckets[key]
        bucket.append(now)

        while bucket and bucket[0] < now - window_seconds:
            bucket.popleft()

        if not bucket:
            del self._message_buckets[key]
            return False

        return len(bucket) > threshold

    async def _is_command_spam_violation(self, message: discord.Message, settings: dict[str, Any]) -> bool:
        if not settings["command_spam_enabled"]:
            return False
        if settings["command_spam_threshold"] <= 0:
            return False
        if not await self._looks_like_command(message):
            return False

        now = time.time()
        key = (message.guild.id if message.guild else 0, message.author.id)
        bucket = self._command_buckets[key]
        bucket.append(now)

        window_seconds = settings["command_spam_window_seconds"]
        while bucket and bucket[0] < now - window_seconds:
            bucket.popleft()

        if not bucket:
            del self._command_buckets[key]
            return False

        return len(bucket) > settings["command_spam_threshold"]

    async def _looks_like_command(self, message: discord.Message) -> bool:
        content = message.content or ""
        if not content:
            return False

        prefixes = []
        get_valid_prefixes = getattr(self.bot, "get_valid_prefixes", None)
        if get_valid_prefixes is not None:
            try:
                maybe_prefixes = get_valid_prefixes(message.guild)
                if hasattr(maybe_prefixes, "__await__"):
                    maybe_prefixes = await maybe_prefixes
                prefixes = list(maybe_prefixes)
            except Exception:
                prefixes = []

        if not prefixes:
            command_prefix = getattr(self.bot, "command_prefix", None)
            if isinstance(command_prefix, str):
                prefixes = [command_prefix]
            elif isinstance(command_prefix, (list, tuple)):
                prefixes = list(command_prefix)

        prefixes = [prefix for prefix in prefixes if isinstance(prefix, str) and prefix]
        if prefixes:
            return any(content.startswith(prefix) for prefix in prefixes)

        try:
            ctx = await self.bot.get_context(message)
            return bool(getattr(ctx, "valid", False))
        except Exception:
            return False

    @staticmethod
    def _count_mentions(message: discord.Message) -> int:
        count = len(message.mentions) + len(message.role_mentions)
        if message.mention_everyone:
            count += 1
        return count

    @staticmethod
    def _count_links(content: str, allowed_domains: tuple[str, ...] = ()) -> int:
        count = 0
        for match in URL_RE.finditer(content or ""):
            host = link_domain(match.group(0))
            if any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
                continue
            count += 1
        return count

    @staticmethod
    def _is_caps_spam(content: str, ratio: float, min_length: int) -> bool:
        letters = [char for char in (content or "") if char.isalpha()]
        if len(letters) < min_length:
            return False
        if ratio <= 0:
            return False

        collapsed_letters = AutoModMixin._collapse_repeated_letters(letters)
        repeated_letters = len(letters) - len(collapsed_letters)
        meaningful_min_length = max(min_length * 2, 24)
        if repeated_letters >= min_length and len(collapsed_letters) < meaningful_min_length:
            return False

        uppercase = sum(1 for char in letters if char.isupper())
        return (uppercase / len(letters)) >= ratio

    @staticmethod
    def _collapse_repeated_letters(letters: list[str]) -> list[str]:
        collapsed: list[str] = []
        previous = ""
        for char in letters:
            folded = char.casefold()
            if folded == previous:
                continue
            collapsed.append(char)
            previous = folded
        return collapsed

    async def _apply_penalty(self, message: discord.Message, violations: list[str]):
        member = message.author
        guild = message.guild
        if guild is None:
            return

        settings = await self.automod_config.guild(guild).all()
        profile = await self._resolve_member_profile(member, settings["role_punishment_profiles"])
        punish = {key: profile.get(key, settings[key]) for key in PROFILE_KEYS}

        member_cfg = self.automod_config.member(member)
        warning_count = await member_cfg.warning_count()
        last_violation = await member_cfg.last_violation_at()
        now = int(time.time())

        if settings["violation_decay_seconds"] and now - last_violation > settings["violation_decay_seconds"]:
            warning_count = 0

        warning_count += 1
        await member_cfg.warning_count.set(warning_count)
        await member_cfg.last_violation_at.set(now)

        last_action = await member_cfg.last_action_at()
        cooldown_active = settings["action_cooldown_seconds"] and now - last_action < settings["action_cooldown_seconds"]

        action = "warn"
        if punish["ban_enabled"] and punish["ban_threshold"] > 0 and warning_count >= punish["ban_threshold"]:
            action = "ban"
        elif punish["kick_enabled"] and punish["kick_threshold"] > 0 and warning_count >= punish["kick_threshold"]:
            action = "kick"
        elif warning_count >= punish["long_timeout_threshold"]:
            action = "long_timeout"
        elif warning_count >= punish["timeout_threshold"]:
            action = "short_timeout"
        do_lockdown = settings["auto_lockdown_enabled"] and warning_count >= settings["lockdown_threshold"]

        if action == "warn" and warning_count < punish["warn_threshold"]:
            await self._automod_notify(message, member, violations, f"strike recorded (count {warning_count})")
            await self._automod_log(guild, member, warning_count, violations, "strike recorded")
            return

        if action != "warn" and cooldown_active:
            await self._automod_notify(
                message, member, violations, f"strike recorded (count {warning_count}; action cooldown)"
            )
            await self._automod_log(guild, member, warning_count, violations, "strike recorded during action cooldown")
            return

        if action != "warn":
            reason = f"AutoMod: {', '.join(violations)}"
            if action in {"short_timeout", "long_timeout"}:
                prefix = "short" if action == "short_timeout" else "long"
                minutes = punish[f"{prefix}_timeout_minutes"]
                success = await self._automod_timeout(member, minutes, f"AutoMod {prefix} timeout")
                label, case_action, failed = f"{prefix} timeout ({minutes}m)", "AutoMod Timeout", "timeout action failed"
                log_label, duration = f"Timeout {minutes}m", f"{minutes} minute(s)"
            elif action == "kick":
                success = await self._automod_kick(member, reason="AutoMod auto kick")
                label, case_action, failed, log_label, duration = "kick", "AutoMod Kick", "kick action failed", "Kick", None
            else:
                success = await self._automod_ban(
                    member, delete_message_days=punish["ban_delete_message_days"], reason="AutoMod auto ban"
                )
                label, case_action, failed, log_label, duration = "ban", "AutoMod Ban", "ban action failed", "Ban", None

            if success:
                await self._automod_notify(message, member, violations, label)
                await self._automod_log(guild, member, warning_count, violations, log_label)
                if guild.me is not None:
                    await self._create_case(
                        guild, guild.me, member, case_action, reason,
                        duration=duration, extra=f"AutoMod strike #{warning_count}",
                    )
            else:
                await self._automod_notify(message, member, violations, failed)
            await member_cfg.last_action_at.set(now)
            if do_lockdown:
                await self._start_lockdown(guild, settings["lockdown_minutes"] * 60, "automatic")
            return

        if do_lockdown:
            duration = settings["lockdown_minutes"]
            if duration <= 0:
                await self._automod_notify(message, member, violations, "warning (lockdown disabled)")
                await member_cfg.last_action_at.set(now)
                return
            await self._start_lockdown(guild, duration * 60, "automatic")
            await self._automod_notify(message, member, violations, f"lockdown {duration}m")
            await member_cfg.last_action_at.set(now)
            await self._automod_log(guild, member, warning_count, violations, f"Lockdown {duration}m")
            return

        await self._automod_notify(message, member, violations, f"warning (count {warning_count})")
        await member_cfg.last_action_at.set(now)
        await self._automod_log(guild, member, warning_count, violations, "warning")

    async def _automod_notify(self, message: discord.Message, member: discord.Member, violations: list[str], action: str):
        channel = message.channel
        if channel is None:
            return
        guild_cfg = self.automod_config.guild(member.guild)
        if not await guild_cfg.notify_in_channel():
            return
        delete_after = await guild_cfg.notify_delete_after()

        reasons = ", ".join(violations)
        lines = [
            f"{member.mention} AutoMod action: **{action}**",
            f"Triggered by: {reasons}",
            self._message_context_line(message),
            f"AutoMod strikes: {await self.automod_config.member(member).warning_count()}.",
        ]
        lines = [line for line in lines if line]
        await channel.send(
            "\n".join(lines),
            allowed_mentions=discord.AllowedMentions(users=True),
            delete_after=delete_after or None,
        )

    @staticmethod
    def _message_context_line(message: discord.Message, limit: int = 220) -> str:
        content = " ".join((message.content or "").split())
        if not content:
            attachment_count = len(getattr(message, "attachments", []) or [])
            if not attachment_count:
                return ""
            content = f"[{attachment_count} attachment(s)]"

        content = discord.utils.escape_mentions(content)
        if len(content) > limit:
            content = content[: limit - 3].rstrip() + "..."

        jump_url = getattr(message, "jump_url", "")
        if jump_url:
            return f"Message: {content}\nJump: {jump_url}"
        return f"Message: {content}"

    async def _automod_log(self, guild: discord.Guild, member: discord.Member, warnings: int, violations: list[str], action: str):
        log_channel_id = await self.automod_config.guild(guild).log_channel_id()
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if log_channel is None:
            return

        embed = discord.Embed(title="AutoMod Log", color=DEFAULT_COLOR)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="AutoMod strikes", value=str(warnings), inline=True)
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(name="Reasons", value=", ".join(violations), inline=False)
        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _automod_timeout(self, member: discord.Member, minutes: int, reason: str) -> bool:
        if minutes <= 0:
            return False
        if member.guild is None:
            return False
        allowed, _ = await self._can_moderate_member(member.guild, member, "timeout")
        if not allowed:
            return False

        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        try:
            current = member.communication_disabled_until
            if current and current > until:
                return True
            await member.edit(communication_disabled_until=until, reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _automod_kick(self, member: discord.Member, reason: str) -> bool:
        if member.guild is None:
            return False
        allowed, _ = await self._can_moderate_member(member.guild, member, "kick")
        if not allowed:
            return False

        try:
            await member.kick(reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _automod_ban(self, member: discord.Member, delete_message_days: int, reason: str) -> bool:
        if member.guild is None:
            return False
        allowed, _ = await self._can_moderate_member(member.guild, member, "ban")
        if not allowed:
            return False

        try:
            await member.ban(delete_message_days=delete_message_days, reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    def _parse_role_profile_spec(self, spec: str, role: discord.Role) -> str | dict[str, Any]:
        if not spec:
            return {}

        valid_keys = {
            "warn_threshold": "warn_threshold",
            "warn": "warn_threshold",
            "timeout_threshold": "timeout_threshold",
            "timeout": "timeout_threshold",
            "long_timeout_threshold": "long_timeout_threshold",
            "long_timeout": "long_timeout_threshold",
            "short_timeout_minutes": "short_timeout_minutes",
            "short_timeout": "short_timeout_minutes",
            "long_timeout_minutes": "long_timeout_minutes",
            "kick_threshold": "kick_threshold",
            "kick": "kick_threshold",
            "ban_threshold": "ban_threshold",
            "ban": "ban_threshold",
            "kick_enabled": "kick_enabled",
            "ban_enabled": "ban_enabled",
            "ban_delete_message_days": "ban_delete_message_days",
            "ban_delete_days": "ban_delete_message_days",
        }

        parsed: dict[str, Any] = {}
        for token in spec.split():
            if "=" not in token:
                return f"Invalid format: `{token}`. Use key=value."
            key, raw_value = token.split("=", 1)
            norm_key = valid_keys.get(key.lower())
            if norm_key is None:
                return f"Unknown key: `{key}`."

            if norm_key in {"kick_enabled", "ban_enabled"}:
                value = self._parse_bool(raw_value)
                if value is None:
                    return (
                        f"Invalid boolean for `{key}`: `{raw_value}`. Use true/false."
                    )
                parsed[norm_key] = value
                continue

            try:
                value_int = int(raw_value)
            except ValueError:
                return f"Invalid integer for `{key}`: `{raw_value}`."

            if value_int < 0:
                return f"`{key}` must be zero or greater."
            if norm_key == "ban_delete_message_days" and value_int > 7:
                return f"`{key}` must be between 0 and 7."
            if norm_key in {"short_timeout_minutes", "long_timeout_minutes"} and value_int <= 0:
                return f"`{key}` must be greater than 0."
            parsed[norm_key] = value_int

            if not parsed:
                return f"No changes parsed for {role.mention}."

        if not self._validate_punish_thresholds(parsed):
            return "Configured role overrides conflict (e.g., non-increasing thresholds)."

        return parsed

    async def _resolve_member_profile(self, member: discord.Member, role_profiles: dict[str, Any]) -> dict[str, Any]:
        selected_profile: dict[str, Any] = {}
        selected_position = -1
        for role in member.roles:
            raw_profile = role_profiles.get(str(role.id))
            if raw_profile is None:
                raw_profile = role_profiles.get(role.id)
            if raw_profile is None:
                continue
            if not isinstance(raw_profile, dict):
                continue
            if role.position <= selected_position:
                continue
            selected_profile = dict(raw_profile)
            selected_position = role.position
        return selected_profile

    def _validate_punish_thresholds(self, values: dict[str, Any]) -> bool:
        warn_threshold = values.get("warn_threshold")
        timeout_threshold = values.get("timeout_threshold")
        long_timeout_threshold = values.get("long_timeout_threshold")
        kick_threshold = values.get("kick_threshold")
        ban_threshold = values.get("ban_threshold")

        if (
            warn_threshold is not None
            and timeout_threshold is not None
            and warn_threshold >= timeout_threshold
            and timeout_threshold >= 0
        ):
            return False

        if (
            timeout_threshold is not None
            and long_timeout_threshold is not None
            and timeout_threshold >= long_timeout_threshold
            and long_timeout_threshold >= 0
        ):
            return False

        if (
            long_timeout_threshold is not None
            and kick_threshold is not None
            and long_timeout_threshold >= kick_threshold
        ):
            return False

        if (
            kick_threshold is not None
            and ban_threshold is not None
            and kick_threshold >= ban_threshold
        ):
            return False

        return True

    def _parse_bool(self, value: str) -> bool | None:
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return None

    def _format_role_profile(self, profile: dict[str, Any]) -> str:
        if not profile:
            return "none"

        fields = []
        for key in (
            "warn_threshold",
            "timeout_threshold",
            "long_timeout_threshold",
            "short_timeout_minutes",
            "long_timeout_minutes",
            "kick_threshold",
            "kick_enabled",
            "ban_threshold",
            "ban_enabled",
            "ban_delete_message_days",
        ):
            if key not in profile:
                continue
            value = profile[key]
            fields.append(f"{key}={value}")
        return ", ".join(fields) if fields else "none"

    async def _start_lockdown(self, guild: discord.Guild, duration_seconds: int, trigger: str):
        lock = self._guild_locks[guild.id]
        async with lock:
            current_until = await self.automod_config.guild(guild).lockdown_until()
            now = time.time()
            now_plus = now + duration_seconds
            if current_until and current_until > now:
                now_plus = max(now_plus, current_until)

            bot_member = guild.me
            if bot_member is None or not bot_member.guild_permissions.manage_channels:
                return

            default_role = guild.default_role
            if default_role is None:
                return

            overrides: dict[str, bool | None] = {}
            for channel in list(guild.text_channels) + list(guild.threads):
                perms = channel.permissions_for(bot_member)
                if not perms.manage_channels:
                    continue

                overwrite = channel.overwrites_for(default_role)
                if overwrite.send_messages is False:
                    continue

                overrides[str(channel.id)] = overwrite.send_messages
                try:
                    await channel.set_permissions(default_role, send_messages=False, reason=f"AutoMod lockdown ({trigger})")
                except (discord.Forbidden, discord.HTTPException):
                    continue

            await self.automod_config.guild(guild).lockdown_overrides.set(overrides)
            await self.automod_config.guild(guild).lockdown_until.set(now_plus)

    async def _stop_lockdown(self, guild: discord.Guild, force: bool = False):
        lock = self._guild_locks[guild.id]
        async with lock:
            settings = await self.automod_config.guild(guild).all()
            overrides = settings.get("lockdown_overrides", {})
            if not overrides:
                await self.automod_config.guild(guild).lockdown_until.set(0)
                if not force:
                    return

            default_role = guild.default_role
            if default_role is None:
                await self.automod_config.guild(guild).lockdown_overrides.set({})
                await self.automod_config.guild(guild).lockdown_until.set(0)
                return

            for channel_id, before_send in overrides.items():
                channel = guild.get_channel(int(channel_id))
                if channel is None:
                    continue
                try:
                    if before_send is None:
                        await channel.set_permissions(default_role, send_messages=None)
                    else:
                        await channel.set_permissions(default_role, send_messages=before_send)
                except (discord.Forbidden, discord.HTTPException):
                    continue

            await self.automod_config.guild(guild).lockdown_overrides.set({})
            await self.automod_config.guild(guild).lockdown_until.set(0)

    async def _ensure_lockdown_expired(self, guild: discord.Guild):
        now = time.time()
        until = await self.automod_config.guild(guild).lockdown_until()
        if not until:
            return
        if now >= until:
            await self._stop_lockdown(guild)
