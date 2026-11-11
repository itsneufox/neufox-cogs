"""EmbedFixer: repost social media links through a webhook with working embeds."""

from __future__ import annotations

import discord
from redbot.core import Config, commands

from .fixers import (
    TRANSLATE_LANGUAGES,
    DomainId,
    MatchedURL,
    available_methods,
    find_matches,
    has_alternatives,
    rewrite,
)

EMBEDFIXER_ID = 728466531
WEBHOOK_NAME = "EmbedFixer"
TRY_OTHER_TIMEOUT = 86400


class TryOtherView(discord.ui.View):
    """Cycles each link to its next fixer service. Lives in memory for a day."""

    def __init__(self, matches: list[MatchedURL], translate_to: str | None = None):
        super().__init__(timeout=TRY_OTHER_TIMEOUT)
        self.matches = matches
        self.translate_to = translate_to
        self.rotations: dict[int, int] = {}

    @discord.ui.button(label="Try other", style=discord.ButtonStyle.secondary)
    async def try_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        for match in self.matches:
            if len(available_methods(match)) > 1:
                key = int(match.domain.id)
                self.rotations[key] = self.rotations.get(key, 0) + 1
        await interaction.response.edit_message(content=rewrite(self.matches, self.rotations, self.translate_to), view=self)


class EmbedFixerMixin:
    """Config, listener and helpers for EmbedFixer. Composed into the Media cog."""

    def _embedfixer_init(self):
        # Same identifier and cog name as the old standalone cog, so its data is reused.
        self.embedfixer_config = Config.get_conf(
            None, identifier=EMBEDFIXER_ID, cog_name="EmbedFixer", force_registration=True
        )
        self.embedfixer_config.register_guild(enabled=True, youtube=False, translate_to=None)
        self.embedfixer_config.register_member(opted_out=False)

    async def _embedfixer_delete_user(self, user_id: int):
        for guild_id, members in (await self.embedfixer_config.all_members()).items():
            if user_id in members:
                await self.embedfixer_config.member_from_ids(guild_id, user_id).clear()

    async def _embedfixer_status(self, guild: discord.Guild, member) -> str:
        enabled = await self.embedfixer_config.guild(guild).enabled()
        lines = [f"**Automatic fixing:** {'On' if enabled else 'Off'}"]
        translate_to = await self.embedfixer_config.guild(guild).translate_to()
        if translate_to:
            lines.append(f"**Translate Twitter/X posts to:** {TRANSLATE_LANGUAGES.get(translate_to, translate_to)}")
        if isinstance(member, discord.Member):
            opted_out = await self.embedfixer_config.member(member).opted_out()
            lines.append(f"**Your links:** {'Left alone (opted out)' if opted_out else 'Fixed'}")
        lines.append("-# Skip one link with `$link` or `<link>`. Opt out with `/media fixlinks`.")
        return "### EmbedFixer\n" + "\n".join(lines)

    @commands.Cog.listener("on_message_without_command")
    async def _embedfixer_on_message(self, message: discord.Message):
        if message.guild is None or not message.content:
            return
        if message.author.bot or message.webhook_id is not None:
            return
        if not isinstance(message.author, discord.Member):
            return
        if not await self.embedfixer_config.guild(message.guild).enabled():
            return
        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return
        if await self.embedfixer_config.member(message.author).opted_out():
            return

        settings = await self.embedfixer_config.guild(message.guild).all()
        opted_in = frozenset({DomainId.YOUTUBE}) if settings["youtube"] else frozenset()
        matches = find_matches(message.content, opted_in)
        if not matches:
            return
        translate_to = settings["translate_to"]
        rewritten = rewrite(matches, {}, translate_to)
        if rewritten == message.content:
            return

        view = TryOtherView(matches, translate_to) if has_alternatives(matches) else None
        if await self._embedfixer_repost(message, rewritten, view=view):
            await self._embedfixer_suppress(message)

    async def _embedfixer_repost(self, message: discord.Message, content: str, *, view=None) -> bool:
        channel = message.channel
        me = message.guild.me
        if me is None or not hasattr(channel, "permissions_for"):
            return False
        if not channel.permissions_for(me).send_messages:
            return False

        webhook, thread = await self._embedfixer_webhook(channel)
        if webhook is not None:
            kwargs = {
                "content": content,
                "username": getattr(message.author, "display_name", str(message.author))[:80],
                "avatar_url": message.author.display_avatar.url,
                "allowed_mentions": discord.AllowedMentions.none(),
                "wait": True,
            }
            if view is not None:
                kwargs["view"] = view
            if thread is not None:
                kwargs["thread"] = thread
            try:
                await webhook.send(**kwargs)
                return True
            except (discord.Forbidden, discord.HTTPException, TypeError):
                pass

        try:
            await channel.send(content=content, allowed_mentions=discord.AllowedMentions.none(), view=view)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _embedfixer_webhook(self, channel) -> tuple[discord.Webhook | None, discord.Thread | None]:
        thread = channel if isinstance(channel, discord.Thread) else None
        parent = channel.parent if thread is not None else channel
        if parent is None or not all(hasattr(parent, name) for name in ("webhooks", "create_webhook", "permissions_for")):
            return None, thread
        me = parent.guild.me
        if me is None or not parent.permissions_for(me).manage_webhooks:
            return None, thread
        try:
            for webhook in await parent.webhooks():
                if webhook.name == WEBHOOK_NAME and webhook.user == self.bot.user:
                    return webhook, thread
            return await parent.create_webhook(name=WEBHOOK_NAME, reason="EmbedFixer repost webhook"), thread
        except (discord.Forbidden, discord.HTTPException):
            return None, thread

    @staticmethod
    async def _embedfixer_suppress(message: discord.Message) -> None:
        me = message.guild.me
        if me is None or not hasattr(message.channel, "permissions_for"):
            return
        if not message.channel.permissions_for(me).manage_messages:
            return
        try:
            await message.edit(suppress=True)
        except (discord.Forbidden, discord.HTTPException):
            return
