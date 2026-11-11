from __future__ import annotations

import typing

import discord
from discord import ui


# Everything here is for short-lived replies. Messages stored or edited later, and
# messages that end up in ticket transcripts, stay classic embeds on purpose.
NO_PINGS = discord.AllowedMentions.none()
MAX_TEXT_LENGTH = 3800

Colour = typing.Optional[typing.Union[discord.Colour, int]]


def info_layout(
    title: str,
    description: str = "",
    sections: typing.Sequence[typing.Tuple[str, str]] = (),
    color: Colour = None,
) -> ui.LayoutView:
    """A read-only reply: a heading, optional intro, and titled blocks of text."""
    view = ui.LayoutView(timeout=None)
    heading = f"## {title}"
    if description:
        heading += f"\n{description}"
    items: typing.List[ui.Item] = [ui.TextDisplay(heading)]
    if sections:
        items.append(ui.Separator())
        body = "\n\n".join(f"**{name}**\n{value}" for name, value in sections)
        items.append(ui.TextDisplay(body[:MAX_TEXT_LENGTH]))
    view.add_item(ui.Container(*items, accent_color=color))
    return view


class ConfirmLayout(ui.LayoutView):
    def __init__(
        self,
        author_id: int,
        title: str,
        description: typing.Optional[str] = None,
        color: Colour = None,
        requested_by: typing.Optional[str] = None,
    ):
        super().__init__(timeout=60)
        self.result: typing.Optional[bool] = None
        self.author_id = author_id
        self.yes_button = ui.Button(label="Yes", style=discord.ButtonStyle.success)
        self.no_button = ui.Button(label="No", style=discord.ButtonStyle.danger)
        self.yes_button.callback = self._on_yes
        self.no_button.callback = self._on_no

        body = f"### {title}"
        if description:
            body += f"\n{description}"
        if requested_by:
            body += f"\n-# {discord.utils.escape_markdown(requested_by)}"
        self.add_item(
            ui.Container(ui.TextDisplay(body[:MAX_TEXT_LENGTH]), ui.ActionRow(self.yes_button, self.no_button), accent_color=color)
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
            return False
        return True

    async def _finish(self, interaction: discord.Interaction, result: bool) -> None:
        self.result = result
        self.yes_button.disabled = self.no_button.disabled = True
        await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)
        self.stop()

    async def _on_yes(self, interaction: discord.Interaction) -> None:
        await self._finish(interaction, True)

    async def _on_no(self, interaction: discord.Interaction) -> None:
        await self._finish(interaction, False)


class PagesLayout(ui.LayoutView):
    """Pages of text. Only the person who ran the command can turn them."""

    def __init__(self, owner_id: int, title: str, pages: typing.Sequence[str], color: Colour = None):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.title = title
        self.pages = list(pages)
        self.color = color
        self.current = 0
        self.render()

    def render(self) -> None:
        self.clear_items()
        heading = f"## {self.title}"
        items: typing.List[ui.Item] = [ui.TextDisplay(heading), ui.Separator(), ui.TextDisplay(self.pages[self.current][:MAX_TEXT_LENGTH])]
        if len(self.pages) > 1:
            items.append(ui.TextDisplay(f"-# Page {self.current + 1}/{len(self.pages)}"))
            previous_page = ui.Button(emoji="◀", style=discord.ButtonStyle.secondary)
            next_page = ui.Button(emoji="▶", style=discord.ButtonStyle.secondary)
            previous_page.callback = self._on_previous
            next_page.callback = self._on_next
            items.append(ui.ActionRow(previous_page, next_page))
        self.add_item(ui.Container(*items, accent_color=self.color))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return False
        return True

    async def _turn(self, interaction: discord.Interaction, step: int) -> None:
        self.current = (self.current + step) % len(self.pages)
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)

    async def _on_previous(self, interaction: discord.Interaction) -> None:
        await self._turn(interaction, -1)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await self._turn(interaction, 1)
