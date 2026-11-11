"""Fine-grained /control settings for the Utility cog: schema limits, round-trips and behaviour."""

import copy
import importlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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

    def __getattr__(self, key):
        return FakeValue(self.store, key)

    async def all(self):
        return dict(self.store)


class FakeConfig:
    def __init__(self, store):
        self.store = store

    def guild(self, guild):
        return FakeGroup(self.store)


def fake_ctx(channel_id=5, category_id=None, guild=True):
    ctx = MagicMock()
    ctx.guild = MagicMock(id=1) if guild else None
    ctx.author = MagicMock(id=10)
    ctx.channel = MagicMock(id=channel_id, parent_id=None, category_id=category_id)
    ctx.send = AsyncMock()
    ctx.interaction = None
    return ctx


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class UtilityPanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import discord

        self.discord = discord
        self.utility = load("utility")
        self.reminders = load("reminders")
        self.sixseven = load("sixseven")
        cls = self.utility.Utility
        self.cog = cls.__new__(cls)
        self.cog.bot = MagicMock()
        self.cog.bot.cog_disabled_in_guild = AsyncMock(return_value=False)
        self.features = copy.deepcopy(self.utility.FEATURE_DEFAULTS)
        self.reminder_store = copy.deepcopy(self.reminders.REMINDER_GUILD_DEFAULTS)
        self.sixseven_store = {
            "enabled": True, "cooldown": 30, "response": "nope", "default_language": "en",
            "channel_languages": {}, "category_languages": {}, "chance": 100, "per_channel_cooldown": False,
            "ignored_channels": [], "ignored_roles": [], "mention_author": False,
        }
        self.cog.utility_config = FakeConfig(self.features)
        self.cog.reminders_config = FakeConfig(self.reminder_store)
        self.cog.sixseven_config = FakeConfig(self.sixseven_store)
        self.cog._feature_last_used = {}
        self.cog._sixseven_last_reply = {}
        self.settings = {s["key"]: s for s in self.cog.control_panel_schema()["settings"]}

    def test_schema_limits(self):
        from control.schema import parse_schema

        schema = parse_schema(self.cog.control_panel_schema())
        self.assertLessEqual(len(schema.sections), 25)
        for section in schema.sections:
            self.assertLessEqual(len(schema.in_section(section)), 25, section)
        for setting in schema.settings:
            self.assertTrue(setting.getter and setting.setter, setting.key)
            self.assertLessEqual(len(setting.label), 100, setting.key)
            self.assertLessEqual(len(setting.help), 100, setting.key)

    async def roundtrip(self, key, value):
        await self.settings[key]["set"](None, value)
        self.assertEqual(await self.settings[key]["get"](None), value)

    async def test_round_trips(self):
        await self.roundtrip("urban_enabled", False)
        await self.roundtrip("urban_channels", [3, 4])
        await self.roundtrip("wiki_cooldown", 0)
        await self.roundtrip("convert_digits", 4)
        await self.roundtrip("reminder_max_days", 30)
        await self.roundtrip("reminder_delivery", "channel")
        await self.roundtrip("sixseven_chance", 25)
        await self.roundtrip("sixseven_ignored_roles", [9])
        self.assertEqual(self.features["urban_channels"], [3, 4])
        self.assertEqual(self.reminder_store["reminder_max_days"], 30)
        self.assertEqual(self.sixseven_store["chance"], 25)

    async def test_feature_gate(self):
        error, _ = await self.cog._feature_gate(fake_ctx(), "convert")
        self.assertIsNone(error)
        self.features["convert_enabled"] = False
        error, _ = await self.cog._feature_gate(fake_ctx(), "convert")
        self.assertIn("turned off", error)
        error, _ = await self.cog._feature_gate(fake_ctx(guild=False), "convert")
        self.assertIsNone(error)
        self.features.update(convert_enabled=True, convert_channels=[7])
        error, _ = await self.cog._feature_gate(fake_ctx(), "convert")
        self.assertIn("<#7>", error)
        error, _ = await self.cog._feature_gate(fake_ctx(category_id=7), "convert")
        self.assertIsNone(error)
        error, _ = await self.cog._feature_gate(fake_ctx(), "wiki")
        self.assertIsNone(error)
        error, _ = await self.cog._feature_gate(fake_ctx(), "wiki")
        self.assertIn("Slow down", error)

    async def test_converter_uses_digits(self):
        self.features["convert_digits"] = 3
        ctx = fake_ctx()
        await self.utility.Utility.convert_command.callback(self.cog, ctx, query="1 mi to km")
        self.assertIn("1.61 km", str(ctx.send.await_args.kwargs["view"].to_components()))

    async def test_urban_nsfw_only_and_examples(self):
        urban = load("urban")
        self.features["urban_nsfw_only"] = True
        ctx = fake_ctx()
        ctx.channel.is_nsfw = MagicMock(return_value=False)
        await self.utility.Utility.urban.callback(self.cog, ctx, term="x")
        self.assertIn("age-restricted", ctx.send.await_args.args[0])
        entry = {"word": "w", "definition": "d", "example": "EXAMPLE"}
        self.assertNotIn("EXAMPLE", urban.definition_embed(entry, show_example=False).description)
        self.assertIn("EXAMPLE", urban.definition_embed(entry).description)

    async def test_reminder_limits(self):
        discord = self.discord
        ctx = fake_ctx()
        ctx.author = MagicMock(spec=discord.Member, id=10)
        ctx.author.guild = MagicMock(id=1)
        target = MagicMock(spec=discord.Member, id=11)
        target.guild_permissions.administrator = False
        target.roles = []
        target.guild = ctx.author.guild
        self.cog._is_reminder_unbounded = AsyncMock(return_value=False)
        self.cog._get_active_reminders = AsyncMock(return_value=[])

        self.assertIsNone(await self.cog._reminder_error(ctx, target, "1d", "hi"))
        self.reminder_store["reminder_max_days"] = 1
        self.assertIn("1 day", await self.cog._reminder_error(ctx, target, "2d", "hi"))
        self.reminder_store["reminder_min_seconds"] = 600
        self.assertIn("10 minutes", await self.cog._reminder_error(ctx, target, "5m", "hi"))
        self.reminder_store["reminder_max_length"] = 10
        self.assertIn("10 characters", await self.cog._reminder_error(ctx, target, "1h", "x" * 11))
        self.reminder_store["reminder_allow_others"] = False
        self.assertIn("Only staff", await self.cog._reminder_error(ctx, target, "1h", "hi"))
        self.assertIsNone(await self.cog._reminder_error(ctx, ctx.author, "1h", "hi"))
        self.reminder_store["reminder_creator_limit"] = 1
        self.cog._get_active_reminders.return_value = [("1", {})]
        self.assertIn("Limit is 1", await self.cog._reminder_error(ctx, ctx.author, "1h", "hi"))
        self.reminder_store["reminders_enabled"] = False
        self.assertIn("turned off", await self.cog._reminder_error(ctx, ctx.author, "1h", "hi"))

    async def test_reminder_delivery_order(self):
        target = MagicMock()
        target.send = AsyncMock()
        channel = MagicMock(spec=self.discord.TextChannel)
        channel.send = AsyncMock()
        self.cog.bot.get_user.return_value = target
        self.cog.bot.get_channel.return_value = channel
        reminder = {"guild_id": 1, "channel_id": 2, "target_id": 3, "creator_id": 3,
                    "message": "m", "created_at": 0, "due_at": 60}
        await self.cog._deliver_reminder(reminder)
        target.send.assert_awaited_once()
        channel.send.assert_not_awaited()
        self.reminder_store["reminder_delivery"] = "channel"
        target.send.reset_mock()
        await self.cog._deliver_reminder(reminder)
        channel.send.assert_awaited_once()
        target.send.assert_not_awaited()

    def sixseven_message(self, channel_id=5, roles=()):
        message = MagicMock()
        message.author.bot = False
        message.author.roles = list(roles)
        message.guild = MagicMock(id=1)
        message.channel = MagicMock(id=channel_id, parent_id=None, category_id=None)
        message.content = "six seven"
        message.reply = AsyncMock()
        return message

    async def test_sixseven_filters(self):
        listener = self.cog.on_message_without_command
        self.sixseven_store.update(ignored_channels=[5])
        message = self.sixseven_message()
        await listener(message)
        message.reply.assert_not_awaited()
        self.sixseven_store.update(ignored_channels=[], ignored_roles=[8])
        message = self.sixseven_message(roles=[MagicMock(id=8)])
        await listener(message)
        message.reply.assert_not_awaited()
        self.sixseven_store.update(ignored_roles=[], chance=50)
        with patch.object(self.sixseven.random, "randint", return_value=90):
            message = self.sixseven_message()
            await listener(message)
            message.reply.assert_not_awaited()

    async def test_sixseven_per_channel_cooldown_and_ping(self):
        listener = self.cog.on_message_without_command
        self.sixseven_store.update(per_channel_cooldown=True, mention_author=True)
        first, second, other = self.sixseven_message(5), self.sixseven_message(5), self.sixseven_message(6)
        await listener(first)
        await listener(second)
        await listener(other)
        first.reply.assert_awaited_once()
        self.assertTrue(first.reply.await_args.kwargs["mention_author"])
        second.reply.assert_not_awaited()
        other.reply.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
