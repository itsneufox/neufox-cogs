"""SixSeven: an annoyed auto-reply to six seven jokes. Configured from the /control panel."""

from __future__ import annotations

import random
import re
import time
from typing import Any

import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import pagify

from .layouts import NO_PINGS, channel_allowed, text_card


CONFIG_ID = 670670067
CONFIG_NAME = "SixSeven"  # The old standalone cog's class name, which keys its stored data.
SIXSEVEN_COLOR = discord.Color.orange()

SIX_SEVEN_PATTERN = re.compile(
    r"(?<!\w)(?:s+\s*i+\s*x+\W*s+\s*e+\s*v+\s*e+\s*n+|six\W*seven|6\W*7|67)(?!\w)",
    re.IGNORECASE,
)

DEFAULT_RESPONSE = (
    "If {term} is funny to you, you probably clap when the plane lands."
)

DEFAULT_RESPONSES = [
    DEFAULT_RESPONSE,
    'I know you giggled before sending that.',
    'You definitely leaned back proud of that one.',
    'Bro typed that and waited for the room to explode laughing.',
    'You sent that like it was the funniest thing ever said.',
    'I can tell you rehearsed that joke in your head first.',
    'You hit send and thought you cooked.',
    'You probably reread that message smiling.',
    'That sounded way funnier in your head huh.',
    'You were too excited to send that.',
    'You typed that with full confidence too.',
    'You really thought that was gonna hit.',
    'You said that like everyone was about to lose it laughing.',
    'You couldn’t wait to send that one.',
    'That message had you grinning at your own screen.',
    'You definitely expected a louder reaction than that.',
]

PORTUGUESE_RESPONSES = [
    "Se {term} tem graça pra você, você provavelmente bate palma quando o avião pousa.",
    'Eu sei que você deu uma risadinha antes de mandar isso.',
    'Você com certeza se recostou na cadeira todo orgulhoso dessa.',
    'O cara digitou isso e ficou esperando a sala explodir de rir.',
    'Você mandou isso como se fosse a coisa mais engraçada já dita.',
    'Dá pra ver que você ensaiou essa piada na cabeça primeiro.',
    'Você apertou enviar e achou que tinha cozinhado.',
    'Você provavelmente releu essa mensagem sorrindo.',
    'Soou bem mais engraçado na sua cabeça, né?',
    'Você estava empolgado demais pra mandar isso.',
    'Você escreveu isso com confiança total também.',
    'Você achou mesmo que isso ia pegar.',
    'Você falou isso como se todo mundo fosse morrer de rir.',
    'Nem conseguiu esperar pra mandar essa.',
    'Essa mensagem te deixou sorrindo pra tela.',
    'Você com certeza esperava uma reação maior do que essa.',
]

RESPONSES_BY_LANGUAGE = {
    "en": DEFAULT_RESPONSES,
    "pt": PORTUGUESE_RESPONSES,
}

LANGUAGE_NAMES = {
    "en": "English",
    "pt": "Brazilian Portuguese",
}

LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "pt": "pt",
    "pt-br": "pt",
    "br": "pt",
    "pt-pt": "pt",
    "portuguese": "pt",
    "portugues": "pt",
    "português": "pt",
}


def resolve_language(channel, data: dict) -> str:
    """Channel override, then category override, then the server default."""
    language = data.get("channel_languages", {}).get(str(channel.id))
    category = getattr(channel, "category", None)
    if language is None and category is not None:
        language = data.get("category_languages", {}).get(str(category.id))
    if language is None:
        language = data.get("default_language", "en")
    return language if language in RESPONSES_BY_LANGUAGE else "en"


class SixSevenMixin:
    def _sixseven_init(self):
        self.sixseven_config = Config.get_conf(
            None, identifier=CONFIG_ID, cog_name=CONFIG_NAME, force_registration=True
        )
        self.sixseven_config.register_guild(
            enabled=True,
            cooldown=30,
            response=None,
            default_language="en",
            channel_languages={},
            category_languages={},
            chance=100,
            per_channel_cooldown=False,
            ignored_channels=[],
            ignored_roles=[],
            mention_author=False,
        )
        self._sixseven_last_reply: dict[int, float] = {}

    def _sixseven_settings(self) -> list[dict[str, Any]]:
        config = self.sixseven_config

        def bind(key):
            return {
                "get": lambda guild: config.guild(guild).get_attr(key)(),
                "set": lambda guild, value: config.guild(guild).get_attr(key).set(value),
            }

        def overrides(store: str, language: str):
            """Expose one language's slice of a {id: language} map as a channel list."""

            async def get(guild):
                mapping = await config.guild(guild).get_attr(store)()
                return [int(key) for key, value in mapping.items() if value == language]

            async def set_(guild, ids):
                wanted = {str(int(item)) for item in ids}
                async with config.guild(guild).get_attr(store)() as mapping:
                    for key in [key for key, value in mapping.items() if value == language and key not in wanted]:
                        del mapping[key]
                    for key in wanted:
                        mapping[key] = language

            return {"get": get, "set": set_}

        settings: list[dict[str, Any]] = [
            {
                "key": "sixseven_enabled",
                "label": "Replies",
                "type": "bool",
                "help": "Reply to six seven jokes in this server.",
                **bind("enabled"),
            },
            {
                "key": "sixseven_cooldown",
                "label": "Cooldown",
                "type": "int",
                "min": 0,
                "max": 3600,
                "unit": "seconds",
                "help": "Minimum time between replies in this server.",
                **bind("cooldown"),
            },
            {
                "key": "sixseven_per_channel_cooldown",
                "label": "Cooldown per channel",
                "type": "bool",
                "help": "Count the cooldown separately in each channel instead of server-wide.",
                **bind("per_channel_cooldown"),
            },
            {
                "key": "sixseven_chance",
                "label": "Reply chance",
                "type": "int",
                "min": 1,
                "max": 100,
                "unit": "%",
                "help": "Chance of replying to a matching message once the cooldown allows it.",
                **bind("chance"),
            },
            {
                "key": "sixseven_mention_author",
                "label": "Ping the author",
                "type": "bool",
                "help": "Mention the author in the reply.",
                **bind("mention_author"),
            },
            {
                "key": "sixseven_ignored_channels",
                "label": "Ignored channels",
                "type": "channels",
                "channel_types": ["text", "news", "voice", "forum", "stage", "category"],
                "help": "Never reply in these channels or categories.",
                **bind("ignored_channels"),
            },
            {
                "key": "sixseven_ignored_roles",
                "label": "Ignored roles",
                "type": "roles",
                "help": "Never reply to members with any of these roles.",
                **bind("ignored_roles"),
            },
            {
                "key": "sixseven_response",
                "label": "Fixed response",
                "type": "text",
                "max": 2000,
                "clearable": True,
                "empty": "Random built-in response",
                "help": "Leave blank to use the random built-in responses.",
                **bind("response"),
            },
            {
                "key": "sixseven_default_language",
                "label": "Default language",
                "type": "choice",
                "choices": [{"value": code, "label": name} for code, name in LANGUAGE_NAMES.items()],
                "help": "Used where no channel or category language is set.",
                **bind("default_language"),
            },
        ]
        for code, name in LANGUAGE_NAMES.items():
            settings.append(
                {
                    "key": f"sixseven_channels_{code}",
                    "label": f"{name} channels",
                    "type": "channels",
                    "channel_types": ["text", "news", "voice", "forum", "stage"],
                    "help": f"Always reply in {name} in these channels.",
                    **overrides("channel_languages", code),
                }
            )
            settings.append(
                {
                    "key": f"sixseven_categories_{code}",
                    "label": f"{name} categories",
                    "type": "channels",
                    "channel_types": ["category"],
                    "help": f"Reply in {name} in these categories unless the channel says otherwise.",
                    **overrides("category_languages", code),
                }
            )
        for setting in settings:
            setting["section"] = "SixSeven"
        return settings

    @commands.command(name="sixseven")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven(self, ctx: commands.Context, language: str = "en"):
        """List SixSeven's built-in replies (`en` or `pt`). Settings live in `/control`."""
        code = LANGUAGE_ALIASES.get(language.casefold())
        if code is None:
            await ctx.send("Language must be `en` or `pt`.")
            return
        lines = "\n".join(f"{index}. {text}" for index, text in enumerate(RESPONSES_BY_LANGUAGE[code], start=1))
        for page in pagify(f"## {LANGUAGE_NAMES[code]} replies\n{lines}", page_length=1900):
            await ctx.send(view=text_card(page, SIXSEVEN_COLOR), allowed_mentions=NO_PINGS)

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        match = SIX_SEVEN_PATTERN.search(message.content)
        if not match:
            return
        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return

        data = await self.sixseven_config.guild(message.guild).all()
        if not data["enabled"]:
            return
        if data.get("ignored_channels") and channel_allowed(message.channel, data["ignored_channels"]):
            return
        ignored_roles = {int(role) for role in data.get("ignored_roles") or []}
        if ignored_roles and any(role.id in ignored_roles for role in getattr(message.author, "roles", [])):
            return
        bucket = message.channel.id if data.get("per_channel_cooldown") else message.guild.id
        now = time.monotonic()
        last_reply = self._sixseven_last_reply.get(bucket, 0)
        if data["cooldown"] and now - last_reply < data["cooldown"]:
            return
        chance = int(data.get("chance", 100))
        if chance < 100 and random.randint(1, 100) > chance:
            return

        response = data["response"]
        if not response:
            language = resolve_language(message.channel, data)
            response = random.choice(RESPONSES_BY_LANGUAGE[language]).format(term=" ".join(match.group(0).split()))
        try:
            mention = bool(data.get("mention_author"))
            mentions = discord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=mention)
            await message.reply(response, mention_author=mention, allowed_mentions=mentions)
        except discord.HTTPException:
            return
        self._sixseven_last_reply[bucket] = now
