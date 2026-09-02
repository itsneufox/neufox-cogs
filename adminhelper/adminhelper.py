from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.red()
DEFAULT_REASON = "No reason provided."
WARNING_PUNISHMENT_ACTIONS = {
    "timeout": "Timeout",
    "addrole": "Add role",
    "removerole": "Remove role",
    "kick": "Kick",
    "ban": "Ban",
}
WARNING_PUNISHMENT_PRIORITY = {
    "addrole": 10,
    "removerole": 10,
    "timeout": 20,
    "kick": 30,
    "ban": 40,
}

NATURAL_ACTION_PATTERNS = (
    (
        "clearwarnings",
        re.compile(
            r"\b(?:clear|remove|reset|limpar|remover|resetar)\s+(?:all\s+|todos?\s+|todas?\s+)?"
            r"(?:the\s+|os?\s+|as?\s+|manual\s+)?(?:warnings?|warns?|avisos?|advert[eê]ncias?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "untimeout",
        re.compile(
            r"\b(?:un[\s-]?timeout|un[\s-]?mute|desmutar|desmute|dessilenciar|"
            r"remove\s+(?:the\s+|o\s+|a\s+)?(?:timeout|mute)|"
            r"remov(?:er|a)\s+(?:o\s+|a\s+)?(?:silêncio|silencio|mute|timeout)|"
            r"take\s+off\s+(?:the\s+)?(?:timeout|mute))\b",
            re.IGNORECASE,
        ),
    ),
    ("softban", re.compile(r"\bsoft[\s-]?ban\b", re.IGNORECASE)),
    (
        "userinfo",
        re.compile(
            r"\b(?:user[\s-]?info|who\s+is|info\s+(?:do|da)(?:\s+usuário)?)\b",
            re.IGNORECASE,
        ),
    ),
    ("modstats", re.compile(r"\bmod(?:eration)?[\s-]?stats?\b", re.IGNORECASE)),
    ("warnings", re.compile(r"\b(?:warnings?|avisos)\b", re.IGNORECASE)),
    ("hackban", re.compile(r"\b(?:hack[\s-]?ban|force[\s-]?ban)\b", re.IGNORECASE)),
    ("purge", re.compile(r"\b(?:purge|prune|limpar)\b", re.IGNORECASE)),
    (
        "timeout",
        re.compile(
            r"\b(?:time[\s-]?out|mute|mutar|muta|silenciar|silencie|silencia|calar|cale)\b",
            re.IGNORECASE,
        ),
    ),
    ("unban", re.compile(r"\b(?:un[\s-]?ban|desbanir|desban)\b", re.IGNORECASE)),
    ("ban", re.compile(r"\b(?:ban(?:ned|ning)?|banir|bane)\b", re.IGNORECASE)),
    ("kick", re.compile(r"\b(?:kick(?:ed|ing)?|expulsar|expulse|chutar|chute)\b", re.IGNORECASE)),
    ("cases", re.compile(r"\bcases?\b", re.IGNORECASE)),
    (
        "warn",
        re.compile(
            r"\b(?:warn(?:ed|ing)?|aviso|avisar|avise|advert[eê]ncia|advertir|advirta)\b",
            re.IGNORECASE,
        ),
    ),
)
USER_MENTION_PATTERN = re.compile(r"<@!?(\d+)>")
TIMEOUT_DURATION_PATTERN = re.compile(
    r"\b(?:(?:for|por|during|durante)\s+)?(\d+)\s*"
    r"(seconds?|secs?|s|segundos?|segs?|seg|minutes?|mins?|m|minutos?|min|"
    r"hours?|hrs?|h|horas?|days?|d|dias?)\b",
    re.IGNORECASE,
)
BAN_DELETE_DAYS_PATTERN = re.compile(
    r"\b(?:delete|remove|clear|apagar|remover|limpar)\s+([0-7])\s*"
    r"(?:days?|day|dias?|dia)\b(?:\s+(?:(?:of|de)\s+)?(?:messages?|mensagens?))?",
    re.IGNORECASE,
)
BAN_BARE_DAYS_PATTERN = re.compile(r"\b([0-7])\s*(?:days?|day|dias?|dia)\b", re.IGNORECASE)
PORTUGUESE_LANGUAGE_PATTERN = re.compile(
    r"(?:\b(?:durante|por|porque|"
    r"apagar|apague|avisar|avise|avisos|banir|bane|calar|cale|chutar|chute|"
    r"desban|desbanir|desmute|desmutar|dessilenciar|dias?|devido|expulse|"
    r"expulsar|horas?|limpar|minutos?|muta|mutar|remover|segundos?|"
    r"silencia|silenciar|silencie|usuário|usuários|advertência|advertências)\b"
    r"|[ãáâàçéêíóôõú])",
    re.IGNORECASE,
)
PORTUGUESE_ARTICLE_PATTERN = re.compile(
    r"\b(?:a|as|da|das|do|dos|o|os|um|uma|uns|umas)\s+(?=<@!?\d+>)",
    re.IGNORECASE,
)

NATURAL_COMMAND_METHODS = {
    "purge": "purge_messages",
    "userinfo": "userinfo_member",
    "timeout": "timeout_member",
    "untimeout": "untimeout_member",
    "kick": "kick_member",
    "ban": "ban_member",
    "hackban": "hackban_user",
    "unban": "unban_user",
    "softban": "softban_member",
    "warn": "warn_member",
    "warnings": "warnings_member",
    "clearwarnings": "clearwarnings_member",
    "cases": "cases_member",
    "modstats": "modstats_member",
}
NATURAL_MEMBER_ACTIONS = {
    "userinfo",
    "timeout",
    "untimeout",
    "kick",
    "softban",
    "warn",
    "warnings",
    "clearwarnings",
    "cases",
    "modstats",
}
NATURAL_OPTIONAL_TARGET_ACTIONS = {"userinfo", "purge"}


def parse_natural_moderation_request(content: str, bot_id: int) -> dict[str, Any] | None:
    """Parse a bot-mention moderation request without making any decisions or API calls."""
    if not content or not bot_id:
        return None

    bot_mention = re.match(r"^\s*<@!?(\d+)>\s*", content)
    if bot_mention is None or int(bot_mention.group(1)) != bot_id:
        return None

    request = content[bot_mention.end() :].strip()
    if not request:
        return None

    mention_matches = list(USER_MENTION_PATTERN.finditer(request))
    target_mentions = [match for match in mention_matches if int(match.group(1)) != bot_id]
    target_ids = list(dict.fromkeys(int(match.group(1)) for match in target_mentions))
    first_target = target_mentions[0] if target_mentions else None
    action_search_end = first_target.start() if first_target is not None else len(request)

    action = None
    action_match = None
    for candidate, pattern in NATURAL_ACTION_PATTERNS:
        match = pattern.search(request, 0, action_search_end)
        if match is not None:
            action = candidate
            action_match = match
            break

    if action is None or action_match is None:
        return None

    # The grammar intentionally expects the action before the target. This avoids
    # interpreting ordinary text such as "@member was banned" as a command.
    if first_target is not None and first_target.start() < action_match.start():
        return None

    tail = request[action_match.end() :]
    tail_mentions = [
        match
        for match in target_mentions
        if match.start() >= action_match.end()
    ]
    tail_without_mentions = _remove_matches(tail, [
        (match.start() - action_match.end(), match.end() - action_match.end())
        for match in tail_mentions
    ])

    minutes = None
    duration_seconds = None
    duration_text = None
    duration_match = None
    if action == "timeout":
        duration_match = TIMEOUT_DURATION_PATTERN.search(tail_without_mentions)
        if duration_match is not None:
            amount = int(duration_match.group(1))
            unit = duration_match.group(2).lower()
            if unit.startswith("second") or unit.startswith("sec") or unit.startswith("seg") or unit == "s":
                duration_seconds = amount
                duration_text = _format_duration_label(amount, unit)
            elif unit.startswith("hour") or unit.startswith("hr") or unit.startswith("hora") or unit == "h":
                duration_seconds = amount * 3600
                duration_text = _format_duration_label(amount, unit)
            elif unit.startswith("day") or unit.startswith("dia") or unit == "d":
                duration_seconds = amount * 86400
                duration_text = _format_duration_label(amount, unit)
            else:
                duration_seconds = amount * 60
                duration_text = _format_duration_label(amount, unit)
            minutes = max(1, (duration_seconds + 59) // 60) if duration_seconds > 0 else 0
        else:
            # `timeout @user 10` is a useful shorthand for ten minutes.
            bare_duration = re.search(r"\b(\d+)\b", tail_without_mentions)
            if bare_duration is not None:
                minutes = int(bare_duration.group(1))
                duration_seconds = minutes * 60
                duration_text = f"{minutes} minute(s)"
                duration_match = bare_duration

    delete_days = None
    delete_days_match = None
    if action in {"ban", "hackban"}:
        delete_days_match = BAN_DELETE_DAYS_PATTERN.search(tail_without_mentions)
        if delete_days_match is None:
            delete_days_match = BAN_BARE_DAYS_PATTERN.search(tail_without_mentions)
        if delete_days_match is not None:
            delete_days = int(delete_days_match.group(1))

    reason_text = tail_without_mentions
    matches_to_remove = []
    if duration_match is not None:
        matches_to_remove.append(duration_match.span())
    if delete_days_match is not None:
        matches_to_remove.append(delete_days_match.span())
    reason_text = _remove_matches(reason_text, matches_to_remove)
    reason_text = re.sub(r"\b(?:now|please|immediately)\b", " ", reason_text, flags=re.IGNORECASE)
    reason_text = re.sub(
        r"^\s*(?:(?:the|a|an|o|os|as|um|uma|do|da)\s+)+",
        "",
        reason_text,
        flags=re.IGNORECASE,
    )
    reason_text = re.sub(
        r"^\s*(?:for|because|porque|since|due\s+to|devido\s+a|as|from|of|por|durante|"
        r"reason(?:\s+is)?)\s*:?[\s-]*",
        "",
        reason_text,
        flags=re.IGNORECASE,
    )
    reason_text = re.sub(r"\s+", " ", reason_text).strip(" .,!?:;-\t\r\n")

    amount = None
    if action == "purge":
        amount_match = re.search(r"\b(\d+)\b", tail_without_mentions)
        if amount_match is not None:
            amount = int(amount_match.group(1))

    return {
        "action": action,
        "language": _detect_natural_language(content),
        "target_id": target_ids[0] if len(target_ids) == 1 else None,
        "target_ids": target_ids,
        "minutes": minutes,
        "duration_seconds": duration_seconds,
        "duration_text": duration_text,
        "amount": amount,
        "delete_days": delete_days,
        "reason": reason_text or DEFAULT_REASON,
    }


def _detect_natural_language(content: str) -> str:
    """Return the response language for a mention-based request."""
    text = content or ""
    return "pt" if PORTUGUESE_LANGUAGE_PATTERN.search(text) or PORTUGUESE_ARTICLE_PATTERN.search(text) else "en"


def _remove_matches(value: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        value = f"{value[:start]} {value[end:]}"
    return value


def _format_duration_label(amount: int, unit: str) -> str:
    if unit.startswith("seg"):
        label = "segundo" if amount == 1 else "segundos"
    elif unit.startswith("min"):
        label = "minuto" if amount == 1 else "minutos"
    elif unit.startswith("hora"):
        label = "hora" if amount == 1 else "horas"
    elif unit.startswith("dia"):
        label = "dia" if amount == 1 else "dias"
    elif unit.startswith("second") or unit.startswith("sec") or unit == "s":
        label = "second" if amount == 1 else "seconds"
    elif unit.startswith("hour") or unit.startswith("hr") or unit == "h":
        label = "hour" if amount == 1 else "hours"
    elif unit.startswith("day") or unit == "d":
        label = "day" if amount == 1 else "days"
    else:
        label = "minute" if amount == 1 else "minutes"
    return f"{amount} {label}"


class ConfirmActionView(discord.ui.View):
    def __init__(
        self,
        author_id: int,
        action_text: str,
        reason: str,
        moderator_name: str,
        thumbnail_url: str,
        language: str = "en",
    ):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.action_text = action_text
        self.reason = reason or DEFAULT_REASON
        self.moderator_name = moderator_name
        self.thumbnail_url = thumbnail_url
        self.language = language
        self.value: bool | None = None
        self.message: discord.Message | None = None
        if self.language == "pt":
            for child in self.children:
                if getattr(child, "label", None) == "Confirm":
                    child.label = "Confirmar"
                elif getattr(child, "label", None) == "Cancel":
                    child.label = "Cancelar"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        message = (
            "Somente o moderador que executou este comando pode usar esta confirmação."
            if self.language == "pt"
            else "Only the moderator who ran this command can use this prompt."
        )
        await interaction.response.send_message(message, ephemeral=True)
        return False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self._disable_buttons()
        await interaction.response.edit_message(embed=self._result_embed("Confirmed", discord.Color.green()), view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self._disable_buttons()
        await interaction.response.edit_message(embed=self._result_embed("Cancelled", discord.Color.light_grey()), view=self)
        self.stop()

    async def on_timeout(self):
        self._disable_buttons()
        if self.message is not None:
            try:
                await self.message.edit(embed=self._result_embed("Expired", discord.Color.light_grey()), view=self)
            except (discord.Forbidden, discord.HTTPException):
                return

    def _disable_buttons(self):
        for child in self.children:
            child.disabled = True

    def prompt_embed(self) -> discord.Embed:
        if self.language == "pt":
            embed = discord.Embed(
                title="Última confirmação",
                description="Esta ação de moderação está prestes a ser executada. Confira uma última vez.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Ação", value=self._embed_value(self.action_text), inline=False)
            embed.add_field(name="Motivo", value=self._embed_value(self.reason), inline=False)
            embed.add_field(name="Moderador", value=self.moderator_name, inline=True)
            embed.add_field(name="Expira em", value="30 segundos", inline=True)
            embed.set_footer(text="Confirmar executa a ação. Cancelar não altera nada.")
            if self.thumbnail_url:
                embed.set_thumbnail(url=self.thumbnail_url)
            return embed

        embed = discord.Embed(
            title="Last Check",
            description="This moderation action is about to go through. Give it one clean look first.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Action", value=self._embed_value(self.action_text), inline=False)
        embed.add_field(name="Reason", value=self._embed_value(self.reason), inline=False)
        embed.add_field(name="Moderator", value=self.moderator_name, inline=True)
        embed.add_field(name="Expires", value="30 seconds", inline=True)
        embed.set_footer(text="Confirm runs it. Cancel keeps the paperwork clean.")
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        return embed

    def _result_embed(self, status: str, color: discord.Color) -> discord.Embed:
        if self.language == "pt":
            descriptions = {
                "Confirmed": "Tudo certo. A ação está sendo executada.",
                "Cancelled": "Cancelado. Nada foi alterado.",
                "Expired": "Sem confirmação, sem ação. O pedido expirou.",
            }
            translated_status = {
                "Confirmed": "confirmada",
                "Cancelled": "cancelada",
                "Expired": "expirada",
            }.get(status, status)
            embed = discord.Embed(
                title=f"Ação de moderação {translated_status}",
                description=descriptions.get(status, ""),
                color=color,
            )
            embed.add_field(name="Ação", value=self._embed_value(self.action_text), inline=False)
            embed.add_field(name="Motivo", value=self._embed_value(self.reason), inline=False)
            embed.add_field(name="Moderador", value=self.moderator_name, inline=True)
            if self.thumbnail_url:
                embed.set_thumbnail(url=self.thumbnail_url)
            return embed

        descriptions = {
            "Confirmed": "Locked in. The action is being carried out.",
            "Cancelled": "Called off. Nothing was changed.",
            "Expired": "No click, no action. The prompt expired.",
        }
        embed = discord.Embed(
            title=f"Moderation Action {status}",
            description=descriptions.get(status, ""),
            color=color,
        )
        embed.add_field(name="Action", value=self._embed_value(self.action_text), inline=False)
        embed.add_field(name="Reason", value=self._embed_value(self.reason), inline=False)
        embed.add_field(name="Moderator", value=self.moderator_name, inline=True)
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        return embed

    @staticmethod
    def _embed_value(value: str) -> str:
        if len(value) <= 1024:
            return value
        return value[:1021] + "..."


class AdminHelper(commands.Cog):
    """Manual moderation actions for server staff."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=911223335)
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
        self.config.register_user(
            previous_usernames=[],
            previous_global_names=[],
        )
        self.config.register_member(
            warnings=[],
            previous_nicknames=[],
        )
        for method_name in set(NATURAL_COMMAND_METHODS.values()):
            command = getattr(type(self), method_name, None)
            if command is not None:
                command.on_error = type(self)._natural_command_error

    @commands.group(name="adminhelper", aliases=["ah"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper(self, ctx: commands.Context):
        """Show AdminHelper settings."""
        settings = await self.config.guild(ctx.guild).all()
        log_channel = ctx.guild.get_channel(settings["log_channel_id"]) if settings["log_channel_id"] else None

        embed = discord.Embed(title="AdminHelper", color=DEFAULT_COLOR)
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        embed.add_field(name="DM users", value="Yes" if settings["dm_users"] else "No", inline=True)
        embed.add_field(name="Name tracking", value="Yes" if settings["name_tracking_enabled"] else "No", inline=True)
        embed.add_field(
            name="Natural-language mentions",
            value="Enabled" if settings["natural_language_enabled"] else "Disabled",
            inline=True,
        )
        embed.add_field(name="Ban cleanup", value=f"{settings['ban_delete_message_days']} day(s)", inline=True)
        embed.add_field(name="Softban cleanup", value=f"{settings['softban_delete_message_days']} day(s)", inline=True)
        warn_punishment_count = len(settings.get("warning_punishments", []))
        embed.add_field(name="Warning punishments", value=f"{warn_punishment_count} configured", inline=True)
        embed.add_field(
            name="Actions",
            value=(
                "ban, hackban, unban, kick, softban, timeout, untimeout, warn, warnings, "
                "clearwarnings, purge, userinfo, adminhelper modcommands/case/cases/reason/modstats/warnpunish"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @adminhelper.command(name="modcommands", aliases=["commands", "cheatsheet", "reference"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_modcommands(self, ctx: commands.Context):
        """Post a moderator-facing command reference."""
        prefix = await self._prefix(ctx)
        bot_mention = ctx.guild.me.mention if ctx.guild.me is not None else "@bot"
        embed = discord.Embed(
            title="Moderator Commands",
            description="Quick reference for day-to-day moderation actions.",
            color=DEFAULT_COLOR,
        )
        embed.add_field(
            name="Member Actions",
            value=(
                f"`{prefix}warn @user <reason>` - add a manual warning\n"
                f"`{prefix}timeout @user <minutes> <reason>` - timeout a member\n"
                f"`{prefix}kick @user <reason>` - kick a member\n"
                f"`{prefix}ban @user [0-7 days] <reason>` - ban with optional message cleanup override\n"
                f"`{prefix}softban @user <reason>` - ban/unban to clean recent messages\n"
                f"`{prefix}unban <user id/name> <reason>` - unban a user"
            ),
            inline=False,
        )
        embed.add_field(
            name="Natural-language mentions",
            value=(
                f"`{bot_mention} ban @user [reason]`\n"
                f"`{bot_mention} kick @user [reason]`\n"
                f"`{bot_mention} timeout @user 10 minutes [reason]`\n"
                f"`{bot_mention} mute o @user por 10 segundos`\n"
                f"`{bot_mention} warn @user [reason]`\n"
                "English and common Portuguese forms are accepted; you can also say `please`, `because`, `por`, or `for`. These requests use the same checks and confirmation prompts.\n"
                f"Toggle with `{prefix}ah natural on|off`."
            ),
            inline=False,
        )
        embed.add_field(
            name="Message Cleanup",
            value=(
                f"`{prefix}purge <1-200> [@user]` - delete recent messages in this channel"
            ),
            inline=False,
        )
        embed.add_field(
            name="Lookup",
            value=(
                f"`{prefix}userinfo [@user]` - account, role, warning, and name-history summary\n"
                f"`{prefix}warnings @user` - list manual warnings and AutoMod strikes\n"
                f"`{prefix}clearwarnings @user <reason>` - clear manual warnings and AutoMod strikes\n"
                f"`{prefix}ah case <id>` - show a case\n"
                f"`{prefix}ah cases @user` - show recent cases for a member\n"
                f"`{prefix}ah modstats @user` - show actions performed/received"
            ),
            inline=False,
        )
        embed.set_footer(text="Destructive actions ask for confirmation when needed.")
        await ctx.send(embed=embed)

    @adminhelper.command(name="logchannel")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_logchannel(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """Set or clear the moderation log channel."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id if channel else None)
        if channel is None:
            await ctx.send("AdminHelper log channel cleared.")
            return
        await ctx.send(f"AdminHelper actions will be logged in {channel.mention}.")

    @adminhelper.command(name="dm")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_dm(self, ctx: commands.Context, enabled: bool):
        """Enable or disable DM notices to moderated users."""
        await self.config.guild(ctx.guild).dm_users.set(enabled)
        await ctx.send(f"User DM notices {'enabled' if enabled else 'disabled'}.")

    @adminhelper.command(name="natural", aliases=["natural-language", "nl"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_natural(self, ctx: commands.Context, enabled: bool | None = None):
        """View or toggle mention-based natural-language moderation."""
        setting = self.config.guild(ctx.guild).natural_language_enabled
        if enabled is None:
            await ctx.send(f"Natural-language moderation is {'enabled' if await setting() else 'disabled'}.")
            return
        await setting.set(enabled)
        await ctx.send(f"Natural-language moderation {'enabled' if enabled else 'disabled'}.")

    @adminhelper.command(name="nametracking")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_nametracking(self, ctx: commands.Context, enabled: bool):
        """Enable or disable username/display-name/nickname history tracking."""
        await self.config.guild(ctx.guild).name_tracking_enabled.set(enabled)
        await ctx.send(f"Name history tracking {'enabled' if enabled else 'disabled'}.")

    @adminhelper.command(name="cleanup")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def adminhelper_cleanup(
        self,
        ctx: commands.Context,
        ban_delete_message_days: int | None = None,
        softban_delete_message_days: int | None = None,
    ):
        """View or set message cleanup days for ban and softban actions."""
        guild_cfg = self.config.guild(ctx.guild)
        if ban_delete_message_days is None and softban_delete_message_days is None:
            settings = await guild_cfg.all()
            await ctx.send(
                f"Ban cleanup: {settings['ban_delete_message_days']} day(s). "
                f"Softban cleanup: {settings['softban_delete_message_days']} day(s)."
            )
            return

        if ban_delete_message_days is not None:
            if not 0 <= ban_delete_message_days <= 7:
                await ctx.send("ban_delete_message_days must be between 0 and 7.")
                return
            await guild_cfg.ban_delete_message_days.set(ban_delete_message_days)
        if softban_delete_message_days is not None:
            if not 0 <= softban_delete_message_days <= 7:
                await ctx.send("softban_delete_message_days must be between 0 and 7.")
                return
            await guild_cfg.softban_delete_message_days.set(softban_delete_message_days)

        await ctx.send("Cleanup settings updated.")

    @commands.command(name="purge", aliases=["prune"])
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def purge_messages(self, ctx: commands.Context, amount: int, member: discord.Member | None = None):
        """Delete recent messages from this channel. Optionally limit cleanup to one member."""
        if amount < 1 or amount > 200:
            await self._send_moderation_response(
                ctx,
                "Purge amount must be between 1 and 200.",
                "A quantidade deve estar entre 1 e 200.",
            )
            return

        purge = getattr(ctx.channel, "purge", None)
        if purge is None:
            await self._send_moderation_response(
                ctx,
                "This channel does not support message purging.",
                "Este canal não permite apagar mensagens.",
            )
            return

        if ctx.guild.me is None or not ctx.guild.me.guild_permissions.manage_messages:
            await self._send_moderation_response(
                ctx,
                "Unable to purge: I am missing the `manage_messages` permission.",
                "Não foi possível limpar: estou sem a permissão `manage_messages`.",
            )
            return

        channel_text = getattr(ctx.channel, "mention", str(ctx.channel))
        target_text = f" from {member.mention}" if member is not None else ""
        if not await self._confirm_action(
            ctx,
            f"purge up to {amount} message(s){target_text} in {channel_text}",
            reason="Manual message cleanup",
            target=member,
        ):
            return

        remaining = amount

        def should_delete(message: discord.Message) -> bool:
            nonlocal remaining
            if remaining <= 0:
                return False
            if member is not None and message.author.id != member.id:
                return False
            remaining -= 1
            return True

        scan_limit = amount if member is None else min(max(amount * 5, amount), 1000)
        try:
            deleted = await purge(
                limit=scan_limit,
                before=ctx.message,
                check=should_delete,
                bulk=True,
                reason=self._audit_reason(ctx.author, "Manual message purge"),
            )
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                "Unable to purge messages in this channel.",
                "Não foi possível limpar as mensagens neste canal.",
            )
            return

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        await self._log_purge(ctx.guild, ctx.author, ctx.channel, len(deleted), member)
        scope = f" from {member.mention}" if member is not None else ""
        portuguese_scope = f" de {member.mention}" if member is not None else ""
        await self._send_moderation_response(
            ctx,
            f"Purged {len(deleted)} message(s){scope}.",
            f"{len(deleted)} mensagem(ns) apagada(s){portuguese_scope}.",
            delete_after=10,
        )

    @commands.command(name="userinfo", aliases=["ui", "whois"])
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def userinfo_member(self, ctx: commands.Context, member: discord.Member = None):
        """Show account, server, and moderation stats for a member."""
        member = member or ctx.author
        await self._maybe_set_natural_context(ctx)
        portuguese = self._natural_language(ctx) == "pt"
        warnings = await self.config.member(member).warnings()
        antiabuse_warnings = await self._antiabuse_warning_count(member)
        timeout_until = getattr(member, "communication_disabled_until", None)
        timeout_text = None
        if timeout_until and timeout_until.timestamp() > datetime.now(timezone.utc).timestamp():
            timeout_text = (
                f"Silenciado até {self._format_profile_timestamp(timeout_until)}"
                if portuguese
                else f"Timed out until {self._format_profile_timestamp(timeout_until)}"
            )

        roles = [role for role in member.roles if role != ctx.guild.default_role]
        sorted_roles = sorted(roles, key=lambda role: role.position, reverse=True)
        role_mentions = [role.mention for role in sorted_roles]
        displayed_roles = ", ".join(role_mentions[:12]) if role_mentions else "None"
        if len(role_mentions) > 12:
            displayed_roles += f", +{len(role_mentions) - 12} more"

        status_icon = self._status_icon(member.status)
        status_text = self._status_text(member)
        if portuguese:
            status_text = {
                "offline": "Descansando no modo offline",
                "online": "Online",
                "idle": "Ausente",
                "dnd": "Não perturbe",
            }.get(str(member.status), status_text)
        name_tracking_enabled = await self.config.guild(ctx.guild).name_tracking_enabled()
        user_history = await self.config.user(member).all()
        member_history = await self.config.member(member).all()
        description_lines = [
            f"{status_icon} **{member.display_name}**",
            status_text,
            "",
            "**Entrou no Discord em**" if portuguese else "**Joined Discord on**",
            self._format_profile_timestamp(member.created_at),
            "**Entrou neste servidor em**" if portuguese else "**Joined this server on**",
            self._format_profile_timestamp(member.joined_at),
        ]

        embed = discord.Embed(description="\n".join(description_lines), color=member.color or DEFAULT_COLOR)
        avatar_url = self._avatar_url(member)
        if avatar_url:
            embed.set_author(name=str(member), icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)
        else:
            embed.set_author(name=str(member))
        embed.add_field(
            name="Usuário" if portuguese else "User",
            value=f"{member.mention}\n`{member.id}`",
            inline=True,
        )
        embed.add_field(
            name="Sinalizadores" if portuguese else "Flags",
            value=(
                f"Bot: {'Sim' if member.bot else 'Não'}\n"
                f"Admin: {'Sim' if member.guild_permissions.administrator else 'Não'}"
                if portuguese
                else (
                    f"Bot: {'Yes' if member.bot else 'No'}\n"
                    f"Admin: {'Yes' if member.guild_permissions.administrator else 'No'}"
                )
            ),
            inline=True,
        )
        embed.add_field(
            name="Impulsionando desde" if portuguese else "Boosting since",
            value=self._format_timestamp(member.premium_since),
            inline=True,
        )
        embed.add_field(
            name="Moderação" if portuguese else "Moderation",
            value=(
                f"Silenciado: {timeout_text or 'Não'}\n"
                f"Avisos: {len(warnings)} manual(is)\n"
                f"Infrações do AutoMod: {antiabuse_warnings}"
                if portuguese
                else (
                    f"Timed out: {timeout_text or 'No'}\n"
                    f"Warnings: {len(warnings)} manual\n"
                    f"AutoMod strikes: {antiabuse_warnings}"
                )
            ),
            inline=False,
        )
        embed.add_field(
            name=f"Cargos ({len(roles)})" if portuguese else f"Roles ({len(roles)})",
            value=displayed_roles[:1024],
            inline=False,
        )
        embed.add_field(
            name="Histórico de nomes" if portuguese else "Name history",
            value=(
                self._format_name_history_portuguese(user_history, member_history, name_tracking_enabled)
                if portuguese
                else self._format_name_history(user_history, member_history, name_tracking_enabled)
            ),
            inline=False,
        )
        member_number = self._member_number(ctx.guild, member)
        embed.set_footer(
            text=(
                f"Membro nº {member_number} | ID do usuário: {member.id}"
                if portuguese
                else f"Member #{member_number} | User ID: {member.id}"
            )
        )
        await ctx.send(embed=embed)

    @commands.command(name="timeout", aliases=["to", "mute"])
    @commands.admin_or_permissions(moderate_members=True)
    @commands.guild_only()
    async def timeout_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        minutes: int,
        *,
        reason: str = DEFAULT_REASON,
    ):
        """Timeout a member for a number of minutes."""
        bot_user = self.bot.user
        message = getattr(ctx, "message", None)
        if bot_user is not None and message is not None and message.guild is not None:
            natural_request = parse_natural_moderation_request(message.content, bot_user.id)
            if (
                natural_request is not None
                and natural_request["action"] == "timeout"
                and natural_request["target_id"] == member.id
                and natural_request["duration_seconds"] is not None
                and await self.config.guild(message.guild).natural_language_enabled()
            ):
                self._set_natural_context(ctx, natural_request)
                await self._apply_timeout(
                    ctx,
                    member,
                    timedelta(seconds=natural_request["duration_seconds"]),
                    natural_request["duration_text"] or f"{natural_request['minutes']} minute(s)",
                    natural_request["reason"],
                )
                return

        if minutes <= 0:
            await self._send_moderation_response(
                ctx,
                "Timeout duration must be greater than 0.",
                "A duração do silêncio deve ser maior que 0.",
            )
            return
        await self._apply_timeout(
            ctx,
            member,
            timedelta(minutes=minutes),
            f"{minutes} minute(s)",
            reason,
        )

    async def _apply_timeout(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: timedelta,
        duration_text: str,
        reason: str,
    ):
        """Apply a timeout after the command-level permission check has run."""
        if duration.total_seconds() <= 0:
            await self._send_moderation_response(
                ctx,
                "Timeout duration must be greater than 0.",
                "A duração do silêncio deve ser maior que 0.",
            )
            return

        allowed, failure = await self._can_moderate_member(ctx.guild, member, "timeout", actor=ctx.author)
        if not allowed:
            await self._send_moderation_response(
                ctx,
                f"Unable to timeout {member.mention}: {failure}",
                f"Não foi possível silenciar {member.mention}: {self._natural_failure(failure)}",
            )
            return

        until = datetime.now(timezone.utc) + duration
        audit_reason = self._audit_reason(ctx.author, reason)
        try:
            await member.edit(communication_disabled_until=until, reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                f"Unable to timeout {member.mention}.",
                f"Não foi possível silenciar {member.mention}.",
            )
            return

        await self._maybe_dm(
            member,
            ctx.guild,
            "timeout",
            reason,
            duration=duration_text,
            language=self._natural_language(ctx),
        )
        case_id = await self._create_case(ctx.guild, ctx.author, member, "Timeout", reason, duration=duration_text)
        await self._log_action(ctx.guild, ctx.author, member, "Timeout", reason, duration=duration_text, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"{member.mention} has been timed out for {duration_text}.",
            f"{member.mention} ficou em silêncio por {duration_text}.",
        )

    @commands.command(name="untimeout", aliases=["unmute"])
    @commands.admin_or_permissions(moderate_members=True)
    @commands.guild_only()
    async def untimeout_member(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = DEFAULT_REASON,
    ):
        """Remove a member timeout."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "timeout", actor=ctx.author)
        if not allowed:
            await self._send_moderation_response(
                ctx,
                f"Unable to remove timeout from {member.mention}: {failure}",
                f"Não foi possível remover o silêncio de {member.mention}: {self._natural_failure(failure)}",
            )
            return

        try:
            await member.edit(communication_disabled_until=None, reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                f"Unable to remove timeout from {member.mention}.",
                f"Não foi possível remover o silêncio de {member.mention}.",
            )
            return

        case_id = await self._create_case(ctx.guild, ctx.author, member, "Untimeout", reason)
        await self._log_action(ctx.guild, ctx.author, member, "Untimeout", reason, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"Timeout removed from {member.mention}.",
            f"O silêncio de {member.mention} foi removido.",
        )

    @commands.command(name="kick")
    @commands.admin_or_permissions(kick_members=True)
    @commands.guild_only()
    async def kick_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Kick a member."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "kick", actor=ctx.author)
        if not allowed:
            await self._send_moderation_response(
                ctx,
                f"Unable to kick {member.mention}: {failure}",
                f"Não foi possível expulsar {member.mention}: {self._natural_failure(failure)}",
            )
            return
        if not await self._confirm_action(ctx, f"kick {member.mention}", reason=reason, target=member):
            return

        await self._maybe_dm(member, ctx.guild, "kick", reason, language=self._natural_language(ctx))
        try:
            await member.kick(reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                f"Unable to kick {member.mention}.",
                f"Não foi possível expulsar {member.mention}.",
            )
            return

        case_id = await self._create_case(ctx.guild, ctx.author, member, "Kick", reason)
        await self._log_action(ctx.guild, ctx.author, member, "Kick", reason, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"{member.mention} has been kicked.",
            f"{member.mention} foi expulso.",
        )

    @commands.command(name="ban")
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def ban_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Ban a member. Optionally start the reason with 0-7 delete days."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "ban", actor=ctx.author)
        if not allowed:
            await self._send_moderation_response(
                ctx,
                f"Unable to ban {member.mention}: {failure}",
                f"Não foi possível banir {member.mention}: {self._natural_failure(failure)}",
            )
            return

        default_delete_days = await self.config.guild(ctx.guild).ban_delete_message_days()
        parsed = self._parse_delete_days_reason(reason, default_delete_days)
        if isinstance(parsed, str):
            await self._send_moderation_response(
                ctx,
                parsed,
                "Os dias de exclusão das mensagens devem estar entre 0 e 7.",
            )
            return
        delete_days, reason = parsed
        if not await self._confirm_action(
            ctx,
            f"ban {member.mention} and delete {delete_days} day(s) of messages",
            reason=reason,
            target=member,
        ):
            return
        await self._maybe_dm(member, ctx.guild, "ban", reason, language=self._natural_language(ctx))
        try:
            await member.ban(delete_message_days=delete_days, reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                f"Unable to ban {member.mention}.",
                f"Não foi possível banir {member.mention}.",
            )
            return

        extra = f"Deleted {delete_days} day(s) of messages"
        case_id = await self._create_case(ctx.guild, ctx.author, member, "Ban", reason, extra=extra)
        await self._log_action(ctx.guild, ctx.author, member, "Ban", reason, extra=extra, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"{member.mention} has been banned.",
            f"{member.mention} foi banido.",
        )

    @commands.command(name="hackban", aliases=["forceban"])
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def hackban_user(self, ctx: commands.Context, user: discord.User, *, reason: str = DEFAULT_REASON):
        """Ban a user by ID or mention. Optionally start the reason with 0-7 delete days."""
        member = ctx.guild.get_member(user.id)
        if member is not None:
            allowed, failure = await self._can_moderate_member(ctx.guild, member, "ban", actor=ctx.author)
            if not allowed:
                await self._send_moderation_response(
                    ctx,
                    f"Unable to ban {member.mention}: {failure}",
                    f"Não foi possível banir {member.mention}: {self._natural_failure(failure)}",
                )
                return
        elif ctx.guild.me is None or not ctx.guild.me.guild_permissions.ban_members:
            await self._send_moderation_response(
                ctx,
                "Unable to ban: I am missing the `ban_members` permission.",
                "Não foi possível banir: estou sem a permissão `ban_members`.",
            )
            return

        default_delete_days = await self.config.guild(ctx.guild).ban_delete_message_days()
        parsed = self._parse_delete_days_reason(reason, default_delete_days)
        if isinstance(parsed, str):
            await self._send_moderation_response(
                ctx,
                parsed,
                "Os dias de exclusão das mensagens devem estar entre 0 e 7.",
            )
            return
        delete_days, reason = parsed
        if not await self._confirm_action(
            ctx,
            f"ban {user} and delete {delete_days} day(s) of messages",
            reason=reason,
            target=user,
        ):
            return
        try:
            await ctx.guild.ban(user, delete_message_days=delete_days, reason=self._audit_reason(ctx.author, reason))
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                f"Unable to ban {user}.",
                f"Não foi possível banir {user}.",
            )
            return

        extra = f"Deleted {delete_days} day(s) of messages"
        case_id = await self._create_case(ctx.guild, ctx.author, user, "Hackban", reason, extra=extra)
        await self._log_action(ctx.guild, ctx.author, user, "Hackban", reason, extra=extra, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"{user} has been banned.",
            f"{user} foi banido.",
        )

    @commands.command(name="unban")
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def unban_user(self, ctx: commands.Context, user: discord.User, *, reason: str = DEFAULT_REASON):
        """Unban a user."""
        if ctx.guild.me is None or not ctx.guild.me.guild_permissions.ban_members:
            await self._send_moderation_response(
                ctx,
                "Unable to unban: I am missing the `ban_members` permission.",
                "Não foi possível desbanir: estou sem a permissão `ban_members`.",
            )
            return
        try:
            await ctx.guild.unban(user, reason=self._audit_reason(ctx.author, reason))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                f"Unable to unban {user}.",
                f"Não foi possível desbanir {user}.",
            )
            return

        case_id = await self._create_case(ctx.guild, ctx.author, user, "Unban", reason)
        await self._log_action(ctx.guild, ctx.author, user, "Unban", reason, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"{user} has been unbanned.",
            f"{user} foi desbanido.",
        )

    @commands.command(name="softban")
    @commands.admin_or_permissions(ban_members=True)
    @commands.guild_only()
    async def softban_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Ban then immediately unban a member to clean recent messages."""
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "ban", actor=ctx.author)
        if not allowed:
            await self._send_moderation_response(
                ctx,
                f"Unable to softban {member.mention}: {failure}",
                f"Não foi possível aplicar softban em {member.mention}: {self._natural_failure(failure)}",
            )
            return

        delete_days = await self.config.guild(ctx.guild).softban_delete_message_days()
        if not await self._confirm_action(
            ctx,
            f"softban {member.mention} and delete {delete_days} day(s) of messages",
            reason=reason,
            target=member,
        ):
            return
        audit_reason = self._audit_reason(ctx.author, f"Softban: {reason}")
        await self._maybe_dm(
            member,
            ctx.guild,
            "softban",
            reason,
            extra=f"Deleted {delete_days} day(s) of messages",
            language=self._natural_language(ctx),
        )
        try:
            await member.ban(delete_message_days=delete_days, reason=audit_reason)
            await ctx.guild.unban(member, reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                f"Unable to softban {member.mention}.",
                f"Não foi possível aplicar softban em {member.mention}.",
            )
            return

        extra = f"Deleted {delete_days} day(s) of messages"
        case_id = await self._create_case(ctx.guild, ctx.author, member, "Softban", reason, extra=extra)
        await self._log_action(ctx.guild, ctx.author, member, "Softban", reason, extra=extra, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"{member.mention} has been softbanned.",
            f"Softban aplicado em {member.mention}.",
        )

    @commands.command(name="warn")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def warn_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Warn a member and store the warning."""
        await self._maybe_set_natural_context(ctx)
        allowed, failure = await self._can_moderate_member(ctx.guild, member, "warn", actor=ctx.author)
        if not allowed:
            await self._send_moderation_response(
                ctx,
                f"Unable to warn {member.mention}: {failure}",
                f"Não foi possível avisar {member.mention}: {self._natural_failure(failure)}",
            )
            return

        current_warnings = await self.config.member(member).warnings()
        next_warning_count = len(current_warnings) + 1
        triggered_punishments = await self._warning_punishments_for_count(ctx.guild, next_warning_count)
        if triggered_punishments and not await self._confirm_action(
            ctx,
            (
                f"warn {member.mention}; warning #{next_warning_count} triggers "
                f"{self._format_warning_punishment_summary(ctx.guild, triggered_punishments)}"
            ),
            reason=reason,
            target=member,
        ):
            return

        case_id = await self._create_case(ctx.guild, ctx.author, member, "Warn", reason)
        warning = {
            "moderator_id": ctx.author.id,
            "reason": reason,
            "created_at": int(datetime.now(timezone.utc).timestamp()),
            "case_id": case_id,
        }
        async with self.config.member(member).warnings() as warnings:
            warnings.append(warning)
            count = len(warnings)

        await self._maybe_dm(member, ctx.guild, "warn", reason, language=self._natural_language(ctx))
        await self._log_action(ctx.guild, ctx.author, member, "Warn", reason, extra=f"Warning #{count}", case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"{member.mention} has been warned. This is warning #{count}.",
            f"{member.mention} recebeu um aviso. Este é o aviso nº {count}.",
        )
        if triggered_punishments:
            results = await self._apply_warning_punishments(ctx, member, count, triggered_punishments)
            if results:
                await self._send_moderation_response(
                    ctx,
                    "\n".join(results),
                    "\n".join(self._translate_warning_result(result) for result in results),
                )

    @commands.command(name="warnings")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def warnings_member(self, ctx: commands.Context, member: discord.Member):
        """Show stored warnings for a member."""
        await self._maybe_set_natural_context(ctx)
        warnings = await self.config.member(member).warnings()
        antiabuse_warnings = await self._antiabuse_warning_count(member)
        if not warnings:
            if antiabuse_warnings:
                await self._send_moderation_response(
                    ctx,
                    f"{member.mention} has no manual AdminHelper warnings.\n"
                    f"AutoMod strikes: {antiabuse_warnings}.",
                    f"{member.mention} não tem avisos manuais do AdminHelper.\n"
                    f"Infrações do AutoMod: {antiabuse_warnings}.",
                )
                return
            await self._send_moderation_response(
                ctx,
                f"{member.mention} has no manual AdminHelper warnings or AutoMod strikes.",
                f"{member.mention} não tem avisos manuais do AdminHelper nem infrações do AutoMod.",
            )
            return

        lines = []
        portuguese_lines = []
        for idx, warning in enumerate(warnings, start=1):
            moderator = ctx.guild.get_member(warning.get("moderator_id"))
            moderator_text = moderator.mention if moderator else str(warning.get("moderator_id", "unknown"))
            created_at = warning.get("created_at", 0)
            timestamp = f"<t:{created_at}:R>" if created_at else "unknown time"
            warning_reason = warning.get("reason", DEFAULT_REASON)
            lines.append(f"`#{idx}` {timestamp} by {moderator_text}: {warning_reason}")
            portuguese_timestamp = f"<t:{created_at}:R>" if created_at else "horário desconhecido"
            portuguese_lines.append(
                f"`#{idx}` {portuguese_timestamp} por {moderator_text}: {warning_reason}"
            )

        portuguese = self._natural_language(ctx) == "pt"
        header = (
            f"Avisos manuais para {member.mention} ({len(warnings)} no total):"
            if portuguese
            else f"Manual warnings for {member.mention} ({len(warnings)} total):"
        )
        footer = (
            f"Infrações do AutoMod: {antiabuse_warnings}."
            if portuguese
            else f"AutoMod strikes: {antiabuse_warnings}."
        )

        await self._send_chunked_lines(
            ctx,
            header,
            portuguese_lines if portuguese else lines,
            footer=footer,
        )

    @commands.command(name="clearwarnings", aliases=["clearwarns"])
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def clearwarnings_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Clear stored warnings and AutoMod strikes for a member."""
        previous_manual = len(await self.config.member(member).warnings())
        previous_automod = await self._antiabuse_warning_count(member)
        if not previous_manual and not previous_automod:
            await self._send_moderation_response(
                ctx,
                f"{member.mention} has no manual AdminHelper warnings or AutoMod strikes.",
                f"{member.mention} não tem avisos manuais do AdminHelper nem infrações do AutoMod.",
            )
            return

        if not await self._confirm_action(
            ctx,
            (
                f"clear {previous_manual} manual warning(s) and "
                f"{previous_automod} AutoMod strike(s) for {member.mention}"
            ),
            reason=reason,
            target=member,
        ):
            return
        await self.config.member(member).warnings.set([])
        await self._clear_antiabuse_warning_count(member)
        extra = f"Cleared {previous_manual} manual warning(s) and {previous_automod} AutoMod strike(s)"
        case_id = await self._create_case(ctx.guild, ctx.author, member, "Clear Warnings", reason, extra=extra)
        await self._log_action(ctx.guild, ctx.author, member, "Clear Warnings", reason, extra=extra, case_id=case_id)
        await self._send_moderation_response(
            ctx,
            f"Cleared {previous_manual} manual warning(s) and "
            f"{previous_automod} AutoMod strike(s) for {member.mention}.",
            f"Foram limpos {previous_manual} aviso(s) manual(is) e "
            f"{previous_automod} infração(ões) do AutoMod de {member.mention}.",
        )

    @adminhelper.command(name="case")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def case_info(self, ctx: commands.Context, case_id: int):
        """Show one moderation case."""
        case = await self._get_case(ctx.guild, case_id)
        if case is None:
            await ctx.send(f"Case #{case_id} was not found.")
            return
        await ctx.send(embed=self._case_embed(ctx.guild, case))

    @adminhelper.command(name="cases")
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
        await ctx.send(header + "\n" + "\n".join(lines))

    @adminhelper.command(name="reason")
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
                    await ctx.send(f"Case #{case_id} reason updated.")
                    return
        await ctx.send(f"Case #{case_id} was not found.")

    @adminhelper.command(name="modstats")
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
        embed = discord.Embed(
            title=f"Estatísticas de moderação: {member}" if portuguese else f"Mod Stats: {member}",
            color=member.color or DEFAULT_COLOR,
        )
        embed.add_field(
            name="Ações realizadas" if portuguese else "Actions performed",
            value=(
                self._format_action_counts_portuguese(moderator_counts)
                if portuguese
                else self._format_action_counts(moderator_counts)
            ),
            inline=False,
        )
        embed.add_field(
            name="Ações recebidas" if portuguese else "Actions received",
            value=(
                self._format_action_counts_portuguese(target_counts)
                if portuguese
                else self._format_action_counts(target_counts)
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Realizadas: {len(as_moderator)} caso(s) | Recebidas: {len(as_target)} caso(s)"
                if portuguese
                else f"Performed {len(as_moderator)} case(s) | Received {len(as_target)} case(s)"
            )
        )
        await ctx.send(embed=embed)

    @adminhelper.group(
        name="warnpunish",
        aliases=["warningpunish", "warnpunishments"],
        invoke_without_command=True,
    )
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def warnpunish_group(self, ctx: commands.Context):
        """Show configured manual-warning punishments."""
        await self._send_warning_punishments(ctx)

    @warnpunish_group.command(name="list")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def warnpunish_list(self, ctx: commands.Context):
        """List configured manual-warning punishments."""
        await self._send_warning_punishments(ctx)

    @warnpunish_group.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def warnpunish_add(
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
        `[p]ah warnpunish add 3 timeout 10 repeated spam`
        `[p]ah warnpunish add 4 addrole @Muted`
        `[p]ah warnpunish add 5 kick repeated issues`
        `[p]ah warnpunish add 6 ban 0 repeated issues`
        """
        entry_or_error = await self._build_warning_punishment(ctx, warnings, action, value, reason)
        if isinstance(entry_or_error, str):
            await ctx.send(entry_or_error)
            return

        guild_cfg = self.config.guild(ctx.guild)
        punishment_id = await guild_cfg.next_warning_punishment_id()
        await guild_cfg.next_warning_punishment_id.set(punishment_id + 1)
        entry_or_error["id"] = punishment_id

        async with guild_cfg.warning_punishments() as punishments:
            punishments.append(entry_or_error)

        await ctx.send(f"Added warning punishment: {self._format_warning_punishment(ctx.guild, entry_or_error)}")

    @warnpunish_group.command(name="delete", aliases=["remove", "del"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def warnpunish_delete(self, ctx: commands.Context, punishment_id: int):
        """Delete one configured warning punishment by ID."""
        async with self.config.guild(ctx.guild).warning_punishments() as punishments:
            for index, punishment in enumerate(punishments):
                if punishment.get("id") == punishment_id:
                    removed = punishments.pop(index)
                    await ctx.send(f"Deleted warning punishment: {self._format_warning_punishment(ctx.guild, removed)}")
                    return

        await ctx.send(f"Warning punishment #{punishment_id} was not found.")

    @warnpunish_group.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def warnpunish_clear(self, ctx: commands.Context):
        """Clear every configured warning punishment."""
        punishments = await self.config.guild(ctx.guild).warning_punishments()
        if not punishments:
            await ctx.send("No warning punishments are configured.")
            return

        if not await self._confirm_action(
            ctx,
            f"clear {len(punishments)} warning punishment(s)",
            reason="Warning punishment cleanup",
        ):
            return

        await self.config.guild(ctx.guild).warning_punishments.set([])
        await ctx.send(f"Cleared {len(punishments)} warning punishment(s).")

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if not await self._name_tracking_enabled_for_user(after):
            return
        if before.name != after.name:
            await self._append_history(self.config.user(after).previous_usernames, before.name)

        before_global_name = getattr(before, "global_name", None)
        after_global_name = getattr(after, "global_name", None)
        if before_global_name and before_global_name != after_global_name:
            await self._append_history(self.config.user(after).previous_global_names, before_global_name)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not await self.config.guild(after.guild).name_tracking_enabled():
            return
        if before.nick and before.nick != after.nick:
            await self._append_history(self.config.member(after).previous_nicknames, before.nick)

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        """Handle moderation requests addressed to the bot without a prefix."""
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return

        bot_user = self.bot.user
        if bot_user is None:
            return
        if not await self.config.guild(message.guild).natural_language_enabled():
            return

        request = parse_natural_moderation_request(message.content, bot_user.id)
        if request is None:
            return

        await self._handle_natural_request(message, request)

    async def _handle_natural_request(
        self,
        message: discord.Message,
        request: dict[str, Any],
    ):
        bot_user = self.bot.user
        if bot_user is None or message.guild is None:
            return

        action = request["action"]
        language = request.get("language", "en")
        target_ids = request["target_ids"]
        if len(target_ids) > 1:
            await self._send_natural_reply(
                message,
                "Please mention exactly one user for a moderation action.",
                "Mencione exatamente um usuário para a ação de moderação.",
                language=language,
            )
            return

        target_id = request["target_id"]
        if target_id is None and action not in NATURAL_OPTIONAL_TARGET_ACTIONS:
            await self._send_natural_reply(
                message,
                f"Please mention the member you want to {action}, for example: `{bot_user.mention} {action} @member`.",
                f"Mencione o membro que deseja moderar, por exemplo: `{bot_user.mention} {action} @membro`.",
                language=language,
            )
            return

        if action == "timeout" and request["duration_seconds"] is None:
            await self._send_natural_reply(
                message,
                f"Please include a timeout duration, for example: `{bot_user.mention} timeout @member 10 minutes`.",
                f"Inclua uma duração para o silêncio, por exemplo: `{bot_user.mention} mute @membro por 10 minutos`.",
                language=language,
            )
            return

        if action == "purge" and request["amount"] is None:
            await self._send_natural_reply(
                message,
                f"Please include a purge amount, for example: `{bot_user.mention} purge 10 messages`.",
                f"Inclua uma quantidade para limpar, por exemplo: `{bot_user.mention} purge 10 mensagens`.",
                language=language,
            )
            return

        try:
            ctx = await self.bot.get_context(message)
        except Exception:
            return
        self._set_natural_context(ctx, request)

        target = None
        if target_id is not None:
            target = await self._resolve_natural_target(message, target_id, action)
            if target is None:
                await self._send_natural_reply(
                    message,
                    "I could not resolve that user in this server.",
                    "Não consegui encontrar esse usuário neste servidor.",
                    language=language,
                )
                return

        command_action = action
        if action == "ban" and not isinstance(target, discord.Member):
            command_action = "hackban"

        reason = request["reason"]
        if command_action in {"ban", "hackban"} and request["delete_days"] is not None:
            reason = f"{request['delete_days']} {reason}"

        args = ()
        kwargs: dict[str, Any] = {}
        if command_action in {"kick", "ban", "hackban", "unban", "softban", "warn", "warnings", "clearwarnings", "cases", "modstats"}:
            args = (target,)
            if command_action in {"kick", "ban", "hackban", "unban", "softban", "warn", "clearwarnings"}:
                kwargs["reason"] = reason
        elif command_action == "timeout":
            await self._invoke_natural_command(
                ctx,
                command_action,
                target,
                request,
                callback_override=self._run_natural_timeout,
            )
            return
        elif command_action == "untimeout":
            args = (target,)
            kwargs["reason"] = reason
        elif command_action == "purge":
            args = (request["amount"], target)
        elif command_action == "userinfo":
            args = (target,) if target is not None else ()

        await self._invoke_natural_command(ctx, command_action, *args, **kwargs)

    async def _natural_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Turn malformed mention-prefixed command invocations into natural requests."""
        message = getattr(ctx, "message", None)
        bot_user = self.bot.user
        request = None
        if message is not None and bot_user is not None:
            request = parse_natural_moderation_request(message.content, bot_user.id)

        if request is not None and message.guild is not None:
            if await self.config.guild(message.guild).natural_language_enabled():
                await self._handle_natural_request(message, request)
                return

        if isinstance(error, commands.UserInputError):
            await ctx.send_help()
            return
        await ctx.send("I could not run that moderation command.")

    async def _resolve_natural_target(
        self,
        message: discord.Message,
        target_id: int,
        action: str,
    ) -> discord.Member | discord.User | None:
        guild = message.guild
        if guild is None:
            return None

        member = guild.get_member(target_id)
        mentioned_user = next(
            (user for user in getattr(message, "mentions", ()) if getattr(user, "id", None) == target_id),
            None,
        )
        if member is None and isinstance(mentioned_user, discord.Member):
            member = mentioned_user

        if action in NATURAL_MEMBER_ACTIONS or action == "purge":
            return member
        if member is not None:
            return member
        if mentioned_user is not None:
            return mentioned_user

        user = self.bot.get_user(target_id)
        if user is not None:
            return user

        fetch_user = getattr(self.bot, "fetch_user", None)
        if fetch_user is None:
            return None
        try:
            return await fetch_user(target_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            return None

    async def _invoke_natural_command(
        self,
        ctx: commands.Context,
        action: str,
        *args,
        callback_override=None,
        **kwargs,
    ):
        method_name = NATURAL_COMMAND_METHODS.get(action)
        if method_name is None:
            return

        command = getattr(type(self), method_name, None)
        callback = getattr(command, "callback", None)
        if command is None or callback is None:
            return

        previous_command = getattr(ctx, "command", None)
        ctx.command = command
        try:
            if not await self.bot.can_run(ctx, call_once=True):
                await self._send_moderation_response(
                    ctx,
                    f"You do not have permission to use `{action}`.",
                    f"Você não tem permissão para usar `{action}`.",
                )
                return
            if not await command.can_run(ctx):
                await self._send_moderation_response(
                    ctx,
                    f"You do not have permission to use `{action}`.",
                    f"Você não tem permissão para usar `{action}`.",
                )
                return
            if callback_override is None:
                await callback(self, ctx, *args, **kwargs)
            else:
                await callback_override(ctx, *args, **kwargs)
        except commands.CheckFailure:
            await self._send_moderation_response(
                ctx,
                f"You do not have permission to use `{action}`.",
                f"Você não tem permissão para usar `{action}`.",
            )
        except commands.CommandError as error:
            detail = str(error).strip()
            await self._send_moderation_response(
                ctx,
                detail or f"I could not run `{action}`.",
                detail or f"Não consegui executar `{action}`.",
            )
        finally:
            ctx.command = previous_command

    async def _run_natural_timeout(
        self,
        ctx: commands.Context,
        member: discord.Member,
        request: dict[str, Any],
    ):
        await self._apply_timeout(
            ctx,
            member,
            timedelta(seconds=request["duration_seconds"]),
            request["duration_text"] or f"{request['minutes']} minute(s)",
            request["reason"],
        )

    @staticmethod
    def _set_natural_context(ctx: commands.Context, request: dict[str, Any]):
        ctx._adminhelper_natural_language = request.get("language", "en")
        ctx._adminhelper_natural_action = request.get("action")

    async def _maybe_set_natural_context(self, ctx: commands.Context):
        if hasattr(ctx, "_adminhelper_natural_language"):
            return
        message = getattr(ctx, "message", None)
        bot_user = self.bot.user
        if message is None or bot_user is None or message.guild is None:
            return
        request = parse_natural_moderation_request(message.content, bot_user.id)
        if request is None:
            return
        if not await self.config.guild(message.guild).natural_language_enabled():
            return
        self._set_natural_context(ctx, request)

    @staticmethod
    def _natural_language(ctx: commands.Context) -> str:
        return getattr(ctx, "_adminhelper_natural_language", "en")

    async def _send_moderation_response(
        self,
        ctx: commands.Context,
        english: str,
        portuguese: str,
        **kwargs,
    ):
        await self._maybe_set_natural_context(ctx)
        content = portuguese if self._natural_language(ctx) == "pt" else english
        await ctx.send(content, **kwargs)

    @staticmethod
    def _natural_failure(failure: str) -> str:
        translations = {
            "this command can only be used in a server.": "este comando só pode ser usado em um servidor.",
            "that member is not in this server.": "esse membro não está neste servidor.",
            "I cannot moderate the server owner.": "não posso moderar o dono do servidor.",
            "I cannot find my server member record.": "não consigo encontrar meu registro de membro neste servidor.",
            "I cannot moderate myself.": "não posso me moderar.",
            "I cannot moderate a bot owner.": "não posso moderar o dono de um bot.",
            "you cannot moderate yourself.": "você não pode se moderar.",
            "that member's top role is higher than or equal to mine.": (
                "o cargo mais alto desse membro é maior ou igual ao meu."
            ),
            "that member's top role is higher than or equal to yours.": (
                "o cargo mais alto desse membro é maior ou igual ao seu."
            ),
            "unknown moderation action.": "ação de moderação desconhecida.",
        }
        if failure in translations:
            return translations[failure]
        permission_match = re.fullmatch(r"I am missing the `([^`]+)` permission\.", failure)
        if permission_match:
            return f"estou sem a permissão `{permission_match.group(1)}`."
        if failure == "the warning moderator is missing `manage_roles`.":
            return "o moderador do aviso está sem a permissão `manage_roles`."
        if failure == "that role is higher than or equal to mine.":
            return "esse cargo é maior ou igual ao meu."
        if failure == "that role is higher than or equal to the warning moderator's top role.":
            return "esse cargo é maior ou igual ao cargo mais alto do moderador do aviso."
        return failure

    @staticmethod
    def _natural_action_summary(action_text: str) -> str:
        if action_text.startswith("softban "):
            translated = "aplicar softban em " + action_text[len("softban ") :]
        elif action_text.startswith("ban "):
            translated = "banir " + action_text[len("ban ") :]
        elif action_text.startswith("kick "):
            translated = "expulsar " + action_text[len("kick ") :]
        elif action_text.startswith("warn "):
            translated = "avisar " + action_text[len("warn ") :]
        elif action_text.startswith("clear "):
            translated = "limpar " + action_text[len("clear ") :]
        elif action_text.startswith("purge up to "):
            translated = "limpar até " + action_text[len("purge up to ") :]
        else:
            translated = action_text

        replacements = (
            ("message(s)", "mensagem(ns)"),
            (" from ", " de "),
            (" in ", " em "),
            (" and delete ", " e apagar "),
            (" and ", " e "),
            (" day(s) of messages", " dia(s) de mensagens"),
            (" manual warning(s)", " aviso(s) manual(is)"),
            ("AutoMod strike(s)", "infração(ões) do AutoMod"),
            ("warning #", "aviso nº "),
            (" triggers ", " aciona "),
            ("Timeout ", "Silenciamento "),
            ("Add role ", "Adicionar cargo "),
            ("Remove role ", "Remover cargo "),
            (" for ", " em "),
        )
        for english, portuguese in replacements:
            translated = translated.replace(english, portuguese)
        return translated

    @staticmethod
    def _translate_warning_result(result: str) -> str:
        replacements = (
            ("Punishment #", "Punição #"),
            (" skipped: ", " ignorada: "),
            (" failed: ", " falhou: "),
            (" applied: ", " aplicada: "),
            ("configured role no longer exists", "a função configurada não existe mais"),
            ("already has ", "já possui "),
            ("does not have ", "não possui "),
            ("unable to timeout ", "não foi possível silenciar "),
            ("unable to add ", "não foi possível adicionar "),
            ("unable to remove ", "não foi possível remover "),
            ("unable to kick ", "não foi possível expulsar "),
            ("unable to ban ", "não foi possível banir "),
            ("timed out ", "silenciou "),
            ("kicked ", "expulsou "),
            ("banned ", "baniu "),
            ("added ", "adicionou "),
            ("removed ", "removeu "),
            (" for ", " em "),
            ("unknown action.", "ação desconhecida."),
        )
        translated = result
        for english, portuguese in replacements:
            translated = translated.replace(english, portuguese)
        return translated

    async def _send_natural_reply(
        self,
        message: discord.Message,
        english: str,
        portuguese: str | None = None,
        *,
        language: str = "en",
    ):
        content = portuguese if language == "pt" and portuguese is not None else english
        try:
            await message.channel.send(
                content,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return

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
        if member.id == guild.owner_id:
            return False, "I cannot moderate the server owner."

        bot_member = guild.me
        if bot_member is None:
            return False, "I cannot find my server member record."
        if member.id == bot_member.id:
            return False, "I cannot moderate myself."
        if await self._is_bot_owner(member):
            return False, "I cannot moderate a bot owner."

        required_permission = {
            "timeout": "moderate_members",
            "kick": "kick_members",
            "ban": "ban_members",
            "warn": None,
        }.get(action)
        if action != "warn" and required_permission is None:
            return False, "unknown moderation action."
        if required_permission and not getattr(bot_member.guild_permissions, required_permission, False):
            return False, f"I am missing the `{required_permission}` permission."

        if action in {"timeout", "kick", "ban"} and member.top_role >= bot_member.top_role:
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

    async def _antiabuse_warning_count(self, member: discord.Member) -> int:
        antiabuse = self.bot.get_cog("AntiAbuse")
        if antiabuse is None:
            return 0
        config = getattr(antiabuse, "config", None)
        if config is None:
            return 0
        try:
            return await config.member(member).warning_count()
        except Exception:
            return 0

    async def _clear_antiabuse_warning_count(self, member: discord.Member):
        antiabuse = self.bot.get_cog("AntiAbuse")
        if antiabuse is None:
            return
        config = getattr(antiabuse, "config", None)
        if config is None:
            return
        try:
            member_cfg = config.member(member)
            await member_cfg.warning_count.set(0)
            await member_cfg.last_violation_at.set(0)
            await member_cfg.last_action_at.set(0)
        except Exception:
            return

    async def _send_chunked_lines(
        self,
        ctx: commands.Context,
        header: str,
        lines: list[str],
        *,
        footer: str = "",
        max_length: int = 1900,
    ):
        current = header
        for raw_line in lines:
            line = raw_line
            if len(line) > max_length - 2:
                line = line[: max_length - 5] + "..."
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > max_length:
                await ctx.send(current)
                current = line
                continue
            current = candidate

        if footer:
            candidate = f"{current}\n{footer}" if current else footer
            if len(candidate) > max_length:
                await ctx.send(current)
                current = footer
            else:
                current = candidate

        if current:
            await ctx.send(current)

    async def _send_warning_punishments(self, ctx: commands.Context):
        punishments = await self.config.guild(ctx.guild).warning_punishments()
        if not punishments:
            await ctx.send(
                "No warning punishments configured.\n"
                "Use `[p]ah warnpunish add <warnings> <timeout|addrole|removerole|kick|ban> [value] [reason]`."
            )
            return

        rows = [
            self._format_warning_punishment(ctx.guild, punishment)
            for punishment in sorted(punishments, key=lambda item: (item.get("warnings", 0), item.get("id", 0)))
        ]
        description = "\n".join(rows)
        if len(description) > 4096:
            description = description[:4093] + "..."

        embed = discord.Embed(title="Warning Punishments", description=description, color=DEFAULT_COLOR)
        embed.set_footer(text="Punishments run only when a manual AdminHelper warning reaches the exact count.")
        await ctx.send(embed=embed)

    async def _build_warning_punishment(
        self,
        ctx: commands.Context,
        warnings: int,
        action: str,
        value: str | None,
        reason: str,
    ) -> dict[str, Any] | str:
        if warnings <= 0:
            return "Warning count must be greater than 0."

        normalized_action = self._normalize_warning_punishment_action(action)
        if normalized_action is None:
            return "Action must be one of: timeout, addrole, removerole, kick, ban."

        entry: dict[str, Any] = {
            "warnings": warnings,
            "action": normalized_action,
            "reason": reason or DEFAULT_REASON,
            "created_by": ctx.author.id,
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }

        if normalized_action == "timeout":
            if value is None:
                return "Timeout punishments need a duration in minutes."
            try:
                minutes = int(value)
            except ValueError:
                return "Timeout duration must be a number of minutes."
            if minutes <= 0 or minutes > 40320:
                return "Timeout duration must be between 1 and 40320 minutes."
            entry["minutes"] = minutes
            return entry

        if normalized_action in {"addrole", "removerole"}:
            if value is None:
                return f"{WARNING_PUNISHMENT_ACTIONS[normalized_action]} punishments need a role."
            try:
                role = await commands.RoleConverter().convert(ctx, value)
            except commands.BadArgument:
                return f"I could not find the role `{value}`."
            if role == ctx.guild.default_role:
                return "Cannot configure a punishment for the everyone role."
            entry["role_id"] = role.id
            return entry

        if normalized_action == "kick":
            entry["reason"] = self._combine_warning_reason(value, reason)
            return entry

        if normalized_action == "ban":
            delete_days = 0
            if value is not None:
                try:
                    delete_days = int(value)
                except ValueError:
                    entry["reason"] = self._combine_warning_reason(value, reason)
                else:
                    if delete_days < 0 or delete_days > 7:
                        return "Ban delete days must be between 0 and 7."
                    entry["reason"] = reason or DEFAULT_REASON
            entry["delete_days"] = delete_days
            return entry

        return "Unknown warning punishment action."

    async def _warning_punishments_for_count(self, guild: discord.Guild, warning_count: int) -> list[dict[str, Any]]:
        punishments = await self.config.guild(guild).warning_punishments()
        matches = []
        for punishment in punishments:
            try:
                configured_count = int(punishment.get("warnings", 0))
            except (TypeError, ValueError):
                continue
            if configured_count == warning_count:
                matches.append(punishment)
        return sorted(
            matches,
            key=lambda item: (
                WARNING_PUNISHMENT_PRIORITY.get(item.get("action"), 100),
                item.get("id", 0),
            ),
        )

    async def _apply_warning_punishments(
        self,
        ctx: commands.Context,
        member: discord.Member,
        warning_count: int,
        punishments: list[dict[str, Any]],
    ) -> list[str]:
        results = []
        for punishment in punishments:
            result, terminal = await self._apply_warning_punishment(ctx, member, warning_count, punishment)
            results.append(result)
            if terminal:
                break
        return results

    async def _apply_warning_punishment(
        self,
        ctx: commands.Context,
        member: discord.Member,
        warning_count: int,
        punishment: dict[str, Any],
    ) -> tuple[str, bool]:
        action = punishment.get("action")
        punishment_id = punishment.get("id", "?")
        reason = punishment.get("reason") or f"Reached warning #{warning_count}"
        extra = f"Triggered by warning #{warning_count} (punishment #{punishment_id})"

        if action == "timeout":
            failure = await self._warning_member_action_failure(ctx, member, "timeout")
            if failure:
                return f"Punishment #{punishment_id} skipped: {failure}", False
            minutes = int(punishment.get("minutes", 0))
            until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            try:
                await member.edit(communication_disabled_until=until, reason=self._audit_reason(ctx.author, reason))
            except (discord.Forbidden, discord.HTTPException):
                return f"Punishment #{punishment_id} failed: unable to timeout {member.mention}.", False

            duration = f"{minutes} minute(s)"
            await self._maybe_dm(
                member,
                ctx.guild,
                "timeout",
                reason,
                duration=duration,
                extra=extra,
                language=self._natural_language(ctx),
            )
            case_id = await self._create_case(
                ctx.guild,
                ctx.author,
                member,
                "Warning Timeout",
                reason,
                duration=duration,
                extra=extra,
            )
            await self._log_action(
                ctx.guild,
                ctx.author,
                member,
                "Warning Timeout",
                reason,
                duration=duration,
                extra=extra,
                case_id=case_id,
            )
            return f"Punishment #{punishment_id} applied: timed out {member.mention} for {duration}.", False

        if action in {"addrole", "removerole"}:
            role = ctx.guild.get_role(int(punishment.get("role_id", 0)))
            if role is None:
                return f"Punishment #{punishment_id} skipped: configured role no longer exists.", False
            failure = self._warning_role_action_failure(ctx, role)
            if failure:
                return f"Punishment #{punishment_id} skipped: {failure}", False

            is_add = action == "addrole"
            if is_add and role in member.roles:
                return f"Punishment #{punishment_id} skipped: {member.mention} already has {role.mention}.", False
            if not is_add and role not in member.roles:
                return f"Punishment #{punishment_id} skipped: {member.mention} does not have {role.mention}.", False

            try:
                if is_add:
                    await member.add_roles(role, reason=self._audit_reason(ctx.author, reason))
                else:
                    await member.remove_roles(role, reason=self._audit_reason(ctx.author, reason))
            except (discord.Forbidden, discord.HTTPException):
                verb = "add" if is_add else "remove"
                return f"Punishment #{punishment_id} failed: unable to {verb} {role.mention}.", False

            action_name = "Warning Role Add" if is_add else "Warning Role Remove"
            case_id = await self._create_case(ctx.guild, ctx.author, member, action_name, reason, extra=f"{extra}; role {role}")
            await self._maybe_dm(
                member,
                ctx.guild,
                action_name.lower(),
                reason,
                extra=f"{extra}; role: {role.name}",
                language=self._natural_language(ctx),
            )
            await self._log_action(
                ctx.guild,
                ctx.author,
                member,
                action_name,
                reason,
                extra=f"{extra}; role {role.mention}",
                case_id=case_id,
            )
            verb = "added" if is_add else "removed"
            return f"Punishment #{punishment_id} applied: {verb} {role.mention} for {member.mention}.", False

        if action == "kick":
            failure = await self._warning_member_action_failure(ctx, member, "kick")
            if failure:
                return f"Punishment #{punishment_id} skipped: {failure}", False
            await self._maybe_dm(
                member,
                ctx.guild,
                "kick",
                reason,
                extra=extra,
                language=self._natural_language(ctx),
            )
            try:
                await member.kick(reason=self._audit_reason(ctx.author, reason))
            except (discord.Forbidden, discord.HTTPException):
                return f"Punishment #{punishment_id} failed: unable to kick {member.mention}.", False
            case_id = await self._create_case(ctx.guild, ctx.author, member, "Warning Kick", reason, extra=extra)
            await self._log_action(ctx.guild, ctx.author, member, "Warning Kick", reason, extra=extra, case_id=case_id)
            return f"Punishment #{punishment_id} applied: kicked {member}.", True

        if action == "ban":
            failure = await self._warning_member_action_failure(ctx, member, "ban")
            if failure:
                return f"Punishment #{punishment_id} skipped: {failure}", False
            delete_days = int(punishment.get("delete_days", 0))
            await self._maybe_dm(
                member,
                ctx.guild,
                "ban",
                reason,
                extra=extra,
                language=self._natural_language(ctx),
            )
            try:
                await member.ban(delete_message_days=delete_days, reason=self._audit_reason(ctx.author, reason))
            except (discord.Forbidden, discord.HTTPException):
                return f"Punishment #{punishment_id} failed: unable to ban {member.mention}.", False
            details = f"{extra}; deleted {delete_days} day(s) of messages"
            case_id = await self._create_case(ctx.guild, ctx.author, member, "Warning Ban", reason, extra=details)
            await self._log_action(ctx.guild, ctx.author, member, "Warning Ban", reason, extra=details, case_id=case_id)
            return f"Punishment #{punishment_id} applied: banned {member}.", True

        return f"Punishment #{punishment_id} skipped: unknown action.", False

    async def _confirm_action(
        self,
        ctx: commands.Context,
        action_text: str,
        *,
        reason: str = DEFAULT_REASON,
        target: discord.abc.User | None = None,
    ) -> bool:
        await self._maybe_set_natural_context(ctx)
        thumbnail_url = self._avatar_url(target) if target is not None else self._avatar_url(ctx.author)
        language = self._natural_language(ctx)
        if language == "pt":
            action_text = self._natural_action_summary(action_text)
        view = ConfirmActionView(
            ctx.author.id,
            action_text,
            reason,
            str(ctx.author),
            thumbnail_url,
            language=language,
        )
        view.message = await ctx.send(embed=view.prompt_embed(), view=view)
        await view.wait()
        return view.value is True

    async def _create_case(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        target: discord.abc.User,
        action: str,
        reason: str,
        *,
        duration: str | None = None,
        extra: str | None = None,
    ) -> int:
        guild_cfg = self.config.guild(guild)
        case_id = await guild_cfg.next_case_id()
        await guild_cfg.next_case_id.set(case_id + 1)
        async with guild_cfg.cases() as cases:
            cases.append(
                {
                    "case_id": case_id,
                    "action": action,
                    "target_id": target.id,
                    "target_name": str(target),
                    "moderator_id": moderator.id,
                    "moderator_name": str(moderator),
                    "reason": reason or DEFAULT_REASON,
                    "duration": duration,
                    "extra": extra,
                    "created_at": int(datetime.now(timezone.utc).timestamp()),
                }
            )
        return case_id

    async def _get_case(self, guild: discord.Guild, case_id: int) -> dict[str, Any] | None:
        cases = await self.config.guild(guild).cases()
        for case in cases:
            if case.get("case_id") == case_id:
                return case
        return None

    def _case_embed(self, guild: discord.Guild, case: dict[str, Any]) -> discord.Embed:
        case_id = case.get("case_id", "?")
        embed = discord.Embed(title=f"Case #{case_id}: {case.get('action', 'Unknown')}", color=DEFAULT_COLOR)
        target_id = case.get("target_id")
        moderator_id = case.get("moderator_id")
        target = guild.get_member(target_id) if target_id else None
        moderator = guild.get_member(moderator_id) if moderator_id else None
        target_text = target.mention if target else f"{case.get('target_name', 'Unknown')} (`{target_id}`)"
        moderator_text = moderator.mention if moderator else f"{case.get('moderator_name', 'Unknown')} (`{moderator_id}`)"

        embed.add_field(name="Target", value=target_text, inline=False)
        embed.add_field(name="Moderator", value=moderator_text, inline=True)
        created_at = case.get("created_at", 0)
        embed.add_field(name="Created", value=f"<t:{created_at}:F>\n<t:{created_at}:R>" if created_at else "Unknown", inline=True)
        if case.get("duration"):
            embed.add_field(name="Duration", value=case["duration"], inline=True)
        if case.get("extra"):
            embed.add_field(name="Details", value=case["extra"], inline=False)
        embed.add_field(name="Reason", value=case.get("reason") or DEFAULT_REASON, inline=False)
        if case.get("reason_updated_at"):
            embed.set_footer(text=f"Reason updated by {case.get('reason_updated_by')} at {case.get('reason_updated_at')}")
        return embed

    def _format_case_line(self, case: dict[str, Any]) -> str:
        created_at = case.get("created_at", 0)
        timestamp = f"<t:{created_at}:R>" if created_at else "unknown time"
        reason = case.get("reason") or DEFAULT_REASON
        if len(reason) > 80:
            reason = reason[:77] + "..."
        return f"`#{case.get('case_id')}` **{case.get('action', 'Unknown')}** {timestamp}: {reason}"

    def _format_case_line_portuguese(self, case: dict[str, Any]) -> str:
        action_names = {
            "Timeout": "Silenciamento",
            "Untimeout": "Remoção de silêncio",
            "Kick": "Expulsão",
            "Ban": "Banimento",
            "Hackban": "Hackban",
            "Unban": "Desbanimento",
            "Softban": "Softban",
            "Warn": "Aviso",
            "Clear Warnings": "Limpeza de avisos",
        }
        created_at = case.get("created_at", 0)
        timestamp = f"<t:{created_at}:R>" if created_at else "horário desconhecido"
        reason = case.get("reason") or DEFAULT_REASON
        if len(reason) > 80:
            reason = reason[:77] + "..."
        action = action_names.get(case.get("action"), case.get("action", "Desconhecido"))
        return f"`#{case.get('case_id')}` **{action}** {timestamp}: {reason}"

    @staticmethod
    def _count_cases_by_action(cases: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in cases:
            action = case.get("action", "Unknown")
            counts[action] = counts.get(action, 0) + 1
        return counts

    @staticmethod
    def _format_action_counts(counts: dict[str, int]) -> str:
        if not counts:
            return "None"
        return "\n".join(f"{action}: {count}" for action, count in sorted(counts.items()))

    @staticmethod
    def _format_action_counts_portuguese(counts: dict[str, int]) -> str:
        if not counts:
            return "Nenhuma"
        action_names = {
            "Timeout": "Silenciamento",
            "Untimeout": "Remoção de silêncio",
            "Kick": "Expulsão",
            "Ban": "Banimento",
            "Hackban": "Hackban",
            "Unban": "Desbanimento",
            "Softban": "Softban",
            "Warn": "Aviso",
            "Clear Warnings": "Limpeza de avisos",
        }
        return "\n".join(
            f"{action_names.get(action, action)}: {count}"
            for action, count in sorted(counts.items())
        )

    @staticmethod
    def _normalize_warning_punishment_action(action: str) -> str | None:
        normalized = (action or "").lower().replace("-", "").replace("_", "")
        aliases = {
            "timeout": "timeout",
            "mute": "timeout",
            "addrole": "addrole",
            "roleadd": "addrole",
            "give": "addrole",
            "removerole": "removerole",
            "roleremove": "removerole",
            "take": "removerole",
            "kick": "kick",
            "ban": "ban",
        }
        return aliases.get(normalized)

    @staticmethod
    def _combine_warning_reason(value: str | None, reason: str) -> str:
        if value is None:
            return reason or DEFAULT_REASON
        if reason and reason != DEFAULT_REASON:
            return f"{value} {reason}"
        return value

    @staticmethod
    def _parse_delete_days_reason(raw_reason: str, default_delete_days: int) -> tuple[int, str] | str:
        text = (raw_reason or "").strip()
        if not text or text == DEFAULT_REASON:
            return default_delete_days, DEFAULT_REASON

        first_word, _, rest = text.partition(" ")
        if first_word.lstrip("+-").isdigit():
            delete_days = int(first_word)
            if delete_days < 0 or delete_days > 7:
                return "Ban delete days must be between 0 and 7."
            return delete_days, rest.strip() or DEFAULT_REASON

        return default_delete_days, text

    def _format_warning_punishment(self, guild: discord.Guild, punishment: dict[str, Any]) -> str:
        punishment_id = punishment.get("id", "?")
        warnings = punishment.get("warnings", "?")
        action = self._format_warning_punishment_action(guild, punishment)
        reason = punishment.get("reason") or DEFAULT_REASON
        if len(reason) > 90:
            reason = reason[:87] + "..."
        return f"`#{punishment_id}` warning #{warnings} -> {action} | {reason}"

    def _format_warning_punishment_summary(self, guild: discord.Guild, punishments: list[dict[str, Any]]) -> str:
        summary = ", ".join(self._format_warning_punishment_action(guild, punishment) for punishment in punishments)
        if len(summary) > 700:
            summary = summary[:697] + "..."
        return summary

    def _format_warning_punishment_action(self, guild: discord.Guild, punishment: dict[str, Any]) -> str:
        action = punishment.get("action")
        if action == "timeout":
            return f"Timeout {punishment.get('minutes', '?')}m"
        if action in {"addrole", "removerole"}:
            role = guild.get_role(int(punishment.get("role_id", 0)))
            role_text = role.mention if role is not None else f"deleted role `{punishment.get('role_id')}`"
            return f"{WARNING_PUNISHMENT_ACTIONS.get(action, action)} {role_text}"
        if action == "ban":
            return f"Ban (delete {punishment.get('delete_days', 0)}d)"
        return WARNING_PUNISHMENT_ACTIONS.get(action, str(action))

    async def _warning_member_action_failure(
        self,
        ctx: commands.Context,
        member: discord.Member,
        action: str,
    ) -> str:
        required_permission = {
            "timeout": "moderate_members",
            "kick": "kick_members",
            "ban": "ban_members",
        }.get(action)
        if required_permission and not getattr(ctx.author.guild_permissions, required_permission, False):
            return f"the warning moderator is missing `{required_permission}`."

        allowed, failure = await self._can_moderate_member(ctx.guild, member, action, actor=ctx.author)
        if not allowed:
            return failure
        return ""

    def _warning_role_action_failure(self, ctx: commands.Context, role: discord.Role) -> str:
        bot_member = ctx.guild.me
        if bot_member is None:
            return "I cannot find my server member record."
        if not bot_member.guild_permissions.manage_roles:
            return "I am missing the `manage_roles` permission."
        if role >= bot_member.top_role:
            return "that role is higher than or equal to mine."
        if not ctx.author.guild_permissions.manage_roles:
            return "the warning moderator is missing `manage_roles`."
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            return "that role is higher than or equal to the warning moderator's top role."
        return ""

    async def _name_tracking_enabled_for_user(self, user: discord.User) -> bool:
        for guild in self.bot.guilds:
            member = guild.get_member(user.id)
            if member is None:
                continue
            if await self.config.guild(guild).name_tracking_enabled():
                return True
        return False

    def _format_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "None"
        return f"<t:{int(value.timestamp())}:F>\n<t:{int(value.timestamp())}:R>"

    def _format_profile_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "Unknown"
        timestamp = int(value.timestamp())
        return f"<t:{timestamp}:f>\n<t:{timestamp}:R>"

    @staticmethod
    def _format_history(values: list[str], tracking_enabled: bool = True) -> str:
        if not tracking_enabled:
            return "Tracking disabled"
        if not values:
            return "Not tracked yet"
        return ", ".join(values[-10:])

    def _format_name_history(
        self,
        user_history: dict[str, Any],
        member_history: dict[str, Any],
        tracking_enabled: bool,
    ) -> str:
        if not tracking_enabled:
            return "Tracking disabled"

        return "\n".join(
            [
                f"Usernames: {self._format_history(user_history['previous_usernames'])}",
                f"Global names: {self._format_history(user_history['previous_global_names'])}",
                f"Nicknames: {self._format_history(member_history['previous_nicknames'])}",
            ]
        )

    def _format_name_history_portuguese(
        self,
        user_history: dict[str, Any],
        member_history: dict[str, Any],
        tracking_enabled: bool,
    ) -> str:
        if not tracking_enabled:
            return "Rastreamento desativado"

        def history(values: list[str]) -> str:
            if not values:
                return "Ainda não rastreado"
            return ", ".join(values[-10:])

        return "\n".join(
            [
                f"Nomes de usuário: {history(user_history['previous_usernames'])}",
                f"Nomes globais: {history(user_history['previous_global_names'])}",
                f"Apelidos: {history(member_history['previous_nicknames'])}",
            ]
        )

    async def _append_history(self, config_value, value: str | None):
        if not value:
            return
        async with config_value() as values:
            if value in values:
                values.remove(value)
            values.append(value)
            del values[:-20]

    @staticmethod
    def _avatar_url(user: discord.abc.User) -> str:
        display_avatar = getattr(user, "display_avatar", None)
        if display_avatar is not None:
            return str(display_avatar.url)
        avatar_url = getattr(user, "avatar_url", None)
        if avatar_url is not None:
            return str(avatar_url)
        default_avatar_url = getattr(user, "default_avatar_url", None)
        if default_avatar_url is not None:
            return str(default_avatar_url)
        return ""

    @staticmethod
    def _status_icon(status: discord.Status) -> str:
        return {
            discord.Status.online: "🟢",
            discord.Status.idle: "🟡",
            discord.Status.dnd: "🔴",
            discord.Status.offline: "⚪",
        }.get(status, "⚪")

    @staticmethod
    def _status_text(member: discord.Member) -> str:
        status = str(member.status)
        if status == "offline":
            return "Chilling in offline status"
        return f"Chilling in {status} status"

    @staticmethod
    def _member_number(guild: discord.Guild, member: discord.Member) -> int:
        members = [guild_member for guild_member in guild.members if guild_member.joined_at is not None]
        members.sort(key=lambda guild_member: guild_member.joined_at)
        try:
            return members.index(member) + 1
        except ValueError:
            return 0

    def _audit_reason(self, moderator: discord.abc.User, reason: str) -> str:
        return f"{moderator} ({moderator.id}): {reason or DEFAULT_REASON}"[:512]

    async def _prefix(self, ctx: commands.Context) -> str:
        clean_prefix = getattr(ctx, "clean_prefix", None)
        if clean_prefix:
            return str(clean_prefix)

        prefixes = []
        get_valid_prefixes = getattr(self.bot, "get_valid_prefixes", None)
        if get_valid_prefixes is not None:
            try:
                maybe_prefixes = get_valid_prefixes(ctx.guild)
                if hasattr(maybe_prefixes, "__await__"):
                    maybe_prefixes = await maybe_prefixes
                prefixes = list(maybe_prefixes)
            except Exception:
                prefixes = []

        for prefix in prefixes:
            if isinstance(prefix, str) and prefix:
                return prefix
        return "[p]"

    async def _maybe_dm(
        self,
        member: discord.Member,
        guild: discord.Guild,
        action: str,
        reason: str,
        *,
        duration: str | None = None,
        extra: str | None = None,
        case_id: int | None = None,
        language: str = "en",
    ):
        if not await self.config.guild(guild).dm_users():
            return

        if language == "pt":
            action_names = {
                "timeout": "silenciamento",
                "kick": "expulsão",
                "ban": "banimento",
                "hackban": "hackban",
                "softban": "softban",
                "warn": "aviso",
            }
            lines = [
                f"Você recebeu uma ação de moderação em **{guild.name}**.",
                f"Ação: **{action_names.get(action, action)}**",
            ]
        else:
            lines = [
                f"You received a moderation action in **{guild.name}**.",
                f"Action: **{action}**",
            ]
        if duration:
            lines.append(f"Duração: {duration}" if language == "pt" else f"Duration: {duration}")
        if extra:
            lines.append(self._translate_dm_extra(extra) if language == "pt" else extra)
        lines.append(
            f"Motivo: {reason or DEFAULT_REASON}"
            if language == "pt"
            else f"Reason: {reason or DEFAULT_REASON}"
        )

        try:
            await member.send("\n".join(lines))
        except (discord.Forbidden, discord.HTTPException):
            return

    @staticmethod
    def _translate_dm_extra(extra: str) -> str:
        replacements = (
            ("Deleted ", "Excluídas "),
            (" day(s) of messages", " dia(s) de mensagens"),
            ("Triggered by warning ", "Acionado pelo aviso "),
            (" (punishment ", " (punição "),
            ("; role:", "; cargo:"),
        )
        translated = extra
        for english, portuguese in replacements:
            translated = translated.replace(english, portuguese)
        return translated

    async def _log_action(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        target: discord.abc.User,
        action: str,
        reason: str,
        *,
        duration: str | None = None,
        extra: str | None = None,
        case_id: int | None = None,
    ):
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if log_channel is None:
            return

        embed = discord.Embed(title=f"AdminHelper: {action}", color=DEFAULT_COLOR)
        if case_id is not None:
            embed.add_field(name="Case", value=f"#{case_id}", inline=True)
        embed.add_field(name="Target", value=f"{target} (`{target.id}`)", inline=False)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        if extra:
            embed.add_field(name="Details", value=extra, inline=False)
        embed.add_field(name="Reason", value=reason or DEFAULT_REASON, inline=False)
        embed.timestamp = datetime.now(timezone.utc)

        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _log_purge(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        channel: discord.abc.GuildChannel,
        deleted_count: int,
        member: discord.Member | None,
    ):
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if log_channel is None:
            return

        embed = discord.Embed(title="AdminHelper: Purge", color=DEFAULT_COLOR)
        embed.add_field(name="Channel", value=getattr(channel, "mention", channel.name), inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Messages", value=str(deleted_count), inline=True)
        embed.add_field(name="Scope", value=member.mention if member is not None else "Any author", inline=False)
        embed.timestamp = datetime.now(timezone.utc)

        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return
