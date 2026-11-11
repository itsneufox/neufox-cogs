import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).parents[1]
HAS_RED = importlib.util.find_spec("redbot") is not None


def assert_valid(test, view):
    test.assertTrue(view.to_components())
    test.assertLessEqual(view.total_children_count, 40)
    test.assertLessEqual(view.content_length(), 4000)


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class LayoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import discord
        import activity.layouts as layouts

        self.discord = discord
        self.layouts = layouts

    def embed(self, **extra):
        return self.discord.Embed(title="T", color=self.discord.Color.blue(), **extra)

    async def test_oversized_embed_is_clipped_to_limit(self):
        embed = self.embed(description="x" * 4096)
        for index in range(24):
            embed.add_field(name=f"name{index}", value="v" * 200, inline=index % 2 == 0)
        embed.set_footer(text="footer")
        assert_valid(self, self.layouts.card(embed))

    async def test_thumbnail_uses_a_section(self):
        embed = self.embed(description="d")
        embed.set_thumbnail(url="https://cdn.example/a.png")
        self.assertIn("'accessory'", str(self.layouts.card(embed).to_components()))

    async def test_field_only_and_empty_embeds_render(self):
        no_title = self.discord.Embed(color=1)
        no_title.add_field(name="a", value="b")
        assert_valid(self, self.layouts.card(no_title))
        assert_valid(self, self.layouts.card(self.discord.Embed(color=1)))

    async def test_leaderboard_view_paging_and_picker(self):
        pages = [self.embed(description=str(index)) for index in range(3)]
        view = self.layouts.LeaderboardView(1, "messages", pages, AsyncMock(return_value=pages[:1]))
        assert_valid(self, view)
        states = lambda: [b.disabled for b in view.walk_children() if isinstance(b, self.discord.ui.Button)]
        self.assertEqual(states(), [True, True, False, False])
        view.page = 2
        view._render()
        self.assertEqual(states(), [False, False, True, True])
        select = next(item for item in view.walk_children() if isinstance(item, self.discord.ui.Select))
        self.assertEqual([option.default for option in select.options].index(True), list(self.layouts.BOARDS).index("messages"))

    async def test_single_page_has_no_buttons(self):
        view = self.layouts.LeaderboardView(1, "xp", [self.embed(description="only")], AsyncMock())
        self.assertFalse([b for b in view.walk_children() if isinstance(b, self.discord.ui.Button)])


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class ActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import activity.activity as module
        import activity.xp as xp
        import activity.rewards as rewards

        self.module, self.xp, self.rewards = module, xp, rewards
        self.cog = module.Activity.__new__(module.Activity)
        self.cog.bot = MagicMock()
        self.economy = MagicMock()
        self.economy.get_multiplier = AsyncMock(return_value=1.0)
        self.cog.bot.get_cog.return_value = self.economy
        self.cog.config = MagicMock()
        self.cog.stats_config = MagicMock()
        self.cog._locks = {}
        self.cog._chat_cooldowns = {}
        self.data = {
            "level_curve_scale": 2.0,
            "users": {"1": {"chat_xp": 9000, "voice_xp": 200}, "2": {"chat_xp": 10}},
            "level_roles": {"chat": {"15": "77", "12": "78"}, "voice": {}},
            "level_cash_interval": 10,
            "level_cash_per_level": 100,
        }
        self.stats = {
            "user_messages": {"1": 40, "2": 50},
            "received_reactions": {"1": {"🔥": 3, "😂": 1}},
        }
        self.cog.config.guild.return_value.all = AsyncMock(return_value=self.data)
        self.cog.stats_config.guild.return_value.all = AsyncMock(return_value=self.stats)
        self.cog._voice_seconds = AsyncMock(return_value={"1": 3700})
        self.cog._reaction_totals = AsyncMock(return_value={"1": 4})
        self.guild = MagicMock()
        self.guild.name = "Server"
        self.guild.get_role.side_effect = lambda role_id: MagicMock(mention=f"<@&{role_id}>") if role_id == 77 else None
        self.member = MagicMock(id=1, display_name="neufox")
        self.member.display_avatar.url = "https://cdn.example/a.png"

    async def test_rank_card_shows_levels_stats_and_boost(self):
        self.economy.get_multiplier.return_value = 2.0
        view = await self.cog._rank_card(self.guild, self.member)
        assert_valid(self, view)
        text = str(view.to_components())
        self.assertIn("▰", text)
        self.assertLess(text.index("Chat: Level"), text.index("Voice: Level"))
        self.assertIn("40 (#2)", text)
        self.assertIn("1h 1m 40s (#1)", text)
        self.assertIn("XP boost active:** 2x", text)

    async def test_rank_card_without_economy_has_no_boost_or_cash(self):
        self.cog.bot.get_cog.return_value = None
        text = str((await self.cog._rank_card(self.guild, self.member)).to_components())
        self.assertNotIn("boost", text.lower())
        self.assertNotIn("LWD$", text)

    def test_next_reward_prefers_nearest_and_skips_deleted_roles(self):
        reward = self.xp.next_reward_text
        roles = {"15": "77", "12": "78"}
        self.assertEqual(reward(self.guild, 9, roles, 10, 100), "level 10: 1,000 LWD$")
        self.assertEqual(reward(self.guild, 10, roles, 0, 0), "level 15: <@&77>")
        self.assertIsNone(reward(self.guild, 10, {"12": "78"}, 0, 0))
        self.assertIsNone(reward(self.guild, 10, {"bad": "x"}, 0, 0))
        self.assertEqual(reward(self.guild, 5, {"10": "77"}, 10, 100), "level 10: 1,000 LWD$ and <@&77>")

    def test_level_cash_total(self):
        total = self.rewards.level_cash_total
        self.assertEqual(total(9, 10, 10, 100), 1000)
        self.assertEqual(total(9, 21, 10, 100), 3000)
        self.assertEqual(total(10, 19, 10, 100), 0)
        self.assertEqual(total(0, 50, 0, 100), 0)

    def test_progress_bar_width_is_constant(self):
        from activity.common import PROGRESS_BAR_WIDTH, progress_bar

        for current, needed in ((0, 10), (5, 10), (10, 10), (50, 10)):
            self.assertEqual(len(progress_bar(current, needed)), PROGRESS_BAR_WIDTH)

    async def test_leaderboard_pages_rank_by_board(self):
        self.cog.config.guild.return_value.users = AsyncMock(return_value=self.data["users"])
        self.cog.config.guild.return_value.level_curve_scale = AsyncMock(return_value=2.0)
        self.cog.stats_config.guild.return_value.user_messages = AsyncMock(return_value=self.stats["user_messages"])
        self.cog.stats_config.guild.return_value.user_display_names = AsyncMock(return_value={"2": "Old name"})
        self.guild.get_member.side_effect = lambda uid: self.member if uid == 1 else None
        messages = (await self.cog._leaderboard_pages(self.guild, "messages"))[0].description
        self.assertLess(messages.index("Old name"), messages.index("neufox"))
        xp = (await self.cog._leaderboard_pages(self.guild, "xp"))[0].description
        self.assertLess(xp.index("neufox"), xp.index("Old name"))


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class XPMultiplierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import activity.activity as module

        self.cog = module.Activity.__new__(module.Activity)
        self.cog.bot = MagicMock()

    async def test_economy_missing(self):
        self.cog.bot.get_cog.return_value = None
        self.assertEqual(await self.cog._xp_multiplier(1), 1.0)
        self.assertEqual(await self.cog._boosted(1, 20), 20)

    async def test_economy_without_method(self):
        self.cog.bot.get_cog.return_value = object()
        self.assertEqual(await self.cog._xp_multiplier(1), 1.0)
        self.assertEqual(await self.cog._boosted(1, 20), 20)

    async def test_multiplier_two(self):
        economy = MagicMock()
        economy.get_multiplier = AsyncMock(return_value=2.0)
        self.cog.bot.get_cog.return_value = economy
        self.assertEqual(await self.cog._xp_multiplier(5), 2.0)
        self.assertEqual(await self.cog._boosted(5, 20), 40)
        economy.get_multiplier.assert_awaited_with(5, "xp")

    async def test_broken_or_silly_multiplier_falls_back(self):
        economy = MagicMock()
        self.cog.bot.get_cog.return_value = economy
        for value in (RuntimeError("boom"), float("nan"), -3.0, "bad"):
            with self.subTest(value=value):
                economy.get_multiplier = AsyncMock(side_effect=value) if isinstance(value, Exception) else AsyncMock(return_value=value)
                self.assertEqual(await self.cog._xp_multiplier(1), 1.0)

    async def test_chat_xp_is_multiplied(self):
        economy = MagicMock()
        economy.get_multiplier = AsyncMock(return_value=2.0)
        self.cog.bot.get_cog.return_value = economy
        self.cog._chat_cooldowns = {}
        self.cog.config = MagicMock()
        conf = self.cog.config.guild.return_value
        conf.chat_cooldown = AsyncMock(return_value=30)
        conf.chat_xp_min = AsyncMock(return_value=15)
        conf.chat_xp_max = AsyncMock(return_value=25)
        self.cog._grant_xp = AsyncMock()
        message = MagicMock()
        message.guild.id, message.author.id = 10, 20
        with patch("activity.xp.random.randint", return_value=15):
            await self.cog._award_chat_xp(message)
            await self.cog._award_chat_xp(message)  # On cooldown: no second award.
        self.cog._grant_xp.assert_awaited_once_with(message.guild, message.author, "chat", 30, message.channel)
