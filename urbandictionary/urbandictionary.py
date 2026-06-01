from __future__ import annotations

import re
from datetime import datetime
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

        await ctx.send(embed=self._build_embed(entries[0]))

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

    def _build_embed(self, entry: dict, *, title_prefix: str | None = None) -> discord.Embed:
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

        written_on = self._format_written_on(entry.get("written_on"))
        if written_on:
            embed.add_field(name="Written", value=written_on, inline=True)

        embed.set_footer(text="Urban Dictionary")
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
    def _format_written_on(value) -> str | None:
        if not value:
            return None

        try:
            written_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

        return written_at.strftime("%Y-%m-%d")

    @staticmethod
    def _term_url(term: str) -> str:
        return f"https://www.urbandictionary.com/define.php?term={quote_plus(term)}"


class UrbanDictionaryError(Exception):
    pass
