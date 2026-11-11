from __future__ import annotations

from dataclasses import replace
import inspect
import logging
from typing import Any

import discord
from discord import ui

from .schema import (
    INPUT_TYPES,
    LIST_ID_TYPES,
    Schema,
    Setting,
    SettingError,
    format_option,
    format_value,
    parse_input,
    parse_schema,
    store_id,
    toggle_ids,
)


log = logging.getLogger("red.neufox.control")

PANEL_TIMEOUT = 840
MAX_SELECT_OPTIONS = 25
MAX_LIST_PICKS = 25
MAX_TEXT_LENGTH = 3400
MAX_INPUT_LENGTH = 4000
NO_PINGS = discord.AllowedMentions.none()
ACCENT = discord.Color.blurple()
CHANNEL_TYPES = {
    "text": discord.ChannelType.text,
    "voice": discord.ChannelType.voice,
    "category": discord.ChannelType.category,
    "forum": discord.ChannelType.forum,
    "stage": discord.ChannelType.stage_voice,
    "news": discord.ChannelType.news,
}


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def discover_schemas(bot) -> dict[str, Schema]:
    """Collect declarations from loaded cogs, skipping any that are malformed."""
    schemas: dict[str, Schema] = {}
    for name, cog in sorted(bot.cogs.items()):
        builder = getattr(cog, "control_panel_schema", None)
        if not callable(builder):
            continue
        try:
            schemas[name] = parse_schema(builder())
        except Exception:
            log.exception("Ignoring invalid control panel schema from cog %s.", name)
    return schemas


def _config_value(cog, setting: Setting, guild: discord.Guild):
    node = cog.config if setting.scope == "global" else cog.config.guild(guild)
    for part in setting.key.split("."):
        node = node.get_attr(part)
    return node


async def read_value(cog, setting: Setting, guild: discord.Guild) -> Any:
    if setting.getter is not None:
        return await _resolve(setting.getter(guild))
    return await _config_value(cog, setting, guild)()


async def write_value(cog, setting: Setting, guild: discord.Guild, value: Any) -> None:
    if setting.validate is not None:
        error = await _resolve(setting.validate(guild, value))
        if error:
            raise SettingError(str(error))
    if setting.setter is not None:
        await _resolve(setting.setter(guild, value))
    else:
        await _config_value(cog, setting, guild).set(value)


async def can_manage(bot, member: discord.Member) -> bool:
    if await bot.is_owner(member) or member.guild_permissions.manage_guild:
        return True
    return await bot.is_admin(member)


def _clip_option(text: str) -> str:
    return text if len(text) <= 100 else text[:99] + "…"


class ValueModal(ui.Modal):
    def __init__(self, panel: "ControlPanel", cog, setting: Setting, current: Any):
        super().__init__(title=setting.label[:45])
        self.panel = panel
        self.cog = cog
        self.setting = setting
        if setting.type == "float" and current is not None:
            default = f"{float(current):g}"
        else:
            default = "" if current is None else str(current)
        is_long = setting.type == "text" and (setting.maximum or 0) > 200
        self.field = ui.TextInput(
            label=setting.label[:45],
            default=default[:MAX_INPUT_LENGTH],
            placeholder=setting.help[:100] or None,
            style=discord.TextStyle.paragraph if is_long else discord.TextStyle.short,
            required=not setting.clearable,
            max_length=min(int(setting.maximum), MAX_INPUT_LENGTH) if setting.type == "text" and setting.maximum else MAX_INPUT_LENGTH,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.interaction_check(interaction):
            return
        raw = self.field.value
        try:
            value = self.setting.clear_value if not raw.strip() and self.setting.clearable else parse_input(self.setting, raw)
        except SettingError as error:
            self.panel.notice = f"⚠️ {error}"
        else:
            await self.panel.apply(self.cog, self.setting, value)
        await self.panel.update(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await self.panel.report_error(interaction, error)


class ControlPanel(ui.LayoutView):
    """One admin's session in the panel. Rebuilt from live settings after every action."""

    def __init__(self, bot, member: discord.Member):
        super().__init__(timeout=PANEL_TIMEOUT)
        self.bot = bot
        self.member = member
        self.guild = member.guild
        self.message: discord.Message | None = None
        self.cog_name: str | None = None
        self.section: str | None = None
        self.editing: str | None = None
        self.notice = ""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("This panel belongs to someone else.", ephemeral=True)
            return False
        if not await can_manage(self.bot, interaction.user):
            await interaction.response.send_message("You no longer have permission to change settings.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item):
        await self.report_error(interaction, error)

    async def report_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Control panel error in guild %s.", self.guild.id, exc_info=error)
        text = "Something went wrong. The details are in the bot log."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def on_timeout(self):
        self.clear_items()
        self.add_item(ui.Container(ui.TextDisplay("This panel has closed. Run the command again to reopen it."), accent_color=ACCENT))
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                pass

    async def update(self, interaction: discord.Interaction):
        await self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)

    async def apply(self, cog, setting: Setting, value: Any) -> bool:
        try:
            await write_value(cog, setting, self.guild, value)
        except SettingError as error:
            self.notice = f"⚠️ {error}"
            return False
        except Exception:
            log.exception("Could not save %s in guild %s.", setting.key, self.guild.id)
            self.notice = "⚠️ That setting could not be saved. The details are in the bot log."
            return False
        try:
            value = await read_value(cog, setting, self.guild)
        except Exception:
            log.exception("Could not re-read %s in guild %s.", setting.key, self.guild.id)
        self.notice = f"✅ **{setting.label}** is now {format_value(setting, value)}."
        return True

    async def _visible_schemas(self) -> dict[str, Schema]:
        is_owner = await self.bot.is_owner(self.member)
        is_admin = is_owner or self.member.guild_permissions.administrator or await self.bot.is_admin(self.member)
        visible = {}
        for name, schema in discover_schemas(self.bot).items():
            settings = tuple(
                s
                for s in schema.settings
                if (is_owner or s.scope == "guild") and (is_admin or s.access == "manage")
            )
            if settings:
                visible[name] = replace(schema, settings=settings)
        return visible

    def _take_notice(self) -> list[ui.Item]:
        notice, self.notice = self.notice, ""
        return [ui.TextDisplay(notice)] if notice else []

    def _button(self, label: str, callback, style=discord.ButtonStyle.secondary) -> ui.Button:
        button = ui.Button(label=label, style=style)
        button.callback = callback
        return button

    async def render(self):
        self.clear_items()
        schemas = await self._visible_schemas()
        schema = schemas.get(self.cog_name) if self.cog_name else None
        cog = self.bot.get_cog(self.cog_name) if self.cog_name else None
        if schema is None or cog is None:
            if self.cog_name:
                self.notice = "⚠️ That cog is no longer available."
            self.cog_name = self.editing = self.section = None
            self._render_home(schemas)
            return

        if self.section not in schema.sections:
            self.section = schema.sections[0]
        setting = schema.setting(self.editing) if self.editing else None
        if setting is not None:
            await self._render_editor(cog, schema, setting)
        else:
            self.editing = None
            await self._render_cog(cog, schema)

    def _render_home(self, schemas: dict[str, Schema]):
        items: list[ui.Item] = [ui.TextDisplay("## Control Panel\nEvery setting for this server, in one place."), *self._take_notice()]
        if not schemas:
            items.append(ui.TextDisplay("No loaded cog has settings to show."))
            self.add_item(ui.Container(*items, accent_color=ACCENT))
            return

        names = list(schemas)[:MAX_SELECT_OPTIONS]
        items.append(ui.Separator())
        items.append(ui.TextDisplay("\n".join(f"**{schemas[n].title}**: {schemas[n].description}" for n in names)[:MAX_TEXT_LENGTH]))
        select = ui.Select(
            placeholder="Choose what to manage",
            options=[
                discord.SelectOption(label=_clip_option(schemas[n].title), value=n, description=_clip_option(schemas[n].description) or None)
                for n in names
            ],
        )

        async def pick(interaction: discord.Interaction):
            self.cog_name, self.section, self.editing = select.values[0], None, None
            await self.update(interaction)

        select.callback = pick
        items.append(ui.ActionRow(select))
        self.add_item(ui.Container(*items, accent_color=ACCENT))

    async def _render_cog(self, cog, schema: Schema):
        settings = schema.in_section(self.section)
        values = {s.key: await read_value(cog, s, self.guild) for s in settings}
        items: list[ui.Item] = [ui.TextDisplay(f"## {schema.title}\n{schema.description}"), *self._take_notice()]

        if len(schema.sections) > 1:
            section_select = ui.Select(
                placeholder="Choose a section",
                options=[
                    discord.SelectOption(label=_clip_option(name), value=str(index), default=name == self.section)
                    for index, name in enumerate(schema.sections[:MAX_SELECT_OPTIONS])
                ],
            )

            async def pick_section(interaction: discord.Interaction):
                self.section = schema.sections[int(section_select.values[0])]
                await self.update(interaction)

            section_select.callback = pick_section
            items.append(ui.ActionRow(section_select))

        items.append(ui.Separator())
        lines = [f"**{s.label}**: {format_value(s, values[s.key])}" for s in settings]
        items.append(ui.TextDisplay("\n".join(lines)[:MAX_TEXT_LENGTH]))

        select = ui.Select(
            placeholder="Choose a setting to change",
            options=[
                discord.SelectOption(
                    label=_clip_option(s.label),
                    value=s.key,
                    description=_clip_option(s.help or format_option(s, values[s.key])) or None,
                )
                for s in settings
            ],
        )

        async def pick_setting(interaction: discord.Interaction):
            await self._begin_edit(interaction, cog, schema, select.values[0])

        select.callback = pick_setting
        items.append(ui.ActionRow(select))

        async def back(interaction: discord.Interaction):
            self.cog_name = self.section = None
            await self.update(interaction)

        items.append(ui.ActionRow(self._button("Back", back)))
        self.add_item(ui.Container(*items, accent_color=ACCENT))

    async def _begin_edit(self, interaction: discord.Interaction, cog, schema: Schema, key: str):
        setting = schema.setting(key)
        if setting is None:
            await self.update(interaction)
            return
        current = await read_value(cog, setting, self.guild)
        if setting.type == "bool":
            await self.apply(cog, setting, not current)
            await self.update(interaction)
        elif setting.type in INPUT_TYPES:
            await interaction.response.send_modal(ValueModal(self, cog, setting, current))
        else:
            self.editing = key
            await self.update(interaction)

    def _value_select(self, cog, setting: Setting, current: Any) -> ui.Item:
        is_list = setting.type in LIST_ID_TYPES

        async def finish(interaction: discord.Interaction, value: Any):
            saved = await self.apply(cog, setting, value)
            if saved and not is_list:
                self.editing = None
            await self.update(interaction)

        if setting.type == "choice":
            select = ui.Select(
                placeholder="Choose a value",
                options=[
                    discord.SelectOption(label=_clip_option(label), value=str(index), default=value == current)
                    for index, (value, label) in enumerate(setting.choices)
                ],
            )

            async def pick_choice(interaction: discord.Interaction):
                await finish(interaction, setting.choices[int(select.values[0])][0])

            select.callback = pick_choice
            return select

        placeholder = "Pick to add or remove" if is_list else "Pick a new value"
        max_values = MAX_LIST_PICKS if is_list else 1
        if setting.type in {"channel", "channels"}:
            options: dict[str, Any] = {}
            if setting.channel_types:
                options["channel_types"] = [CHANNEL_TYPES[name] for name in setting.channel_types]
            select = ui.ChannelSelect(placeholder=placeholder, max_values=max_values, **options)
        else:
            select = ui.RoleSelect(placeholder=placeholder, max_values=max_values)

        async def pick_ids(interaction: discord.Interaction):
            picked = [item.id for item in select.values]
            value = toggle_ids(setting, current or [], picked) if is_list else store_id(setting, picked[0])
            await finish(interaction, value)

        select.callback = pick_ids
        return select

    async def _render_editor(self, cog, schema: Schema, setting: Setting):
        current = await read_value(cog, setting, self.guild)
        body = f"## {setting.label}\n"
        if setting.help:
            body += f"{setting.help}\n"
        body += f"Current: {format_value(setting, current)}"
        items: list[ui.Item] = [ui.TextDisplay(body), *self._take_notice(), ui.ActionRow(self._value_select(cog, setting, current))]

        async def back(interaction: discord.Interaction):
            self.editing = None
            await self.update(interaction)

        buttons = [self._button("Back", back)]
        if setting.clearable and current not in (None, "", []):

            async def clear(interaction: discord.Interaction):
                if await self.apply(cog, setting, setting.clear_value):
                    self.editing = None
                await self.update(interaction)

            buttons.append(self._button("Clear", clear, discord.ButtonStyle.danger))
        items.append(ui.ActionRow(*buttons))
        self.add_item(ui.Container(*items, accent_color=ACCENT))
