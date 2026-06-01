from __future__ import annotations

import re
from urllib.parse import quote_plus

import aiohttp
import discord
from redbot.core import commands


API_BASE_URL = "https://api.urbandictionary.com/v0"
DEFAULT_COLOR = discord.Color.dark_teal()
REQUEST_TIMEOUT = 10
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_LIMIT = 1024
MAX_RESULTS = 10
BRACKETED_TERM_RE = re.compile(r"\[([^\]]+)\]")
PAGINATION_TIMEOUT = 120


class DefinitionView(discord.ui.View):
    def __init__(self, owner_id: int, cog: "UrbanDictionary", entries: list[dict]):
        super().__init__(timeout=PAGINATION_TIMEOUT)
        self.owner_id = owner_id
        self.cog = cog
        self.entries = entries
        self.page = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This lookup is not yours to control.", ephemeral=True)
        return False

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.cog._build_embed(self.entries[self.page], page=self.page, total=len(self.entries)),
            view=self,
        )

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.page = min(len(self.entries) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.cog._build_embed(self.entries[self.page], page=self.page, total=len(self.entries)),
            view=self,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _update_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= len(self.entries) - 1


class UrbanDictionary(commands.Cog):
    """Look up Urban Dictionary definitions."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="urban", aliases=["ud"], invoke_without_command=True)
    async def urban(self, ctx: commands.Context, *, term: str | None = None):
        """Look up a term on Urban Dictionary."""
        if not term:
            await ctx.send_help()
            return

        await self._send_lookup(ctx, term)

    @urban.command(name="random")
    async def urban_random(self, ctx: commands.Context):
        """Show a random Urban Dictionary entry."""
        async with ctx.typing():
            try:
                entries = await self._fetch_random()
            except UrbanDictionaryError as error:
                await ctx.send(str(error))
                return

        if not entries:
            await ctx.send("Urban Dictionary did not return a random entry.")
            return

        await ctx.send(embed=self._build_embed(entries[0], title_prefix="Random"))

    @commands.command(name="define")
    async def define(self, ctx: commands.Context, *, term: str):
        """Look up a term on Urban Dictionary."""
        await self._send_lookup(ctx, term)

    async def _send_lookup(self, ctx: commands.Context, term: str):
        async with ctx.typing():
            try:
                entries = await self._fetch_definitions(term)
            except UrbanDictionaryError as error:
                await ctx.send(str(error))
                return

        if not entries:
            await ctx.send(f"No Urban Dictionary definitions found for `{term}`.")
            return

        view = DefinitionView(ctx.author.id, self, entries) if len(entries) > 1 else None
        message = await ctx.send(
            embed=self._build_embed(entries[0], page=0, total=len(entries)),
            view=view,
        )
        if view is not None:
            view.message = message

    async def _fetch_definitions(self, term: str) -> list[dict]:
        query = term.strip()
        if not query:
            return []

        return await self._request_json(f"{API_BASE_URL}/define", params={"term": query})

    async def _fetch_random(self) -> list[dict]:
        return await self._request_json(f"{API_BASE_URL}/random")

    async def _request_json(self, url: str, params: dict[str, str] | None = None) -> list[dict]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        raise UrbanDictionaryError(
                            f"Urban Dictionary returned HTTP {response.status}."
                        )
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError):
            raise UrbanDictionaryError("Urban Dictionary did not respond in time.")
        except ValueError:
            raise UrbanDictionaryError("Urban Dictionary returned an invalid response.")

        entries = payload.get("list")
        if not isinstance(entries, list):
            raise UrbanDictionaryError("Urban Dictionary returned an unexpected response.")

        return [
            entry
            for entry in entries[:MAX_RESULTS]
            if isinstance(entry, dict) and entry.get("word") and entry.get("definition")
        ]

    def _build_embed(
        self,
        entry: dict,
        *,
        title_prefix: str | None = None,
        page: int | None = None,
        total: int | None = None,
    ) -> discord.Embed:
        word = self._clean_text(str(entry.get("word", "Unknown")))
        title = f"{title_prefix}: {word}" if title_prefix else word
        permalink = entry.get("permalink") or self._term_url(word)
        definition = self._clean_text(str(entry.get("definition", "")))
        example = self._clean_text(str(entry.get("example", "")))

        embed = discord.Embed(
            title=self._truncate(title, 256),
            url=str(permalink),
            description=self._truncate(definition, EMBED_DESCRIPTION_LIMIT),
            color=DEFAULT_COLOR,
        )

        if example:
            embed.add_field(
                name="Example",
                value=self._truncate(example, EMBED_FIELD_LIMIT),
                inline=False,
            )

        thumbs_up = self._format_count(entry.get("thumbs_up"))
        thumbs_down = self._format_count(entry.get("thumbs_down"))
        embed.add_field(name="Votes", value=f"+{thumbs_up} / -{thumbs_down}", inline=True)

        author = entry.get("author")
        if author:
            embed.add_field(
                name="Author",
                value=self._truncate(self._clean_text(str(author)), 256),
                inline=True,
            )

        footer = "Urban Dictionary"
        if page is not None and total is not None and total > 1:
            footer = f"{footer} | Definition {page + 1}/{total}"
        embed.set_footer(text=footer)
        return embed

    @staticmethod
    def _clean_text(value: str) -> str:
        value = BRACKETED_TERM_RE.sub(r"\1", value)
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3].rstrip() + "..."

    @staticmethod
    def _format_count(value) -> str:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "0"

    @staticmethod
    def _term_url(term: str) -> str:
        return f"https://www.urbandictionary.com/define.php?term={quote_plus(term)}"


class UrbanDictionaryError(Exception):
    pass
