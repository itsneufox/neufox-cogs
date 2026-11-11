import importlib
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("control_schema", ROOT / "control" / "schema.py")
schema = importlib.util.module_from_spec(SPEC)
sys.modules["control_schema"] = schema
SPEC.loader.exec_module(schema)


def build(*settings):
    return schema.parse_schema({"title": "Example", "description": "Test.", "settings": list(settings)})


class SchemaTests(unittest.TestCase):
    def test_rejects_bad_declarations(self):
        cases = [
            {"title": "X", "settings": [{"key": "a", "label": "A", "type": "nope"}]},
            {"title": "X", "settings": [{"key": "a", "label": "A", "type": "bool", "typo": 1}]},
            {"title": "X", "settings": [{"key": "a", "type": "bool"}]},
            {"title": "X", "settings": [{"key": "a", "label": "A", "type": "choice"}]},
            {"title": "X", "settings": [{"key": "a", "label": "A", "type": "bool"}] * 2},
            {"title": "X", "settings": [{"key": f"k{i}", "label": "A", "type": "bool"} for i in range(26)]},
            {"settings": []},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(schema.SchemaError):
                schema.parse_schema(case)

    def test_number_bounds(self):
        setting = build({"key": "n", "label": "N", "type": "int", "min": 0, "max": 3600}).settings[0]
        self.assertEqual(schema.parse_input(setting, " 1,200 "), 1200)
        for bad in ("-1", "3601", "abc", "1.5", ""):
            with self.subTest(bad=bad), self.assertRaises(schema.SettingError):
                schema.parse_input(setting, bad)

    def test_float_rejects_non_finite(self):
        setting = build({"key": "n", "label": "N", "type": "float"}).settings[0]
        self.assertEqual(schema.parse_input(setting, "0.5"), 0.5)
        for bad in ("nan", "inf", "-inf"):
            with self.subTest(bad=bad), self.assertRaises(schema.SettingError):
                schema.parse_input(setting, bad)

    def test_toggle_ids_preserves_stored_format_and_unpicked_entries(self):
        setting = build({"key": "c", "label": "C", "type": "channels", "id_format": "str"}).settings[0]
        self.assertEqual(schema.toggle_ids(setting, ["1", "2"], [2, 3]), ["1", "3"])
        setting = build({"key": "c", "label": "C", "type": "roles"}).settings[0]
        self.assertEqual(schema.toggle_ids(setting, [1, 2], [2, 3]), [1, 3])

    def test_format_values(self):
        settings = build(
            {"key": "b", "label": "B", "type": "bool"},
            {"key": "c", "label": "C", "type": "channel", "empty": "Source channel"},
            {"key": "l", "label": "L", "type": "roles"},
            {"key": "n", "label": "N", "type": "int", "unit": "seconds"},
        ).settings
        self.assertEqual(schema.format_value(settings[0], True), "On")
        self.assertEqual(schema.format_value(settings[1], None), "Source channel")
        self.assertEqual(schema.format_value(settings[1], 5), "<#5>")
        self.assertIn("and 2 more", schema.format_value(settings[2], list(range(10))))
        self.assertEqual(schema.format_value(settings[3], 1200), "1,200 seconds")


try:
    importlib.import_module("discord")
    importlib.import_module("redbot")
    HAS_RED = True
except ImportError:
    HAS_RED = False


class FakeValue:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get_attr(self, key):
        return FakeValue(self.store, key)

    async def __call__(self):
        return self.store[self.key]

    async def set(self, value):
        self.store[self.key] = value


class FakeConfig:
    def __init__(self, store):
        self.store = store

    def guild(self, guild):
        return FakeValue(self.store, None)


class FakeCog:
    def __init__(self, broken=False):
        self.broken = broken
        self.config = FakeConfig({"on": True, "wait": 5, "chan": None, "ignored": ["1"], "mode": "a", "secret": 1})

    def control_panel_schema(self):
        if self.broken:
            return {"title": "Broken", "settings": [{"key": "x"}]}
        return {
            "title": "Example",
            "description": "A test cog.",
            "settings": [
                {"key": "on", "label": "On", "type": "bool"},
                {"key": "wait", "label": "Wait", "type": "int", "min": 0, "max": 10, "section": "Timing"},
                {"key": "chan", "label": "Channel", "type": "channel", "channel_types": ["text"]},
                {"key": "ignored", "label": "Ignored", "type": "channels", "id_format": "str"},
                {"key": "mode", "label": "Mode", "type": "choice", "choices": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
                {"key": "secret", "label": "Secret", "type": "int", "scope": "global", "section": "Owner"},
            ],
        }


class FakeBot:
    def __init__(self, cogs, owner=False):
        self.cogs, self.owner = cogs, owner

    def get_cog(self, name):
        return self.cogs.get(name)

    async def is_owner(self, user):
        return self.owner

    async def is_admin(self, user):
        return False


class FakeMember:
    id = 1
    guild = type("G", (), {"id": 9})()
    guild_permissions = type("P", (), {"manage_guild": True, "administrator": False})()


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class PanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        self.panel_module = importlib.import_module("control.panel")
        self.cog = FakeCog()
        self.bot = FakeBot({"Example": self.cog, "Broken": FakeCog(broken=True), "Plain": object()})

    def panel(self):
        return self.panel_module.ControlPanel(self.bot, FakeMember())

    def assert_valid(self, panel):
        self.assertTrue(panel.to_components())
        self.assertLessEqual(panel.total_children_count, 40)
        self.assertLessEqual(panel.content_length(), 4000)

    async def test_home_skips_malformed_and_plain_cogs(self):
        panel = self.panel()
        with self.assertLogs("red.neufox.control", "ERROR"):
            await panel.render()
        self.assert_valid(panel)
        text = str(panel.to_components())
        self.assertIn("Example", text)
        self.assertNotIn("Broken", text)

    async def test_every_page_renders(self):
        panel = self.panel()
        panel.cog_name = "Example"
        for section in ("General", "Timing"):
            panel.section, panel.editing = section, None
            with self.assertLogs("red.neufox.control", "ERROR"):
                await panel.render()
            self.assert_valid(panel)
        for key in ("chan", "ignored", "mode"):
            panel.section, panel.editing = "General", key
            with self.assertLogs("red.neufox.control", "ERROR"):
                await panel.render()
            self.assert_valid(panel)
            self.assertEqual(panel.editing, key)

    async def test_global_settings_are_owner_only(self):
        panel = self.panel()
        with self.assertLogs("red.neufox.control", "ERROR"):
            self.assertNotIn("Owner", (await panel._visible_schemas())["Example"].sections)
        self.bot.owner = True
        with self.assertLogs("red.neufox.control", "ERROR"):
            self.assertIn("Owner", (await panel._visible_schemas())["Example"].sections)

    async def test_admin_settings_hidden_from_manage_server_only(self):
        class AdminCog(FakeCog):
            def control_panel_schema(self):
                return {"title": "A", "settings": [{"key": "on", "label": "On", "type": "bool", "access": "admin"}]}

        self.bot.cogs = {"A": AdminCog()}
        self.assertEqual(await self.panel()._visible_schemas(), {})
        self.bot.owner = True
        self.assertIn("A", await self.panel()._visible_schemas())

    def test_activity_milestones(self):
        milestone = importlib.import_module("activity.xp").level_up_is_milestone
        self.assertFalse(milestone(1, 2, 5, 0, {}))
        self.assertTrue(milestone(4, 5, 5, 0, {}))
        self.assertTrue(milestone(3, 7, 5, 0, {}))
        self.assertTrue(milestone(1, 2, 5, 200, {}))
        self.assertTrue(milestone(2, 3, 5, 0, {"3": 1}))
        self.assertFalse(milestone(3, 4, 5, 0, {"3": 1}))
        self.assertTrue(milestone(1, 2, 1, 0, {}))

    def test_activity_schema_is_valid(self):
        module = importlib.import_module("activity.activity")
        stub = types.SimpleNamespace(_set_enabled=None, config=None, stats_config=None)
        parsed = self.panel_module.parse_schema(module.Activity.control_panel_schema(stub))
        self.assertEqual(parsed.sections, ["Tracking", "XP", "Stats", "Rewards"])

    async def test_validator_blocks_save(self):
        schema_obj = self.panel_module.parse_schema(self.cog.control_panel_schema())
        setting = self.panel_module.replace(schema_obj.setting("wait"), validate=lambda guild, value: "nope")
        panel = self.panel()
        self.assertFalse(await panel.apply(self.cog, setting, 7))
        self.assertEqual(self.cog.config.store["wait"], 5)
        self.assertIn("nope", panel.notice)
