import json
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify
import discord
import aiohttp
import asyncio
from typing import Optional, List, Dict, Any
import re
from bs4 import BeautifulSoup
import html
import time

class WikiCog(commands.Cog):

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)

        self.algolia_app_id = "AOKXGK39Z7"
        self.algolia_api_key = "54204f37e5c8fc2871052d595ee0505e"
        self.algolia_index_name = "open"

        # Cache for search results (similar to base.js)
        self.search_results_cache = {}
        self.cache_expiry = 10 * 60  # 10 minutes

    def truncate_text(self, text: str, max_length: int) -> str:
        if len(text) <= max_length:
            return text
        return text[:text.rfind(' ', max_length)] + ' ...'

    def format_description(self, content: str) -> str:
        return content.replace('<mark>', '**').replace('</mark>', '**')

    def decode_html_entities(self, text: str) -> str:
        return html.unescape(text)

    async def parse_openmp_doc_content(self, url: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
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

                    # Check if this is a function or callback
                    is_function = '/functions/' in url
                    is_callback = '/callbacks/' in url

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

                        code = self.decode_html_entities(code.strip())

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
                            in_related_section = False

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

        except Exception as e:
            return f"Error fetching content: {str(e)}"

    async def search_documentation(self, query: str, language: str = "en") -> List[Dict[str, Any]]:
        try:
            search_data = {
                "params": f"hitsPerPage=20&filters=language:{language}",
                "query": query
            }
            
            headers = {
                'X-Algolia-API-Key': self.algolia_api_key,
                'X-Algolia-Application-Id': self.algolia_app_id,
                'Content-Type': 'application/json'
            }
            
            url = f"https://{self.algolia_app_id}-dsn.algolia.net/1/indexes/{self.algolia_index_name}/query"
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=search_data, headers=headers) as response:
                    if response.status != 200:
                        return []
                    
                    data = await response.json()
                    return data.get('hits', [])
                    
        except asyncio.TimeoutError:
            return []
        except aiohttp.ClientError:
            return []
        except Exception as e:
            return []

    @commands.command()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def wiki(self, ctx: commands.Context, *, search_term: str):
        if len(search_term) < 3:
            embed = discord.Embed(
                title="Search Error",
                description="Query must be 3 characters or more.",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return

        async with ctx.typing():
            language = "en"
            results = await self.search_documentation(search_term, language)

        if not results:
            embed = discord.Embed(
                title=f"No results: {search_term}",
                description="There were no results for that query.",
                color=0x0099ff
            )
            await ctx.send(embed=embed)
            return

        # Filter results similar to base.js
        filtered_results = []
        seen_urls = set()

        for hit in results:
            url = hit.get('url_without_anchor', '')
            if url in seen_urls:
                continue

            seen_urls.add(url)
            url_parts = url.rstrip('/').split('/')

            # Filter out blog posts, tags, and generic listing pages
            if (len(url_parts) < 2 or
                url_parts[-2] == 'blog' or
                'tags' in url_parts or
                url_parts[-1].startswith('omp-') or
                url_parts[-1] in ['functions', 'callbacks', 'natives', 'constants', 'libraries']):
                continue

            # Only include pages that are actual documentation items
            allowed_types = ['functions', 'callbacks', 'natives', 'constants', 'libraries']
            if not any(t in url_parts for t in allowed_types):
                continue

            filtered_results.append(hit)
            if len(filtered_results) >= 10:
                break

        if not filtered_results:
            embed = discord.Embed(
                title=f"No results: {search_term}",
                description="There were no results for that query.",
                color=0x0099ff
            )
            await ctx.send(embed=embed)
            return

        # Create search results cache entry
        search_id = str(int(time.time()))
        self.search_results_cache[search_id] = {
            'hits': filtered_results,
            'user_id': ctx.author.id,
            'timestamp': time.time()
        }

        # Build description for search results
        description = ""
        for i, hit in enumerate(filtered_results[:5], 1):
            url_parts = hit['url_without_anchor'].rstrip('/').split('/')
            name = url_parts[-1]

            # Determine type
            type_name = ""
            if 'functions' in url_parts:
                type_name = "Function"
            elif 'callbacks' in url_parts:
                type_name = "Callback"
            elif 'natives' in url_parts:
                type_name = "Native"
            elif 'constants' in url_parts:
                type_name = "Constant"
            elif 'libraries' in url_parts:
                type_name = "Library"

            # Get description
            desc = "(No description found)"
            if hit.get('content'):
                desc = self.format_description(hit['content'])
            elif hit.get('hierarchy', {}).get('lvl1'):
                desc = hit['hierarchy']['lvl1']
            elif hit.get('description'):
                desc = hit['description']
            elif hit.get('text'):
                desc = hit['text']
            elif hit.get('snippet'):
                desc = hit['snippet']

            desc = re.sub(r'<[^>]*>', '', desc)
            if desc in ['undefined', 'null']:
                desc = "(No description found)"

            description += f"**{i}.** [**{name}**]({hit['url_without_anchor']})"
            if type_name:
                description += f" `{type_name}`"
            description += "\n"
            description += f"{self.truncate_text(desc, 120)}\n\n"

        # Create embed with button navigation
        embed = discord.Embed(
            title=f'Documentation Search Results: "{search_term}"',
            description=description,
            color=0x0099ff
        )
        embed.set_footer(text="Click a button number to view the full documentation")

        # Create buttons for results (up to 5)
        view = WikiSearchView(self, search_id, min(len(filtered_results), 5))
        await ctx.send(embed=embed, view=view)


    @commands.command()
    @commands.is_owner()
    async def wikisetup(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Wiki Cog Setup",
            description="Wiki cog is ready to use! No additional configuration needed.",
            color=0x00ff00
        )
        embed.add_field(
            name="Current Configuration",
            value=f"• **App ID**: {self.algolia_app_id}\n"
                  f"• **Index Name**: {self.algolia_index_name}\n"
                  f"• **API Key**: Configured",
            inline=False
        )
        embed.add_field(
            name="Usage",
            value="Use `[p]wiki <search term>` to search the open.mp documentation.",
            inline=False
        )
        await ctx.send(embed=embed)

    def cleanup_cache(self):
        """Clean up expired cache entries"""
        current_time = time.time()
        expired_keys = [
            key for key, value in self.search_results_cache.items()
            if current_time - value['timestamp'] > self.cache_expiry
        ]
        for key in expired_keys:
            del self.search_results_cache[key]

    async def handle_button_interaction(self, interaction: discord.Interaction, search_id: str, index: int):
        """Handle button click for search results"""
        # Check if cache entry exists
        if search_id not in self.search_results_cache:
            await interaction.response.send_message(
                "This search result has expired. Please run the search command again.",
                ephemeral=True
            )
            return

        search_data = self.search_results_cache[search_id]

        # Check if the user who clicked is the same as who issued the command
        if interaction.user.id != search_data['user_id']:
            await interaction.response.send_message(
                "Only the person who ran the search command can use these buttons.",
                ephemeral=True
            )
            return

        hits = search_data['hits']
        if index >= len(hits):
            await interaction.response.send_message(
                "Invalid result selection. Please try again.",
                ephemeral=True
            )
            return

        hit = hits[index]
        url_parts = hit['url_without_anchor'].rstrip('/').split('/')
        title = url_parts[-1]

        await interaction.response.defer()

        try:
            content = await self.parse_openmp_doc_content(hit['url_without_anchor'])

            # Split content into chunks for multiple embeds if needed
            content_chunks = []
            remaining_content = content

            while remaining_content:
                chunk_size = min(4000, len(remaining_content))

                if chunk_size < len(remaining_content):
                    # Find a good break point
                    break_point = remaining_content.rfind('\n\n', 0, chunk_size)
                    if break_point > chunk_size // 2:
                        chunk_size = break_point + 2

                content_chunks.append(remaining_content[:chunk_size])
                remaining_content = remaining_content[chunk_size:]

            embeds = []
            for i, chunk in enumerate(content_chunks):
                embed = discord.Embed(color=0x0099ff)

                if i == 0:
                    embed.title = title
                    embed.url = hit['url_without_anchor']

                if i == len(content_chunks) - 1:
                    embed.set_footer(text="Documentation from open.mp")

                embed.description = chunk
                embeds.append(embed)

            # Create view with link button
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="View Full Documentation",
                url=hit['url_without_anchor'],
                style=discord.ButtonStyle.link
            ))

            await interaction.edit_original_response(embeds=embeds, view=view)

        except Exception as e:
            await interaction.edit_original_response(
                content=f"An error occurred while retrieving the documentation. Please visit the website directly: {hit['url_without_anchor']}",
                embeds=[],
                view=None
            )


class WikiSearchView(discord.ui.View):
    def __init__(self, cog: WikiCog, search_id: str, num_results: int):
        super().__init__(timeout=600.0)  # 10 minute timeout
        self.cog = cog
        self.search_id = search_id

        # Add numbered buttons for each result
        for i in range(num_results):
            button = discord.ui.Button(
                label=str(i + 1),
                style=discord.ButtonStyle.primary,
                custom_id=f"wiki_{search_id}_{i}"
            )
            button.callback = self.create_callback(i)
            self.add_item(button)

    def create_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            await self.cog.handle_button_interaction(interaction, self.search_id, index)
        return callback

    async def on_timeout(self):
        # Clean up the cache entry when view times out
        if self.search_id in self.cog.search_results_cache:
            del self.cog.search_results_cache[self.search_id]


async def setup(bot):
    cog = WikiCog(bot)
    await bot.add_cog(cog)

    # Set up periodic cache cleanup
    async def cleanup_task():
        while True:
            await asyncio.sleep(3600)  # Run every hour
            cog.cleanup_cache()

    bot.loop.create_task(cleanup_task())
