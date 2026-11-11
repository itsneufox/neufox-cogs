"""Slash command help built from the bot's registered application commands."""

from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import app_commands, ui


HELP_TIMEOUT = 600
MAX_PAGE_CHARS = 3800
MAX_CATEGORIES = 25
NO_PINGS = discord.AllowedMentions.none()
ACCENT = discord.Color.blurple()
OTHER = "Other"
OVERVIEW_VALUE = "__overview__"  # Discord rejects empty select option values


@dataclass(frozen=True)
class HelpEntry:
    name: str
    description: str

    def line(self) -> str:
        return f"`/{self.name}` - {self.description}" if self.description else f"`/{self.name}`"


@dataclass(frozen=True)
class HelpCategory:
    name: str
    description: str
    entries: tuple[HelpEntry, ...]


def _cog_name(command: app_commands.Command | app_commands.Group) -> str:
    binding = getattr(command, "binding", None)
    if binding is None and isinstance(command, app_commands.Group):
        # Groups defined on a cog bind their children rather than themselves.
        for child in command.walk_commands():
            binding = getattr(child, "binding", None)
            if binding is not None:
                break
    name = getattr(binding, "qualified_name", None)
    return name or OTHER


def _can_see(
    command: app_commands.Command | app_commands.Group,
    permissions: discord.Permissions | None,
    in_guild: bool,
) -> bool:
    if not in_guild and command.guild_only:
        return False
    required = command.default_permissions
    if required is None or permissions is None:
        return True
    return permissions.administrator or required.is_subset(permissions)


def _leaves(command: app_commands.Command | app_commands.Group) -> list[app_commands.Command]:
    if isinstance(command, app_commands.Group):
        return [child for child in command.walk_commands() if isinstance(child, app_commands.Command)]
    return [command]


def collect_help(
    commands: list[app_commands.Command | app_commands.Group],
    cog_descriptions: dict[str, str],
    *,
    permissions: discord.Permissions | None,
    in_guild: bool,
) -> list[HelpCategory]:
    """Group visible slash commands by cog, sorted by cog then command name."""
    grouped: dict[str, list[HelpEntry]] = {}
    for command in commands:
        if not isinstance(command, (app_commands.Command, app_commands.Group)):
            continue  # context menus have no slash name
        if not _can_see(command, permissions, in_guild):
            continue
        entries = grouped.setdefault(_cog_name(command), [])
        for leaf in _leaves(command):
            entries.append(HelpEntry(leaf.qualified_name, (leaf.description or "").strip()))
    categories = [
        HelpCategory(name, (cog_descriptions.get(name) or "").strip(), tuple(sorted(entries, key=lambda e: e.name)))
        for name, entries in grouped.items()
        if entries
    ]
    categories.sort(key=lambda c: (c.name == OTHER, c.name.casefold()))
    return categories


def paginate(lines: list[str], limit: int = MAX_PAGE_CHARS) -> list[str]:
    pages: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        line = line[:limit]
        if current and size + len(line) + 1 > limit:
            pages.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pages.append("\n".join(current))
    return pages or ["No commands."]


def overview_page(categories: list[HelpCategory], prefix: str) -> str:
    lines = ["Pick a category below to see its commands.", ""]
    for category in categories:
        summary = category.description.splitlines()[0] if category.description else ""
        count = len(category.entries)
        lines.append(f"**{category.name}** ({count}){f' - {summary}' if summary else ''}")
    lines += ["", f"Prefix commands are listed by `{prefix}help`."]
    return paginate(lines)[0]


class HelpView(ui.View):
    def __init__(self, author_id: int, categories: list[HelpCategory], prefix: str):
        super().__init__(timeout=HELP_TIMEOUT)
        self.author_id = author_id
        self.categories = categories[:MAX_CATEGORIES]
        self.prefix = prefix
        self.category: HelpCategory | None = None
        self.pages: list[str] = [overview_page(self.categories, prefix)]
        self.page = 0
        self.message: discord.Message | discord.InteractionMessage | None = None

        self.picker = ui.Select(
            placeholder="Choose a category",
            options=[discord.SelectOption(label="Overview", value=OVERVIEW_VALUE)]
            + [
                discord.SelectOption(label=c.name[:100], value=c.name[:100], description=f"{len(c.entries)} commands")
                for c in self.categories[: MAX_CATEGORIES - 1]
            ],
        )
        self.picker.callback = self._pick
        self.previous = ui.Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.previous.callback = self._previous
        self.next = ui.Button(label="Next", style=discord.ButtonStyle.secondary)
        self.next.callback = self._next
        self.add_item(self.picker)
        self.add_item(self.previous)
        self.add_item(self.next)
        self._sync_buttons()

    def embed(self) -> discord.Embed:
        title = "Help" if self.category is None else f"Help: {self.category.name}"
        embed = discord.Embed(title=title, description=self.pages[self.page], color=ACCENT)
        if len(self.pages) > 1:
            embed.set_footer(text=f"Page {self.page + 1}/{len(self.pages)}")
        return embed

    def show(self, name: str) -> None:
        self.category = next((c for c in self.categories if c.name[:100] == name), None)
        if self.category is None:
            self.pages = [overview_page(self.categories, self.prefix)]
        else:
            lines = [self.category.description, ""] if self.category.description else []
            self.pages = paginate(lines + [entry.line() for entry in self.category.entries])
        self.page = 0
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("Run `/help` to open your own help menu.", ephemeral=True)
        return False

    async def _pick(self, interaction: discord.Interaction) -> None:
        self.show(self.picker.values[0] if self.picker.values else OVERVIEW_VALUE)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(len(self.pages) - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
