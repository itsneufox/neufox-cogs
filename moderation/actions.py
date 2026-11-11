"""Manual moderation actions, warnings and warning punishments."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from redbot.core import commands

from .common import DEFAULT_COLOR, DEFAULT_REASON, audit_reason, avatar_url, can_moderate_member
from .layouts import NO_PINGS, send_embed, send_text
from .natural import parse_natural_moderation_request
from .views import ConfirmActionView


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


class ActionsMixin:
    """Ban, kick, timeout, warn, purge and friends, plus manual-warning punishments."""

    @commands.command(name="purge")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def purge_messages(self, ctx: commands.Context, amount: int, member: discord.Member | None = None):
        """Delete recent messages from this channel. Optionally limit cleanup to one member."""
        purge_max = int(await self.config.guild(ctx.guild).purge_max())
        if amount < 1 or amount > purge_max:
            await self._send_moderation_response(
                ctx,
                f"Purge amount must be between 1 and {purge_max}.",
                f"A quantidade deve estar entre 1 e {purge_max}.",
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
                reason=audit_reason(ctx.author, "Manual message purge"),
            )
        except (discord.Forbidden, discord.HTTPException):
            await self._send_moderation_response(
                ctx,
                "Unable to purge messages in this channel.",
                "Não foi possível limpar as mensagens neste canal.",
            )
            return

        if ctx.interaction is None:
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

    @commands.command(name="userinfo")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def userinfo_member(self, ctx: commands.Context, member: discord.Member = None):
        """Show account, server, and moderation stats for a member."""
        member = member or ctx.author
        await self._maybe_set_natural_context(ctx)
        portuguese = self._natural_language(ctx) == "pt"
        warnings = await self.config.member(member).warnings()
        automod_strikes = await self._automod_strikes(member)
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
        avatar = avatar_url(member)
        if avatar:
            embed.set_author(name=str(member), icon_url=avatar)
            embed.set_thumbnail(url=avatar)
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
                f"Infrações do AutoMod: {automod_strikes}"
                if portuguese
                else (
                    f"Timed out: {timeout_text or 'No'}\n"
                    f"Warnings: {len(warnings)} manual\n"
                    f"AutoMod strikes: {automod_strikes}"
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
        await send_embed(ctx, embed)

    @commands.command(name="timeout")
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

        if await self._reason_missing(ctx, reason):
            return
        until = datetime.now(timezone.utc) + duration
        audit_text = audit_reason(ctx.author, reason)
        try:
            await member.edit(communication_disabled_until=until, reason=audit_text)
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

    @commands.command(name="untimeout")
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
            await member.edit(communication_disabled_until=None, reason=audit_reason(ctx.author, reason))
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
        if await self._reason_missing(ctx, reason):
            return
        if not await self._confirm_action(ctx, f"kick {member.mention}", reason=reason, target=member):
            return

        await self._maybe_dm(member, ctx.guild, "kick", reason, language=self._natural_language(ctx))
        try:
            await member.kick(reason=audit_reason(ctx.author, reason))
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
        if await self._reason_missing(ctx, reason):
            return
        if not await self._confirm_action(
            ctx,
            f"ban {member.mention} and delete {delete_days} day(s) of messages",
            reason=reason,
            target=member,
        ):
            return
        await self._maybe_dm(member, ctx.guild, "ban", reason, language=self._natural_language(ctx))
        try:
            await member.ban(delete_message_days=delete_days, reason=audit_reason(ctx.author, reason))
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

    @commands.command(name="hackban")
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
        if await self._reason_missing(ctx, reason):
            return
        if not await self._confirm_action(
            ctx,
            f"ban {user} and delete {delete_days} day(s) of messages",
            reason=reason,
            target=user,
        ):
            return
        try:
            await ctx.guild.ban(user, delete_message_days=delete_days, reason=audit_reason(ctx.author, reason))
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
            await ctx.guild.unban(user, reason=audit_reason(ctx.author, reason))
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

        if await self._reason_missing(ctx, reason):
            return
        delete_days = await self.config.guild(ctx.guild).softban_delete_message_days()
        if not await self._confirm_action(
            ctx,
            f"softban {member.mention} and delete {delete_days} day(s) of messages",
            reason=reason,
            target=member,
        ):
            return
        audit_text = audit_reason(ctx.author, f"Softban: {reason}")
        await self._maybe_dm(
            member,
            ctx.guild,
            "softban",
            reason,
            extra=f"Deleted {delete_days} day(s) of messages",
            language=self._natural_language(ctx),
        )
        try:
            await member.ban(delete_message_days=delete_days, reason=audit_text)
            await ctx.guild.unban(member, reason=audit_text)
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

        if await self._reason_missing(ctx, reason):
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
        """Show a member's manual warnings and AutoMod strikes."""
        await self._maybe_set_natural_context(ctx)
        portuguese = self._natural_language(ctx) == "pt"
        warnings = await self.config.member(member).warnings()
        strikes = await self.automod_config.member(member).all()
        footer = self._strike_summary(strikes, portuguese)
        if not warnings:
            await self._send_moderation_response(
                ctx,
                f"{member.mention} has no manual warnings.\n{footer}",
                f"{member.mention} não tem avisos manuais.\n{footer}",
            )
            return

        lines = []
        for idx, warning in enumerate(warnings, start=1):
            moderator = ctx.guild.get_member(warning.get("moderator_id"))
            moderator_text = moderator.mention if moderator else str(warning.get("moderator_id", "unknown"))
            created_at = warning.get("created_at", 0)
            unknown = "horário desconhecido" if portuguese else "unknown time"
            timestamp = f"<t:{created_at}:R>" if created_at else unknown
            case_text = f" (case #{warning['case_id']})" if warning.get("case_id") else ""
            by = "por" if portuguese else "by"
            lines.append(f"`#{idx}` {timestamp} {by} {moderator_text}{case_text}: {warning.get('reason', DEFAULT_REASON)}")

        header = (
            f"Avisos manuais para {member.mention} ({len(warnings)} no total):"
            if portuguese
            else f"Manual warnings for {member.mention} ({len(warnings)} total):"
        )
        await self._send_chunked_lines(ctx, header, lines, footer=footer)

    @staticmethod
    def _strike_summary(strikes: dict[str, Any], portuguese: bool = False) -> str:
        count = strikes.get("warning_count", 0)
        last_violation = strikes.get("last_violation_at", 0)
        last_action = strikes.get("last_action_at", 0)
        if portuguese:
            text = f"Infrações do AutoMod: {count}."
            if last_violation:
                text += f" Última infração <t:{last_violation}:R>."
            if last_action:
                text += f" Última ação <t:{last_action}:R>."
            return text
        text = f"AutoMod strikes: {count}."
        if last_violation:
            text += f" Last violation <t:{last_violation}:R>."
        if last_action:
            text += f" Last action <t:{last_action}:R>."
        return text

    @commands.command(name="clearwarnings")
    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def clearwarnings_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = DEFAULT_REASON):
        """Clear stored warnings and AutoMod strikes for a member."""
        previous_manual = len(await self.config.member(member).warnings())
        previous_automod = await self._automod_strikes(member)
        if not previous_manual and not previous_automod:
            await self._send_moderation_response(
                ctx,
                f"{member.mention} has no manual warnings or AutoMod strikes.",
                f"{member.mention} não tem avisos manuais nem infrações do AutoMod.",
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
        await self._clear_automod_strikes(member)
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

    async def _can_moderate_member(
        self,
        guild: discord.Guild | None,
        member: discord.Member,
        action: str,
        *,
        actor: discord.Member | None = None,
    ) -> tuple[bool, str]:
        return await can_moderate_member(self.bot, guild, member, action, actor=actor)

    async def _automod_strikes(self, member: discord.Member) -> int:
        return await self.automod_config.member(member).warning_count()

    async def _clear_automod_strikes(self, member: discord.Member):
        member_cfg = self.automod_config.member(member)
        await member_cfg.warning_count.set(0)
        await member_cfg.last_violation_at.set(0)
        await member_cfg.last_action_at.set(0)

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
                await send_text(ctx, current, DEFAULT_COLOR, ephemeral=True)
                current = line
                continue
            current = candidate

        if footer:
            candidate = f"{current}\n{footer}" if current else footer
            if len(candidate) > max_length:
                await send_text(ctx, current, DEFAULT_COLOR, ephemeral=True)
                current = footer
            else:
                current = candidate

        if current:
            await send_text(ctx, current, DEFAULT_COLOR, ephemeral=True)

    async def _send_warning_punishments(self, ctx: commands.Context):
        punishments = await self.config.guild(ctx.guild).warning_punishments()
        if not punishments:
            await ctx.send(
                "No warning punishments configured.\n"
                "Add one with `/mod punish add` or `[p]mod punish add <warnings> <timeout|addrole|removerole|kick|ban> [value] [reason]`."
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
        embed.set_footer(text="Punishments run when a manual warning reaches the exact count.")
        await send_embed(ctx, embed)

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
                await member.edit(communication_disabled_until=until, reason=audit_reason(ctx.author, reason))
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
                    await member.add_roles(role, reason=audit_reason(ctx.author, reason))
                else:
                    await member.remove_roles(role, reason=audit_reason(ctx.author, reason))
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
                await member.kick(reason=audit_reason(ctx.author, reason))
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
                await member.ban(delete_message_days=delete_days, reason=audit_reason(ctx.author, reason))
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
        guild_cfg = self.config.guild(ctx.guild)
        if not await guild_cfg.confirm_actions():
            return True
        thumbnail_url = avatar_url(target) if target is not None else avatar_url(ctx.author)
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
            timeout=await guild_cfg.confirm_timeout_seconds(),
        )
        view.message = await ctx.send(view=view, allowed_mentions=NO_PINGS)
        await view.wait()
        return view.value is True

    async def _reason_missing(self, ctx: commands.Context, reason: str | None) -> bool:
        """When the server requires reasons, refuse actions that were given none."""
        if reason and reason.strip() and reason != DEFAULT_REASON:
            return False
        if not await self.config.guild(ctx.guild).require_reason():
            return False
        await self._send_moderation_response(
            ctx,
            "This server requires a reason for moderation actions.",
            "Este servidor exige um motivo para ações de moderação.",
        )
        return True

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
