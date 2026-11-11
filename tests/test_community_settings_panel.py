"""Control panel settings of TicketTool, VoiceChannels, BanAppeals, MessageArchive and ProjectRanking."""

import importlib
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock


ROOT = Path(__file__).parents[1]

try:
    importlib.import_module("discord")
    importlib.import_module("chat_exporter")
    from redbot.core import _drivers as drivers
    from redbot.core import data_manager

    HAS_RED = True
except ImportError:
    HAS_RED = False


COGS = (
    ("tickettool.tickettool", "TicketTool"),
    ("voicechannels.voicechannels", "VoiceChannels"),
    ("banappeals.banappeals", "BanAppeals"),
    ("messagearchive.messagearchive", "MessageArchive"),
    ("projectranking.projectranking", "ProjectRanking"),
)


def fake_bot():
    bot = MagicMock()
    bot.loop.create_task = lambda coro: (coro.close(), MagicMock())[1]
    return bot


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot and the cogs' requirements installed")
class CommunitySettingsPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data_manager.basic_config = {
            "DATA_PATH": self.tmp.name,
            "STORAGE_TYPE": "JSON",
            "STORAGE_DETAILS": {},
            "COG_PATH_APPEND": "cogs",
            "CORE_PATH_APPEND": "core",
        }
        await drivers.get_driver_class().initialize(**data_manager.storage_details())
        sys.path.insert(0, str(ROOT))
        self.schema = importlib.import_module("control.schema")
        self.panel = importlib.import_module("control.panel")
        from redbot.core import Config

        self.cogs = {}
        for module, name in COGS:
            cog = getattr(importlib.import_module(module), name)(fake_bot())
            # A unique identifier per test gives a fresh JSON driver rooted in this test's directory.
            fresh = Config.get_conf(None, identifier=hash((self.id(), name)) & 0xFFFFFFF, cog_name=f"{name}PanelTest")
            fresh.register_global(**cog.config._defaults.get(Config.GLOBAL, {}))
            fresh.register_guild(**cog.config._defaults.get(Config.GUILD, {}))
            cog.config = fresh
            self.cogs[name] = cog
        self.guild = MagicMock()
        self.guild.id = int(time.time_ns() % 10**12)
        self.guild.get_channel.return_value = None

    async def asyncTearDown(self):
        await drivers.get_driver_class().teardown()
        self.tmp.cleanup()

    def parsed(self, name):
        return self.schema.parse_schema(self.cogs[name].control_panel_schema())

    async def get(self, name, key):
        cog = self.cogs[name]
        return await self.panel.read_value(cog, self.parsed(name).setting(key), self.guild)

    async def set(self, name, key, value):
        cog = self.cogs[name]
        await self.panel.write_value(cog, self.parsed(name).setting(key), self.guild, value)

    async def round_trip(self, name, key, value, expected=None):
        await self.set(name, key, value)
        self.assertEqual(await self.get(name, key), value if expected is None else expected, key)

    def test_schemas_parse_within_limits(self):
        for _module, name in COGS:
            with self.subTest(cog=name):
                raw = self.cogs[name].control_panel_schema()
                parsed = self.schema.parse_schema(raw)
                self.assertLessEqual(len(parsed.sections), 25)
                for section in parsed.sections:
                    self.assertLessEqual(len(parsed.in_section(section)), 25, section)
                    self.assertLessEqual(len(section), 100)
                for setting in parsed.settings:
                    self.assertLessEqual(len(setting.label), 100, setting.key)
                    self.assertLessEqual(len(setting.help), 100, setting.key)
                    self.assertLessEqual(len(setting.choices), 25, setting.key)

    async def test_every_setting_reads_its_default(self):
        for _module, name in COGS:
            for setting in self.parsed(name).settings:
                with self.subTest(cog=name, key=setting.key):
                    await self.panel.read_value(self.cogs[name], setting, self.guild)

    async def test_voicechannels_round_trip(self):
        self.assertEqual(await self.get("VoiceChannels", "empty_delete_delay"), 120)
        await self.round_trip("VoiceChannels", "bitrate_kbps", 96)
        await self.round_trip("VoiceChannels", "empty_delete_delay", 300)
        await self.round_trip("VoiceChannels", "creator_role_ids", [5, 6])
        await self.round_trip("VoiceChannels", "invite_dms", False)
        voice = importlib.import_module("voicechannels.voicechannels")
        self.assertEqual(voice._duration_label(120), "2 minutes")
        self.assertEqual(voice._duration_label(3600), "1 hour")
        self.assertEqual(voice._duration_label(45), "45 seconds")
        self.assertIn("2 minutes", voice.VoiceChannels._dashboard_text())
        guild = SimpleNamespace(bitrate_limit=96000)
        self.assertIsNone(voice.VoiceChannels._bitrate_for(guild, 0))
        self.assertEqual(voice.VoiceChannels._bitrate_for(guild, 384), 96000)

    async def test_messagearchive_round_trip_and_filters(self):
        await self.round_trip("MessageArchive", "retention_days", 30)
        await self.round_trip("MessageArchive", "ignored_channel_ids", [11])
        await self.round_trip("MessageArchive", "archive_bots", False)
        cog = self.cogs["MessageArchive"]
        settings = await cog.config.guild(self.guild).all()
        human = SimpleNamespace(bot=False, roles=[])
        message = lambda channel, author=human, webhook=None: SimpleNamespace(channel=channel, author=author, webhook_id=webhook)
        self.assertTrue(cog._should_archive(message(SimpleNamespace(id=1, category_id=None)), settings))
        self.assertFalse(cog._should_archive(message(SimpleNamespace(id=11)), settings))
        self.assertFalse(cog._should_archive(message(SimpleNamespace(id=2, parent_id=11)), settings))
        self.assertFalse(cog._should_archive(message(SimpleNamespace(id=1), SimpleNamespace(bot=True, roles=[])), settings))

        path = Path(self.tmp.name) / "archive.db"
        cog._initialize_db(path)
        now = int(time.time())
        for message_id, created in ((1, now - 40 * 86400), (2, now)):
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "INSERT INTO messages (message_id, guild_id, channel_id, author_id, created_at, is_webhook, is_bot, created_in_seconds)"
                    " VALUES (?, 1, 1, 1, ?, 0, 0, ?)",
                    (message_id, created, now),
                )
        self.assertEqual(cog._delete_older_than(path, now - 30 * 86400), 1)
        self.assertEqual(cog._db_stats(path)[0], 1)

    async def test_banappeals_round_trip_and_checks(self):
        await self.round_trip("BanAppeals", "min_why_words", 3)
        await self.round_trip("BanAppeals", "fake_evidence_multiplier", 3)
        await self.round_trip("BanAppeals", "challenge_phrase", "I will play fair")
        self.assertTrue(await self.get("BanAppeals", "required.ban_id"))
        await self.round_trip("BanAppeals", "required.ban_id", False)
        cog = self.cogs["BanAppeals"]
        self.assertEqual(await cog.config.guild(self.guild).optional_fields(), ["ban_id"])

        await self.set("BanAppeals", "api_bearer_token", "secret")
        self.assertEqual(await cog.config.guild(self.guild).api_bearer_token(), "secret")
        mask = await self.get("BanAppeals", "api_bearer_token")
        self.assertNotIn("secret", mask)
        await self.set("BanAppeals", "api_bearer_token", mask)
        self.assertEqual(await cog.config.guild(self.guild).api_bearer_token(), "secret")
        with self.assertRaises(self.schema.SettingError):
            await self.set("BanAppeals", "api_url_template", "https://example.com/nope")

        checks = cog._checks(await cog.config.guild(self.guild).all())
        fields = {"ingame_name": "a", "ban_date": "x", "ban_reason": "b", "admin": "c", "why": "it was my brother"}
        warnings = cog._validate_fields(fields, checks)
        self.assertFalse(any("Missing" in w for w in warnings))
        self.assertFalse(any("detail" in w for w in warnings))
        self.assertTrue(any("Missing" in w for w in cog._validate_fields(fields)))
        self.assertIn("I will play fair", cog._challenge_message({}, 5, SimpleNamespace(mention="@m"), phrase="I will play fair"))

    async def test_projectranking_round_trip_and_week_offset(self):
        await self.round_trip("ProjectRanking", "winners_per_week", 3)
        await self.round_trip("ProjectRanking", "week_start_day", 6)
        await self.round_trip("ProjectRanking", "week_start_hour", 18)
        await self.round_trip("ProjectRanking", "announcement_title", "Project of the Week")
        with self.assertRaises(self.schema.SettingError):
            await self.set("ProjectRanking", "favorite_tag_id", "Winner")

        ranking = importlib.import_module("projectranking.projectranking")
        offset = ranking._week_offset({"week_start_day": 6, "week_start_hour": 18})
        sunday_evening = datetime(2026, 11, 8, 19, tzinfo=timezone.utc)
        sunday_noon = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        monday = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        # Default weeks still run Monday to Sunday.
        self.assertEqual(ranking._week_key(sunday_noon), ranking._week_key(sunday_evening))
        self.assertNotEqual(ranking._week_key(sunday_evening), ranking._week_key(monday))
        # With a Sunday 18:00 start, Sunday evening already belongs to the next week.
        self.assertNotEqual(ranking._week_key(sunday_noon, offset), ranking._week_key(sunday_evening, offset))
        self.assertEqual(ranking._week_key(sunday_evening, offset), ranking._week_key(monday, offset))
        self.assertTrue(ranking._week_range(ranking._week_key(monday, offset), offset).startswith("08 Nov 18:00"))
        self.assertEqual(ranking._week_range("2026-W45"), ranking._week_range("2026-W45", 0))

    async def test_tickettool_profile_settings(self):
        cog = self.cogs["TicketTool"]
        with self.assertRaises(self.schema.SettingError):
            await self.set("TicketTool", "profile.nb_max", 3)
        self.assertEqual(await self.get("TicketTool", "profile.nb_max"), 5)

        defaults = cog.config._defaults[cog.config.GUILD]["default_profile_settings"]
        await cog.config.guild(self.guild).profiles.set_raw("main", value=dict(defaults))
        await cog.config.guild(self.guild).profiles.set_raw("support", value=dict(defaults))
        self.assertEqual(await self.get("TicketTool", "panel_profile"), "main")
        await self.round_trip("TicketTool", "panel_profile", "Support", "support")
        with self.assertRaises(self.schema.SettingError):
            await self.set("TicketTool", "panel_profile", "missing")

        await self.round_trip("TicketTool", "profile.nb_max", 3)
        await self.round_trip("TicketTool", "profile.auto_close_hours", 48)
        await self.round_trip("TicketTool", "profile.transcript", False)
        await self.round_trip("TicketTool", "profile.support_roles", [7])
        await self.round_trip("TicketTool", "profile.color", "#ff0000", "#FF0000")
        await self.round_trip("TicketTool", "profile.embed_button.title", "Need help?")
        self.assertEqual(await cog.config.guild(self.guild).profiles.get_raw("support", "color"), 0xFF0000)
        self.assertEqual(await cog.config.guild(self.guild).profiles.get_raw("main", "nb_max"), 5)
        with self.assertRaises(self.schema.SettingError):
            await self.set("TicketTool", "profile.dynamic_channel_name", "{nope}")
        with self.assertRaises(self.schema.SettingError):
            await self.set("TicketTool", "profile.color", "blue")
        config = await cog.get_config(self.guild, "support")
        self.assertEqual(config["embed_button"]["title"], "Need help?")
        self.assertEqual(config["auto_close_hours"], 48)


if __name__ == "__main__":
    unittest.main()
