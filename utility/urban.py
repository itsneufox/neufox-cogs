"""/urban: Urban Dictionary lookups."""

from __future__ import annotations

from urllib.parse import quote_plus

import aiohttp
import discord
from redbot.core import app_commands, commands

from .layouts import send_paged


API_BASE_URL = "https://api.urbandictionary.com/v0"
URBAN_COLOR = discord.Color.dark_teal()
REQUEST_TIMEOUT = 10
DESCRIPTION_LIMIT = 3500
MAX_RESULTS = 10


class UrbanDictionaryError(Exception):
    pass


def _clean(value) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _count(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def definition_embed(entry: dict, *, title_prefix: str | None = None, page: int = 0, total: int = 1) -> discord.Embed:
    word = _clean(entry.get("word", "Unknown"))
    title = f"{word} by {_clean(entry.get('author') or 'Unknown')}"
    if title_prefix:
        title = f"{title_prefix}: {title}"
    permalink = entry.get("permalink") or f"https://www.urbandictionary.com/define.php?term={quote_plus(word)}"
    description = _clean(entry.get("definition", ""))
    example = _clean(entry.get("example", ""))
    if example:
        description = f"{description}\n\n**Example:** {example}"

    embed = discord.Embed(
        title=_truncate(title, 256),
        url=str(permalink),
        description=_truncate(description, DESCRIPTION_LIMIT),
        color=URBAN_COLOR,
    )
    footer = f"{_count(entry.get('thumbs_down'))} Down / {_count(entry.get('thumbs_up'))} Up, Powered by Urban Dictionary"
    if total > 1:
        footer = f"{footer} | Definition {page + 1}/{total}"
    embed.set_footer(text=footer)
    return embed


async def fetch_entries(path: str, params: dict[str, str] | None = None) -> list[dict]:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{API_BASE_URL}/{path}", params=params) as response:
                if response.status != 200:
                    raise UrbanDictionaryError(f"Urban Dictionary returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        raise UrbanDictionaryError("Urban Dictionary did not respond in time.") from None
    except ValueError:
        raise UrbanDictionaryError("Urban Dictionary returned an invalid response.") from None

    entries = payload.get("list") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise UrbanDictionaryError("Urban Dictionary returned an unexpected response.")
    return [
        entry
        for entry in entries[:MAX_RESULTS]
        if isinstance(entry, dict) and entry.get("word") and entry.get("definition")
    ]


class UrbanMixin:
    @commands.hybrid_command(name="urban", aliases=["ud", "define"])
    @app_commands.describe(term="The word or phrase to look up. Leave empty for a random entry.")
    async def urban(self, ctx: commands.Context, *, term: str | None = None):
        """Look up a term on Urban Dictionary, or get a random entry."""
        term = (term or "").strip()
        if term.casefold() == "random" and ctx.interaction is None:
            term = ""  # keep the old `[p]urban random` behaviour for prefix users
        async with ctx.typing():
            try:
                entries = await fetch_entries("define", {"term": term}) if term else await fetch_entries("random")
            except UrbanDictionaryError as error:
                await ctx.send(str(error), ephemeral=True)
                return

        if not entries:
            message = "No Urban Dictionary definitions found for that term." if term else "Urban Dictionary did not return a random entry."
            await ctx.send(message, ephemeral=True)
            return
        if not term:
            await send_paged(ctx, [definition_embed(entries[0], title_prefix="Random")])
            return
        pages = [definition_embed(entry, page=index, total=len(entries)) for index, entry in enumerate(entries)]
        await send_paged(ctx, pages, denied="This lookup is not yours to control.")
