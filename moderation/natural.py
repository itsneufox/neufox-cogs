"""Mention-based moderation: "@Bot ban @user spam" in English or Portuguese."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import discord
from redbot.core import commands

from .common import DEFAULT_REASON


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


class NaturalLanguageMixin:
    """Turns bot mentions into calls to the regular moderation commands."""

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

        command = getattr(self, method_name, None)
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
