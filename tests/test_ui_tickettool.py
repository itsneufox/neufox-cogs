import importlib
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]

try:
    importlib.import_module("discord")
    importlib.import_module("redbot")
    importlib.import_module("chat_exporter")
    HAS_RED = True
except ImportError:
    HAS_RED = False


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot and the cog's requirements installed")
class TicketToolLayoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        self.layouts = importlib.import_module("tickettool.layouts")

    def assert_valid(self, view):
        self.assertTrue(view.to_components())
        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 4000)

    async def test_info_layout_is_capped(self):
        view = self.layouts.info_layout("Title", "Intro", [("Name", "x" * 9000)], 0x123456)
        self.assert_valid(view)

    async def test_confirm_layout(self):
        view = self.layouts.ConfirmLayout(1, "Close?", "y" * 9000, 0x123456, "some_*user")
        self.assert_valid(view)

    async def test_pages_wrap_and_render(self):
        view = self.layouts.PagesLayout(1, "Tickets", ["a", "b", "c"], None)
        self.assert_valid(view)
        self.assertIn("Page 1/3", str(view.to_components()))
        view.current = 2
        view.render()
        self.assert_valid(view)
        self.assertIn("Page 3/3", str(view.to_components()))

    async def test_single_page_has_no_buttons(self):
        view = self.layouts.PagesLayout(1, "Tickets", ["a"], None)
        self.assertNotIn("'type': 2", str(view.to_components()))

    async def test_persistent_panel_stays_classic_with_its_custom_id(self):
        import discord

        cog = importlib.import_module("tickettool.tickettool").TicketTool
        async def handler(interaction):
            return None

        stub = types.SimpleNamespace(on_button_interaction=handler)
        view = cog.get_buttons(
            stub,
            [{"style": discord.ButtonStyle(2), "label": "Open", "emoji": "🎟️", "custom_id": "create_ticket_button", "disabled": False}],
        )
        self.assertIsInstance(view, discord.ui.View)
        self.assertIsNone(view.timeout)
        self.assertIn("create_ticket_button", [child.custom_id for child in view.children])

    def test_profile_list_is_reachable_on_slash(self):
        cog = importlib.import_module("tickettool.tickettool").TicketTool
        command = next(c for c in cog.__cog_commands__ if c.qualified_name == "settickettool profile")
        self.assertIn("list", [child.name for child in command.app_command.commands])
