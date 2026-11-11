"""Settings declarations for the control panel.

This module has no Discord or Red imports. A cog joins the panel by defining
``control_panel_schema()`` and returning a plain dictionary, so cogs never need
to import this package and can still be installed on their own::

    def control_panel_schema(self):
        return {
            "title": "Example",
            "description": "What this cog does.",
            "settings": [
                {"key": "enabled", "label": "Enabled", "type": "bool"},
                {"key": "cooldown", "label": "Cooldown", "type": "int", "min": 0, "max": 60, "unit": "s"},
            ],
        }

Setting fields:

``key``       Config attribute. Dots address nested values.
``label``     Name shown in the panel.
``type``      bool, int, float, text, choice, channel, role, channels or roles.
``section``   Heading the setting is grouped under. Defaults to General.
``help``      One-line explanation.
``min/max``   Bounds for int and float, and length bounds for text.
``unit``      Suffix shown after numbers.
``choices``   ``[{"value": ..., "label": ...}]`` for choice settings.
``empty``     Text shown when nothing is set.
``clearable`` Whether the value can be emptied. Defaults to True for channel and role.
``clear_value`` Value stored when cleared. Defaults to None, or [] for lists.
``channel_types`` Any of text, voice, category, forum, stage, news.
``id_format`` How IDs are stored: int (default) or str.
``scope``     guild (default) or global. Global settings are visible to bot owners only.
``access``    manage (default, Manage Server) or admin (administrator or Red admin role).
              Also accepted at the top level of the schema, as the default for every setting.
``validate``  ``validate(guild, value)``, sync or async, returning an error or None.
``get``/``set`` ``get(guild)`` and ``set(guild, value)``, sync or async, replacing Config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


NUMBER_TYPES = {"int", "float"}
INPUT_TYPES = NUMBER_TYPES | {"text"}
SINGLE_ID_TYPES = {"channel", "role"}
LIST_ID_TYPES = {"channels", "roles"}
SETTING_TYPES = INPUT_TYPES | SINGLE_ID_TYPES | LIST_ID_TYPES | {"bool", "choice"}
CHANNEL_TYPE_NAMES = {"text", "voice", "category", "forum", "stage", "news"}
MAX_SECTION_SETTINGS = 25
MAX_CHOICES = 25
MAX_LIST_PREVIEW = 8
MAX_VALUE_LENGTH = 160
_UNSET = object()


class SchemaError(ValueError):
    """A cog declared its settings incorrectly."""


class SettingError(ValueError):
    """A value entered in the panel was rejected. The message is shown to the user."""


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    type: str
    section: str = "General"
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    choices: tuple[tuple[Any, str], ...] = ()
    empty: str = "Not set"
    clearable: bool = False
    clear_value: Any = None
    channel_types: tuple[str, ...] = ()
    id_format: str = "int"
    scope: str = "guild"
    access: str = "manage"
    validate: Callable[..., Any] | None = None
    getter: Callable[..., Any] | None = None
    setter: Callable[..., Any] | None = None


@dataclass(frozen=True)
class Schema:
    title: str
    description: str
    settings: tuple[Setting, ...]

    @property
    def sections(self) -> list[str]:
        return list(dict.fromkeys(setting.section for setting in self.settings))

    def in_section(self, section: str) -> list[Setting]:
        return [setting for setting in self.settings if setting.section == section]

    def setting(self, key: str) -> Setting | None:
        return next((setting for setting in self.settings if setting.key == key), None)


def _parse_setting(raw: dict[str, Any], default_access: str = "manage") -> Setting:
    known = {
        "key", "label", "type", "section", "help", "min", "max", "unit", "choices", "empty",
        "clearable", "clear_value", "channel_types", "id_format", "scope", "access", "validate", "get", "set",
    }
    unknown = set(raw) - known
    if unknown:
        raise SchemaError(f"unknown field(s) {sorted(unknown)} on setting {raw.get('key')!r}")
    try:
        key, label, kind = str(raw["key"]), str(raw["label"]), str(raw["type"])
    except KeyError as error:
        raise SchemaError(f"setting is missing {error.args[0]!r}") from None
    if kind not in SETTING_TYPES:
        raise SchemaError(f"setting {key!r} has unknown type {kind!r}")

    choices = tuple((choice["value"], str(choice["label"])) for choice in raw.get("choices", ()))
    if kind == "choice" and not 1 <= len(choices) <= MAX_CHOICES:
        raise SchemaError(f"setting {key!r} needs 1-{MAX_CHOICES} choices")

    channel_types = tuple(raw.get("channel_types", ()))
    if set(channel_types) - CHANNEL_TYPE_NAMES:
        raise SchemaError(f"setting {key!r} has unknown channel types {channel_types}")

    id_format = raw.get("id_format", "int")
    if id_format not in {"int", "str"}:
        raise SchemaError(f"setting {key!r} has unknown id_format {id_format!r}")
    scope = raw.get("scope", "guild")
    if scope not in {"guild", "global"}:
        raise SchemaError(f"setting {key!r} has unknown scope {scope!r}")
    access = raw.get("access", default_access)
    if access not in {"manage", "admin"}:
        raise SchemaError(f"setting {key!r} has unknown access {access!r}")

    is_list = kind in LIST_ID_TYPES
    return Setting(
        key=key,
        label=label,
        type=kind,
        section=str(raw.get("section", "General")),
        help=str(raw.get("help", "")),
        minimum=raw.get("min"),
        maximum=raw.get("max"),
        unit=str(raw.get("unit", "")),
        choices=choices,
        empty=str(raw.get("empty", "None" if is_list else "Not set")),
        clearable=bool(raw.get("clearable", kind in SINGLE_ID_TYPES or is_list)),
        clear_value=raw.get("clear_value", [] if is_list else None),
        channel_types=channel_types,
        id_format=id_format,
        scope=scope,
        access=access,
        validate=raw.get("validate"),
        getter=raw.get("get"),
        setter=raw.get("set"),
    )


def parse_schema(raw: dict[str, Any]) -> Schema:
    """Turn a cog's declaration into a Schema, or raise SchemaError."""
    if not isinstance(raw, dict) or not raw.get("title"):
        raise SchemaError("schema needs a title")
    default_access = raw.get("access", "manage")
    settings = tuple(_parse_setting(item, default_access) for item in raw.get("settings", ()))
    keys = [setting.key for setting in settings]
    duplicates = {key for key in keys if keys.count(key) > 1}
    if duplicates:
        raise SchemaError(f"duplicate setting keys: {sorted(duplicates)}")
    schema = Schema(str(raw["title"]), str(raw.get("description", "")), settings)
    for section in schema.sections:
        if len(schema.in_section(section)) > MAX_SECTION_SETTINGS:
            raise SchemaError(f"section {section!r} has more than {MAX_SECTION_SETTINGS} settings")
    return schema


def parse_input(setting: Setting, raw: str) -> Any:
    """Convert text typed into the panel, enforcing the setting's bounds."""
    text = raw.strip()
    if setting.type == "text":
        return _check_bounds(setting, len(text), text, "characters")
    if setting.type not in NUMBER_TYPES:
        raise SettingError(f"{setting.label} cannot be typed in.")
    cleaned = text.replace(",", "")
    try:
        value = int(cleaned) if setting.type == "int" else float(cleaned)
    except ValueError:
        kind = "whole number" if setting.type == "int" else "number"
        raise SettingError(f"{setting.label} must be a {kind}.") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise SettingError(f"{setting.label} must be a number.")
    return _check_bounds(setting, value, value, setting.unit)


def _check_bounds(setting: Setting, measured: float, value: Any, unit: str) -> Any:
    suffix = f" {unit}" if unit else ""
    if setting.minimum is not None and measured < setting.minimum:
        raise SettingError(f"{setting.label} must be at least {setting.minimum:g}{suffix}.")
    if setting.maximum is not None and measured > setting.maximum:
        raise SettingError(f"{setting.label} must be at most {setting.maximum:g}{suffix}.")
    return value


def store_id(setting: Setting, object_id: int) -> int | str:
    return str(object_id) if setting.id_format == "str" else int(object_id)


def toggle_ids(setting: Setting, current: Iterable[Any], picked: Iterable[int]) -> list[Any]:
    """Add picked IDs that are absent and remove those present, keeping everything else."""
    result = list(current)
    existing = {int(item): item for item in result}
    for object_id in picked:
        if int(object_id) in existing:
            result.remove(existing.pop(int(object_id)))
        else:
            stored = store_id(setting, object_id)
            result.append(stored)
            existing[int(object_id)] = stored
    return result


def _clip(text: str) -> str:
    return text if len(text) <= MAX_VALUE_LENGTH else text[: MAX_VALUE_LENGTH - 1] + "…"


def _mention(setting_type: str, object_id: Any) -> str:
    prefix = "#" if setting_type in {"channel", "channels"} else "@&"
    return f"<{prefix}{object_id}>"


def format_value(setting: Setting, value: Any) -> str:
    """Render a value for the panel's text. Mentions are never allowed to ping."""
    if setting.type == "bool":
        return "On" if value else "Off"
    if value is None or value == "" or value == []:
        return setting.empty
    if setting.type == "choice":
        return next((label for choice, label in setting.choices if choice == value), str(value))
    if setting.type in SINGLE_ID_TYPES:
        return _mention(setting.type, value)
    if setting.type in LIST_ID_TYPES:
        items = list(value)
        shown = ", ".join(_mention(setting.type, item) for item in items[:MAX_LIST_PREVIEW])
        extra = len(items) - MAX_LIST_PREVIEW
        return f"{shown} and {extra} more" if extra > 0 else shown
    if setting.type in NUMBER_TYPES:
        number = f"{value:,}" if setting.type == "int" else f"{float(value):g}"
        return f"{number} {setting.unit}" if setting.unit else number
    return _clip(str(value))


def format_option(setting: Setting, value: Any) -> str:
    """Render a value for a dropdown, where mentions show up as raw text."""
    if setting.type in SINGLE_ID_TYPES:
        return "Set" if value else setting.empty
    if setting.type in LIST_ID_TYPES:
        return f"{len(value)} selected" if value else setting.empty
    return format_value(setting, value)
