import importlib
import importlib.util
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).parents[1]
HAS_RED = importlib.util.find_spec("redbot") is not None


class _Stored:
    """Awaitable and async-context value, like Red's Config value accessors."""

    def __init__(self, data, key):
        self.data, self.key = data, key

    def __await__(self):
        async def get():
            return self.data[self.key]

        return get().__await__()

    async def __aenter__(self):
        return self.data[self.key]

    async def __aexit__(self, *exc):
        return False


class _GuildConfig:
    def __init__(self, data):
        self.data = data

    def __getattr__(self, key):
        return lambda: _Stored(self.data, key)


class FakeRedConfig:
    def __init__(self):
        self.data = {"disabled_commands": [], "admin_role": [], "mod_role": []}

    def guild(self, guild):
        return _GuildConfig(self.data)


def make_member(guild, member_id=1, roles=()):
    member = MagicMock()
    member.id = member_id
    member.guild = guild
    member.get_role = lambda snowflake: snowflake in roles
    member.guild_permissions = SimpleNamespace(manage_guild=True, administrator=False)
    return member


def check_components(test, panel):
    """Discord limits the bot would hit when sending: discord.py does not check these locally."""
    data = panel.to_components()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == 1:  # action row
                test.assertLessEqual(len(node.get("components", [])), 5)
            if node.get("type") == 3:  # string select
                options = node.get("options", [])
                test.assertTrue(1 <= len(options) <= 25)
                for option in options:
                    test.assertTrue(1 <= len(option["value"]) <= 100, option)
                    test.assertTrue(1 <= len(option["label"]) <= 100, option)
                    test.assertLessEqual(len(option.get("description") or ""), 100)
                test.assertLessEqual(node.get("max_values", 1), len(options))
            if node.get("type") == 2:  # button
                test.assertLessEqual(len(node.get("label") or ""), 80)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    test.assertLessEqual(panel.content_length(), 4000)


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class ControlToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from redbot.core import commands

        self.tools = importlib.import_module("control.tools")
        self.pages = importlib.import_module("control.pages")
        self.panel_module = importlib.import_module("control.panel")

        class Sample(commands.Cog):
            @commands.group()
            async def grp(self, ctx):
                pass

            @grp.command()
            async def sub(self, ctx):
                pass

            @commands.command()
            @commands.is_owner()
            async def secret(self, ctx):
                pass

        class FakeCore(commands.Cog):
            @commands.group(name="command")
            async def command_manager(self, ctx):
                pass

            @command_manager.command(name="disable")
            async def command_disable(self, ctx):
                pass

        self.sample, self.core = Sample(), FakeCore()
        self.core._load = AsyncMock(return_value={"loaded_packages": ["x"]})
        self.core._unload = AsyncMock(return_value={"unloaded_packages": ["x"]})
        self.core._reload = AsyncMock(return_value={"loaded_packages": ["economy"]})

        self.guild = SimpleNamespace(id=9, owner=None)
        self.member = make_member(self.guild)
        self.bot = MagicMock()
        self.bot._config = FakeRedConfig()
        self.bot.cogs = {"Sample": self.sample, "Core": self.core}
        self.bot.get_cog = lambda name: self.bot.cogs.get(name)
        self.bot.is_owner = AsyncMock(return_value=False)
        self.bot.is_admin = AsyncMock(return_value=False)
        self.bot.extensions = {"economy": None, "economy.core": None, "control": None}
        self.bot._cog_mgr.available_modules = AsyncMock(return_value=["economy", "control", "media"])
        self.bot.guilds = [self.guild]
        self.bot.latency = 0.05
        self.bot.uptime = datetime(2026, 11, 11)
        self.bot._last_exception = None
        self.tools._last_sync = 0.0

    def command(self, name):
        return next(c for c in self.sample.walk_commands() if c.qualified_name == name)

    async def test_toggle_uses_red_storage_and_runtime_state(self):
        sub = self.command("grp sub")
        self.assertTrue(await self.tools.set_command_enabled(self.bot, self.guild, sub, enabled=False))
        self.assertEqual(self.bot._config.data["disabled_commands"], ["grp sub"])
        self.assertTrue(self.tools.is_disabled(sub, self.guild))
        self.assertFalse(await self.tools.set_command_enabled(self.bot, self.guild, sub, enabled=False))
        self.assertEqual(self.bot._config.data["disabled_commands"], ["grp sub"])
        self.assertTrue(await self.tools.set_command_enabled(self.bot, self.guild, sub, enabled=True))
        self.assertEqual(self.bot._config.data["disabled_commands"], [])
        self.assertFalse(self.tools.is_disabled(sub, self.guild))

    async def test_lock_reasons_follow_red_rules(self):
        level = await self.tools.member_level(self.bot, self.member)
        self.assertIsNone(self.tools.lock_reason(self.bot, self.command("grp"), level))
        self.assertIn("Owner", self.tools.lock_reason(self.bot, self.command("secret"), level))
        manager_sub = next(c for c in self.core.walk_commands() if c.qualified_name == "command disable")
        self.assertIsNotNone(self.tools.lock_reason(self.bot, manager_sub, level))
        self.bot.is_owner.return_value = True
        owner = await self.tools.member_level(self.bot, self.member)
        self.assertIsNone(self.tools.lock_reason(self.bot, self.command("secret"), owner))

    async def test_packages_and_manage(self):
        loaded, available = await self.tools.package_names(self.bot)
        self.assertEqual((loaded, available), (["control", "economy"], ["media"]))
        self.assertEqual(await self.tools.manage_package(self.bot, "reload", "economy"), "Loaded: economy")
        self.core._reload.assert_awaited_with(["economy"])

    async def test_sync_has_a_cooldown(self):
        self.bot.tree.sync = AsyncMock(return_value=[1, 2, 3])
        self.assertIn("Synced 3", await self.tools.sync_commands(self.bot))
        self.assertIn("try again", await self.tools.sync_commands(self.bot))
        self.assertEqual(self.bot.tree.sync.await_count, 1)

    def test_last_error_is_trimmed(self):
        self.assertIn("No errors", self.tools.last_error(self.bot))
        self.bot._last_exception = "x" * 10_000
        self.assertLess(len(self.tools.last_error(self.bot)), 4000)

    async def test_force_update_reinstalls_and_reloads(self):
        def module(name, pinned=False):
            return SimpleNamespace(name=name, repo_name="neufox", pinned=pinned)

        repo = MagicMock()
        repo.update = AsyncMock()
        repo.available_cogs = (module("economy"), module("media"), module("gone_upstream"))
        downloader = MagicMock()
        downloader._repo_manager.get_repo = lambda name: repo if name == "neufox" else None
        downloader.installed_cogs = AsyncMock(
            return_value=(module("economy"), module("media", pinned=True), module("removed"), SimpleNamespace(name="other", repo_name="x", pinned=False))
        )
        downloader._install_requirements = AsyncMock(return_value=())
        downloader._install_cogs = AsyncMock(side_effect=lambda cogs: (tuple(cogs), ()))
        downloader._save_to_installed = AsyncMock()
        self.bot.cogs["Downloader"] = downloader

        report = await self.tools.force_update_repo(self.bot, "neufox")
        self.assertEqual(report.updated, ["economy"])
        self.assertEqual(report.skipped_pinned, ["media"])
        self.assertEqual(report.failed, ["removed"])
        self.core._reload.assert_awaited_with(["economy"])
        self.assertIn("Reinstalled: economy", report.text())
        self.assertIn("No repo", (await self.tools.force_update_repo(self.bot, "nope")).text())

    async def test_pages_render_within_discord_limits(self):
        panel = self.panel_module.ControlPanel(self.bot, self.member)
        await panel.render()
        check_components(self, panel)
        self.assertIn("Commands", str(panel.to_components()))
        self.assertNotIn("Owner tools", str(panel.to_components()))

        panel.page = self.pages.COMMANDS_PAGE
        await panel.render()
        check_components(self, panel)
        panel.cmd_cog = "Sample"
        await panel.render()
        check_components(self, panel)
        text = str(panel.to_components())
        self.assertIn("grp sub", text)
        self.assertIn("🔒 `secret`", text)

        self.bot.is_owner.return_value = True
        panel = self.panel_module.ControlPanel(self.bot, self.member)
        await panel.render()
        self.assertIn("Owner tools", str(panel.to_components()))
        panel.page = self.pages.OWNER_PAGE
        for view in ("home", "packages", "repos", "error"):
            panel.owner_view = view
            await panel.render()
            check_components(self, panel)
        panel.owner_view, panel.owner_package = "packages", "economy"
        await panel.render()
        check_components(self, panel)
        self.assertIn("Reload", str(panel.to_components()))

    async def test_owner_page_bounces_non_owners(self):
        panel = self.panel_module.ControlPanel(self.bot, self.member)
        panel.page = self.pages.OWNER_PAGE
        await panel.render()
        self.assertIsNone(panel.page)
        self.assertIn("bot owner only", str(panel.to_components()))

    def test_pages_clamp(self):
        items = list(range(30))
        self.assertEqual(self.pages._page(items, 5), (items[25:], 1, 2))
        self.assertEqual(self.pages._page(items, -1), (items[:25], 0, 2))
        self.assertEqual(self.pages._page([], 0), ([], 0, 1))


if __name__ == "__main__":
    unittest.main()
