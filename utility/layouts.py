"""Shared Components V2 layouts for every Utility feature."""

from __future__ import annotations

import discord
from discord import ui


NO_PINGS = discord.AllowedMentions.none()
MAX_CARD_TEXT = 3800
PAGE_TIMEOUT = 120


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _link_title(title: str, url: str) -> str:
    return "[{}]({})".format(title.replace("[", "\\[").replace("]", "\\]"), url)


def _field_text(embed: discord.Embed) -> str:
    blocks: list[str] = []
    inline_run: list[str] = []
    for field in embed.fields:
        if field.inline:
            inline_run.append(f"**{field.name}** {field.value}")
            continue
        if inline_run:
            blocks.append("  ·  ".join(inline_run))
            inline_run = []
        blocks.append(f"**{field.name}**\n{field.value}")
    if inline_run:
        blocks.append("  ·  ".join(inline_run))
    return "\n\n".join(blocks)


def _accent(color: discord.Color | int | None) -> discord.Color | int | None:
    return color if getattr(color, "value", color) else None


def embed_items(embed: discord.Embed) -> list[ui.Item]:
    """Lay out an embed's content as display components, within Discord's text limit."""
    title = embed.title or ""
    if title and embed.url:
        title = _link_title(title, embed.url)
    head = "\n".join(part for part in (f"## {title}" if title else "", embed.description or "") if part)
    fields = _field_text(embed)
    footer = f"-# {embed.footer.text}" if embed.footer.text else ""

    budget = MAX_CARD_TEXT - len(footer)
    fields = _clip(fields, budget - min(len(head), budget // 2))
    head = _clip(head, budget - len(fields))
    if not head:
        head, fields = fields or "​", ""

    thumbnail = embed.thumbnail.url
    items: list[ui.Item] = [
        ui.Section(ui.TextDisplay(head), accessory=ui.Thumbnail(thumbnail)) if thumbnail else ui.TextDisplay(head)
    ]
    if fields:
        items.extend([ui.Separator(), ui.TextDisplay(fields)])
    if footer:
        items.append(ui.TextDisplay(footer))
    return items


def card(embed: discord.Embed) -> ui.LayoutView:
    """A static message with no controls."""
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*embed_items(embed), accent_color=_accent(embed.color)))
    return view


def text_card(text: str, color: discord.Color | int | None = discord.Color.blurple()) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.TextDisplay(_clip(text, MAX_CARD_TEXT)), accent_color=_accent(color)))
    return view


class Pager(ui.LayoutView):
    """Button pages over a list of embeds, usable only by the person who asked.

    ``link`` adds a link button to the page controls.
    """

    def __init__(
        self,
        owner_id: int,
        pages: list[discord.Embed],
        *,
        link: tuple[str, str] | None = None,
        denied: str = "Only the person who ran this command can use these buttons.",
        timeout: float = PAGE_TIMEOUT,
    ):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.pages = pages
        self.link = link
        self.denied = denied
        self.message: discord.Message | None = None
        self.page = 0
        self._render()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(self.denied, ephemeral=True)
        return False

    def _button(self, emoji: str, target: int, disabled: bool) -> ui.Button:
        button = ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary, disabled=disabled)

        async def go(interaction: discord.Interaction):
            self.page = target
            self._render()
            await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)

        button.callback = go
        return button

    def _render(self):
        self.clear_items()
        embed = self.pages[self.page]
        last = len(self.pages) - 1
        items = embed_items(embed)
        controls: list[ui.Item] = []
        if last > 0:
            controls.append(self._button("◀️", self.page - 1, self.page <= 0))
            controls.append(self._button("▶️", self.page + 1, self.page >= last))
        if self.link:
            label, url = self.link
            controls.append(ui.Button(label=label, url=url, style=discord.ButtonStyle.link))
        if controls:
            items.append(ui.ActionRow(*controls))
        self.add_item(ui.Container(*items, accent_color=_accent(embed.color)))

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, ui.Button) and item.url is None:
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                pass


async def send_paged(ctx, pages: list[discord.Embed], **kwargs) -> None:
    """Send one embed as a static card, or several behind page buttons."""
    if len(pages) == 1 and "link" not in kwargs:
        await ctx.send(view=card(pages[0]), allowed_mentions=NO_PINGS)
        return
    view = Pager(ctx.author.id, pages, **kwargs)
    view.message = await ctx.send(view=view, allowed_mentions=NO_PINGS)
