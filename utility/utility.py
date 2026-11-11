from __future__ import annotations

from redbot.core import commands

from .converter import ConverterMixin
from .reminders import RemindersMixin
from .sixseven import SixSevenMixin
from .urban import UrbanMixin
from .wiki import WikiMixin


class Utility(ConverterMixin, UrbanMixin, WikiMixin, RemindersMixin, SixSevenMixin, commands.Cog):
    """Unit conversion, Urban Dictionary, open.mp docs, reminders, and the six seven auto-reply."""

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self._wiki_init()
        self._reminders_init()
        self._sixseven_init()

    async def cog_unload(self):
        self._reminders_unload()

    def control_panel_schema(self):
        return {
            "title": "Utility",
            "description": "Reminder limits and the six seven auto-reply.",
            "settings": self._reminders_settings() + self._sixseven_settings(),
        }
