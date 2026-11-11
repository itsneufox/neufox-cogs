"""Case store, mod-log, DM notices and name history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord
from redbot.core import commands

from .common import DEFAULT_COLOR, DEFAULT_REASON, now_ts


PORTUGUESE_CASE_NAMES = {
    "Timeout": "Silenciamento",
    "Untimeout": "Remoção de silêncio",
    "Kick": "Expulsão",
    "Ban": "Banimento",
    "Hackban": "Hackban",
    "Unban": "Desbanimento",
    "Softban": "Softban",
    "Warn": "Aviso",
    "Clear Warnings": "Limpeza de avisos",
    "AutoMod Timeout": "Silenciamento do AutoMod",
    "AutoMod Kick": "Expulsão do AutoMod",
    "AutoMod Ban": "Banimento do AutoMod",
    "Honeypot Ban": "Banimento do honeypot",
    "Honeypot Kick": "Expulsão do honeypot",
    "Honeypot Role": "Cargo do honeypot",
}


class CasesMixin:
    """One case log shared by manual actions, AutoMod and the honeypot."""

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

    async def _create_case(
        self,
        guild: discord.Guild,
        moderator: discord.abc.User,
        target: discord.abc.User,
        action: str,
        reason: str,
        *,
        duration: str | None = None,
        extra: str | None = None,
        target_name: str | None = None,
    ) -> int:
        guild_cfg = self.config.guild(guild)
        async with self._case_locks[guild.id]:
            case_id = await guild_cfg.next_case_id()
            await guild_cfg.next_case_id.set(case_id + 1)
            async with guild_cfg.cases() as cases:
                cases.append(
                    {
                        "case_id": case_id,
                        "action": action,
                        "target_id": target.id,
                        "target_name": target_name or str(target),
                        "moderator_id": moderator.id,
                        "moderator_name": str(moderator),
                        "reason": reason or DEFAULT_REASON,
                        "duration": duration,
                        "extra": extra,
                        "created_at": now_ts(),
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
        action_names = PORTUGUESE_CASE_NAMES
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
        action_names = PORTUGUESE_CASE_NAMES
        return "\n".join(
            f"{action_names.get(action, action)}: {count}"
            for action, count in sorted(counts.items())
        )

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

        embed = discord.Embed(title=f"Moderation: {action}", color=DEFAULT_COLOR)
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

        embed = discord.Embed(title="Moderation: Purge", color=DEFAULT_COLOR)
        embed.add_field(name="Channel", value=getattr(channel, "mention", channel.name), inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Messages", value=str(deleted_count), inline=True)
        embed.add_field(name="Scope", value=member.mention if member is not None else "Any author", inline=False)
        embed.timestamp = datetime.now(timezone.utc)

        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return
