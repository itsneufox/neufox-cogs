import asyncio
import importlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock


ROOT = Path(__file__).parents[1]
HAS_RED = importlib.util.find_spec("discord") is not None and importlib.util.find_spec("redbot") is not None


def load(module):
    sys.path.insert(0, str(ROOT))
    return importlib.import_module(f"media.{module}")


def fake_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.defer = AsyncMock()
    ctx.clean_prefix = "!"
    ctx.interaction = None
    return ctx


class FakeValue:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def __call__(self):
        async def read():
            return self.store[self.key]

        return read()

    async def set(self, value):
        self.store[self.key] = value

    async def clear(self):
        self.store[self.key] = None


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
    def __init__(self, **data):
        self.data = data

    def guild(self, guild):
        return FakeGroup(self.data)

    member = guild


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class MediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.media = load("media")
        self.cog = self.media.Media.__new__(self.media.Media)
        self.cog.bot = MagicMock()
        self.cog.bot.cog_disabled_in_guild = AsyncMock(return_value=False)
        self.cog.radio_config = FakeConfig(
            enabled=True, stream_url="https://radio.example/stream", voice_channel_id=None, reconnect=True,
            status_channel_id=5, status_message_id=9, status_api_url=None, voice_status_enabled=True, auto_play=True,
        )
        self.cog.musicboard_config = FakeConfig(
            enabled=True, music_channel_id=None, posted_message_ids=[1, 2], react_timeout_seconds=600
        )
        self.cog.embedfixer_config = FakeConfig(enabled=True, youtube=False, translate_to=None, opted_out=False)
        self.cog._radio_sync_tasks = {}

    def sent_view(self, ctx):
        kwargs = ctx.send.await_args.kwargs
        self.assertNotIn("embed", kwargs)
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        view = kwargs["view"]
        self.assertTrue(view.to_components())
        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 4000)
        return view

    def test_one_slash_group(self):
        group = self.media.Media.media.app_command
        self.assertEqual({c.name for c in group.commands}, {"status", "play", "stop", "fixlinks", "clearboard"})
        self.assertTrue(group.guild_only)
        top = [c for c in self.media.Media.__cog_commands__ if c.parent is None]
        self.assertEqual([c.name for c in top], ["media"])
        self.assertTrue(self.media.Media.media_clearboard.requires.user_perms.manage_guild)

    def test_listeners_are_separate(self):
        listeners = dict((method, event) for event, method in self.media.Media.__cog_listeners__)
        self.assertEqual(
            listeners,
            {
                "_embedfixer_on_message": "on_message_without_command",
                "_musicboard_on_message": "on_message",
                "_musicboard_on_reaction": "on_raw_reaction_add",
                "_radio_on_voice_state": "on_voice_state_update",
            },
        )

    def test_config_reuses_old_namespaces(self):
        source = (ROOT / "media").glob("*.py")
        text = "\n".join(path.read_text() for path in source)
        for name, ident in (("Radio", 938475610), ("MusicBoard", 728463910), ("EmbedFixer", 728466531)):
            self.assertIn(f'cog_name="{name}"', text)
            self.assertIn(str(ident), text)

    def test_panel_schema_is_valid(self):
        sys.path.insert(0, str(ROOT))
        schema_module = importlib.import_module("control.schema")
        schema = schema_module.parse_schema(self.cog.control_panel_schema())
        self.assertEqual(schema.sections, ["Radio", "MusicBoard", "EmbedFixer"])
        for setting in schema.settings:
            self.assertIsNotNone(setting.getter, setting.key)
            self.assertIsNotNone(setting.setter, setting.key)

    async def test_panel_reads_and_writes_each_feature(self):
        settings = {s["key"]: s for s in self.cog.control_panel_schema()["settings"]}
        guild = MagicMock()
        self.assertEqual(await settings["musicboard.react_timeout"]["get"](guild), 10)
        await settings["musicboard.react_timeout"]["set"](guild, 0)
        self.assertEqual(self.cog.musicboard_config.data["react_timeout_seconds"], 0)
        await settings["embedfixer.enabled"]["set"](guild, False)
        self.assertFalse(self.cog.embedfixer_config.data["enabled"])
        await settings["radio.status_channel_id"]["set"](guild, 7)
        self.assertIsNone(self.cog.radio_config.data["status_message_id"])
        self.assertIsNotNone(settings["radio.stream_url"]["validate"](guild, "ftp://x"))
        self.assertIsNone(settings["radio.status_api_url"]["validate"](guild, None))
        translate = settings["embedfixer.translate_to"]
        self.assertIsNotNone(translate["validate"](guild, "klingon"))
        self.assertIsNone(translate["validate"](guild, "Portuguese"))
        await translate["set"](guild, "Portuguese")
        self.assertEqual(self.cog.embedfixer_config.data["translate_to"], "pt")
        self.assertEqual(await translate["get"](guild), "Portuguese (pt)")
        await translate["set"](guild, None)
        self.assertIsNone(await translate["get"](guild))

    async def test_status_renders(self):
        ctx = fake_ctx()
        ctx.guild.get_channel.return_value = None
        ctx.guild.voice_client = None
        self.cog.radio_config.data["stream_url"] = "https://x/" + "y" * 3990
        await self.media.Media.media.callback(self.cog, ctx)
        self.sent_view(ctx)

    async def test_member_commands_render(self):
        ctx = fake_ctx()
        await self.media.Media.media_fixlinks.callback(self.cog, ctx, False)
        self.assertTrue(self.cog.embedfixer_config.data["opted_out"])
        self.sent_view(ctx)

        ctx = fake_ctx()
        await self.media.Media.media_clearboard.callback(self.cog, ctx)
        self.assertEqual(self.cog.musicboard_config.data["posted_message_ids"], [])
        self.sent_view(ctx)

        ctx = fake_ctx()
        ctx.guild.voice_client = None
        await self.media.Media.media_stop.callback(self.cog, ctx)
        self.sent_view(ctx)

    async def test_play_respects_feature_toggle(self):
        self.cog.radio_config.data["enabled"] = False
        message = await self.cog._radio_play(MagicMock(), MagicMock(), None)
        self.assertIn("turned off", message)

    async def test_musicboard_ignores_messages_when_disabled(self):
        self.cog.musicboard_config.data.update(enabled=False, music_channel_id=5)
        message = MagicMock()
        message.author.bot = False
        message.content = "https://youtu.be/abc"
        message.add_reaction = AsyncMock()
        await self.cog._musicboard_on_message(message)
        message.add_reaction.assert_not_awaited()

    async def test_embedfixer_respects_toggle_and_opt_out(self):
        discord = load("embedfixer").discord
        message = MagicMock()
        message.author = MagicMock(spec=discord.Member)
        message.author.bot = False
        message.webhook_id = None
        message.content = "https://x.com/user/status/123"
        self.cog._embedfixer_repost = AsyncMock(return_value=True)
        self.cog._embedfixer_suppress = AsyncMock()
        self.cog.embedfixer_config.data["opted_out"] = True
        await self.cog._embedfixer_on_message(message)
        self.cog._embedfixer_repost.assert_not_awaited()
        self.cog.embedfixer_config.data["opted_out"] = False
        await self.cog._embedfixer_on_message(message)
        self.assertEqual(self.cog._embedfixer_repost.await_args.args[1], "https://fixupx.com/user/status/123")
        self.cog.embedfixer_config.data["translate_to"] = "pt"
        await self.cog._embedfixer_on_message(message)
        self.assertEqual(self.cog._embedfixer_repost.await_args.args[1], "https://fixupx.com/user/status/123/pt")
        self.assertEqual(self.cog._embedfixer_repost.await_args.kwargs["view"].translate_to, "pt")


class FixerTests(unittest.TestCase):
    @unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
    def test_rewrite_and_rotation(self):
        fixers = load("fixers")
        matches = fixers.find_matches("look https://twitter.com/a/status/1?s=20 and $https://x.com/b/status/2")
        self.assertEqual(len(matches), 1)
        self.assertEqual(fixers.rewrite(matches, {}), "https://fxtwitter.com/a/status/1")
        self.assertEqual(fixers.rewrite(matches, {1: 1}), "https://vxtwitter.com/a/status/1")
        self.assertTrue(fixers.has_alternatives(matches))
        self.assertEqual(fixers.find_matches("<https://x.com/b/status/2>"), [])

    @unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
    def test_upstream_sites(self):
        fixers = load("fixers")

        def fix(url, opted_in=frozenset()):
            matches = fixers.find_matches(url, opted_in)
            return fixers.rewrite(matches, {}) if matches else None

        self.assertEqual(fix("https://www.instagram.com/p/abc/"), "https://oginstagram.com/p/abc/")
        story = fixers.find_matches("https://instagram.com/stories/some.user/123/")
        self.assertEqual([m.name for m in fixers.available_methods(story[0])], ["OGInstagram"])
        self.assertEqual(fix("https://www.tiktok.com/@a.b/photo/1"), "https://tnktok.com/@a.b/photo/1")
        self.assertEqual(fix("https://www.bilibili.com/opus/42"), "https://vxbilibili.com/opus/42")
        self.assertEqual(fix("https://pinterest.com/pin/9/"), "https://pinterestez.com/pin/9/")
        self.assertEqual(fix("https://threads.com/share/xyz"), "https://fixthreads.seria.moe/share/xyz")
        self.assertIsNotNone(fix("https://web.facebook.com/reel/5"))
        market = fixers.find_matches("https://facebook.com/marketplace/item/1")
        self.assertEqual([m.name for m in fixers.available_methods(market[0])], ["fxfacebook"])
        self.assertNotIn("EmbedEZ", [m.name for d in fixers.DOMAINS if d.name == "Threads" for m in d.fix_methods])
        self.assertIsNone(fix("https://youtu.be/abc"))
        self.assertEqual(fix("https://youtu.be/abc", frozenset({fixers.DomainId.YOUTUBE})), "https://koutu.be/abc")
        tweet = fixers.find_matches("https://twitter.com/a/status/1/")
        self.assertEqual(fixers.rewrite(tweet, {}, "ja"), "https://fxtwitter.com/a/status/1/ja")
        self.assertEqual(fixers.rewrite(tweet, {1: 1}, "ja"), "https://vxtwitter.com/a/status/1/")
        sky = fixers.find_matches("https://bsky.app/profile/a.b/post/x")
        self.assertEqual(fixers.rewrite(sky, {}, "ja"), "https://bskx.app/profile/a.b/post/x")
        self.assertEqual(fixers.normalize_language(" PT "), "pt")
        self.assertEqual(fixers.normalize_language("japanese"), "ja")
        self.assertIsNone(fixers.normalize_language("xx"))
        ids = [(d.id, m.id) for d in fixers.DOMAINS for m in d.fix_methods]
        self.assertEqual(len(ids), len(set(ids)))

    @unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
    def test_music_and_radio_helpers(self):
        board = load("musicboard")
        self.assertEqual(board.extract_music_url("hi https://youtu.be/abc-1 x"), "https://youtu.be/abc-1")
        self.assertIsNone(board.extract_music_url("https://example.com"))
        self.assertEqual(board.format_timeout(0), "never")
        radio = load("radio")
        self.assertEqual(radio.current_show_name(23), "Night Drive")
        self.assertEqual(radio.current_show_name(7), "Early Haul")
        self.assertEqual(radio.status_api_url({"stream_url": "https://r.example:8000/live"}), "https://r.example:8000/status-json.xsl")


if __name__ == "__main__":
    unittest.main()
