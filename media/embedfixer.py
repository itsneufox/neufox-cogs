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
    fixable_domains,
    has_alternatives,
    rewrite,
    rewrite_inline,
)
from .layouts import channel_listed, config_field

EMBEDFIXER_ID = 728466531
WEBHOOK_NAME = "EmbedFixer"
TRY_OTHER_TIMEOUT = 86400
EMBEDFIXER_GUILD_DEFAULTS = {
    "enabled": True,
    "youtube": False,
    "translate_to": None,
    "disabled_sites": [],
    "services": {},
    "ignored_channels": [],
    "ignored_roles": [],
    "repost_mode": "webhook",
    "original_action": "suppress",
    "try_other": True,
    "try_other_hours": TRY_OTHER_TIMEOUT // 3600,
}


class TryOtherView(discord.ui.View):
    """Cycles each link to its next fixer service. Lives in memory (a day by default).

    ``preferred`` maps domain ids to the service each rotation starts from. With ``content`` the whole
    message is rewritten in place (used when the original is deleted); ``prefix`` is kept in front.
    """

    def __init__(
        self,
        matches: list[MatchedURL],
        translate_to: str | None = None,
        *,
        preferred: dict[int, int] | None = None,
        content: str | None = None,
        prefix: str = "",
        timeout: float = TRY_OTHER_TIMEOUT,
    ):
        super().__init__(timeout=timeout)
        self.matches = matches
        self.translate_to = translate_to
        self.preferred = preferred or {}
        self.content = content
        self.prefix = prefix
        self.rotations: dict[int, int] = {}

    def render(self) -> str:
        if self.content is not None:
            body = rewrite_inline(self.content, self.matches, self.rotations, self.translate_to, self.preferred)
        else:
            body = rewrite(self.matches, self.rotations, self.translate_to, self.preferred)
        return (self.prefix + body)[:2000]

    @discord.ui.button(label="Try other", style=discord.ButtonStyle.secondary)
    async def try_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        for match in self.matches:
            if len(available_methods(match)) > 1:
                key = int(match.domain.id)
                self.rotations[key] = self.rotations.get(key, 0) + 1
        await interaction.response.edit_message(content=self.render(), view=self)


class EmbedFixerMixin:
    """Config, listener and helpers for EmbedFixer. Composed into the Media cog."""

    def _embedfixer_init(self):
        # Same identifier and cog name as the old standalone cog, so its data is reused.
        self.embedfixer_config = Config.get_conf(
            None, identifier=EMBEDFIXER_ID, cog_name="EmbedFixer", force_registration=True
        )
        self.embedfixer_config.register_guild(**EMBEDFIXER_GUILD_DEFAULTS)
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

    def _embedfixer_settings(self) -> dict[str, list[dict]]:
        """Panel settings added on top of the basic toggles, keyed by section."""
        config = self.embedfixer_config
        field = lambda name: config_field(config, name)  # noqa: E731

        def site_toggle(domain):
            async def get(guild):
                return int(domain.id) not in await config.guild(guild).disabled_sites()

            async def set_(guild, value):
                async with config.guild(guild).disabled_sites() as disabled:
                    if value and int(domain.id) in disabled:
                        disabled.remove(int(domain.id))
                    elif not value and int(domain.id) not in disabled:
                        disabled.append(int(domain.id))

            return {
                "key": f"embedfixer.site.{int(domain.id)}", "label": domain.name, "type": "bool",
                "section": "EmbedFixer sites", "help": f"Fix {domain.name} links.", "get": get, "set": set_,
            }

        def service_choice(domain):
            async def get(guild):
                services = await config.guild(guild).services()
                stored = services.get(str(int(domain.id)))
                if stored is not None and domain.get_fix_method(int(stored)) is not None:
                    return int(stored)
                return domain.default_fix_method.id

            async def set_(guild, value):
                async with config.guild(guild).services() as services:
                    services[str(int(domain.id))] = int(value)

            return {
                "key": f"embedfixer.service.{int(domain.id)}", "label": domain.name, "type": "choice",
                "section": "EmbedFixer services",
                "choices": [
                    {"value": method.id, "label": method.name + (" (ads)" if method.has_ads else "")}
                    for method in domain.fix_methods
                ],
                "help": f"Service tried first for {domain.name}. Try other moves on from it.",
                "get": get, "set": set_,
            }

        domains = [domain for domain in fixable_domains() if domain.id != DomainId.YOUTUBE]
        general = [
            {"key": "embedfixer.ignored_channels", "label": "Ignored channels", "type": "channels",
             "section": "EmbedFixer", "channel_types": ["text", "news", "voice", "forum", "stage", "category"],
             "help": "Never fix links in these channels or categories.", **field("ignored_channels")},
            {"key": "embedfixer.ignored_roles", "label": "Ignored roles", "type": "roles", "section": "EmbedFixer",
             "help": "Never fix links posted by members with these roles.", **field("ignored_roles")},
            {"key": "embedfixer.repost_mode", "label": "Repost as", "type": "choice", "section": "EmbedFixer",
             "choices": [
                 {"value": "webhook", "label": "The author (webhook with their name and avatar)"},
                 {"value": "bot", "label": "The bot (a reply to the original)"},
             ],
             "help": "Who the fixed links are posted as. Webhooks need Manage Webhooks.",
             **field("repost_mode")},
            {"key": "embedfixer.original_action", "label": "Original message", "type": "choice",
             "section": "EmbedFixer",
             "choices": [
                 {"value": "suppress", "label": "Hide its embeds"},
                 {"value": "delete", "label": "Delete it (the repost keeps its text)"},
                 {"value": "keep", "label": "Leave it alone"},
             ],
             "help": "What happens to the original after reposting. Needs Manage Messages.",
             **field("original_action")},
            {"key": "embedfixer.try_other", "label": "Try other button", "type": "bool", "section": "EmbedFixer",
             "help": "Add a button that switches links to the next service.", **field("try_other")},
            {"key": "embedfixer.try_other_hours", "label": "Try other lifetime", "type": "int",
             "section": "EmbedFixer", "min": 1, "max": 48, "unit": "hours",
             "help": "How long the Try other button keeps working. Resets on bot restart.",
             **field("try_other_hours")},
        ]
        return {
            "EmbedFixer": general,
            "EmbedFixer sites": [site_toggle(domain) for domain in domains],
            "EmbedFixer services": [service_choice(domain) for domain in domains if len(domain.fix_methods) > 1],
        }

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

        settings = {**EMBEDFIXER_GUILD_DEFAULTS, **await self.embedfixer_config.guild(message.guild).all()}
        if settings["ignored_channels"] and channel_listed(message.channel, settings["ignored_channels"]):
            return
        ignored_roles = {int(role) for role in settings["ignored_roles"]}
        if ignored_roles and any(role.id in ignored_roles for role in getattr(message.author, "roles", [])):
            return
        opted_in = frozenset({DomainId.YOUTUBE}) if settings["youtube"] else frozenset()
        disabled = frozenset(int(site) for site in settings["disabled_sites"])
        matches = find_matches(message.content, opted_in, disabled)
        if not matches:
            return
        translate_to = settings["translate_to"]
        preferred = {int(key): int(value) for key, value in (settings["services"] or {}).items()}
        action, mode = settings["original_action"], settings["repost_mode"]
        if action == "delete" and (message.attachments or message.stickers):
            action = "suppress"  # Deleting would lose the files, which a repost cannot carry.
        full_content = message.content if action == "delete" else None
        prefix = f"{message.author.mention}: " if action == "delete" and mode == "bot" else ""
        if full_content is not None:
            rewritten = rewrite_inline(full_content, matches, {}, translate_to, preferred)
        else:
            rewritten = rewrite(matches, {}, translate_to, preferred)
        if rewritten == message.content:
            return

        view = None
        if settings["try_other"] and has_alternatives(matches):
            view = TryOtherView(
                matches, translate_to, preferred=preferred, content=full_content, prefix=prefix,
                timeout=max(1, int(settings["try_other_hours"])) * 3600,
            )
        content = (prefix + rewritten)[:2000]
        if not await self._embedfixer_repost(message, content, view=view, mode=mode, reply=action != "delete"):
            return
        if action == "delete":
            if not await self._embedfixer_delete(message):
                await self._embedfixer_suppress(message)
        elif action == "suppress":
            await self._embedfixer_suppress(message)

    async def _embedfixer_repost(
        self, message: discord.Message, content: str, *, view=None, mode: str = "webhook", reply: bool = False
    ) -> bool:
        channel = message.channel
        me = message.guild.me
        if me is None or not hasattr(channel, "permissions_for"):
            return False
        if not channel.permissions_for(me).send_messages:
            return False

        webhook, thread = (None, None) if mode == "bot" else await self._embedfixer_webhook(channel)
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

        kwargs = {"content": content, "allowed_mentions": discord.AllowedMentions.none(), "view": view}
        if mode == "bot" and reply:
            kwargs["reference"] = message.to_reference(fail_if_not_exists=False)
        try:
            await channel.send(**kwargs)
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
    async def _embedfixer_delete(message: discord.Message) -> bool:
        me = message.guild.me
        if me is None or not hasattr(message.channel, "permissions_for"):
            return False
        if not message.channel.permissions_for(me).manage_messages:
            return False
        try:
            await message.delete()
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

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
