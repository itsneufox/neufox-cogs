"""Read-only Components V2 messages shared by every Media feature."""

from __future__ import annotations

import discord

NO_PINGS = discord.AllowedMentions.none()
MAX_LAYOUT_TEXT = 3800
DEFAULT_COLOR = discord.Color.blurple()


def config_field(config, name: str, *, set_hook=None) -> dict:
    """Panel get/set callables for one guild value in one feature's Config."""

    async def get(guild):
        return await config.guild(guild).get_attr(name)()

    async def set_(guild, value):
        await config.guild(guild).get_attr(name).set(value)
        if set_hook is not None:
            await set_hook(guild)

    return {"get": get, "set": set_}


def channel_listed(channel, listed) -> bool:
    """True when the channel, its parent (for threads) or its category is in ``listed``."""
    wanted = {int(item) for item in listed or ()}
    ids = {getattr(channel, "id", None), getattr(channel, "parent_id", None), getattr(channel, "category_id", None)}
    parent = getattr(channel, "parent", None)
    if parent is not None:
        ids.add(getattr(parent, "category_id", None))
    return bool(wanted & {item for item in ids if isinstance(item, int)})


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
