"""/wiki: open.mp documentation search."""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any

import aiohttp
import discord
from bs4 import BeautifulSoup
from redbot.core import app_commands, commands

from .layouts import NO_PINGS, Pager, card, embed_items


log = logging.getLogger("red.neufox.utility.wiki")

ALGOLIA_APP_ID = "AOKXGK39Z7"
ALGOLIA_API_KEY = "54204f37e5c8fc2871052d595ee0505e"  # Public search-only key used by open.mp's own site.
ALGOLIA_INDEX = "open"
REQUEST_TIMEOUT = 10
WIKI_COLOR = 0x0099FF
DOC_CHUNK_SIZE = 3400
DOC_VIEW_TIMEOUT = 600.0
CACHE_SECONDS = 10 * 60
DOC_TYPES = {
    "functions": "Function",
    "callbacks": "Callback",
    "natives": "Native",
    "constants": "Constant",
    "libraries": "Library",
}
NOT_YOURS = "Only the person who ran the search command can use these buttons."


def truncate_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    cut = text.rfind(" ", 0, max_length)
    return text[: cut if cut > 0 else max_length] + " ..."


def split_chunks(content: str, size: int = DOC_CHUNK_SIZE) -> list[str]:
    """Split text into pages, preferring paragraph breaks."""
    chunks = []
    while content:
        cut = min(size, len(content))
        if cut < len(content):
            paragraph = content.rfind("\n\n", 0, cut)
            if paragraph > cut // 2:
                cut = paragraph + 2
        chunks.append(content[:cut])
        content = content[cut:]
    return chunks


def filter_hits(hits: list[dict]) -> list[dict]:
    """Keep up to 10 distinct documentation pages, dropping blog posts and listings."""
    results, seen = [], set()
    for hit in hits:
        url = hit.get("url_without_anchor", "")
        if not url or url in seen:
            continue
        seen.add(url)
        parts = url.rstrip("/").split("/")
        if (
            len(parts) < 2
            or parts[-2] == "blog"
            or "tags" in parts
            or parts[-1].startswith("omp-")
            or parts[-1] in DOC_TYPES
            or not any(kind in parts for kind in DOC_TYPES)
        ):
            continue
        results.append(hit)
        if len(results) >= 10:
            break
    return results


def hit_summary(hit: dict) -> str:
    hierarchy = hit.get("hierarchy") or {}
    if hit.get("content"):
        desc = hit["content"].replace("<mark>", "**").replace("</mark>", "**")
    else:
        desc = hierarchy.get("lvl1") or hit.get("description") or hit.get("text") or hit.get("snippet") or ""
    desc = re.sub(r"<[^>]*>", "", str(desc))
    if not desc or desc in ("undefined", "null"):
        desc = "(No description found)"
    return truncate_text(desc, 120)


def results_embed(search_term: str, hits: list[dict], shown: int = 5) -> discord.Embed:
    lines = []
    for index, hit in enumerate(hits[: max(1, shown)], 1):
        url = hit["url_without_anchor"]
        parts = url.rstrip("/").split("/")
        kind = next((label for key, label in DOC_TYPES.items() if key in parts), "")
        line = f"**{index}.** [**{parts[-1]}**]({url})" + (f" `{kind}`" if kind else "")
        lines.append(f"{line}\n{hit_summary(hit)}\n")
    embed = discord.Embed(
        title=f'Documentation Search Results: "{search_term}"',
        description="\n".join(lines),
        color=WIKI_COLOR,
    )
    embed.set_footer(text="Click a button number to view the full documentation")
    return embed


def doc_view(owner_id: int, title: str, url: str, content: str, timeout: float = DOC_VIEW_TIMEOUT) -> Pager:
    chunks = split_chunks(content) or ["No content found."]
    pages = []
    for index, chunk in enumerate(chunks):
        embed = discord.Embed(title=title, url=url, description=chunk, color=WIKI_COLOR)
        footer = "Documentation from open.mp"
        if len(chunks) > 1:
            footer += f" · Page {index + 1}/{len(chunks)}"
        embed.set_footer(text=footer)
        pages.append(embed)
    return Pager(
        owner_id, pages, link=("View Full Documentation", url), denied=NOT_YOURS, timeout=timeout
    )


class WikiSearchView(discord.ui.LayoutView):
    """Search results with one numbered button per result."""

    def __init__(
        self, cog, search_id: str, num_results: int, embed: discord.Embed, timeout: float = DOC_VIEW_TIMEOUT
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.search_id = search_id
        row = discord.ui.ActionRow()
        for index in range(num_results):
            button = discord.ui.Button(
                label=str(index + 1),
                style=discord.ButtonStyle.primary,
                custom_id=f"wiki_{search_id}_{index}",
            )
            button.callback = self._callback(index)
            row.add_item(button)
        self.add_item(discord.ui.Container(*embed_items(embed), row, accent_color=embed.color))

    def _callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            await self.cog._wiki_open_result(interaction, self.search_id, index)

        return callback

    async def on_timeout(self):
        self.cog._wiki_cache.pop(self.search_id, None)


class WikiMixin:
    def _wiki_init(self):
        self._wiki_cache: dict[str, dict[str, Any]] = {}

    def _wiki_prune(self):
        now = time.time()
        expired = [
            key
            for key, value in self._wiki_cache.items()
            if now - value["timestamp"] > value.get("lifetime", CACHE_SECONDS)
        ]
        for key in expired:
            del self._wiki_cache[key]

    @commands.hybrid_command(name="wiki")
    @app_commands.describe(search_term="What to search for, like SetPlayerPos. Leave empty for help.")
    async def wiki(self, ctx: commands.Context, *, search_term: str | None = None):
        """Search the open.mp documentation."""
        error, settings = await self._feature_gate(ctx, "wiki")
        if error:
            await ctx.send(error, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        lifetime = float(settings["wiki_button_minutes"]) * 60
        shown = int(settings["wiki_max_results"])
        if not search_term or search_term.casefold() in {"help", "commands"}:
            command = "/wiki" if ctx.interaction else f"{ctx.clean_prefix}wiki"
            embed = discord.Embed(
                title="Wiki Help",
                description=(
                    f"`{command} <search term>` searches the open.mp documentation, for example "
                    f"`{command} SetPlayerPos`. Pick a numbered button to read a result."
                ),
                color=WIKI_COLOR,
            )
            await ctx.send(view=card(embed), ephemeral=True, allowed_mentions=NO_PINGS)
            return
        if len(search_term) < 3:
            await ctx.send("Query must be 3 characters or more.", ephemeral=True)
            return

        async with ctx.typing():
            hits = filter_hits(await search_documentation(search_term))
        if not hits:
            embed = discord.Embed(
                title=f"No results: {search_term}",
                description="There were no results for that query.",
                color=WIKI_COLOR,
            )
            await ctx.send(view=card(embed), allowed_mentions=NO_PINGS)
            return

        self._wiki_prune()
        search_id = str(ctx.message.id if ctx.message else int(time.time()))
        self._wiki_cache[search_id] = {
            "hits": hits[:shown], "user_id": ctx.author.id, "timestamp": time.time(), "lifetime": lifetime
        }
        view = WikiSearchView(
            self, search_id, min(len(hits), shown), results_embed(search_term, hits, shown), timeout=lifetime
        )
        await ctx.send(view=view, allowed_mentions=NO_PINGS)

    async def _wiki_open_result(self, interaction: discord.Interaction, search_id: str, index: int):
        search = self._wiki_cache.get(search_id)
        if search is None:
            await interaction.response.send_message(
                "This search result has expired. Please run the search command again.", ephemeral=True
            )
            return
        if interaction.user.id != search["user_id"]:
            await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            return
        if index >= len(search["hits"]):
            await interaction.response.send_message("Invalid result selection. Please try again.", ephemeral=True)
            return

        url = search["hits"][index]["url_without_anchor"]
        await interaction.response.defer()
        lifetime = search.get("lifetime", DOC_VIEW_TIMEOUT)
        view = doc_view(interaction.user.id, url.rstrip("/").split("/")[-1], url, await fetch_doc(url), lifetime)
        view.message = await interaction.edit_original_response(view=view, allowed_mentions=NO_PINGS)


async def search_documentation(query: str, language: str = "en") -> list[dict[str, Any]]:
    payload = {"params": f"hitsPerPage=20&filters=language:{language}", "query": query}
    headers = {
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "Content-Type": "application/json",
    }
    url = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    return []
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return []
    hits = data.get("hits") if isinstance(data, dict) else None
    return hits if isinstance(hits, list) else []


async def fetch_doc(url: str) -> str:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return "Error fetching content. Please check the documentation website directly."

                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')

                final_content = ''

                # Title
                title_elem = soup.find('h1')
                if title_elem:
                    final_content += f"# {title_elem.get_text().strip()}\n\n"

                article = soup.find('article')
                if not article:
                    return "Could not find article content."

                # Get article text for pattern matching
                article_text = article.get_text()

                # Description
                desc_elem = article.find('p')
                if desc_elem:
                    description = desc_elem.get_text().strip()
                    if description:
                        final_content += f"## Description\n{description}\n\n"

                # Parameters Table - Complete rewrite for robustness
                params_table = article.find('table')
                if params_table:
                    final_content += "## Parameters\n"

                    # Collect all parameter data first
                    param_data = {}

                    rows = params_table.find_all('tr')
                    if len(rows) > 1:  # Has header + data rows
                        for row in rows[1:]:  # Skip header row
                            cells = row.find_all(['td', 'th'])
                            if len(cells) >= 2:
                                raw_name = cells[0].get_text(strip=True)
                                raw_desc = cells[1].get_text(strip=True)

                                # Skip empty or invalid entries
                                if not raw_name or not raw_desc or len(raw_desc) < 5:
                                    continue

                                # Clean and extract parameter name
                                param_name = raw_name.strip()

                                # Handle different parameter formats
                                if re.match(r'^\w+$', param_name):
                                    # Simple parameter like "playerid"
                                    clean_name = param_name
                                elif 'const' in param_name.lower() and '[' in param_name:
                                    # Array parameter like "const name[]"
                                    clean_name = param_name
                                elif '[' in param_name and ']' in param_name:
                                    # Array without const like "name[]"
                                    clean_name = param_name
                                else:
                                    # Multi-word parameter, take the last word as the main identifier
                                    words = param_name.split()
                                    clean_name = words[-1] if words else param_name

                                # Store unique parameters only
                                if clean_name not in param_data:
                                    param_data[clean_name] = raw_desc

                    # Output parameters in a consistent order
                    for param_name, param_desc in param_data.items():
                        final_content += f"- **{param_name}**: {param_desc}\n"

                    final_content += '\n'

                # Returns section - Multiple parsing approaches
                returns_found = False

                # Try DOM-based parsing first
                returns_h2 = article.find('h2', string=re.compile(r'Returns', re.IGNORECASE))
                if returns_h2:
                    returns_content = ""
                    current = returns_h2.next_sibling

                    while current and not returns_found:
                        if hasattr(current, 'name'):
                            if current.name == 'h2':  # Next section
                                break
                            elif current.name in ['p', 'div', 'ul']:
                                text = current.get_text(strip=True)
                                if text and len(text) > 3:
                                    returns_content += f"{text}\n\n"
                                    returns_found = True
                        current = current.next_sibling

                    if returns_content.strip():
                        final_content += f"## Returns\n{returns_content.strip()}\n\n"

                # Fallback: enhanced regex pattern matching
                if not returns_found:
                    returns_patterns = [
                        r'## Returns\s*([\s\S]*?)(?=## Examples|## Notes|## Related|$)',
                        r'Returns\s*([\s\S]*?)(?=## Examples|## Notes|## Related|Examples|Notes|Related|$)',
                        r'Returns[:\s]*([\s\S]*?)(?=Examples|Notes|Related Functions|Tags|$)',
                        # More specific patterns for common return value formats
                        r'(?:## )?Returns?[:\s]*\n((?:\s*[-*]?\s*\*?\*?\d+\*?\*?\s*[^\n]*\n?)+)',
                        r'(?:## )?Returns?[:\s]*\n((?:\s*\*?\*?\d+\*?\*?[^\n]*\n?)+)'
                    ]

                    for pattern in returns_patterns:
                        returns_match = re.search(pattern, article_text, re.IGNORECASE | re.MULTILINE)
                        if returns_match and returns_match.group(1):
                            returns_text = returns_match.group(1).strip()
                            if returns_text and len(returns_text) > 10:
                                # Clean up the returns text
                                returns_text = re.sub(r'\s+', ' ', returns_text)
                                # Handle common return value formatting
                                returns_text = re.sub(r'(\d+)\s*-\s*', r'**\1** - ', returns_text)
                                returns_text = re.sub(r'\*\*(\d+)\*\*', r'**\1**', returns_text)
                                final_content += f"## Returns\n{returns_text}\n\n"
                                returns_found = True
                                break

                # Code blocks and examples
                code_blocks = article.find_all('pre')
                examples_added = False
                seen_code_blocks = set()

                for block in code_blocks:
                    # Get the code content
                    code_elem = block.find('code')
                    if code_elem:
                        code = code_elem.get_text()
                    else:
                        code = block.get_text()

                    code = html.unescape(code.strip())

                    # Remove line numbers if present
                    if re.match(r'^\s*\d+\s+', code, re.MULTILINE):
                        lines = code.split('\n')
                        cleaned_lines = [re.sub(r'^\s*\d+\s+', '', line) for line in lines]
                        code = '\n'.join(cleaned_lines)

                    # Skip if we've already seen this exact code block
                    code_hash = hash(code.strip())
                    if code_hash in seen_code_blocks or not code.strip():
                        continue
                    seen_code_blocks.add(code_hash)

                    if not examples_added:
                        final_content += "## Examples\n"
                        examples_added = True

                    # Determine language
                    language = 'pawn'
                    if code_elem and code_elem.get('class'):
                        classes = code_elem.get('class', [])
                        if 'language-c' in classes:
                            language = 'c'
                        elif 'language-cpp' in classes:
                            language = 'cpp'

                    final_content += f"```{language}\n{code}\n```\n\n"

                # Notes section with tips and warnings
                notes_pattern = re.search(
                    r'Notes[,\s]*([\s\S]*?)(?=Related Functions|Related Callbacks|Tags|$)',
                    article_text,
                    re.IGNORECASE
                )
                if notes_pattern and notes_pattern.group(1):
                    notes_text = notes_pattern.group(1).strip()
                    # Clean up related callbacks from notes
                    notes_text = re.sub(r'Related Callbacks.*$', '', notes_text, flags=re.IGNORECASE | re.DOTALL).strip()

                    tip_match = re.search(r'Tip:?\s*([\s\S]*?)(?=Warning:|Related Callbacks|$)', notes_text, re.IGNORECASE)
                    warning_match = re.search(r'Warning:?\s*([\s\S]*?)(?=Related Callbacks|$)', notes_text, re.IGNORECASE)

                    final_content += "## Notes\n"

                    if tip_match and tip_match.group(1).strip():
                        tip_text = tip_match.group(1).strip()
                        tip_text = re.sub(r'Related Callbacks.*$', '', tip_text, flags=re.IGNORECASE | re.DOTALL).strip()
                        final_content += f"**💡 Tip:** {tip_text}\n\n"

                    if warning_match and warning_match.group(1).strip():
                        warning_text = warning_match.group(1).strip()
                        warning_text = re.sub(r'^warning\s*you', 'You', warning_text, flags=re.IGNORECASE)
                        warning_text = re.sub(r'Related Callbacks.*$', '', warning_text, flags=re.IGNORECASE | re.DOTALL).strip()
                        final_content += f"**⚠️ Warning:** {warning_text}\n\n"

                    # If no specific tip/warning found, add general notes
                    if (not tip_match or not tip_match.group(1).strip()) and (not warning_match or not warning_match.group(1).strip()):
                        cleaned_text = re.sub(r'warning(?!\s*:)', '', notes_text, flags=re.IGNORECASE)
                        cleaned_text = re.sub(r'Related Callbacks.*$', '', cleaned_text, flags=re.IGNORECASE | re.DOTALL).strip()
                        if cleaned_text:
                            final_content += f"{cleaned_text}\n\n"

                # Related Callbacks
                related_callbacks_pattern = re.search(
                    r'Related Callbacks[,\s]*([\s\S]*?)(?=Tags|$)',
                    article_text,
                    re.IGNORECASE
                )
                if related_callbacks_pattern and related_callbacks_pattern.group(1):
                    final_content += f"## Related Callbacks\n{related_callbacks_pattern.group(1).strip()}\n\n"

                # Related Functions - Multiple parsing approaches
                related_functions_found = False

                # Try DOM-based parsing first
                related_functions_section = article.find('h2', string=re.compile(r'Related Functions', re.IGNORECASE))
                if related_functions_section:
                    # Find the list after the Related Functions header
                    current = related_functions_section.next_sibling
                    while current:
                        if hasattr(current, 'name'):
                            if current.name == 'h2':  # Next section
                                break
                            elif current.name == 'ul':  # Found the list
                                final_content += "## Related Functions\n"
                                for li in current.find_all('li'):
                                    link = li.find('a')
                                    if link:
                                        func_name = link.get_text(strip=True)
                                        func_url = link.get('href', '')

                                        if func_name and not any(x in func_name.lower() for x in ['previous', 'next', 'edit']):
                                            if func_url and not func_url.startswith('http'):
                                                func_url = f"https://open.mp{func_url}"
                                            if func_url:
                                                final_content += f"- [{func_name}]({func_url})\n"
                                            else:
                                                final_content += f"- {func_name}\n"
                                            related_functions_found = True
                                    else:
                                        # No link, just text
                                        text = li.get_text(strip=True)
                                        if text and not any(x in text.lower() for x in ['previous', 'next', 'edit']):
                                            final_content += f"- {text}\n"
                                            related_functions_found = True
                                break
                        current = current.next_sibling

                # Fallback: try to find all links in the Related Functions area
                if not related_functions_found:
                    related_pattern = re.search(
                        r'Related Functions\s*([\s\S]*?)(?=Related Callbacks|Tags|$)',
                        article_text,
                        re.IGNORECASE
                    )
                    if related_pattern:
                        # Find all links in the article after "Related Functions"
                        all_links = article.find_all('a')

                        for link in all_links:
                            link_text = link.get_text(strip=True)
                            link_url = link.get('href', '')

                            # Check if this link appears to be a function name
                            if (link_text and
                                not any(x in link_text.lower() for x in ['previous', 'next', 'edit', 'home']) and
                                re.match(r'^[A-Z][a-zA-Z0-9_]*$', link_text.replace(' ', ''))):

                                if not related_functions_found:
                                    final_content += "## Related Functions\n"
                                    related_functions_found = True

                                if link_url and not link_url.startswith('http'):
                                    link_url = f"https://open.mp{link_url}"
                                if link_url:
                                    final_content += f"- [{link_text}]({link_url})\n"
                                else:
                                    final_content += f"- {link_text}\n"

                if related_functions_found:
                    final_content += '\n'

                # Tags
                tags_elem = article.find(string=re.compile(r'Tags', re.IGNORECASE))
                if tags_elem:
                    tags_section = tags_elem.find_parent().find_next_sibling('ul')
                    if tags_section:
                        tags_content = ""
                        for li in tags_section.find_all('li'):
                            tag = li.get_text().strip()
                            tag = re.sub(r'Edit this page.*$', '', tag, flags=re.IGNORECASE).strip()
                            if tag and not re.match(r'^\s*$', tag):
                                tags_content += f"- {tag}\n"

                        if tags_content:
                            final_content += f"## Tags\n{tags_content}\n"

                # Clean up the final content - Enhanced cleanup
                final_content = re.sub(r'\n{3,}', '\n\n', final_content)
                final_content = re.sub(r'\s+\n', '\n', final_content)
                final_content = re.sub(r'warning(?!\s*:)', '', final_content, flags=re.IGNORECASE)
                final_content = re.sub(r'(\*\*⚠️ Warning:\*\* [^\n]+)\n\n\1', r'\1', final_content, flags=re.IGNORECASE)
                final_content = re.sub(r'(\*\*💡 Tip:\*\* [^\n]+)\n\n\1', r'\1', final_content, flags=re.IGNORECASE)
                final_content = re.sub(r'Edit this page.*$', '', final_content, flags=re.MULTILINE | re.IGNORECASE)
                final_content = re.sub(r'## Tags\n\s*\n', '', final_content)
                final_content = re.sub(r'client\.\s*Tags:', 'Tags:', final_content, flags=re.IGNORECASE)

                # Remove any comma-separated formatting artifacts
                final_content = re.sub(r',\s*\n', '\n', final_content)
                final_content = re.sub(r'([a-zA-Z]),([a-zA-Z])', r'\1 \2', final_content)

                # Fix broken markdown formatting
                final_content = re.sub(r'(\w+),\s*\n', r'\1\n', final_content)

                final_content = final_content.strip()

                return final_content

    except Exception:
        log.exception("Failed to parse open.mp documentation page %s", url)
        return "Error fetching content. Please check the documentation website directly."
