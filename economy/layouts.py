"""Layout and slash helpers for the Economy cog."""

from __future__ import annotations

import discord
from discord import ui
from redbot.core import commands


NO_PINGS = discord.AllowedMentions.none()
MAX_TEXT_LENGTH = 3800
PAGE_TIMEOUT = 120


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
    if embed.image.url:
        items.append(ui.MediaGallery(discord.MediaGalleryItem(embed.image.url)))
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


class EmbedPages(ui.LayoutView):
    """Button paging over already-built embeds, usable only by the person who asked."""

    def __init__(self, owner_id: int, pages: list[discord.Embed], *, timeout: float = PAGE_TIMEOUT):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.pages = pages
        self.page = 0
        self.message: discord.Message | None = None
        self.render()

    def _button(self, label: str, step: int, disabled: bool) -> ui.Button:
        button = ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=disabled)

        async def callback(interaction: discord.Interaction):
            self.page = max(0, min(len(self.pages) - 1, self.page + step))
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)

        button.callback = callback
        return button

    def render(self, *, closed: bool = False):
        self.clear_items()
        controls = ui.ActionRow(
            self._button("Previous", -1, closed or self.page == 0),
            self._button("Next", 1, closed or self.page >= len(self.pages) - 1),
        )
        self.add_item(embed_container(self.pages[self.page], controls))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Only the person who opened this can change pages.", ephemeral=True)
        return False

    async def on_timeout(self):
        self.render(closed=True)
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                pass


async def send_pages(ctx: commands.Context, pages: list[discord.Embed]):
    if len(pages) == 1:
        return await send_embed(ctx, pages[0])
    view = EmbedPages(ctx.author.id, pages)
    view.message = await ctx.send(view=view, allowed_mentions=NO_PINGS)
    return view.message


async def use_real_prefix(bot, ctx: commands.Context) -> None:
    """Make help text inside slash replies name the prefix commands that actually exist."""
    prefixes = await bot.get_valid_prefixes(ctx.guild)
    usable = [prefix for prefix in prefixes if not prefix.startswith("<@")]
    if usable:
        ctx.prefix = usable[0]


async def run_slash(
    bot,
    interaction: discord.Interaction,
    command: commands.Command,
    *args,
    ephemeral: bool = False,
    **kwargs,
) -> None:
    """Run a prefix command for one slash interaction, with its checks and cooldown.

    The interaction is acknowledged before the command runs, so an expired interaction
    stops here and nothing is charged or paid.
    """
    ctx = await bot.get_context(interaction)
    ctx.command = command
    try:
        allowed = await command.can_run(ctx, check_all_parents=True)
    except commands.CommandError:
        allowed = False
    if not allowed:
        await interaction.response.send_message("You can't use this command.", ephemeral=True)
        return
    try:
        command._prepare_cooldowns(ctx)
    except commands.CommandOnCooldown as error:
        await interaction.response.send_message(
            f"Slow down. Try again in {error.retry_after:.1f} seconds.",
            ephemeral=True,
        )
        return
    await use_real_prefix(bot, ctx)
    await ctx.defer(ephemeral=ephemeral)
    await ctx.invoke(command, *args, **kwargs)
