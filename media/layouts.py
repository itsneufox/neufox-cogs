"""Read-only Components V2 messages shared by every Media feature."""

from __future__ import annotations

import discord

NO_PINGS = discord.AllowedMentions.none()
MAX_LAYOUT_TEXT = 3800
DEFAULT_COLOR = discord.Color.blurple()


def layout(*blocks: str, accent: discord.Color = DEFAULT_COLOR) -> discord.ui.LayoutView:
    """Build a read-only message, trimmed to Discord's total text limit."""
    view = discord.ui.LayoutView(timeout=None)
    items: list[discord.ui.Item] = []
    remaining = MAX_LAYOUT_TEXT
    for block in blocks:
        text = block[:remaining]
        if not text:
            break
        remaining -= len(text)
        if items:
            items.append(discord.ui.Separator())
        items.append(discord.ui.TextDisplay(text))
    view.add_item(discord.ui.Container(*items, accent_color=accent))
    return view


async def reply(ctx, *blocks: str, accent: discord.Color = DEFAULT_COLOR, ephemeral: bool = False):
    """Send a layout message. ``ephemeral`` only applies to slash invocations."""
    return await ctx.send(view=layout(*blocks, accent=accent), allowed_mentions=NO_PINGS, ephemeral=ephemeral)
