from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.red()
URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)


class AntiAbuse(commands.Cog):
    """Automatic anti-abuse moderation helper."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=911223334)
        self.config.register_guild(
            antiabuse_enabled=True,
            log_channel_id=None,
            exempt_role_ids=[],
            # Detection
            rate_window_seconds=8,
            rate_threshold=7,
            command_spam_enabled=False,
            command_spam_window_seconds=10,
            command_spam_threshold=5,
            mention_threshold=8,
            caps_ratio=0.75,
            caps_min_length=12,
            link_threshold=4,
            ignored_channel_ids=[],
            ignored_category_ids=[],
            # Escalation logic
            violation_decay_seconds=900,
            action_cooldown_seconds=20,
            warn_threshold=2,
            timeout_threshold=7,
            long_timeout_threshold=13,
            short_timeout_minutes=3,
            long_timeout_minutes=15,
            kick_threshold=22,
            kick_enabled=False,
            ban_threshold=26,
            ban_enabled=False,
            ban_delete_message_days=0,
            # Lockdown
            auto_lockdown_enabled=False,
            lockdown_threshold=21,
            lockdown_minutes=10,
            lockdown_until=0,
            lockdown_overrides={},
            role_punishment_profiles={},
        )
        self.config.register_member(
            warning_count=0,
            last_violation_at=0,
            last_action_at=0,
        )

        self._message_buckets: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._command_buckets: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._guild_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    @commands.group(name="antiabuse", aliases=["abuse"], invoke_without_command=True)
    async def antiabuse(self, ctx: commands.Context):
        """Show anti-abuse settings and current status."""
        await self._send_status(ctx)

    @antiabuse.command(name="status")
    async def antiabuse_status(self, ctx: commands.Context):
        """Show anti-abuse status."""
        await self._send_status(ctx)

    @antiabuse.command(name="on")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_on(self, ctx: commands.Context):
        """Enable anti-abuse automation for this server."""
        await self.config.guild(ctx.guild).antiabuse_enabled.set(True)
        await ctx.send("Anti-abuse enforcement enabled.")

    @antiabuse.command(name="off")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_off(self, ctx: commands.Context):
        """Disable anti-abuse automation for this server."""
        await self.config.guild(ctx.guild).antiabuse_enabled.set(False)
        await ctx.send("Anti-abuse enforcement disabled.")

    @antiabuse.command(name="rate")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_rate(
        self,
        ctx: commands.Context,
        window_seconds: int | None = None,
        max_messages: int | None = None,
    ):
        """View or set message-rate protection."""
        guild_cfg = self.config.guild(ctx.guild)
        if window_seconds is None and max_messages is None:
            window = await guild_cfg.rate_window_seconds()
            max_count = await guild_cfg.rate_threshold()
            await ctx.send(f"Rate rule: max {max_count} messages per {window}s.")
            return

        if window_seconds is not None and window_seconds <= 0:
            await ctx.send("Rate window must be greater than 0.")
            return
        if max_messages is not None and max_messages <= 0:
            await ctx.send("Rate message limit must be greater than 0.")
            return

        if window_seconds is not None:
            await guild_cfg.rate_window_seconds.set(window_seconds)
        if max_messages is not None:
            await guild_cfg.rate_threshold.set(max_messages)
        await ctx.send("Rate protection updated.")

    @antiabuse.command(name="commandspam")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_commandspam(
        self,
        ctx: commands.Context,
        max_commands: int | None = None,
        window_seconds: int | None = None,
    ):
        """View or set command-spam protection."""
        guild_cfg = self.config.guild(ctx.guild)
        if max_commands is None and window_seconds is None:
            current = await guild_cfg.all()
            await ctx.send(
                "Command spam rule: "
                f"{'enabled' if current['command_spam_enabled'] else 'disabled'}, "
                f"max {current['command_spam_threshold']} commands per "
                f"{current['command_spam_window_seconds']}s."
            )
            return

        if max_commands is not None and max_commands < 0:
            await ctx.send("Command limit must be 0 or greater. Use 0 to disable command-spam checks.")
            return
        if window_seconds is not None and window_seconds <= 0:
            await ctx.send("Command-spam window must be greater than 0.")
            return

        if max_commands is not None:
            await guild_cfg.command_spam_threshold.set(max_commands)
            await guild_cfg.command_spam_enabled.set(max_commands > 0)
        if window_seconds is not None:
            await guild_cfg.command_spam_window_seconds.set(window_seconds)
        current = await guild_cfg.all()
        await ctx.send(
            "Command spam rule updated: "
            f"{'enabled' if current['command_spam_enabled'] else 'disabled'}, "
            f"max {current['command_spam_threshold']} commands per "
            f"{current['command_spam_window_seconds']}s."
        )

    @antiabuse.command(name="mentions")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_mentions(self, ctx: commands.Context, max_mentions: int | None = None):
        """View or set mention-spam protection."""
        guild_cfg = self.config.guild(ctx.guild)
        if max_mentions is None:
            current = await guild_cfg.mention_threshold()
            await ctx.send(f"Mention rule: max {current} mentions per message.")
            return

        if max_mentions < 0:
            await ctx.send("Mention limit must be 0 or greater.")
            return

        await guild_cfg.mention_threshold.set(max_mentions)
        await ctx.send(f"Mention rule updated: max {max_mentions} mentions per message.")

    @antiabuse.command(name="links")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_links(self, ctx: commands.Context, max_links: int | None = None):
        """View or set per-message link limit."""
        guild_cfg = self.config.guild(ctx.guild)
        if max_links is None:
            current = await guild_cfg.link_threshold()
            await ctx.send(f"Link rule: max {current} links per message.")
            return

        if max_links < 0:
            await ctx.send("Link limit must be 0 or greater.")
            return

        await guild_cfg.link_threshold.set(max_links)
        await ctx.send(f"Link rule updated: max {max_links} links per message.")

    @antiabuse.command(name="caps")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_caps(
        self,
        ctx: commands.Context,
        ratio_percent: float | None = None,
        min_length: int | None = None,
    ):
        """View or set all-caps protection.

        ratio_percent is optional and accepts 0-100.
        min_length is optional and is the minimum letter count before caps rule is checked.
        """
        guild_cfg = self.config.guild(ctx.guild)
        if ratio_percent is None and min_length is None:
            current_ratio = await guild_cfg.caps_ratio()
            current_min = await guild_cfg.caps_min_length()
            await ctx.send(
                f"Caps rule: uppercase ratio > {current_ratio:.2f} with at least {current_min} letters."
            )
            return

        if ratio_percent is not None:
            if ratio_percent <= 0:
                await ctx.send("Caps ratio must be above 0.")
                return
            if ratio_percent > 100:
                await ctx.send("Caps ratio percent cannot be above 100.")
                return
            ratio = ratio_percent / 100
            if ratio <= 0:
                ratio = 0.01
            await guild_cfg.caps_ratio.set(ratio)

        if min_length is not None:
            if min_length < 0:
                await ctx.send("Caps minimum length must be 0 or greater.")
                return
            await guild_cfg.caps_min_length.set(min_length)

        new_ratio = await guild_cfg.caps_ratio()
        new_min = await guild_cfg.caps_min_length()
        await ctx.send(f"Caps rule updated: ratio > {new_ratio * 100:.0f}% and min length {new_min}.")

    @antiabuse.command(name="punish")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_punish(
        self,
        ctx: commands.Context,
        warn_threshold: int | None = None,
        timeout_threshold: int | None = None,
        long_timeout_threshold: int | None = None,
        short_timeout_minutes: int | None = None,
        long_timeout_minutes: int | None = None,
        kick_threshold: int | None = None,
        ban_threshold: int | None = None,
        kick_enabled: bool | None = None,
        ban_enabled: bool | None = None,
        ban_delete_message_days: int | None = None,
    ):
        """View or set punishment thresholds and escalate actions.

        The warning counter increases on each detected violation.
        """
        guild_cfg = self.config.guild(ctx.guild)
        if (
            warn_threshold is None
            and timeout_threshold is None
            and long_timeout_threshold is None
            and short_timeout_minutes is None
            and long_timeout_minutes is None
            and kick_threshold is None
            and ban_threshold is None
            and kick_enabled is None
            and ban_enabled is None
            and ban_delete_message_days is None
        ):
            current = await guild_cfg.all()
            await ctx.send(
                "Current punishment thresholds: "
                f"warn at {current['warn_threshold']}, "
                f"timeout {current['short_timeout_minutes']}m @ {current['timeout_threshold']}, "
                f"timeout {current['long_timeout_minutes']}m @ {current['long_timeout_threshold']}."
                f" kick {'enabled' if current['kick_enabled'] else 'disabled'} @ {current['kick_threshold']}, "
                f"ban {'enabled' if current['ban_enabled'] else 'disabled'} @ {current['ban_threshold']} "
                f"(delete {current['ban_delete_message_days']}d)."
            )
            return

        if warn_threshold is not None and warn_threshold < 0:
            await ctx.send("warn_threshold cannot be negative.")
            return
        if timeout_threshold is not None and timeout_threshold < 0:
            await ctx.send("timeout_threshold cannot be negative.")
            return
        if long_timeout_threshold is not None and long_timeout_threshold < 0:
            await ctx.send("long_timeout_threshold cannot be negative.")
            return
        if short_timeout_minutes is not None and short_timeout_minutes <= 0:
            await ctx.send("short_timeout_minutes must be greater than 0.")
            return
        if long_timeout_minutes is not None and long_timeout_minutes <= 0:
            await ctx.send("long_timeout_minutes must be greater than 0.")
            return
        if kick_threshold is not None and kick_threshold < 0:
            await ctx.send("kick_threshold cannot be negative.")
            return
        if ban_threshold is not None and ban_threshold < 0:
            await ctx.send("ban_threshold cannot be negative.")
            return
        if ban_delete_message_days is not None:
            if ban_delete_message_days < 0 or ban_delete_message_days > 7:
                await ctx.send("ban_delete_message_days must be between 0 and 7.")
                return

        if warn_threshold is not None:
            await guild_cfg.warn_threshold.set(warn_threshold)
        if timeout_threshold is not None:
            await guild_cfg.timeout_threshold.set(timeout_threshold)
        if long_timeout_threshold is not None:
            await guild_cfg.long_timeout_threshold.set(long_timeout_threshold)
        if short_timeout_minutes is not None:
            await guild_cfg.short_timeout_minutes.set(short_timeout_minutes)
        if long_timeout_minutes is not None:
            await guild_cfg.long_timeout_minutes.set(long_timeout_minutes)
        if kick_threshold is not None:
            await guild_cfg.kick_threshold.set(kick_threshold)
        if ban_threshold is not None:
            await guild_cfg.ban_threshold.set(ban_threshold)
        if kick_enabled is not None:
            await guild_cfg.kick_enabled.set(kick_enabled)
        if ban_enabled is not None:
            await guild_cfg.ban_enabled.set(ban_enabled)
        if ban_delete_message_days is not None:
            await guild_cfg.ban_delete_message_days.set(ban_delete_message_days)

        thresholds = await guild_cfg.all()
        if thresholds["warn_threshold"] >= thresholds["timeout_threshold"]:
            await ctx.send(
                "Warning: `warn_threshold` is now greater than or equal to `timeout_threshold`, "
                "so short timeout escalation may never happen."
            )
        if thresholds["timeout_threshold"] >= thresholds["long_timeout_threshold"]:
            await ctx.send(
                "Warning: `timeout_threshold` is now greater than or equal to `long_timeout_threshold`, "
                "so long timeout escalation may never happen."
            )
        if thresholds["long_timeout_threshold"] >= thresholds["kick_threshold"] and thresholds["kick_enabled"]:
            await ctx.send(
                "Warning: `long_timeout_threshold` is now greater than or equal to `kick_threshold`, "
                "so kick escalation may never happen."
            )
        if thresholds["kick_threshold"] >= thresholds["ban_threshold"] and thresholds["ban_enabled"]:
            await ctx.send(
                "Warning: `kick_threshold` is now greater than or equal to `ban_threshold`, "
                "so ban escalation may never happen."
            )
        await ctx.send("Punishment values updated.")

    @antiabuse.group(name="role", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_role(self, ctx: commands.Context):
        """Show role-based anti-abuse profile overrides."""
        role_profiles = await self.config.guild(ctx.guild).role_punishment_profiles()
        if not role_profiles:
            await ctx.send("No role overrides configured.")
            return

        rows = []
        for role_id, profile in role_profiles.items():
            role = ctx.guild.get_role(int(role_id)) if isinstance(role_id, str) and role_id.isdigit() else None
            if role is None:
                role = ctx.guild.get_role(int(role_id)) if isinstance(role_id, int) else None
            role_name = role.mention if role is not None else f"Deleted role ({role_id})"
            rows.append(f"{role_name}: {self._format_role_profile(profile)}")
        await ctx.send("\n".join(rows))

    @antiabuse_role.command(name="set")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_role_set(
        self,
        ctx: commands.Context,
        role: discord.Role,
        *,
        spec: str | None = None,
    ):
        """Set role-specific punishment overrides.

        Provide values as key=value pairs separated by spaces.
        Keys:
        warn_threshold, timeout_threshold, long_timeout_threshold,
        short_timeout_minutes, long_timeout_minutes,
        kick_threshold, ban_threshold, kick_enabled, ban_enabled, ban_delete_message_days.
        Example:
        warn_threshold=3 kick_enabled=true ban=0
        """
        guild_cfg = self.config.guild(ctx.guild)
        profile = {}
        parsed = self._parse_role_profile_spec(spec or "", role=role)
        if isinstance(parsed, str):
            await ctx.send(parsed)
            return
        if not parsed:
            current = await guild_cfg.role_punishment_profiles()
            current_profile = current.get(str(role.id), {})
            if not current_profile:
                await ctx.send(f"No overrides configured for {role.mention}.")
                return
            await ctx.send(f"{role.mention}: {self._format_role_profile(current_profile)}")
            return

        current_profiles = await guild_cfg.role_punishment_profiles()
        updated = dict(current_profiles)
        updated[str(role.id)] = dict(current_profiles.get(str(role.id), {}))
        for key, value in parsed.items():
            if value is None:
                updated[str(role.id)].pop(key, None)
            else:
                updated[str(role.id)][key] = value

        if not updated[str(role.id)]:
            updated.pop(str(role.id), None)
            await guild_cfg.role_punishment_profiles.set(updated)
            await ctx.send(f"Override removed for {role.mention}.")
            return

        await guild_cfg.role_punishment_profiles.set(updated)
        await ctx.send(f"Role override updated for {role.mention}: {self._format_role_profile(updated[str(role.id)])}")

    @antiabuse_role.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_role_clear(self, ctx: commands.Context, role: discord.Role):
        """Clear role-specific anti-abuse punishment overrides."""
        guild_cfg = self.config.guild(ctx.guild)
        current_profiles = await guild_cfg.role_punishment_profiles()
        current_profiles.pop(str(role.id), None)
        await guild_cfg.role_punishment_profiles.set(current_profiles)
        await ctx.send(f"Role override cleared for {role.mention}.")

    @antiabuse.command(name="decay")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_decay(self, ctx: commands.Context, reset_seconds: int | None = None, action_cooldown_seconds: int | None = None):
        """View or tune cooldown values."""
        guild_cfg = self.config.guild(ctx.guild)
        if reset_seconds is None and action_cooldown_seconds is None:
            current = await guild_cfg.all()
            await ctx.send(
                "Decay: "
                f"{current['violation_decay_seconds']} seconds (warning counter reset), "
                f"action cooldown {current['action_cooldown_seconds']} seconds."
            )
            return

        if reset_seconds is not None:
            if reset_seconds < 0:
                await ctx.send("violation_decay_seconds must be 0 or greater.")
                return
            await guild_cfg.violation_decay_seconds.set(reset_seconds)
        if action_cooldown_seconds is not None:
            if action_cooldown_seconds < 0:
                await ctx.send("action_cooldown_seconds must be 0 or greater.")
                return
            await guild_cfg.action_cooldown_seconds.set(action_cooldown_seconds)
        await ctx.send("Cooldown values updated.")

    @antiabuse.group(name="exempt", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_exempt(self, ctx: commands.Context):
        """View exempt roles for anti-abuse checks."""
        exempt_ids = await self.config.guild(ctx.guild).exempt_role_ids()
        roles = [ctx.guild.get_role(role_id) for role_id in exempt_ids]
        roles = [role for role in roles if role is not None]
        if roles:
            await ctx.send("Exempt roles: " + ", ".join(role.mention for role in roles))
        else:
            await ctx.send("No exempt roles configured.")

    @antiabuse_exempt.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_exempt_add(self, ctx: commands.Context, role: discord.Role):
        """Add a role that bypasses anti-abuse checks."""
        current = set(await self.config.guild(ctx.guild).exempt_role_ids())
        if role.id in current:
            await ctx.send(f"{role.mention} is already exempt.")
            return
        current.add(role.id)
        await self.config.guild(ctx.guild).exempt_role_ids.set(sorted(current))
        await ctx.send(f"{role.mention} added to exempt role list.")

    @antiabuse_exempt.command(name="remove")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_exempt_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from anti-abuse exemption."""
        current = set(await self.config.guild(ctx.guild).exempt_role_ids())
        if role.id not in current:
            await ctx.send(f"{role.mention} is not exempt.")
            return
        current.remove(role.id)
        await self.config.guild(ctx.guild).exempt_role_ids.set(sorted(current))
        await ctx.send(f"{role.mention} removed from exempt role list.")

    @antiabuse.command(name="logchannel")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_logchannel(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """Set or clear log channel for anti-abuse actions."""
        channel_id = None if channel is None else channel.id
        await self.config.guild(ctx.guild).log_channel_id.set(channel_id)
        if channel is None:
            await ctx.send("Anti-abuse log channel cleared.")
        else:
            await ctx.send(f"Anti-abuse actions will be logged in {channel.mention}.")

    @antiabuse.command(name="ignorechannel")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_ignorechannel(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """Toggle a channel ignored by anti-abuse checks."""
        channel = channel or ctx.channel
        ignored = set(await self.config.guild(ctx.guild).ignored_channel_ids())
        if channel.id in ignored:
            ignored.remove(channel.id)
            await self.config.guild(ctx.guild).ignored_channel_ids.set(sorted(ignored))
            await ctx.send(f"{channel.mention} is no longer ignored by anti-abuse.")
            return

        ignored.add(channel.id)
        await self.config.guild(ctx.guild).ignored_channel_ids.set(sorted(ignored))
        await ctx.send(f"{channel.mention} is now ignored by anti-abuse.")

    @antiabuse.command(name="ignorecategory")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_ignorecategory(self, ctx: commands.Context, category: discord.CategoryChannel):
        """Toggle a category ignored by anti-abuse checks."""
        ignored = set(await self.config.guild(ctx.guild).ignored_category_ids())
        if category.id in ignored:
            ignored.remove(category.id)
            await self.config.guild(ctx.guild).ignored_category_ids.set(sorted(ignored))
            await ctx.send(f"{category.name} is no longer ignored by anti-abuse.")
            return

        ignored.add(category.id)
        await self.config.guild(ctx.guild).ignored_category_ids.set(sorted(ignored))
        await ctx.send(f"{category.name} is now ignored by anti-abuse.")

    @antiabuse.group(name="lockdown", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_lockdown(self, ctx: commands.Context):
        """Manage guild lockdown settings and state."""
        await self._send_lockdown_status(ctx)

    @antiabuse_lockdown.command(name="start")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_lockdown_start(self, ctx: commands.Context, minutes: int | None = None):
        """Start a manual lockdown."""
        duration = minutes if minutes is not None else await self.config.guild(ctx.guild).lockdown_minutes()
        if duration <= 0:
            await ctx.send("Lockdown duration must be positive.")
            return
        await self._start_lockdown(ctx.guild, duration * 60, "manual")
        await ctx.send(f"Guild lockdown started for {duration} minute(s).")

    @antiabuse_lockdown.command(name="stop")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_lockdown_stop(self, ctx: commands.Context):
        """Stop any active anti-abuse lockdown."""
        await self._stop_lockdown(ctx.guild, force=True)
        await ctx.send("Active anti-abuse lockdown stopped and permissions restored where possible.")

    @antiabuse_lockdown.command(name="auto")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_lockdown_auto(self, ctx: commands.Context, enabled: bool, threshold: int | None = None, minutes: int | None = None):
        """Enable/disable automatic lockdown and tune thresholds."""
        if threshold is not None and threshold < 0:
            await ctx.send("lockdown threshold must be 0 or greater.")
            return
        if minutes is not None and minutes <= 0:
            await ctx.send("lockdown duration must be positive.")
            return

        guild_cfg = self.config.guild(ctx.guild)
        await guild_cfg.auto_lockdown_enabled.set(enabled)
        if threshold is not None:
            await guild_cfg.lockdown_threshold.set(threshold)
        if minutes is not None:
            await guild_cfg.lockdown_minutes.set(minutes)

        state = "enabled" if enabled else "disabled"
        current = await guild_cfg.all()
        await ctx.send(
            f"Auto-lockdown {state}. Trigger: {current['lockdown_threshold']} warnings, "
            f"duration: {current['lockdown_minutes']} minute(s)."
        )

    @antiabuse.command(name="reset")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_reset(self, ctx: commands.Context, member: discord.Member | None = None):
        """Reset warning counters. Omit member to clear all members in this server."""
        if member is None:
            for target in ctx.guild.members:
                await self.config.member(target).warning_count.set(0)
                await self.config.member(target).last_violation_at.set(0)
                await self.config.member(target).last_action_at.set(0)
            await ctx.send("All anti-abuse counters cleared for this server.")
            return

        await self.config.member(member).warning_count.set(0)
        await self.config.member(member).last_violation_at.set(0)
        await self.config.member(member).last_action_at.set(0)
        await ctx.send(f"Warning counters reset for {member.mention}.")

    @antiabuse.command(name="offender")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def antiabuse_offender(self, ctx: commands.Context, member: discord.Member):
        """Show offense profile for a member."""
        count = await self.config.member(member).warning_count()
        last_violation = await self.config.member(member).last_violation_at()
        last_action = await self.config.member(member).last_action_at()

        now = int(time.time())
        embed = discord.Embed(title="Offender Profile", color=DEFAULT_COLOR)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Warnings", value=str(count), inline=True)
        embed.add_field(name="Last violation", value=f"<t:{last_violation}:R>" if last_violation > 0 else "None", inline=True)
        embed.add_field(name="Last action", value=f"<t:{last_action}:R>" if last_action > 0 else "None", inline=True)
        embed.add_field(name="Activity", value=("Idle" if not last_violation or now - last_violation > 60 * 60 else "Recently active"), inline=True)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if message.author is None or not isinstance(message.author, discord.Member):
            return
        if message.author.bot:
            return
        if message.author.guild_permissions.administrator:
            return

        settings = await self.config.guild(message.guild).all()
        if not settings["antiabuse_enabled"]:
            return
        if self._is_ignored_location(message, settings):
            return

        await self._ensure_lockdown_expired(message.guild)

        exempt_ids = set(settings["exempt_role_ids"])
        if any(role.id in exempt_ids for role in message.author.roles):
            return

        violations = await self._collect_violations(message, settings)
        if not violations:
            return

        await self._apply_penalty(message, violations)

    async def _send_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return

        settings = await self.config.guild(ctx.guild).all()
        lockdown_until = settings.get("lockdown_until", 0)
        now = time.time()

        embed = discord.Embed(title="Anti-Abuse Protection", color=DEFAULT_COLOR)
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
        embed.add_field(name="Caps", value=f">{settings['caps_ratio'] * 100:.0f}% uppercase ({settings['caps_min_length']} letters min)", inline=True)
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

        log_channel = ctx.guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "Not set", inline=True)

        exempt_ids = settings["exempt_role_ids"]
        roles = [ctx.guild.get_role(role_id) for role_id in exempt_ids]
        roles = [role for role in roles if role is not None]
        exempt_text = ", ".join(role.mention for role in roles) if roles else "None"
        embed.add_field(name="Exempt roles", value=exempt_text, inline=False)
        ignored_channels = [ctx.guild.get_channel(channel_id) for channel_id in settings["ignored_channel_ids"]]
        ignored_channels = [channel for channel in ignored_channels if channel is not None]
        ignored_categories = [ctx.guild.get_channel(category_id) for category_id in settings["ignored_category_ids"]]
        ignored_categories = [category for category in ignored_categories if category is not None]
        ignored_text = []
        if ignored_channels:
            ignored_text.append("Channels: " + ", ".join(channel.mention for channel in ignored_channels))
        if ignored_categories:
            ignored_text.append("Categories: " + ", ".join(category.name for category in ignored_categories))
        embed.add_field(name="Ignored locations", value="\n".join(ignored_text) if ignored_text else "None", inline=False)
        await ctx.send(embed=embed)

    async def _send_lockdown_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return
        settings = await self.config.guild(ctx.guild).all()
        lockdown_until = settings.get("lockdown_until", 0)
        now = int(time.time())

        if lockdown_until and lockdown_until > now:
            await ctx.send(f"Lockdown is active. Restores <t:{lockdown_until}:R>.")
            return

        await ctx.send(
            "No lockdown currently active. "
            f"Auto-lockdown is {'enabled' if settings['auto_lockdown_enabled'] else 'disabled'}."
        )

    async def _collect_violations(self, message: discord.Message, settings: dict[str, Any]) -> list[str]:
        violations: list[str] = []

        if self._is_rate_violation(message, settings["rate_window_seconds"], settings["rate_threshold"]):
            violations.append("message rate")

        if await self._is_command_spam_violation(message, settings):
            violations.append("command spam")

        mentions = self._count_mentions(message)
        if mentions > settings["mention_threshold"]:
            violations.append(f"mentions ({mentions})")

        if self._is_caps_spam(message.content, settings["caps_ratio"], settings["caps_min_length"]):
            violations.append("caps spam")

        links = self._count_links(message.content)
        if links > settings["link_threshold"]:
            violations.append(f"links ({links})")

        return violations

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
    def _count_links(content: str) -> int:
        return len(URL_RE.findall(content or ""))

    @staticmethod
    def _is_caps_spam(content: str, ratio: float, min_length: int) -> bool:
        letters = [char for char in (content or "") if char.isalpha()]
        if len(letters) < min_length:
            return False
        if ratio <= 0:
            return False

        uppercase = sum(1 for char in letters if char.isupper())
        return (uppercase / len(letters)) >= ratio

    async def _apply_penalty(self, message: discord.Message, violations: list[str]):
        member = message.author
        if message.guild is None:
            return

        guild_cfg = self.config.guild(message.guild)
        settings = await guild_cfg.all()
        profile_settings = await self._resolve_member_profile(member, settings["role_punishment_profiles"])
        punish_settings = {
            "warn_threshold": profile_settings.get("warn_threshold", settings["warn_threshold"]),
            "timeout_threshold": profile_settings.get("timeout_threshold", settings["timeout_threshold"]),
            "long_timeout_threshold": profile_settings.get("long_timeout_threshold", settings["long_timeout_threshold"]),
            "short_timeout_minutes": profile_settings.get("short_timeout_minutes", settings["short_timeout_minutes"]),
            "long_timeout_minutes": profile_settings.get("long_timeout_minutes", settings["long_timeout_minutes"]),
            "kick_threshold": profile_settings.get("kick_threshold", settings["kick_threshold"]),
            "kick_enabled": profile_settings.get("kick_enabled", settings["kick_enabled"]),
            "ban_threshold": profile_settings.get("ban_threshold", settings["ban_threshold"]),
            "ban_enabled": profile_settings.get("ban_enabled", settings["ban_enabled"]),
            "ban_delete_message_days": profile_settings.get("ban_delete_message_days", settings["ban_delete_message_days"]),
            "auto_lockdown_enabled": settings["auto_lockdown_enabled"],
            "lockdown_threshold": settings["lockdown_threshold"],
            "lockdown_minutes": settings["lockdown_minutes"],
            "violation_decay_seconds": settings["violation_decay_seconds"],
            "action_cooldown_seconds": settings["action_cooldown_seconds"],
        }

        member_cfg = self.config.member(member)
        warning_count = await member_cfg.warning_count()
        last_violation = await member_cfg.last_violation_at()
        now = int(time.time())

        if punish_settings["violation_decay_seconds"] and now - last_violation > punish_settings["violation_decay_seconds"]:
            warning_count = 0

        warning_count += 1
        await member_cfg.warning_count.set(warning_count)
        await member_cfg.last_violation_at.set(now)

        last_action = await member_cfg.last_action_at()
        if punish_settings["action_cooldown_seconds"] and now - last_action < punish_settings["action_cooldown_seconds"]:
            return

        action = "warn"
        timeout_minutes = 0
        do_lockdown = False

        if punish_settings["ban_enabled"] and punish_settings["ban_threshold"] > 0 and warning_count >= punish_settings["ban_threshold"]:
            action = "ban"
        elif (
            punish_settings["kick_enabled"]
            and punish_settings["kick_threshold"] > 0
            and warning_count >= punish_settings["kick_threshold"]
        ):
            action = "kick"
        elif warning_count >= punish_settings["long_timeout_threshold"]:
            action = "long_timeout"
            timeout_minutes = punish_settings["long_timeout_minutes"]
        elif warning_count >= punish_settings["timeout_threshold"]:
            action = "short_timeout"
            timeout_minutes = punish_settings["short_timeout_minutes"]

        if punish_settings["auto_lockdown_enabled"] and warning_count >= punish_settings["lockdown_threshold"]:
            do_lockdown = True

        if action == "warn" and warning_count < punish_settings["warn_threshold"]:
            return

        if action == "short_timeout":
            success = await self._timeout_member(member, timeout_minutes, "Anti-abuse short timeout")
            if success:
                await self._notify(message, member, violations, f"short timeout ({timeout_minutes}m)")
                await member_cfg.last_action_at.set(now)
                await self._log_action(message.guild, member, warning_count, violations, f"Timeout {timeout_minutes}m")
            else:
                await self._notify(message, member, violations, "timeout action failed")
                await member_cfg.last_action_at.set(now)
            if do_lockdown:
                await self._start_lockdown(message.guild, punish_settings["lockdown_minutes"] * 60, "automatic")
            return

        if action == "long_timeout":
            success = await self._timeout_member(member, timeout_minutes, "Anti-abuse long timeout")
            if success:
                await self._notify(message, member, violations, f"long timeout ({timeout_minutes}m)")
                await member_cfg.last_action_at.set(now)
                await self._log_action(message.guild, member, warning_count, violations, f"Timeout {timeout_minutes}m")
            else:
                await self._notify(message, member, violations, "timeout action failed")
                await member_cfg.last_action_at.set(now)
            if do_lockdown:
                await self._start_lockdown(message.guild, punish_settings["lockdown_minutes"] * 60, "automatic")
            return

        if action == "kick":
            success = await self._kick_member(member, reason="Anti-abuse auto kick")
            if success:
                await self._notify(message, member, violations, "kick")
                await member_cfg.last_action_at.set(now)
                await self._log_action(message.guild, member, warning_count, violations, "Kick")
            else:
                await self._notify(message, member, violations, "kick action failed")
                await member_cfg.last_action_at.set(now)
            if do_lockdown:
                await self._start_lockdown(message.guild, punish_settings["lockdown_minutes"] * 60, "automatic")
            return

        if action == "ban":
            success = await self._ban_member(
                member,
                delete_message_days=punish_settings["ban_delete_message_days"],
                reason="Anti-abuse auto ban",
            )
            if success:
                await self._notify(message, member, violations, "ban")
                await member_cfg.last_action_at.set(now)
                await self._log_action(message.guild, member, warning_count, violations, "Ban")
            else:
                await self._notify(message, member, violations, "ban action failed")
                await member_cfg.last_action_at.set(now)
            if do_lockdown:
                await self._start_lockdown(message.guild, punish_settings["lockdown_minutes"] * 60, "automatic")
            return

        if do_lockdown:
            duration = settings["lockdown_minutes"]
            if duration <= 0:
                await self._notify(message, member, violations, "warning (lockdown disabled)")
                await member_cfg.last_action_at.set(now)
                return
            await self._start_lockdown(message.guild, duration * 60, "automatic")
            await self._notify(message, member, violations, f"lockdown {duration}m")
            await member_cfg.last_action_at.set(now)
            await self._log_action(message.guild, member, warning_count, violations, f"Lockdown {duration}m")
            return

        await self._notify(message, member, violations, f"warning (count {warning_count})")
        await member_cfg.last_action_at.set(now)
        await self._log_action(message.guild, member, warning_count, violations, "warning")

    async def _notify(self, message: discord.Message, member: discord.Member, violations: list[str], action: str):
        channel = message.channel
        if channel is None:
            return

        reasons = ", ".join(violations)
        lines = [
            f"{member.mention} Anti-abuse action: **{action}**",
            f"Triggered by: {reasons}",
            f"This is warning #{await self.config.member(member).warning_count()} for this user.",
        ]
        await channel.send(
            "\n".join(lines),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def _log_action(self, guild: discord.Guild, member: discord.Member, warnings: int, violations: list[str], action: str):
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if log_channel is None:
            return

        embed = discord.Embed(title="Anti-Abuse Log", color=DEFAULT_COLOR)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Warnings", value=str(warnings), inline=True)
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(name="Reasons", value=", ".join(violations), inline=False)
        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _timeout_member(self, member: discord.Member, minutes: int, reason: str) -> bool:
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

    async def _kick_member(self, member: discord.Member, reason: str) -> bool:
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

    async def _ban_member(self, member: discord.Member, delete_message_days: int, reason: str) -> bool:
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

    async def _can_moderate_member(
        self,
        guild: discord.Guild | None,
        member: discord.Member,
        action: str,
        *,
        actor: discord.Member | None = None,
    ) -> tuple[bool, str]:
        if guild is None:
            return False, "this command can only be used in a server."
        if member.guild.id != guild.id:
            return False, "that member is not in this server."

        bot_member = guild.me
        if bot_member is None:
            return False, "I cannot find my server member record."
        if member.id == bot_member.id:
            return False, "I cannot moderate myself."
        if member.id == guild.owner_id:
            return False, "I cannot moderate the server owner."
        if await self._is_bot_owner(member):
            return False, "I cannot moderate a bot owner."

        required_permission = {
            "timeout": "moderate_members",
            "kick": "kick_members",
            "ban": "ban_members",
        }.get(action)
        if required_permission is None:
            return False, "unknown moderation action."
        if not getattr(bot_member.guild_permissions, required_permission, False):
            return False, f"I am missing the `{required_permission}` permission."
        if member.top_role >= bot_member.top_role:
            return False, "that member's top role is higher than or equal to mine."

        if actor is not None:
            if member.id == actor.id:
                return False, "you cannot moderate yourself."
            if actor.id != guild.owner_id and member.top_role >= actor.top_role:
                return False, "that member's top role is higher than or equal to yours."

        return True, ""

    async def _is_bot_owner(self, member: discord.Member) -> bool:
        try:
            return await self.bot.is_owner(member)
        except Exception:
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
            current_until = await self.config.guild(guild).lockdown_until()
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
                    await channel.set_permissions(default_role, send_messages=False, reason=f"Anti-abuse lockdown ({trigger})")
                except (discord.Forbidden, discord.HTTPException):
                    continue

            await self.config.guild(guild).lockdown_overrides.set(overrides)
            await self.config.guild(guild).lockdown_until.set(now_plus)

    async def _stop_lockdown(self, guild: discord.Guild, force: bool = False):
        lock = self._guild_locks[guild.id]
        async with lock:
            settings = await self.config.guild(guild).all()
            overrides = settings.get("lockdown_overrides", {})
            if not overrides:
                await self.config.guild(guild).lockdown_until.set(0)
                if not force:
                    return

            default_role = guild.default_role
            if default_role is None:
                await self.config.guild(guild).lockdown_overrides.set({})
                await self.config.guild(guild).lockdown_until.set(0)
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

            await self.config.guild(guild).lockdown_overrides.set({})
            await self.config.guild(guild).lockdown_until.set(0)

    async def _ensure_lockdown_expired(self, guild: discord.Guild):
        now = time.time()
        until = await self.config.guild(guild).lockdown_until()
        if not until:
            return
        if now >= until:
            await self._stop_lockdown(guild)
