"""The Moderation cog: manual actions, cases, AutoMod and the honeypot in one place."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import discord
from redbot.core import Config, app_commands, commands

from .actions import ActionsMixin
from .automod import AutoModMixin
from .cases import CasesMixin
from .common import (
    ADMINHELPER_STORAGE,
    AUTOMOD_STORAGE,
    DEFAULT_COLOR,
    DEFAULT_REASON,
    HONEYPOT_STORAGE,
)
from .honeypot import ACTION_CHOICES, HoneypotMixin
from .layouts import run_slash, send_embed, send_text
from .natural import NATURAL_COMMAND_METHODS, NaturalLanguageMixin


def _open_config(storage: tuple[str, int]) -> Config:
    """Open a merged cog's Config by its old name, so its saved data is reused as-is."""
    cog_name, identifier = storage
    return Config.get_conf(None, identifier=identifier, cog_name=cog_name)


class Moderation(
    NaturalLanguageMixin,
    ActionsMixin,
    CasesMixin,
    AutoModMixin,
    HoneypotMixin,
    commands.Cog,
):
    """Moderation actions, cases, AutoMod and a honeypot trap channel."""

    def __init__(self, bot):
        self.bot = bot
        # Cases, warnings, warning punishments and name history (formerly AdminHelper).
        self.config = _open_config(ADMINHELPER_STORAGE)
        self.config.register_guild(
            log_channel_id=None,
            dm_users=True,
            name_tracking_enabled=True,
            natural_language_enabled=True,
            softban_delete_message_days=1,
            ban_delete_message_days=0,
            next_case_id=1,
            cases=[],
            next_warning_punishment_id=1,
            warning_punishments=[],
        )
        self.config.register_user(previous_usernames=[], previous_global_names=[])
        self.config.register_member(warnings=[], previous_nicknames=[])

        # Spam detection, strikes and lockdowns (formerly AntiAbuse).
        self.automod_config = _open_config(AUTOMOD_STORAGE)
        self.automod_config.register_guild(
            antiabuse_enabled=True,
            log_channel_id=None,
            exempt_role_ids=[],
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
            auto_lockdown_enabled=False,
            lockdown_threshold=21,
            lockdown_minutes=10,
            lockdown_until=0,
            lockdown_overrides={},
            role_punishment_profiles={},
        )
        self.automod_config.register_member(warning_count=0, last_violation_at=0, last_action_at=0)

        # Trap channel (formerly Honeypot).
        self.honeypot_config = _open_config(HONEYPOT_STORAGE)
        self.honeypot_config.register_guild(
            channel_id=None,
            exempt_roles=[],
            log_channel_id=None,
            action="ban",
            punish_role_id=None,
            remove_other_roles=False,
            role_exception_ids=[],
        )

        self._message_buckets: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._command_buckets: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._guild_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._case_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

        # A malformed "@Bot ban ..." that Red parsed as a command falls back to the natural-language parser.
        for method_name in set(NATURAL_COMMAND_METHODS.values()):
            command = getattr(self, method_name, None)
            if isinstance(command, commands.Command):
                command.on_error = type(self)._natural_command_error

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        if requester not in {"discord_deleted_user", "user"}:
            return
        await self.config.user_from_id(user_id).clear()
        for guild_id in await self.config.all_members():
            await self.config.member_from_ids(guild_id, user_id).clear()
        for guild_id in await self.automod_config.all_members():
            await self.automod_config.member_from_ids(guild_id, user_id).clear()

    # ------------------------------------------------------------------ panel

    def control_panel_schema(self) -> dict[str, Any]:
        def bound(conf: Config, key: str) -> dict[str, Any]:
            async def get(guild):
                return await conf.guild(guild).get_attr(key)()

            async def set_(guild, value):
                await conf.guild(guild).get_attr(key).set(value)

            return {"get": get, "set": set_}

        def automod(key: str, label: str, kind: str, section: str = "AutoMod", **extra) -> dict[str, Any]:
            return {"key": f"automod:{key}", "label": label, "type": kind, "section": section, **bound(self.automod_config, key), **extra}

        def number(key: str, label: str, section: str, minimum: int, unit: str = "", help_text: str = "", **extra) -> dict[str, Any]:
            return automod(key, label, "int", section, min=minimum, unit=unit, help=help_text, **extra)

        def honeypot(key: str, label: str, kind: str, **extra) -> dict[str, Any]:
            return {
                "key": f"honeypot:{key}", "label": label, "type": kind, "section": "Honeypot",
                "access": "admin", **bound(self.honeypot_config, key), **extra,
            }

        escalation = "AutoMod escalation"
        return {
            "title": "Moderation",
            "description": "Moderation actions and cases, AutoMod spam protection and the honeypot trap.",
            "settings": [
                # Moderation
                {"key": "log_channel_id", "label": "Mod-log channel", "type": "channel", "section": "Moderation",
                 "channel_types": ["text"], "help": "Where manual moderation actions are logged."},
                {"key": "dm_users", "label": "DM moderated users", "type": "bool", "section": "Moderation",
                 "help": "Tell members by DM when they are moderated."},
                {"key": "natural_language_enabled", "label": "Natural-language moderation", "type": "bool",
                 "section": "Moderation", "help": "Allow moderation by mentioning the bot in plain language."},
                {"key": "name_tracking_enabled", "label": "Name history tracking", "type": "bool", "section": "Moderation",
                 "help": "Record username, display name and nickname changes."},
                {"key": "ban_delete_message_days", "label": "Ban message cleanup", "type": "int", "section": "Moderation",
                 "min": 0, "max": 7, "unit": "days", "help": "Days of messages deleted by default on a ban."},
                {"key": "softban_delete_message_days", "label": "Softban message cleanup", "type": "int",
                 "section": "Moderation", "min": 0, "max": 7, "unit": "days", "help": "Days of messages deleted on a softban."},
                # AutoMod detection
                automod("antiabuse_enabled", "AutoMod enforcement", "bool", help="Detect and punish spam in this server."),
                automod("log_channel_id", "AutoMod log channel", "channel", channel_types=["text"],
                        help="Where AutoMod actions are logged."),
                automod("exempt_role_ids", "Exempt roles", "roles", help="Members with these roles bypass AutoMod."),
                automod("ignored_channel_ids", "Ignored channels", "channels", channel_types=["text"],
                        help="Channels AutoMod does not check."),
                automod("ignored_category_ids", "Ignored categories", "channels", channel_types=["category"],
                        help="Categories AutoMod does not check."),
                number("rate_window_seconds", "Message rate window", "AutoMod", 1, "seconds"),
                number("rate_threshold", "Message rate limit", "AutoMod", 1, "messages", "Most messages allowed per window."),
                number("command_spam_threshold", "Command spam limit", "AutoMod", 0, "commands",
                       "Most commands per window. 0 turns this check off.")
                | {"set": self._set_command_spam_threshold},
                number("command_spam_window_seconds", "Command spam window", "AutoMod", 1, "seconds"),
                number("mention_threshold", "Mention limit", "AutoMod", 0, "mentions", "Most mentions allowed per message."),
                number("link_threshold", "Link limit", "AutoMod", 0, "links", "Most links allowed per message."),
                automod("caps_ratio", "Caps ratio", "float", min=0, max=100, unit="%",
                        help="Share of uppercase letters that counts as caps spam.",
                        validate=self._validate_caps_percent)
                | {"get": self._caps_ratio_percent, "set": self._set_caps_ratio_percent},
                number("caps_min_length", "Caps minimum length", "AutoMod", 0, "letters", "Shorter messages skip the caps check."),
                # AutoMod escalation and lockdown
                number("violation_decay_seconds", "Strike reset time", escalation, 0, "seconds", "Idle time before strikes reset."),
                number("action_cooldown_seconds", "Action cooldown", escalation, 0, "seconds"),
                number("warn_threshold", "Warn at", escalation, 0, "strikes", "Keep these thresholds increasing so each step can happen."),
                number("timeout_threshold", "Short timeout at", escalation, 0, "strikes"),
                number("short_timeout_minutes", "Short timeout length", escalation, 1, "minutes"),
                number("long_timeout_threshold", "Long timeout at", escalation, 0, "strikes"),
                number("long_timeout_minutes", "Long timeout length", escalation, 1, "minutes"),
                automod("kick_enabled", "Automatic kicks", "bool", escalation),
                number("kick_threshold", "Kick at", escalation, 0, "strikes"),
                automod("ban_enabled", "Automatic bans", "bool", escalation),
                number("ban_threshold", "Ban at", escalation, 0, "strikes"),
                number("ban_delete_message_days", "AutoMod ban cleanup", escalation, 0, "days", max=7),
                automod("auto_lockdown_enabled", "Automatic lockdown", "bool", escalation),
                number("lockdown_threshold", "Lockdown trigger", escalation, 0, "strikes"),
                number("lockdown_minutes", "Lockdown length", escalation, 1, "minutes"),
                # Honeypot
                honeypot("channel_id", "Trap channel", "channel", channel_types=["text"],
                         help="Anyone who posts here is punished. Clear it to turn the honeypot off."),
                honeypot("log_channel_id", "Honeypot log channel", "channel", channel_types=["text"],
                         empty="Logging off", help="Where honeypot events are reported."),
                honeypot("exempt_roles", "Trap exempt roles", "roles", help="Members with these roles can post in the trap."),
                honeypot("action", "Trap punishment", "choice",
                         choices=[{"value": choice, "label": choice.title()} for choice in ACTION_CHOICES],
                         help="What happens to someone who posts in the trap."),
                honeypot("punish_role_id", "Punish role", "role", help="Applied when the punishment is Role."),
                honeypot("remove_other_roles", "Strip existing roles", "bool",
                         help="Remove other roles before applying the punish role."),
                honeypot("role_exception_ids", "Roles kept when stripping", "roles",
                         help="These roles are not removed when stripping."),
            ],
        }

    # ------------------------------------------------------------------ slash

    mod_slash = app_commands.Group(
        name="mod",
        description="Moderation, AutoMod and honeypot tools.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_messages=True),
    )
    punish_slash = app_commands.Group(
        name="punish",
        description="Punishments that run when a member reaches a warning count.",
        parent=mod_slash,
    )

    @mod_slash.command(name="ban", description="Ban a member.")
    @app_commands.describe(member="Member to ban", reason="Reason; may start with 0-7 message delete days")
    async def slash_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.ban_member, member, reason=reason)

    @mod_slash.command(name="kick", description="Kick a member.")
    @app_commands.describe(member="Member to kick", reason="Reason for the kick")
    async def slash_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.kick_member, member, reason=reason)

    @mod_slash.command(name="softban", description="Ban then unban a member to clean recent messages.")
    @app_commands.describe(member="Member to softban", reason="Reason for the softban")
    async def slash_softban(self, interaction: discord.Interaction, member: discord.Member, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.softban_member, member, reason=reason)

    @mod_slash.command(name="hackban", description="Ban a user by ID, even if they are not in the server.")
    @app_commands.describe(user="User to ban, or their ID", reason="Reason; may start with 0-7 message delete days")
    async def slash_hackban(self, interaction: discord.Interaction, user: discord.User, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.hackban_user, user, reason=reason)

    @mod_slash.command(name="unban", description="Unban a user.")
    @app_commands.describe(user="User to unban, or their ID", reason="Reason for the unban")
    async def slash_unban(self, interaction: discord.Interaction, user: discord.User, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.unban_user, user, reason=reason)

    @mod_slash.command(name="timeout", description="Timeout a member for a number of minutes.")
    @app_commands.describe(member="Member to timeout", minutes="Length in minutes", reason="Reason for the timeout")
    async def slash_timeout(
        self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = DEFAULT_REASON
    ):
        await run_slash(self.bot, interaction, self.timeout_member, member, minutes, reason=reason)

    @mod_slash.command(name="untimeout", description="Remove a member's timeout.")
    @app_commands.describe(member="Member to release", reason="Reason for removing the timeout")
    async def slash_untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.untimeout_member, member, reason=reason)

    @mod_slash.command(name="warn", description="Warn a member and store the warning.")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def slash_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.warn_member, member, reason=reason)

    @mod_slash.command(name="warnings", description="Show a member's warnings and AutoMod strikes.")
    @app_commands.describe(member="Member to look up")
    async def slash_warnings(self, interaction: discord.Interaction, member: discord.Member):
        await run_slash(self.bot, interaction, self.warnings_member, member, ephemeral=True)

    @mod_slash.command(name="clearwarnings", description="Clear a member's warnings and AutoMod strikes.")
    @app_commands.describe(member="Member to clear", reason="Reason for clearing")
    async def slash_clearwarnings(self, interaction: discord.Interaction, member: discord.Member, reason: str = DEFAULT_REASON):
        await run_slash(self.bot, interaction, self.clearwarnings_member, member, reason=reason)

    @mod_slash.command(name="purge", description="Delete recent messages from this channel.")
    @app_commands.describe(amount="How many messages, 1-200", member="Only delete this member's messages")
    async def slash_purge(self, interaction: discord.Interaction, amount: int, member: discord.Member | None = None):
        await run_slash(self.bot, interaction, self.purge_messages, amount, member)

    @mod_slash.command(name="userinfo", description="Show account, server and moderation details for a member.")
    @app_commands.describe(member="Member to look up; defaults to you")
    async def slash_userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await run_slash(self.bot, interaction, self.userinfo_member, member, ephemeral=True)

    @mod_slash.command(name="case", description="Show one moderation case.")
    @app_commands.describe(case_id="Case number")
    async def slash_case(self, interaction: discord.Interaction, case_id: int):
        await run_slash(self.bot, interaction, self.case_info, case_id, ephemeral=True)

    @mod_slash.command(name="cases", description="Show a member's recent moderation cases.")
    @app_commands.describe(member="Member to look up")
    async def slash_cases(self, interaction: discord.Interaction, member: discord.Member):
        await run_slash(self.bot, interaction, self.cases_member, member, ephemeral=True)

    @mod_slash.command(name="reason", description="Change the reason on a moderation case.")
    @app_commands.describe(case_id="Case number", reason="New reason")
    async def slash_reason(self, interaction: discord.Interaction, case_id: int, reason: str):
        await run_slash(self.bot, interaction, self.reason_case, case_id, reason=reason, ephemeral=True)

    @mod_slash.command(name="modstats", description="Show moderation actions done by and received by a member.")
    @app_commands.describe(member="Member to look up")
    async def slash_modstats(self, interaction: discord.Interaction, member: discord.Member):
        await run_slash(self.bot, interaction, self.modstats_member, member, ephemeral=True)

    @mod_slash.command(name="status", description="Show moderation, AutoMod and honeypot settings.")
    async def slash_status(self, interaction: discord.Interaction):
        await run_slash(self.bot, interaction, self.mod_status, ephemeral=True)

    @mod_slash.command(name="help", description="Show a moderator command reference.")
    async def slash_help(self, interaction: discord.Interaction):
        await run_slash(self.bot, interaction, self.mod_help, ephemeral=True)

    @mod_slash.command(name="lockdown", description="Start, stop or check a server lockdown.")
    @app_commands.describe(action="What to do", minutes="Lockdown length; omit for the configured default")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status", value="status"),
            app_commands.Choice(name="Start", value="start"),
            app_commands.Choice(name="Stop", value="stop"),
        ]
    )
    async def slash_lockdown(self, interaction: discord.Interaction, action: str = "status", minutes: int | None = None):
        if action == "start":
            await run_slash(self.bot, interaction, self.lockdown_start, minutes)
        elif action == "stop":
            await run_slash(self.bot, interaction, self.lockdown_stop)
        else:
            await run_slash(self.bot, interaction, self.lockdown_group, ephemeral=True)

    @mod_slash.command(name="resetstrikes", description="Reset AutoMod strikes for everyone in the server.")
    async def slash_resetstrikes(self, interaction: discord.Interaction):
        await run_slash(self.bot, interaction, self.reset_strikes, ephemeral=True)

    @mod_slash.command(name="roleoverride", description="Show or set AutoMod escalation overrides for a role.")
    @app_commands.describe(
        role="Role to show or change; omit to list every override",
        spec="key=value pairs such as warn=3 kick_enabled=true, or 'clear' to remove",
    )
    async def slash_roleoverride(
        self, interaction: discord.Interaction, role: discord.Role | None = None, spec: str | None = None
    ):
        await run_slash(self.bot, interaction, self.role_override, role, spec=spec, ephemeral=True)

    @punish_slash.command(name="list", description="List warning punishments.")
    async def slash_punish_list(self, interaction: discord.Interaction):
        await run_slash(self.bot, interaction, self.punish_group, ephemeral=True)

    @punish_slash.command(name="add", description="Add a punishment for reaching a warning count.")
    @app_commands.describe(
        warnings="Warning count that triggers it",
        action="What to do",
        minutes="Timeout length in minutes",
        role="Role to add or remove",
        delete_days="Days of messages to delete on ban, 0-7",
        reason="Reason shown to the member",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Timeout", value="timeout"),
            app_commands.Choice(name="Add role", value="addrole"),
            app_commands.Choice(name="Remove role", value="removerole"),
            app_commands.Choice(name="Kick", value="kick"),
            app_commands.Choice(name="Ban", value="ban"),
        ]
    )
    async def slash_punish_add(
        self,
        interaction: discord.Interaction,
        warnings: int,
        action: str,
        minutes: int | None = None,
        role: discord.Role | None = None,
        delete_days: int | None = None,
        reason: str = DEFAULT_REASON,
    ):
        value = {"timeout": minutes, "addrole": role.id if role else None, "removerole": role.id if role else None,
                 "ban": delete_days}.get(action)
        value = None if value is None else str(value)
        await run_slash(self.bot, interaction, self.punish_add, warnings, action, value, reason=reason, ephemeral=True)

    @punish_slash.command(name="remove", description="Remove one warning punishment.")
    @app_commands.describe(punishment_id="Punishment number from the list")
    async def slash_punish_remove(self, interaction: discord.Interaction, punishment_id: int):
        await run_slash(self.bot, interaction, self.punish_remove, punishment_id, ephemeral=True)

    @punish_slash.command(name="clear", description="Remove every warning punishment.")
    async def slash_punish_clear(self, interaction: discord.Interaction):
        await run_slash(self.bot, interaction, self.punish_clear, ephemeral=True)

    # ------------------------------------------------------------------ [p]mod

    @commands.group(name="mod", invoke_without_command=True)
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def mod_status(self, ctx: commands.Context):
        """Show moderation, AutoMod and honeypot settings. Change them with `[p]control`."""
        settings = await self.config.guild(ctx.guild).all()
        log_channel = ctx.guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None
        embed = discord.Embed(title="Moderation", color=DEFAULT_COLOR)
        embed.add_field(name="Mod-log channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        embed.add_field(name="DM users", value="Yes" if settings["dm_users"] else "No", inline=True)
        embed.add_field(name="Name tracking", value="Yes" if settings["name_tracking_enabled"] else "No", inline=True)
        embed.add_field(
            name="Natural-language mentions",
            value="Enabled" if settings["natural_language_enabled"] else "Disabled",
            inline=True,
        )
        embed.add_field(name="Ban cleanup", value=f"{settings['ban_delete_message_days']} day(s)", inline=True)
        embed.add_field(name="Softban cleanup", value=f"{settings['softban_delete_message_days']} day(s)", inline=True)
        embed.add_field(name="Cases", value=str(len(settings["cases"])), inline=True)
        embed.add_field(name="Warning punishments", value=f"{len(settings['warning_punishments'])} configured", inline=True)
        embed.set_footer(text="Change settings in the /control panel. See /mod help for commands.")
        for section in (embed, await self._automod_status_embed(ctx.guild), await self._honeypot_status_embed(ctx.guild)):
            await send_embed(ctx, section, ephemeral=True)

    @mod_status.command(name="help")
    async def mod_help(self, ctx: commands.Context):
        """Post a moderator command reference."""
        prefix = await self._prefix(ctx)
        bot_mention = ctx.guild.me.mention if ctx.guild.me is not None else "@bot"
        embed = discord.Embed(
            title="Moderator Commands",
            description="Every command is available as `/mod <command>` and as a prefix command.",
            color=DEFAULT_COLOR,
        )
        embed.add_field(
            name="Member actions",
            value=(
                f"`{prefix}warn @user <reason>` - add a manual warning\n"
                f"`{prefix}timeout @user <minutes> <reason>` - timeout a member\n"
                f"`{prefix}untimeout @user` - remove a timeout\n"
                f"`{prefix}kick @user <reason>` - kick a member\n"
                f"`{prefix}ban @user [0-7 days] <reason>` - ban with optional message cleanup override\n"
                f"`{prefix}softban @user <reason>` - ban/unban to clean recent messages\n"
                f"`{prefix}hackban <user id> <reason>` - ban someone who is not in the server\n"
                f"`{prefix}unban <user id> <reason>` - unban a user\n"
                f"`{prefix}purge <1-200> [@user]` - delete recent messages in this channel"
            ),
            inline=False,
        )
        embed.add_field(
            name="Lookup",
            value=(
                f"`{prefix}userinfo [@user]` - account, roles, warnings and name history\n"
                f"`{prefix}warnings @user` - manual warnings and AutoMod strikes\n"
                f"`{prefix}clearwarnings @user <reason>` - clear warnings and AutoMod strikes\n"
                f"`{prefix}mod case <id>` / `{prefix}mod cases @user` / `{prefix}mod reason <id> <text>`\n"
                f"`{prefix}mod modstats @user` - actions performed and received"
            ),
            inline=False,
        )
        embed.add_field(
            name="AutoMod and server",
            value=(
                f"`{prefix}mod` - show every setting\n"
                f"`{prefix}mod lockdown [start [minutes]|stop]` - lock or unlock the server\n"
                f"`{prefix}mod resetstrikes` - reset everyone's AutoMod strikes\n"
                f"`{prefix}mod roleoverride [@role] [key=value ...|clear]` - per-role AutoMod escalation\n"
                f"`{prefix}mod punish [add|remove|clear]` - punishments at warning counts\n"
                "Everything else, including the honeypot, lives in the `/control` panel."
            ),
            inline=False,
        )
        embed.add_field(
            name="Natural-language mentions",
            value=(
                f"`{bot_mention} ban @user [reason]`\n"
                f"`{bot_mention} timeout @user 10 minutes [reason]`\n"
                f"`{bot_mention} mute o @user por 10 segundos`\n"
                "English and common Portuguese forms work and use the same checks and confirmation prompts."
            ),
            inline=False,
        )
        embed.set_footer(text="Destructive actions ask for confirmation when needed.")
        await send_embed(ctx, embed)

    @mod_status.command(name="case")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def case_info(self, ctx: commands.Context, case_id: int):
        """Show one moderation case."""
        case = await self._get_case(ctx.guild, case_id)
        if case is None:
            await ctx.send(f"Case #{case_id} was not found.")
            return
        await send_embed(ctx, self._case_embed(ctx.guild, case))

    @mod_status.command(name="cases")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def cases_member(self, ctx: commands.Context, member: discord.Member):
        """Show recent cases for a member."""
        await self._maybe_set_natural_context(ctx)
        cases = await self.config.guild(ctx.guild).cases()
        matches = [case for case in cases if case.get("target_id") == member.id]
        if not matches:
            await self._send_moderation_response(
                ctx,
                f"No cases found for {member.mention}.",
                f"Nenhum caso foi encontrado para {member.mention}.",
            )
            return
        portuguese = self._natural_language(ctx) == "pt"
        lines = [
            self._format_case_line_portuguese(case) if portuguese else self._format_case_line(case)
            for case in matches[-10:]
        ]
        header = (
            f"Casos recentes de {member.mention} ({len(matches)} no total):"
            if portuguese
            else f"Recent cases for {member.mention} ({len(matches)} total):"
        )
        await send_text(ctx, header + "\n" + "\n".join(lines), DEFAULT_COLOR)

    @mod_status.command(name="reason")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def reason_case(self, ctx: commands.Context, case_id: int, *, reason: str):
        """Update the reason on a moderation case."""
        async with self.config.guild(ctx.guild).cases() as cases:
            for case in cases:
                if case.get("case_id") == case_id:
                    case["reason"] = reason
                    case["reason_updated_by"] = ctx.author.id
                    case["reason_updated_at"] = int(datetime.now(timezone.utc).timestamp())
                    break
            else:
                case = None
        await ctx.send(f"Case #{case_id} reason updated." if case else f"Case #{case_id} was not found.")

    @mod_status.command(name="modstats")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def modstats_member(self, ctx: commands.Context, member: discord.Member):
        """Show moderation actions done by and received by a member."""
        await self._maybe_set_natural_context(ctx)
        cases = await self.config.guild(ctx.guild).cases()
        as_moderator = [case for case in cases if case.get("moderator_id") == member.id]
        as_target = [case for case in cases if case.get("target_id") == member.id]
        moderator_counts = self._count_cases_by_action(as_moderator)
        target_counts = self._count_cases_by_action(as_target)

        portuguese = self._natural_language(ctx) == "pt"
        fmt = self._format_action_counts_portuguese if portuguese else self._format_action_counts
        embed = discord.Embed(
            title=f"Estatísticas de moderação: {member}" if portuguese else f"Mod Stats: {member}",
            color=member.color or DEFAULT_COLOR,
        )
        embed.add_field(name="Ações realizadas" if portuguese else "Actions performed", value=fmt(moderator_counts), inline=False)
        embed.add_field(name="Ações recebidas" if portuguese else "Actions received", value=fmt(target_counts), inline=False)
        embed.set_footer(
            text=(
                f"Realizadas: {len(as_moderator)} caso(s) | Recebidas: {len(as_target)} caso(s)"
                if portuguese
                else f"Performed {len(as_moderator)} case(s) | Received {len(as_target)} case(s)"
            )
        )
        await send_embed(ctx, embed)

    @mod_status.group(name="lockdown", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def lockdown_group(self, ctx: commands.Context):
        """Show whether a lockdown is active."""
        await self._send_lockdown_status(ctx)

    @lockdown_group.command(name="start")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def lockdown_start(self, ctx: commands.Context, minutes: int | None = None):
        """Stop @everyone from sending messages for a while."""
        duration = minutes if minutes is not None else await self.automod_config.guild(ctx.guild).lockdown_minutes()
        if duration <= 0:
            await ctx.send("Lockdown duration must be positive.")
            return
        await self._start_lockdown(ctx.guild, duration * 60, "manual")
        await ctx.send(f"Guild lockdown started for {duration} minute(s).")

    @lockdown_group.command(name="stop")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def lockdown_stop(self, ctx: commands.Context):
        """End any active lockdown and restore channel permissions."""
        await self._stop_lockdown(ctx.guild, force=True)
        await ctx.send("Active lockdown stopped and permissions restored where possible.")

    @mod_status.command(name="resetstrikes")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def reset_strikes(self, ctx: commands.Context):
        """Reset AutoMod strikes for every member. Use `clearwarnings` for one member."""
        await self.automod_config.clear_all_members(ctx.guild)
        await ctx.send("All AutoMod strike counters cleared for this server.")

    @mod_status.command(name="roleoverride")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def role_override(self, ctx: commands.Context, role: discord.Role | None = None, *, spec: str | None = None):
        """Show or set per-role AutoMod escalation overrides.

        Without a role, every override is listed. With a role and no values, that role's override is shown.
        Values are key=value pairs: warn_threshold, timeout_threshold, long_timeout_threshold,
        short_timeout_minutes, long_timeout_minutes, kick_threshold, ban_threshold, kick_enabled,
        ban_enabled, ban_delete_message_days. Example: `warn=3 kick_enabled=true`. Use `clear` to remove it.
        """
        guild_cfg = self.automod_config.guild(ctx.guild)
        profiles = await guild_cfg.role_punishment_profiles()
        if role is None:
            if not profiles:
                await ctx.send("No role overrides configured.")
                return
            rows = []
            for role_id, profile in profiles.items():
                found = ctx.guild.get_role(int(role_id)) if str(role_id).isdigit() else None
                name = found.mention if found is not None else f"Deleted role ({role_id})"
                rows.append(f"{name}: {self._format_role_profile(profile)}")
            await send_text(ctx, "\n".join(rows), DEFAULT_COLOR)
            return

        key = str(role.id)
        if spec and spec.strip().lower() == "clear":
            profiles.pop(key, None)
            await guild_cfg.role_punishment_profiles.set(profiles)
            await ctx.send(f"Role override cleared for {role.mention}.", allowed_mentions=discord.AllowedMentions.none())
            return

        parsed = self._parse_role_profile_spec(spec or "", role=role)
        if isinstance(parsed, str):
            await ctx.send(parsed, allowed_mentions=discord.AllowedMentions.none())
            return
        if not parsed:
            current = profiles.get(key, {})
            text = f"{role.mention}: {self._format_role_profile(current)}" if current else f"No overrides configured for {role.mention}."
            await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())
            return

        profiles[key] = {**profiles.get(key, {}), **parsed}
        await guild_cfg.role_punishment_profiles.set(profiles)
        await ctx.send(
            f"Role override updated for {role.mention}: {self._format_role_profile(profiles[key])}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @mod_status.group(name="punish", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def punish_group(self, ctx: commands.Context):
        """List punishments that run when a member reaches a warning count."""
        await self._send_warning_punishments(ctx)

    @punish_group.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def punish_add(
        self,
        ctx: commands.Context,
        warnings: int,
        action: str,
        value: str | None = None,
        *,
        reason: str = DEFAULT_REASON,
    ):
        """Add a punishment that runs when a manual warning count is reached.

        Examples:
        `[p]mod punish add 3 timeout 10 repeated spam`
        `[p]mod punish add 4 addrole @Muted`
        `[p]mod punish add 5 kick repeated issues`
        `[p]mod punish add 6 ban 0 repeated issues`
        """
        entry = await self._build_warning_punishment(ctx, warnings, action, value, reason)
        if isinstance(entry, str):
            await ctx.send(entry)
            return

        guild_cfg = self.config.guild(ctx.guild)
        punishment_id = await guild_cfg.next_warning_punishment_id()
        await guild_cfg.next_warning_punishment_id.set(punishment_id + 1)
        entry["id"] = punishment_id
        async with guild_cfg.warning_punishments() as punishments:
            punishments.append(entry)
        await ctx.send(
            f"Added warning punishment: {self._format_warning_punishment(ctx.guild, entry)}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @punish_group.command(name="remove")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def punish_remove(self, ctx: commands.Context, punishment_id: int):
        """Remove one warning punishment by its number."""
        async with self.config.guild(ctx.guild).warning_punishments() as punishments:
            for index, punishment in enumerate(punishments):
                if punishment.get("id") == punishment_id:
                    removed = punishments.pop(index)
                    break
            else:
                removed = None
        if removed is None:
            await ctx.send(f"Warning punishment #{punishment_id} was not found.")
            return
        await ctx.send(
            f"Deleted warning punishment: {self._format_warning_punishment(ctx.guild, removed)}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @punish_group.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def punish_clear(self, ctx: commands.Context):
        """Remove every warning punishment."""
        punishments = await self.config.guild(ctx.guild).warning_punishments()
        if not punishments:
            await ctx.send("No warning punishments are configured.")
            return
        if not await self._confirm_action(
            ctx, f"clear {len(punishments)} warning punishment(s)", reason="Warning punishment cleanup"
        ):
            return
        await self.config.guild(ctx.guild).warning_punishments.set([])
        await ctx.send(f"Cleared {len(punishments)} warning punishment(s).")
