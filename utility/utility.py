from __future__ import annotations

import time
from typing import Any

from redbot.core import Config, commands

from .converter import ConverterMixin
from .layouts import channel_allowed
from .reminders import RemindersMixin
from .sixseven import SixSevenMixin
from .urban import UrbanMixin
from .wiki import WikiMixin


CONFIG_ID = 1970563436
CONFIG_NAME = "Utility"
GATE_CHANNEL_TYPES = ["text", "news", "voice", "forum", "stage", "category"]
FEATURE_NAMES = {"convert": "Unit conversion", "urban": "Urban Dictionary", "wiki": "Wiki search"}
FEATURE_DEFAULTS: dict[str, Any] = {
    "convert_enabled": True,
    "convert_channels": [],
    "convert_digits": 10,
    "urban_enabled": True,
    "urban_channels": [],
    "urban_nsfw_only": False,
    "urban_max_results": 10,
    "urban_examples": True,
    "urban_cooldown": 0,
    "wiki_enabled": True,
    "wiki_channels": [],
    "wiki_max_results": 5,
    "wiki_cooldown": 5,
    "wiki_button_minutes": 10,
}


class Utility(ConverterMixin, UrbanMixin, WikiMixin, RemindersMixin, SixSevenMixin, commands.Cog):
    """Unit conversion, Urban Dictionary, open.mp docs, reminders, and the six seven auto-reply."""

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.utility_config = Config.get_conf(None, identifier=CONFIG_ID, cog_name=CONFIG_NAME, force_registration=True)
        self.utility_config.register_guild(**FEATURE_DEFAULTS)
        self._feature_last_used: dict[tuple[str, int, int], float] = {}
        self._wiki_init()
        self._reminders_init()
        self._sixseven_init()

    async def cog_unload(self):
        self._reminders_unload()

    # ----- per-feature gate -----

    async def _feature_settings(self, guild) -> dict[str, Any]:
        config = getattr(self, "utility_config", None)
        if guild is None or config is None:
            return dict(FEATURE_DEFAULTS)
        return {**FEATURE_DEFAULTS, **await config.guild(guild).all()}

    async def _feature_gate(self, ctx: commands.Context, feature: str) -> tuple[str | None, dict[str, Any]]:
        """Return (error, settings). The error is shown to the user and the command stops."""
        data = await self._feature_settings(ctx.guild)
        name = FEATURE_NAMES[feature]
        if ctx.guild is not None:
            if not data[f"{feature}_enabled"]:
                return f"{name} is turned off in this server.", data
            allowed = data.get(f"{feature}_channels") or []
            if not channel_allowed(ctx.channel, allowed):
                listed = ", ".join(f"<#{int(item)}>" for item in allowed[:10])
                return f"{name} can only be used in {listed}.", data
        cooldown = int(data.get(f"{feature}_cooldown") or 0)
        if cooldown:
            key = (feature, ctx.guild.id if ctx.guild else 0, ctx.author.id)
            now = time.monotonic()
            last = self._feature_last_used.get(key)
            if last is not None and now - last < cooldown:
                return f"Slow down. Try again in {cooldown - (now - last):.0f}s.", data
            self._feature_last_used[key] = now
        return None, data

    # ----- settings -----

    def _feature_panel_settings(self) -> list[dict[str, Any]]:
        def bind(key):
            return {
                "get": lambda guild: self.utility_config.guild(guild).get_attr(key)(),
                "set": lambda guild, value: self.utility_config.guild(guild).get_attr(key).set(value),
            }

        def toggle(feature, section):
            return {
                "key": f"{feature}_enabled", "label": "Enabled", "type": "bool", "section": section,
                "help": f"Allow {FEATURE_NAMES[feature]} in this server.", **bind(f"{feature}_enabled"),
            }

        def channels(feature, section):
            return {
                "key": f"{feature}_channels", "label": "Allowed channels", "type": "channels", "section": section,
                "channel_types": GATE_CHANNEL_TYPES, "empty": "Every channel",
                "help": "Only allow it in these channels or categories. Empty allows every channel.",
                **bind(f"{feature}_channels"),
            }

        return [
            toggle("convert", "Converter"),
            channels("convert", "Converter"),
            {"key": "convert_digits", "label": "Significant digits", "type": "int", "section": "Converter",
             "min": 1, "max": 15, "unit": "digits", "help": "How many significant digits results show.",
             **bind("convert_digits")},
            toggle("urban", "Urban Dictionary"),
            channels("urban", "Urban Dictionary"),
            {"key": "urban_nsfw_only", "label": "Age-restricted channels only", "type": "bool",
             "section": "Urban Dictionary", "help": "Only allow lookups in age-restricted (NSFW) channels.",
             **bind("urban_nsfw_only")},
            {"key": "urban_max_results", "label": "Definitions per lookup", "type": "int",
             "section": "Urban Dictionary", "min": 1, "max": 10, "unit": "pages",
             "help": "How many definitions a lookup pages through.", **bind("urban_max_results")},
            {"key": "urban_examples", "label": "Show examples", "type": "bool", "section": "Urban Dictionary",
             "help": "Include the example text under each definition.", **bind("urban_examples")},
            {"key": "urban_cooldown", "label": "Cooldown per member", "type": "int", "section": "Urban Dictionary",
             "min": 0, "max": 3600, "unit": "seconds", "help": "Time a member waits between lookups. 0 means none.",
             **bind("urban_cooldown")},
            toggle("wiki", "Wiki"),
            channels("wiki", "Wiki"),
            {"key": "wiki_max_results", "label": "Results shown", "type": "int", "section": "Wiki",
             "min": 1, "max": 5, "unit": "results", "help": "How many search results get a button.",
             **bind("wiki_max_results")},
            {"key": "wiki_cooldown", "label": "Cooldown per member", "type": "int", "section": "Wiki",
             "min": 0, "max": 3600, "unit": "seconds", "help": "Time a member waits between searches. 0 means none.",
             **bind("wiki_cooldown")},
            {"key": "wiki_button_minutes", "label": "Button lifetime", "type": "int", "section": "Wiki",
             "min": 1, "max": 60, "unit": "minutes", "help": "How long result and page buttons keep working.",
             **bind("wiki_button_minutes")},
        ]

    def control_panel_schema(self):
        return {
            "title": "Utility",
            "description": "Converter, Urban Dictionary, wiki search, reminders and the six seven auto-reply.",
            "settings": self._feature_panel_settings() + self._reminders_settings() + self._sixseven_settings(),
        }
