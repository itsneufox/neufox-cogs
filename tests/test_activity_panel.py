"""Control panel settings of the Activity cog: schema limits, Config round trips and the new XP switches."""

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
class ActivityPanelTests(unittest.IsolatedAsyncioTestCase):
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
        self.cog = load("activity.activity").Activity(MagicMock())
        self.schema = self.schema_module.parse_schema(self.cog.control_panel_schema())
        self.guild = SimpleNamespace(id=next(_GUILD_IDS), name="Guild")

    async def asyncTearDown(self):
        await drivers.get_driver_class().teardown()

    def test_schema_limits(self):
        limits = self.schema_module
        for section in self.schema.sections:
            self.assertLessEqual(len(self.schema.in_section(section)), limits.MAX_SECTION_SETTINGS, section)
        for setting in self.schema.settings:
            self.assertLessEqual(len(setting.label), 100, setting.key)
            self.assertLessEqual(len(setting.help), 100, setting.key)
        self.assertEqual(self.schema.sections, ["Tracking", "XP", "Stats", "Rewards"])

    async def test_defaults_match_old_behaviour(self):
        expected = {
            "levelup_message": load("activity.xp").DEFAULT_LEVELUP_MESSAGE,
            "levelup_dm": False,
            "levelup_ping": False,
            "level_roles_stack": True,
            "voice_xp_while_muted": False,
            "bonus_xp_channel_ids": [],
            "bonus_xp_role_ids": [],
            "top_role_sync_interval": 300,
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(await self.panel.read_value(self.cog, self.schema.setting(key), self.guild), value)

    async def test_round_trips(self):
        samples = {
            "levelup_message": "{member} hit {kind} {level} in {server}{cash}",
            "levelup_dm": True,
            "level_roles_stack": False,
            "voice_xp_while_muted": True,
            "bonus_xp_channel_ids": ["10", "11"],
            "bonus_channel_multiplier": 3.0,
            "bonus_xp_role_ids": ["12"],
            "bonus_role_multiplier": 0.5,
            "top_role_sync_interval": 600,
        }
        for key, value in samples.items():
            with self.subTest(key=key):
                setting = self.schema.setting(key)
                await self.panel.write_value(self.cog, setting, self.guild, value)
                self.assertEqual(await self.panel.read_value(self.cog, setting, self.guild), value)
        self.assertEqual(await self.cog.stats_config.guild(self.guild).top_role_sync_interval(), 600)

    async def test_bad_levelup_message_is_rejected(self):
        with self.assertRaises(self.schema_module.SettingError):
            await self.panel.write_value(self.cog, self.schema.setting("levelup_message"), self.guild, "{who}")

    async def test_bonus_xp_applies(self):
        conf = self.cog.config.guild(self.guild)
        await conf.bonus_xp_role_ids.set(["5"])
        await conf.bonus_xp_channel_ids.set(["9"])
        member = SimpleNamespace(roles=[SimpleNamespace(id=5)])
        channel = SimpleNamespace(id=9, parent_id=None, category=None, parent=None)
        other = SimpleNamespace(id=8, parent_id=None, category=None, parent=None)
        self.assertEqual(await self.cog._with_bonus(self.guild, member, channel, 10), 30)  # 1.5 x 2.0
        self.assertEqual(await self.cog._with_bonus(self.guild, member, other, 10), 15)
        self.assertEqual(await self.cog._with_bonus(self.guild, SimpleNamespace(roles=[]), None, 10), 10)

    async def test_level_roles_replace_mode_keeps_only_highest(self):
        low, high = MagicMock(id=1), MagicMock(id=2)
        for role in (low, high):
            role.is_default.return_value = False
            role.managed = False
            role.__lt__ = lambda self, other: True
        await self.cog.config.guild(self.guild).level_roles.set({"chat": {"5": "1", "10": "2"}, "voice": {}})
        await self.cog.config.guild(self.guild).level_roles_stack.set(False)
        me = MagicMock()
        me.guild_permissions.manage_roles = True
        me.top_role.__le__ = lambda self, other: False
        guild = SimpleNamespace(
            id=self.guild.id, owner_id=0, me=me, get_role=lambda role_id: {1: low, 2: high}.get(role_id)
        )
        member = MagicMock(id=3, roles=[low], add_roles=AsyncMock(), remove_roles=AsyncMock())
        await self.cog._apply_level_roles(guild, member, "chat", 12)
        member.add_roles.assert_awaited_once()
        self.assertIs(member.add_roles.await_args.args[0], high)
        member.remove_roles.assert_awaited_once()
        self.assertIs(member.remove_roles.await_args.args[0], low)

    async def test_levelup_message_uses_template_and_dm(self):
        conf = self.cog.config.guild(self.guild)
        await conf.levelup_message.set("{name} is now {kind} {level}{cash}")
        await conf.levelup_dm.set(True)
        await conf.announce_interval.set(1)
        member = MagicMock(id=3, display_name="Fox", mention="<@3>", send=AsyncMock())
        self.guild.name = "Guild"
        await self.cog._announce_level_up(self.guild, member, "voice", 1, 2, 0, None)
        member.send.assert_awaited_once()
        self.assertIn("Fox is now voice 2", str(member.send.await_args.kwargs["view"].to_components()))


class LevelUpTextTests(unittest.TestCase):
    def test_bad_template_falls_back(self):
        if not HAS_RED:
            self.skipTest("needs Red-DiscordBot installed")
        xp = load("activity.xp")
        values = dict(member="<@1>", kind="chat", level=5, cash="")
        self.assertEqual(xp.format_levelup_message("{oops", **values), "🎉 <@1> reached chat level **5**!")
        self.assertIsNone(xp.levelup_message_error("{member} {level}"))


if __name__ == "__main__":
    unittest.main()
