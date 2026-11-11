import asyncio
import copy
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


class FakeRead:
    """What ``value()`` returns: awaitable, and usable as ``async with`` for lists and dicts."""

    def __init__(self, store, key):
        self.store, self.key = store, key

    def __await__(self):
        async def read():
            return self.store[self.key]

        return read().__await__()

    async def __aenter__(self):
        return self.store[self.key]

    async def __aexit__(self, *exc):
        return False


class FakeValue:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def __call__(self):
        return FakeRead(self.store, self.key)

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

    def __getattr__(self, key):
        return FakeValue(self.data, key)


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
        self.assertEqual(
            schema.sections,
            ["Radio", "MusicBoard", "MusicBoard links", "EmbedFixer", "EmbedFixer sites", "EmbedFixer services"],
        )
        self.assertLessEqual(len(schema.sections), 25)
        for section in schema.sections:
            self.assertLessEqual(len(schema.in_section(section)), 25, section)
        for setting in schema.settings:
            self.assertIsNotNone(setting.getter, setting.key)
            self.assertIsNotNone(setting.setter, setting.key)
            self.assertLessEqual(len(setting.label), 100, setting.key)
            self.assertLessEqual(len(setting.help), 100, setting.key)
            for value, label in setting.choices:
                self.assertLessEqual(len(label), 100, setting.key)
        fixers = load("fixers")
        site_keys = {s.key for s in schema.in_section("EmbedFixer sites")}
        expected = {f"embedfixer.site.{int(d.id)}" for d in fixers.fixable_domains() if d.id != fixers.DomainId.YOUTUBE}
        self.assertEqual(site_keys, expected)
        twitter = schema.setting("embedfixer.service.1")
        self.assertEqual([value for value, _ in twitter.choices], [1, 2, 29, 1001, 1002])
        self.assertEqual(schema.setting("radio.status_poll_seconds").scope, "global")

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


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class MediaFineSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.media = load("media")
        self.embedfixer = load("embedfixer")
        self.musicboard = load("musicboard")
        self.radio = load("radio")
        self.cog = self.media.Media.__new__(self.media.Media)
        self.cog.bot = MagicMock()
        self.cog.bot.cog_disabled_in_guild = AsyncMock(return_value=False)
        self.cog.radio_config = FakeConfig(**copy.deepcopy(self.radio.RADIO_GUILD_DEFAULTS), status_poll_seconds=30)
        self.cog.musicboard_config = FakeConfig(**copy.deepcopy(self.musicboard.MUSICBOARD_GUILD_DEFAULTS))
        self.cog.embedfixer_config = FakeConfig(
            **copy.deepcopy(self.embedfixer.EMBEDFIXER_GUILD_DEFAULTS), opted_out=False
        )
        self.cog._radio_sync_tasks = {}
        self.settings = {s["key"]: s for s in self.cog.control_panel_schema()["settings"]}
        self.guild = MagicMock()

    async def roundtrip(self, key, value):
        await self.settings[key]["set"](self.guild, value)
        self.assertEqual(await self.settings[key]["get"](self.guild), value)

    async def test_new_settings_round_trip(self):
        fixer, board, radio = self.cog.embedfixer_config.data, self.cog.musicboard_config.data, self.cog.radio_config.data
        self.assertTrue(await self.settings["embedfixer.site.5"]["get"](self.guild))
        await self.roundtrip("embedfixer.site.5", False)
        self.assertEqual(fixer["disabled_sites"], [5])
        await self.roundtrip("embedfixer.site.5", True)
        self.assertEqual(fixer["disabled_sites"], [])
        self.assertEqual(await self.settings["embedfixer.service.1"]["get"](self.guild), 1)
        await self.roundtrip("embedfixer.service.1", 2)
        self.assertEqual(fixer["services"], {"1": 2})
        await self.roundtrip("embedfixer.original_action", "delete")
        await self.roundtrip("embedfixer.try_other_hours", 6)
        await self.roundtrip("musicboard.source.spotify", False)
        self.assertEqual(board["disabled_sources"], ["spotify"])
        await self.roundtrip("musicboard.votes_needed", 3)
        await self.roundtrip("radio.volume", 50)
        await self.roundtrip("radio.leave_delay", 60)
        await self.roundtrip("radio.status_poll_seconds", 90)
        self.assertEqual(radio["status_poll_seconds"], 90)

    def test_validators(self):
        guild = MagicMock(emojis=[MagicMock(id=123456789012345678)])
        emoji = self.settings["musicboard.nominate_emoji"]["validate"]
        self.assertIsNone(emoji(guild, "🎶"))
        self.assertIsNone(emoji(guild, "<:tune:123456789012345678>"))
        self.assertIsNotNone(emoji(guild, "<:tune:999999999999999999>"))
        self.assertIsNotNone(emoji(guild, "abc"))
        fmt = self.settings["musicboard.nomination_format"]["validate"]
        self.assertIsNone(fmt(guild, "{user} shared {url}"))
        self.assertIsNotNone(fmt(guild, "{user} {nope} {url}"))
        self.assertIsNotNone(fmt(guild, "{user} only"))
        voice = self.settings["radio.voice_status_format"]["validate"]
        self.assertIsNone(voice(guild, "{show} | {title} ({listeners})"))
        self.assertIsNotNone(voice(guild, "{artist}"))

    def message(self, content="https://x.com/user/status/123 hi"):
        discord = self.embedfixer.discord
        message = MagicMock()
        message.author = MagicMock(spec=discord.Member)
        message.author.bot = False
        message.author.roles = []
        message.author.mention = "<@1>"
        message.webhook_id = None
        message.content = content
        message.attachments = []
        message.stickers = []
        message.channel = MagicMock(id=5, parent_id=None, category_id=7)
        return message

    def stub_outputs(self):
        self.cog._embedfixer_repost = AsyncMock(return_value=True)
        self.cog._embedfixer_suppress = AsyncMock()
        self.cog._embedfixer_delete = AsyncMock(return_value=True)

    async def test_embedfixer_site_toggle(self):
        self.stub_outputs()
        await self.settings["embedfixer.site.1"]["set"](self.guild, False)
        await self.cog._embedfixer_on_message(self.message())
        self.cog._embedfixer_repost.assert_not_awaited()
        await self.cog._embedfixer_on_message(self.message("https://www.tiktok.com/@a.b/video/1"))
        self.assertEqual(self.cog._embedfixer_repost.await_args.args[1], "https://tnktok.com/@a.b/video/1")
        await self.settings["embedfixer.site.1"]["set"](self.guild, True)
        await self.cog._embedfixer_on_message(self.message())
        self.assertEqual(self.cog._embedfixer_repost.await_args.args[1], "https://fixupx.com/user/status/123")

    async def test_embedfixer_default_service_starts_rotation(self):
        self.stub_outputs()
        await self.settings["embedfixer.service.1"]["set"](self.guild, 2)
        await self.cog._embedfixer_on_message(self.message())
        self.assertEqual(self.cog._embedfixer_repost.await_args.args[1], "https://fixvx.com/user/status/123")
        view = self.cog._embedfixer_repost.await_args.kwargs["view"]
        interaction = MagicMock()
        interaction.response.edit_message = AsyncMock()
        await view.try_other.callback(interaction)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["content"], "https://xeezz.com/user/status/123")
        self.cog._embedfixer_suppress.assert_awaited()

    async def test_embedfixer_delete_keep_and_button(self):
        self.stub_outputs()
        data = self.cog.embedfixer_config.data
        data.update(original_action="delete", try_other=False)
        await self.cog._embedfixer_on_message(self.message("look https://x.com/user/status/123 hi"))
        call = self.cog._embedfixer_repost.await_args
        self.assertEqual(call.args[1], "look https://fixupx.com/user/status/123 hi")
        self.assertIsNone(call.kwargs["view"])
        self.cog._embedfixer_delete.assert_awaited_once()
        self.cog._embedfixer_suppress.assert_not_awaited()

        data.update(repost_mode="bot")
        await self.cog._embedfixer_on_message(self.message("look https://x.com/user/status/123 hi"))
        call = self.cog._embedfixer_repost.await_args
        self.assertTrue(call.args[1].startswith("<@1>: look https://fixupx.com"))
        self.assertEqual(call.kwargs["mode"], "bot")

        self.stub_outputs()
        data.update(original_action="keep", try_other=True)
        await self.cog._embedfixer_on_message(self.message())
        self.assertIsNotNone(self.cog._embedfixer_repost.await_args.kwargs["view"])
        self.cog._embedfixer_suppress.assert_not_awaited()
        self.cog._embedfixer_delete.assert_not_awaited()

    async def test_embedfixer_ignores_channels_and_roles(self):
        self.stub_outputs()
        self.cog.embedfixer_config.data["ignored_channels"] = [7]
        await self.cog._embedfixer_on_message(self.message())
        self.cog._embedfixer_repost.assert_not_awaited()
        self.cog.embedfixer_config.data.update(ignored_channels=[], ignored_roles=[42])
        message = self.message()
        message.author.roles = [MagicMock(id=42)]
        await self.cog._embedfixer_on_message(message)
        self.cog._embedfixer_repost.assert_not_awaited()
        await self.cog._embedfixer_on_message(self.message())
        self.cog._embedfixer_repost.assert_awaited_once()

    async def test_musicboard_sources_and_channels(self):
        board = self.cog.musicboard_config.data
        board["music_channel_id"] = 99
        self.cog._musicboard_channel = AsyncMock(return_value=MagicMock())
        self.cog._musicboard_schedule_cleanup = AsyncMock()

        def message(content, channel_id=5):
            msg = MagicMock()
            msg.author.bot = False
            msg.content = content
            msg.reactions = []
            msg.channel = MagicMock(id=channel_id, parent_id=None, category_id=None)
            msg.add_reaction = AsyncMock()
            return msg

        board.update(disabled_sources=["spotify"], block_enabled=False, nominate_emoji="🎶")
        skipped = message("https://open.spotify.com/track/abc")
        await self.cog._musicboard_on_message(skipped)
        skipped.add_reaction.assert_not_awaited()
        prompted = message("https://youtu.be/abc")
        await self.cog._musicboard_on_message(prompted)
        self.assertEqual([c.args[0] for c in prompted.add_reaction.await_args_list], ["🎶"])
        board["source_channels"] = [6]
        elsewhere = message("https://youtu.be/abc")
        await self.cog._musicboard_on_message(elsewhere)
        elsewhere.add_reaction.assert_not_awaited()

    def test_music_and_radio_formats(self):
        board, radio = self.musicboard, self.radio
        self.assertIsNone(board.extract_music_url("https://open.spotify.com/track/abc", ["spotify"]))
        self.assertEqual(board.extract_music_url("https://youtu.be/x", ["spotify"]), "https://youtu.be/x")
        self.assertIsNone(board.extract_music_url("https://youtu.be/x", list(board.MUSIC_SOURCES)))
        default = board.nomination_text(None, user="A", jump="J", url="U")
        self.assertEqual(default, "\U0001f3b5 Nominated by **A** • [Jump to message](J)\nU")
        self.assertEqual(board.nomination_text("{user}: {url}", user="A", jump="J", url="U"), "A: U")
        data = dict(radio.RADIO_GUILD_DEFAULTS)
        online = {"online": True, "title": "Song", "listeners": 4}
        self.assertEqual(radio.voice_status_text(data, online, 23), "Night Drive: Song")
        self.assertEqual(radio.voice_status_text(data, {"online": False}, 23), "Night Drive: offline")
        data.update(show_schedule=False, station_name="Fox FM", voice_status_format="{show} | {title} ({listeners})")
        self.assertEqual(radio.voice_status_text(data, online, 23), "Fox FM | Song (4)")
        self.assertEqual(radio.show_name(data), "Fox FM")


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
        self.assertEqual(fix("https://x.com/a/status/1", frozenset()), "https://fixupx.com/a/status/1")
        self.assertEqual(fixers.find_matches("https://x.com/a/status/1", disabled=frozenset({1})), [])
        self.assertEqual(fixers.rewrite(tweet, {}, None, {1: 29}), "https://xeezz.com/a/status/1/")
        self.assertEqual(fixers.rewrite(tweet, {1: 1}, None, {1: 1002}), "https://fxtwitter.com/a/status/1/")
        self.assertEqual(fixers.rewrite(tweet, {}, None, {1: 12345}), "https://fxtwitter.com/a/status/1/")
        inline = fixers.find_matches("a https://x.com/a/status/1 b https://x.com/c/status/2 c")
        self.assertEqual(
            fixers.rewrite_inline("a https://x.com/a/status/1 b https://x.com/c/status/2 c", inline, {}),
            "a https://fixupx.com/a/status/1 b https://fixupx.com/c/status/2 c",
        )
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
