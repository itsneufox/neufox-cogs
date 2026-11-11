"""Commands and Owner tools pages of the control panel."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

import discord
from discord import ui

from . import tools

log = logging.getLogger("red.neufox.control")

PAGE_SIZE = 25
MAX_NOTICE = 3800
COMMANDS_PAGE = "__commands__"
OWNER_PAGE = "__owner__"


def _clip(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _page(items: list, page: int) -> tuple[list, int, int]:
    pages = max(1, -(-len(items) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    return items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE], page, pages


class PagesMixin:
    """Mixed into ControlPanel. Relies on its bot, guild, member, notice, _button, _take_notice and update."""

    page: str | None
    list_page: int
    cmd_cog: str | None
    owner_view: str
    owner_package: str | None

    def _reset_pages(self):
        self.page = None
        self.list_page = 0
        self.cmd_cog = None
        self.owner_view = "home"
        self.owner_package = None

    async def _run_slow(self, interaction: discord.Interaction, work: Callable[[], Awaitable[str]]):
        """Defer, run something that may take a while, then show its result as the notice."""
        await interaction.response.defer()
        try:
            self.notice = _clip(await work(), MAX_NOTICE)
        except Exception as error:
            log.exception("Control panel owner action failed.")
            self.notice = f"⚠️ {type(error).__name__}: {error}"[:MAX_NOTICE]
        await self.render()
        await interaction.edit_original_response(view=self, allowed_mentions=discord.AllowedMentions.none())

    def _pager(self, page: int, pages: int) -> list[ui.Button]:
        if pages <= 1:
            return []

        async def previous(interaction: discord.Interaction):
            self.list_page -= 1
            await self.update(interaction)

        async def next_(interaction: discord.Interaction):
            self.list_page += 1
            await self.update(interaction)

        back = self._button("◀", previous)
        back.disabled = page == 0
        forward = self._button("▶", next_)
        forward.disabled = page >= pages - 1
        label = self._button(f"Page {page + 1}/{pages}", previous)
        label.disabled = True
        return [back, label, forward]

    def _back_home(self) -> ui.Button:
        async def back(interaction: discord.Interaction):
            self._reset_pages()
            await self.update(interaction)

        return self._button("Back", back)

    # ----- commands ---------------------------------------------------------------

    async def _render_commands(self):
        if self.cmd_cog and self.bot.get_cog(self.cmd_cog) is None:
            self.notice = "⚠️ That cog is no longer loaded."
            self.cmd_cog = None
        if self.cmd_cog is None:
            self._render_command_cogs()
        else:
            await self._render_cog_commands(self.bot.get_cog(self.cmd_cog))

    def _render_command_cogs(self):
        cogs = sorted(
            (name for name, cog in self.bot.cogs.items() if any(True for _ in cog.walk_commands())),
            key=str.casefold,
        )
        shown, page, pages = _page(cogs, self.list_page)
        self.list_page = page
        lines = []
        for name in shown:
            commands = tools.cog_commands(self.bot.get_cog(name))
            off = sum(tools.is_disabled(c, self.guild) for c in commands)
            lines.append(f"**{name}**: {len(commands)} commands" + (f", {off} off" if off else ""))
        items: list[ui.Item] = [
            ui.TextDisplay(
                "## Commands\nTurn commands on or off in this server. "
                "This is the same setting as `[p]command disable server`."
            ),
            *self._take_notice(),
            ui.Separator(),
            ui.TextDisplay("\n".join(lines) or "No loaded cog has commands."),
        ]
        if shown:
            select = ui.Select(
                placeholder="Choose a cog",
                options=[discord.SelectOption(label=_clip(name), value=name) for name in shown],
            )

            async def pick(interaction: discord.Interaction):
                self.cmd_cog, self.list_page = select.values[0], 0
                await self.update(interaction)

            select.callback = pick
            items.append(ui.ActionRow(select))
        items.append(ui.ActionRow(self._back_home(), *self._pager(page, pages)))
        self.add_item(ui.Container(*items, accent_color=discord.Color.blurple()))

    async def _render_cog_commands(self, cog):
        level = await tools.member_level(self.bot, self.member)
        commands = tools.cog_commands(cog)
        shown, page, pages = _page(commands, self.list_page)
        self.list_page = page
        lines, toggleable = [], []
        for command in shown:
            reason = tools.lock_reason(self.bot, command, level)
            off = tools.is_disabled(command, self.guild)
            mark = "🔒" if reason else ("🚫" if off else "✅")
            lines.append(f"{mark} `{command.qualified_name}`" + (f" ({reason})" if reason else ""))
            if not reason:
                toggleable.append((command, off))

        items: list[ui.Item] = [
            ui.TextDisplay(f"## Commands: {cog.qualified_name}\n✅ on · 🚫 off · 🔒 can't be changed by you"),
            *self._take_notice(),
            ui.Separator(),
            ui.TextDisplay("\n".join(lines)[:3900] or "This cog has no commands."),
        ]
        if toggleable:
            select = ui.Select(
                placeholder="Pick commands to switch on or off",
                min_values=1,
                max_values=len(toggleable),
                options=[
                    discord.SelectOption(
                        label=_clip(command.qualified_name),
                        value=command.qualified_name,
                        description="Off now, pick to turn on" if off else "On now, pick to turn off",
                    )
                    for command, off in toggleable
                ],
            )

            async def toggle(interaction: discord.Interaction):
                by_name = {c.qualified_name: (c, off) for c, off in toggleable}
                on, off_ = [], []
                for name in select.values:
                    command, was_off = by_name[name]
                    await tools.set_command_enabled(self.bot, self.guild, command, enabled=was_off)
                    (on if was_off else off_).append(name)
                parts = []
                if on:
                    parts.append(f"turned on {', '.join(f'`{n}`' for n in on)}")
                if off_:
                    parts.append(f"turned off {', '.join(f'`{n}`' for n in off_)}")
                self.notice = "✅ " + "; ".join(parts).capitalize() + "."
                await self.update(interaction)

            select.callback = toggle
            items.append(ui.ActionRow(select))

        async def all_on(interaction: discord.Interaction):
            changed = []
            for command in commands:
                if tools.is_disabled(command, self.guild) and not tools.lock_reason(self.bot, command, level):
                    await tools.set_command_enabled(self.bot, self.guild, command, enabled=True)
                    changed.append(command.qualified_name)
            self.notice = f"✅ Turned on {len(changed)} commands." if changed else "Every command you can change is already on."
            await self.update(interaction)

        async def back(interaction: discord.Interaction):
            self.cmd_cog, self.list_page = None, 0
            await self.update(interaction)

        items.append(ui.ActionRow(self._button("Back", back), self._button("Turn all on", all_on), *self._pager(page, pages)))
        self.add_item(ui.Container(*items, accent_color=discord.Color.blurple()))

    # ----- owner tools ------------------------------------------------------------

    async def _owner_guard(self, interaction: discord.Interaction) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        self._reset_pages()
        self.notice = "⚠️ Owner tools are for the bot owner only."
        await self.update(interaction)
        return False

    async def _render_owner(self):
        if not await self.bot.is_owner(self.member):
            self._reset_pages()
            self.notice = "⚠️ Owner tools are for the bot owner only."
            await self.render()
            return
        view = self.owner_view
        if view == "packages":
            await self._render_owner_packages()
        elif view == "repos":
            self._render_owner_repos()
        elif view == "error":
            self._render_owner_error()
        else:
            self._render_owner_home()

    def _owner_nav(self, view: str):
        async def go(interaction: discord.Interaction):
            if await self._owner_guard(interaction):
                self.owner_view, self.list_page, self.owner_package = view, 0, None
                await self.update(interaction)

        return go

    def _render_owner_home(self):
        async def sync(interaction: discord.Interaction):
            if await self._owner_guard(interaction):
                await self._run_slow(interaction, lambda: tools.sync_commands(self.bot))

        items: list[ui.Item] = [
            ui.TextDisplay("## Owner tools\n" + tools.bot_status(self.bot)),
            *self._take_notice(),
            ui.Separator(),
            ui.TextDisplay(
                "**Cogs**: load, unload or reload any cog.\n"
                "**Update repo**: pull a repo, reinstall its cogs and reload them. Works even when "
                "`[p]cog update` says everything is up to date.\n"
                "**Sync slash commands**: push slash command changes to Discord.\n"
                "**Last error**: the most recent command error, like `[p]traceback`."
            ),
            ui.ActionRow(
                self._button("Cogs", self._owner_nav("packages"), discord.ButtonStyle.primary),
                self._button("Update repo", self._owner_nav("repos"), discord.ButtonStyle.primary),
                self._button("Sync slash commands", sync),
                self._button("Last error", self._owner_nav("error")),
            ),
            ui.ActionRow(self._back_home()),
        ]
        self.add_item(ui.Container(*items, accent_color=discord.Color.dark_red()))

    async def _render_owner_packages(self):
        loaded, available = await tools.package_names(self.bot)
        rows = [(name, True) for name in loaded] + [(name, False) for name in available]
        shown, page, pages = _page(rows, self.list_page)
        self.list_page = page
        items: list[ui.Item] = [
            ui.TextDisplay(f"## Owner tools: Cogs\n{len(loaded)} loaded · {len(available)} installed but not loaded"),
            *self._take_notice(),
        ]
        if self.owner_package:
            name = self.owner_package
            is_loaded = name in loaded

            def run(action: str):
                async def callback(interaction: discord.Interaction):
                    if await self._owner_guard(interaction):
                        await self._run_slow(interaction, lambda: tools.manage_package(self.bot, action, name))

                return callback

            buttons = (
                [self._button("Reload", run("reload"), discord.ButtonStyle.primary), self._button("Unload", run("unload"), discord.ButtonStyle.danger)]
                if is_loaded
                else [self._button("Load", run("load"), discord.ButtonStyle.success)]
            )
            note = " Reloading or unloading `control` closes this panel." if name == "control" else ""
            items += [
                ui.Separator(),
                ui.TextDisplay(f"**{name}** is {'loaded' if is_loaded else 'not loaded'}.{note}"),
                ui.ActionRow(*buttons),
            ]
        if shown:
            select = ui.Select(
                placeholder="Choose a cog package",
                options=[
                    discord.SelectOption(
                        label=_clip(name), value=name, description="Loaded" if is_loaded else "Not loaded",
                        default=name == self.owner_package,
                    )
                    for name, is_loaded in shown
                ],
            )

            async def pick(interaction: discord.Interaction):
                if await self._owner_guard(interaction):
                    self.owner_package = select.values[0]
                    await self.update(interaction)

            select.callback = pick
            items.append(ui.ActionRow(select))
        items.append(ui.ActionRow(self._button("Back", self._owner_nav("home")), *self._pager(page, pages)))
        self.add_item(ui.Container(*items, accent_color=discord.Color.dark_red()))

    def _render_owner_repos(self):
        repos = tools.repo_names(self.bot)
        items: list[ui.Item] = [
            ui.TextDisplay(
                "## Owner tools: Update repo\nPick a repo to pull it, reinstall every cog installed from it "
                "(pinned cogs are skipped) and reload the ones that are loaded. Saved data is not touched."
            ),
            *self._take_notice(),
        ]
        if repos:
            select = ui.Select(
                placeholder="Choose a repo to update",
                options=[discord.SelectOption(label=_clip(name), value=name) for name in repos[:PAGE_SIZE]],
            )

            async def pick(interaction: discord.Interaction):
                if await self._owner_guard(interaction):
                    name = select.values[0]

                    async def work() -> str:
                        report = await tools.force_update_repo(self.bot, name)
                        return f"**{name}**\n{report.text()}"

                    await self._run_slow(interaction, work)

            select.callback = pick
            items.append(ui.ActionRow(select))
        else:
            items.append(ui.TextDisplay("No repos found. Is the Downloader cog loaded?"))
        items.append(ui.ActionRow(self._button("Back", self._owner_nav("home"))))
        self.add_item(ui.Container(*items, accent_color=discord.Color.dark_red()))

    def _render_owner_error(self):
        items: list[ui.Item] = [
            ui.TextDisplay("## Owner tools: Last error"),
            *self._take_notice(),
            ui.TextDisplay(tools.last_error(self.bot)),
            ui.ActionRow(self._button("Back", self._owner_nav("home"))),
        ]
        self.add_item(ui.Container(*items, accent_color=discord.Color.dark_red()))
