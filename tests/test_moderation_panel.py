"""Control panel settings of the Moderation cog: schema limits, Config round trips and the new behaviour switches."""

import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock


ROOT = Path(__file__).parents[1]

try:
    import discord
    from redbot.core import _drivers as drivers
    from redbot.core import data_manager

    HAS_RED = True
except ImportError:
    HAS_RED = False


# Red's JSON driver remembers each cog's data path for the whole process, so every test here shares one
# directory and uses its own guild ID instead of a fresh directory per test.
_TMP: tempfile.TemporaryDirectory | None = None
_GUILD_IDS = iter(range(1000, 100000))


def setUpModule():
    global _TMP
    _TMP = tempfile.TemporaryDirectory()


def tearDownModule():
    if _TMP is not None:
        _TMP.cleanup()


def load(module):
    sys.path.insert(0, str(ROOT))
    return importlib.import_module(module)


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class ModerationPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        data_manager.basic_config = {
            "DATA_PATH": _TMP.name,
            "STORAGE_TYPE": "JSON",
            "STORAGE_DETAILS": {},
            "COG_PATH_APPEND": "cogs",
            "CORE_PATH_APPEND": "core",
        }
        await drivers.get_driver_class().initialize(**data_manager.storage_details())
        self.schema_module = load("control.schema")
        self.panel = load("control.panel")
        self.cog = load("moderation.moderation").Moderation(MagicMock())
        self.schema = self.schema_module.parse_schema(self.cog.control_panel_schema())
        self.guild = SimpleNamespace(id=next(_GUILD_IDS), name="Guild")

    async def asyncTearDown(self):
        await drivers.get_driver_class().teardown()

    def test_schema_limits(self):
        limits = self.schema_module
        self.assertLessEqual(len(self.schema.sections), 25)
        for section in self.schema.sections:
            self.assertLessEqual(len(self.schema.in_section(section)), limits.MAX_SECTION_SETTINGS, section)
        for setting in self.schema.settings:
            self.assertLessEqual(len(setting.label), 100, setting.key)
            self.assertLessEqual(len(setting.help), 100, setting.key)
            self.assertLessEqual(len(setting.choices), limits.MAX_CHOICES, setting.key)
        for expected in ("DM notices", "Filter: links", "Honeypot"):
            self.assertIn(expected, self.schema.sections)

    async def test_defaults_match_old_behaviour(self):
        expected = {
            "require_reason": False,
            "confirm_actions": True,
            "confirm_timeout_seconds": 30,
            "purge_max": 200,
            "dm_on_ban": True,
            "dm_template": "",
            "automod:delete_violations": False,
            "automod:notify_in_channel": True,
            "automod:link_enabled": True,
            "automod:allowed_link_domains": "",
            "automod:caps_exempt_role_ids": [],
            "honeypot:ban_delete_message_days": 1,
            "honeypot:cleanup_hours": 1,
            "honeypot:log_exempt": True,
            "honeypot:dm_member": False,
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(await self.panel.read_value(self.cog, self.schema.setting(key), self.guild), value)

    async def test_round_trips(self):
        samples = {
            "require_reason": True,
            "purge_max": 500,
            "default_timeout_minutes": 60,
            "dm_on_kick": False,
            "dm_template": "You were {action} in {server}: {reason}",
            "dm_footer": "Appeal at example.com",
            "automod:mention_enabled": False,
            "automod:notify_delete_after": 30,
            "automod:allowed_link_domains": "youtube.com, github.com",
            "automod:link_exempt_channel_ids": [10, 11],
            "automod:rate_exempt_role_ids": [12],
            "honeypot:action": "timeout",
            "honeypot:timeout_minutes": 120,
            "honeypot:dm_message": "Bye from {server}, you were {action}.",
        }
        for key, value in samples.items():
            with self.subTest(key=key):
                setting = self.schema.setting(key)
                await self.panel.write_value(self.cog, setting, self.guild, value)
                self.assertEqual(await self.panel.read_value(self.cog, setting, self.guild), value)
        self.assertFalse(await self.cog.config.guild(self.guild).dm_on_kick())
        self.assertEqual(await self.cog.automod_config.guild(self.guild).notify_delete_after(), 30)
        self.assertEqual(await self.cog.honeypot_config.guild(self.guild).action(), "timeout")

    async def test_bad_templates_are_rejected(self):
        for key, bad in (("dm_template", "{nope}"), ("honeypot:dm_message", "{server")):
            with self.subTest(key=key), self.assertRaises(self.schema_module.SettingError):
                await self.panel.write_value(self.cog, self.schema.setting(key), self.guild, bad)

    async def test_per_action_dm_switch_and_template(self):
        member = SimpleNamespace(send=AsyncMock())
        conf = self.cog.config.guild(self.guild)
        await conf.dm_on_warn.set(False)
        await self.cog._maybe_dm(member, self.guild, "warn", "spam")
        member.send.assert_not_awaited()
        await conf.dm_template.set("{action} in {server}: {reason} ({duration})")
        await conf.dm_footer.set("Appeal: example.com")
        await self.cog._maybe_dm(member, self.guild, "timeout", "spam", duration="5 minute(s)")
        self.assertEqual(member.send.await_args.args[0], "timeout in Guild: spam (5 minute(s))\nAppeal: example.com")


class AutoModHelperTests(unittest.TestCase):
    def setUp(self):
        if not HAS_RED:
            self.skipTest("needs Red-DiscordBot installed")
        self.automod = load("moderation.automod")

    def test_allowed_domains(self):
        domains = self.automod.parse_domains("https://www.YouTube.com/, github.com  example.org/path")
        self.assertEqual(domains, ("youtube.com", "github.com", "example.org"))
        count = self.automod.AutoModMixin._count_links
        text = "https://youtube.com/a http://m.github.com/x https://evilyoutube.com www.other.net"
        self.assertEqual(count(text), 4)
        self.assertEqual(count(text, domains), 2)

    def test_filter_exemptions(self):
        exempt = self.automod.AutoModMixin._filter_exempt
        channel = SimpleNamespace(id=1, parent_id=None, category=SimpleNamespace(id=2), parent=None)
        message = SimpleNamespace(channel=channel, author=SimpleNamespace(roles=[SimpleNamespace(id=7)]))
        self.assertFalse(exempt(message, {}, "link"))
        self.assertTrue(exempt(message, {"link_exempt_channel_ids": [2]}, "link"))
        self.assertTrue(exempt(message, {"caps_exempt_role_ids": [7]}, "caps"))
        self.assertFalse(exempt(message, {"caps_exempt_role_ids": [7]}, "link"))

    def test_honeypot_dm_text(self):
        honeypot = load("moderation.honeypot")
        self.assertEqual(honeypot.honeypot_dm_text("", "G", "timeout"), "You posted in a trap channel in G and were timed out.")
        self.assertEqual(honeypot.honeypot_dm_text("{bad", "G", "ban"), "You posted in a trap channel in G and were banned.")
        self.assertIsNone(honeypot.honeypot_dm_error("Bye {server}"))


if __name__ == "__main__":
    unittest.main()
