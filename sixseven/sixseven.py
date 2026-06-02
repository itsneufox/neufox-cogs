import random
import re
import time

import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import pagify


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


class SixSeven(commands.Cog):
    """Replies with an annoyed comeback to six seven jokes."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=670670067)
        self.config.register_guild(
            enabled=True,
            cooldown=30,
            response=None,
            default_language="en",
            channel_languages={},
            category_languages={},
        )
        self._last_reply: dict[int, float] = {}

    @commands.group(name="sixseven", aliases=["67"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven(self, ctx: commands.Context):
        """Manage SixSeven settings."""
        await ctx.invoke(self.sixseven_show)

    @sixseven.command(name="help", aliases=["commands"])
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_help(self, ctx: commands.Context):
        """Show SixSeven help."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="SixSeven Help",
            description="Configure automatic annoyed replies to six seven jokes.",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Commands",
            value="\n".join(
                [
                    f"`{prefix}sixseven show` - show current settings",
                    f"`{prefix}sixseven toggle` - enable or disable replies",
                    f"`{prefix}sixseven cooldown <seconds>` - set the server cooldown",
                    f"`{prefix}sixseven response <text>` - set a fixed response",
                    f"`{prefix}sixseven reset` - return to random built-in responses",
                    f"`{prefix}sixseven responses [en|pt]` - list built-in responses",
                    f"`{prefix}sixseven language default <en|pt>` - set default language",
                    f"`{prefix}sixseven language channel <channel> <en|pt>` - set channel language",
                    f"`{prefix}sixseven language category <category> <en|pt>` - set category language",
                    f"`{prefix}sixseven language clearchannel <channel>` - remove channel override",
                    f"`{prefix}sixseven language clearcategory <category>` - remove category override",
                ]
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @sixseven.command(name="show")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_show(self, ctx: commands.Context):
        """Show current SixSeven configuration."""
        data = await self.config.guild(ctx.guild).all()
        embed = discord.Embed(
            title="SixSeven Configuration",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Status",
            value="Enabled" if data["enabled"] else "Disabled",
            inline=True,
        )
        embed.add_field(
            name="Cooldown",
            value=f'{data["cooldown"]} seconds',
            inline=True,
        )
        embed.add_field(
            name="Response",
            value=data["response"] or "Random built-in response",
            inline=False,
        )
        embed.add_field(
            name="Default Language",
            value=self._language_name(data["default_language"]),
            inline=True,
        )
        embed.add_field(
            name="Channel Languages",
            value=self._format_channel_languages(ctx.guild, data["channel_languages"]),
            inline=False,
        )
        embed.add_field(
            name="Category Languages",
            value=self._format_category_languages(ctx.guild, data["category_languages"]),
            inline=False,
        )
        await ctx.send(embed=embed)

    @sixseven.command(name="toggle")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_toggle(self, ctx: commands.Context):
        """Toggle automatic six seven replies in this server."""
        enabled = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not enabled)
        status = "enabled" if not enabled else "disabled"
        await ctx.send(f"SixSeven replies are now {status}.")

    @sixseven.command(name="cooldown")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_cooldown(self, ctx: commands.Context, seconds: int):
        """Set the per-server reply cooldown in seconds."""
        if seconds < 0 or seconds > 3600:
            await ctx.send("Cooldown must be between 0 and 3600 seconds.")
            return

        await self.config.guild(ctx.guild).cooldown.set(seconds)
        await ctx.send(f"SixSeven cooldown set to {seconds} seconds.")

    @sixseven.command(name="response")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_response(self, ctx: commands.Context, *, response: str):
        """Set one fixed annoyed response text."""
        await self.config.guild(ctx.guild).response.set(response)
        await ctx.send("SixSeven response updated.")

    @sixseven.command(name="responses")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_responses(self, ctx: commands.Context, language: str = "en"):
        """List the built-in random responses."""
        language = self._normalize_language(language)
        if not language:
            await ctx.send("Language must be `en` or `pt`.")
            return

        response_list = "\n".join(
            f"{index}. {response}"
            for index, response in enumerate(RESPONSES_BY_LANGUAGE[language], start=1)
        )
        for page in pagify(response_list, page_length=1900):
            await ctx.send(page)

    @sixseven.group(name="language", aliases=["lang"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_language(self, ctx: commands.Context):
        """Manage response languages by server, category, or channel."""
        await ctx.invoke(self.sixseven_show)

    @sixseven_language.command(name="default")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_language_default(self, ctx: commands.Context, language: str):
        """Set the default response language for this server."""
        language = self._normalize_language(language)
        if not language:
            await ctx.send("Language must be `en` or `pt`.")
            return

        await self.config.guild(ctx.guild).default_language.set(language)
        await ctx.send(f"Default SixSeven language set to {self._language_name(language)}.")

    @sixseven_language.command(name="channel")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_language_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        language: str,
    ):
        """Set the response language for a channel."""
        language = self._normalize_language(language)
        if not language:
            await ctx.send("Language must be `en` or `pt`.")
            return

        async with self.config.guild(ctx.guild).channel_languages() as channel_languages:
            channel_languages[str(channel.id)] = language
        await ctx.send(
            f"SixSeven language for {channel.mention} set to {self._language_name(language)}."
        )

    @sixseven_language.command(name="category")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_language_category(
        self,
        ctx: commands.Context,
        category: discord.CategoryChannel,
        language: str,
    ):
        """Set the response language for a category."""
        language = self._normalize_language(language)
        if not language:
            await ctx.send("Language must be `en` or `pt`.")
            return

        async with self.config.guild(ctx.guild).category_languages() as category_languages:
            category_languages[str(category.id)] = language
        await ctx.send(
            f"SixSeven language for {category.name} set to {self._language_name(language)}."
        )

    @sixseven_language.command(name="clearchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_language_clearchannel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ):
        """Remove a channel-specific language setting."""
        async with self.config.guild(ctx.guild).channel_languages() as channel_languages:
            channel_languages.pop(str(channel.id), None)
        await ctx.send(f"Removed SixSeven language override for {channel.mention}.")

    @sixseven_language.command(name="clearcategory")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_language_clearcategory(
        self,
        ctx: commands.Context,
        category: discord.CategoryChannel,
    ):
        """Remove a category-specific language setting."""
        async with self.config.guild(ctx.guild).category_languages() as category_languages:
            category_languages.pop(str(category.id), None)
        await ctx.send(f"Removed SixSeven language override for {category.name}.")

    @sixseven.command(name="reset")
    @commands.admin_or_permissions(manage_guild=True)
    async def sixseven_reset(self, ctx: commands.Context):
        """Reset to random built-in responses."""
        await self.config.guild(ctx.guild).response.set(None)
        await ctx.send("SixSeven response reset to random built-in replies.")

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        match = SIX_SEVEN_PATTERN.search(message.content)
        if not match:
            return

        guild_config = self.config.guild(message.guild)
        if not await guild_config.enabled():
            return

        now = time.monotonic()
        cooldown = await guild_config.cooldown()
        last_reply = self._last_reply.get(message.guild.id, 0)
        if cooldown and now - last_reply < cooldown:
            return

        response = await guild_config.response()
        if not response:
            data = await guild_config.all()
            language = self._resolve_language(message, data)
            response = random.choice(RESPONSES_BY_LANGUAGE[language])
            response = response.format(term=self._clean_term(match.group(0)))

        try:
            await message.reply(
                response,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            return

        self._last_reply[message.guild.id] = now

    def _normalize_language(self, language: str) -> str | None:
        return LANGUAGE_ALIASES.get(language.casefold())

    def _language_name(self, language: str) -> str:
        return LANGUAGE_NAMES.get(language, language)

    def _clean_term(self, term: str) -> str:
        return " ".join(term.split())

    def _resolve_language(self, message: discord.Message, data: dict) -> str:
        channel_id = str(message.channel.id)
        channel_languages = data.get("channel_languages", {})
        if channel_id in channel_languages:
            return channel_languages[channel_id]

        category = getattr(message.channel, "category", None)
        category_languages = data.get("category_languages", {})
        if category and str(category.id) in category_languages:
            return category_languages[str(category.id)]

        language = data.get("default_language", "en")
        if language not in RESPONSES_BY_LANGUAGE:
            return "en"
        return language

    def _format_channel_languages(self, guild: discord.Guild, channel_languages: dict) -> str:
        if not channel_languages:
            return "*None*"

        lines = []
        for channel_id, language in channel_languages.items():
            channel = guild.get_channel(int(channel_id))
            name = channel.mention if channel else f"Deleted channel `{channel_id}`"
            lines.append(f"{name}: {self._language_name(language)}")
        return "\n".join(lines[:10])

    def _format_category_languages(self, guild: discord.Guild, category_languages: dict) -> str:
        if not category_languages:
            return "*None*"

        lines = []
        for category_id, language in category_languages.items():
            category = guild.get_channel(int(category_id))
            name = category.name if category else f"Deleted category `{category_id}`"
            lines.append(f"{name}: {self._language_name(language)}")
        return "\n".join(lines[:10])
