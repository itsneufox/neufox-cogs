from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import ui

from .common import BOARDS


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
    view.add_item(ui.Container(*embed_items(embed), accent_color=embed.color))
    return view


PageLoader = Callable[[str], Awaitable[list[discord.Embed]]]


class LeaderboardView(ui.LayoutView):
    """One leaderboard with a type picker and page buttons, controlled by the person who asked."""

    def __init__(self, owner_id: int, board: str, pages: list[discord.Embed], loader: PageLoader):
        super().__init__(timeout=PAGE_TIMEOUT)
        self.owner_id = owner_id
        self.board = board
        self.pages = pages or [discord.Embed(description="Nothing to show yet.")]
        self.loader = loader
        self.page = 0
        self.message: discord.Message | None = None
        self._render()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Run the leaderboard command yourself to browse it.", ephemeral=True)
        return False

    def _picker(self) -> ui.Select:
        select = ui.Select(
            placeholder="Leaderboard type",
            options=[
                discord.SelectOption(label=label, value=key, description=help_text, default=key == self.board)
                for key, (label, help_text) in BOARDS.items()
            ],
        )

        async def pick(interaction: discord.Interaction):
            self.board = select.values[0]
            self.pages = await self.loader(self.board) or [discord.Embed(description="Nothing to show yet.")]
            self.page = 0
            self._render()
            await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)

        select.callback = pick
        return select

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
        items.append(ui.ActionRow(self._picker()))
        if last > 0:
            items.append(
                ui.ActionRow(
                    self._button("⏮️", 0, self.page <= 0),
                    self._button("◀️", max(0, self.page - 1), self.page <= 0),
                    self._button("▶️", min(last, self.page + 1), self.page >= last),
                    self._button("⏭️", last, self.page >= last),
                )
            )
        self.add_item(ui.Container(*items, accent_color=embed.color))

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (ui.Button, ui.Select)):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                pass
