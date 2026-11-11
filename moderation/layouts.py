"""Layout and slash helpers. Each cog keeps its own copy so it can be installed alone."""

from __future__ import annotations

import discord
from discord import ui
from redbot.core import commands


NO_PINGS = discord.AllowedMentions.none()
MAX_TEXT_LENGTH = 3800


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


def _accent(color: discord.Color | int | None) -> discord.Color | int | None:
    value = getattr(color, "value", color)
    return color if value else None


def _field_blocks(embed: discord.Embed) -> list[str]:
    blocks: list[str] = []
    last_was_short = False
    for field in embed.fields:
        short = bool(field.inline) and "\n" not in field.value
        if short and last_was_short:
            blocks[-1] += f"\n**{field.name}**: {field.value}"
        elif short:
            blocks.append(f"**{field.name}**: {field.value}")
        else:
            blocks.append(f"**{field.name}**\n{field.value}")
        last_was_short = short
    return blocks


def embed_container(embed: discord.Embed, *extras: ui.Item) -> ui.Container:
    """Show an Embed's content as a Container, keeping every field's text."""
    head_parts = []
    if embed.author.name:
        head_parts.append(f"-# {embed.author.name}")
    if embed.title:
        head_parts.append(f"## {embed.title}")
    if embed.description:
        head_parts.append(embed.description)
    head = "\n".join(head_parts)
    footer = f"-# {embed.footer.text}" if embed.footer.text else ""
    body = "\n\n".join(_field_blocks(embed))

    footer = _clip(footer, 300)
    head = _clip(head, MAX_TEXT_LENGTH - len(footer))
    body = _clip(body, MAX_TEXT_LENGTH - len(footer) - len(head))

    items: list[ui.Item] = []
    if head and embed.thumbnail.url:
        items.append(ui.Section(ui.TextDisplay(head), accessory=ui.Thumbnail(embed.thumbnail.url)))
    elif head:
        items.append(ui.TextDisplay(head))
    if body:
        if items:
            items.append(ui.Separator())
        items.append(ui.TextDisplay(body))
    if footer:
        items.append(ui.TextDisplay(footer))
    items.extend(extras)
    return ui.Container(*items, accent_color=_accent(embed.color))


def embed_view(embed: discord.Embed) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(embed_container(embed))
    return view


def text_view(text: str, color: discord.Color | int | None = None) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.TextDisplay(_clip(text, MAX_TEXT_LENGTH)), accent_color=_accent(color)))
    return view


async def send_embed(target, embed: discord.Embed, **kwargs):
    kwargs.setdefault("allowed_mentions", NO_PINGS)
    return await target.send(view=embed_view(embed), **kwargs)


async def send_text(target, text: str, color: discord.Color | int | None = None, **kwargs):
    kwargs.setdefault("allowed_mentions", NO_PINGS)
    return await target.send(view=text_view(text, color), **kwargs)


async def run_slash(
    bot,
    interaction: discord.Interaction,
    command: commands.Command,
    *args,
    ephemeral: bool = False,
    **kwargs,
) -> None:
    """Run a prefix command for a slash interaction, applying all of its permission checks."""
    ctx = await bot.get_context(interaction)
    try:
        allowed = await command.can_run(ctx, check_all_parents=True)
    except commands.CommandError:
        allowed = False
    if not allowed:
        await interaction.response.send_message("You can't use this command.", ephemeral=True)
        return
    await ctx.defer(ephemeral=ephemeral)
    await ctx.invoke(command, *args, **kwargs)
