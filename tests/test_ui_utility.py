"""Layout, command surface and settings checks for the Utility cog."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).parents[1]
HAS_RED = importlib.util.find_spec("redbot") is not None


def load(name):
    sys.path.insert(0, str(ROOT))
    return importlib.import_module(f"utility.{name}")


class FakeValue:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def __call__(self):
        return self

    async def _get(self):
        return self.store[self.key]

    def __await__(self):
        return self._get().__await__()

    async def set(self, value):
        self.store[self.key] = value

    async def __aenter__(self):
        return self.store[self.key]

    async def __aexit__(self, *exc):
        return False


class FakeGroup:
    def __init__(self, store):
        self.store = store

    def get_attr(self, key):
        return FakeValue(self.store, key)


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class UtilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import discord

        self.discord = discord
        self.cog_cls = load("utility").Utility

    def buttons(self, view):
        return [b for b in view.walk_children() if isinstance(b, self.discord.ui.Button)]

    def test_command_surface(self):
        prefix = {c.name: c for c in self.cog_cls.__cog_commands__ if c.parent is None}
        self.assertEqual(set(prefix), {"convert", "urban", "wiki", "remind", "sixseven"})
        slash = {name for name, c in prefix.items() if getattr(c, "app_command", None)}
        self.assertEqual(slash, {"convert", "urban", "wiki", "remind"})
        self.assertEqual({c.name for c in prefix["remind"].commands}, {"me", "member", "list", "cancel"})
        self.assertEqual(set(prefix["urban"].aliases), {"ud", "define"})
        self.assertEqual(self.cog_cls.__cog_app_commands__, [])

    def test_control_panel_schema(self):
        from control.schema import parse_schema

        cog = self.cog_cls.__new__(self.cog_cls)
        cog.reminders_config, cog.sixseven_config = MagicMock(), MagicMock()
        schema = parse_schema(cog.control_panel_schema())
        self.assertEqual(schema.sections, ["Reminders", "SixSeven"])
        self.assertTrue(all(setting.getter and setting.setter for setting in schema.settings))

    async def test_sixseven_language_lists_edit_one_language_only(self):
        cog = self.cog_cls.__new__(self.cog_cls)
        store = {"channel_languages": {"1": "pt", "2": "en"}, "category_languages": {}}
        cog.sixseven_config = MagicMock()
        cog.sixseven_config.guild.return_value = FakeGroup(store)
        cog.reminders_config = MagicMock()
        settings = {s["key"]: s for s in cog.control_panel_schema()["settings"]}
        self.assertEqual(await settings["sixseven_channels_pt"]["get"](None), [1])
        await settings["sixseven_channels_pt"]["set"](None, [2, 3])
        self.assertEqual(store["channel_languages"], {"2": "pt", "3": "pt"})

    def test_sixseven_language_resolution(self):
        resolve = load("sixseven").resolve_language
        channel = MagicMock(id=1, category=MagicMock(id=7))
        data = {"channel_languages": {}, "category_languages": {"7": "pt"}, "default_language": "en"}
        self.assertEqual(resolve(channel, data), "pt")
        data["channel_languages"]["1"] = "en"
        self.assertEqual(resolve(channel, data), "en")
        self.assertEqual(resolve(MagicMock(id=5, category=None), {"default_language": "xx"}), "en")

    async def test_converter_card(self):
        converter, layouts = load("converter"), load("layouts")
        view = layouts.text_card(converter.units_text("/convert"))
        self.assertLessEqual(view.content_length(), 4000)
        self.assertIn("mpg-UK", str(view.to_components()))
        self.assertEqual(converter.convert_query("1 km to m"), "1 km ≈ **1000 m**")
        with self.assertRaises(ValueError):
            converter.convert_query("banana")

    async def test_urban_pages_stay_within_limits_and_titles_link_safely(self):
        urban, layouts = load("urban"), load("layouts")
        entry = {"word": "a]b", "author": "x", "definition": "d" * 9000, "example": "e" * 9000, "permalink": "https://x.example/"}
        view = layouts.Pager(1, [urban.definition_embed(entry, page=i, total=2) for i in range(2)])
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(view.total_children_count, 40)
        self.assertIn("a\\\\]b", str(view.to_components()))
        self.assertIn("Definition 1/2", str(view.to_components()))

    async def test_wiki_search_view_keeps_custom_ids(self):
        wiki = load("wiki")
        embed = self.discord.Embed(title="Results", description="d", color=1)
        view = wiki.WikiSearchView(MagicMock(), "123", 3, embed)
        self.assertEqual([b.custom_id for b in self.buttons(view)], ["wiki_123_0", "wiki_123_1", "wiki_123_2"])
        self.assertLessEqual(view.content_length(), 4000)

    async def test_wiki_doc_paginates_within_limits(self):
        wiki = load("wiki")
        view = wiki.doc_view(1, "SetTimer", "https://open.mp/x", "a" * (wiki.DOC_CHUNK_SIZE * 3))
        self.assertLessEqual(view.content_length(), 4000)
        buttons = self.buttons(view)
        self.assertEqual(len(buttons), 3)
        self.assertTrue(buttons[0].disabled)
        self.assertFalse(buttons[1].disabled)
        self.assertEqual(buttons[2].url, "https://open.mp/x")
        single = wiki.doc_view(1, "SetTimer", "https://open.mp/x", "a")
        self.assertEqual([b.url for b in self.buttons(single)], ["https://open.mp/x"])

    def test_wiki_filters_listing_pages(self):
        wiki = load("wiki")
        hits = [
            {"url_without_anchor": "https://open.mp/docs/scripting/functions/SetTimer"},
            {"url_without_anchor": "https://open.mp/docs/scripting/functions/SetTimer"},
            {"url_without_anchor": "https://open.mp/docs/scripting/functions"},
            {"url_without_anchor": "https://open.mp/blog/post"},
        ]
        self.assertEqual(len(wiki.filter_hits(hits)), 1)

    def test_reminder_durations(self):
        reminders = load("reminders")
        self.assertEqual(reminders.parse_duration("in 2h30m"), 9000)
        self.assertIsNone(reminders.parse_duration("soon"))
        self.assertIsNone(reminders.parse_duration("0m"))
        self.assertEqual(reminders.format_duration(90061), "1 day 1 hour")
        self.assertEqual(reminders.sanitize_message("@everyone <@&5>"), "@​everyone <@​&5>")


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class ReminderListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cls = load("utility").Utility
        self.cog = cls.__new__(cls)
        self.cog._is_reminder_unbounded = AsyncMock(return_value=False)
        self.cog._get_active_reminders = AsyncMock(
            return_value=[(i, {"target_id": 2, "creator_id": 3, "due_at": 1800000000, "message": "m" * 200}) for i in range(25)]
        )
        self.guild, self.actor = MagicMock(id=9), MagicMock(id=3)

    async def test_list_renders_within_limits_and_filters_to_creator(self):
        error, view = await self.cog._reminder_list(self.guild, self.actor, None)
        self.assertEqual(error, "")
        self.assertLessEqual(view.content_length(), 4000)
        self.assertIn("5 more", str(view.to_components()))
        self.cog._get_active_reminders.assert_awaited_with(9, creator_id=3)

    async def test_staff_can_filter_by_member(self):
        self.cog._is_reminder_unbounded.return_value = True
        await self.cog._reminder_list(self.guild, self.actor, MagicMock(id=5))
        self.cog._get_active_reminders.assert_awaited_with(9, creator_id=5)

    async def test_non_staff_cannot_list_another_member(self):
        error, view = await self.cog._reminder_list(self.guild, self.actor, MagicMock(id=5))
        self.assertIsNone(view)
        self.assertIn("only list", error)

    async def test_empty_list_reports_a_message(self):
        self.cog._get_active_reminders.return_value = []
        error, view = await self.cog._reminder_list(self.guild, self.actor, None)
        self.assertIsNone(view)
        self.assertIn("No active", error)


if __name__ == "__main__":
    unittest.main()
