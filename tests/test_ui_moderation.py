"""Layout, slash, storage and settings checks for moderation (plus banappeals, which shares layouts.py)."""

import importlib
import re
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock


ROOT = Path(__file__).parents[1]
COGS = ("moderation", "banappeals")

try:
    import discord
    from redbot.core import commands

    HAS_RED = True
except ImportError:
    HAS_RED = False


def layouts(cog):
    sys.path.insert(0, str(ROOT))
    importlib.import_module(cog)
    return importlib.import_module(f"{cog}.layouts")


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class LayoutTests(unittest.IsolatedAsyncioTestCase):
    def assert_valid(self, view):
        self.assertTrue(view.to_components())
        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 4000)

    async def test_copies_are_identical(self):
        texts = {(ROOT / cog / "layouts.py").read_text(encoding="utf-8") for cog in COGS}
        self.assertEqual(len(texts), 1)

    async def test_embed_text_is_kept(self):
        embed = discord.Embed(title="Title", description="Body", color=discord.Color.red())
        embed.set_author(name="Author")
        embed.set_thumbnail(url="https://example.com/a.png")
        embed.add_field(name="One", value="1", inline=True)
        embed.add_field(name="Two", value="line\nline", inline=True)
        embed.add_field(name="Three", value="3", inline=False)
        embed.set_footer(text="Foot")
        view = layouts("moderation").embed_view(embed)
        self.assert_valid(view)
        text = str(view.to_components())
        for expected in ("Title", "Body", "Author", "One", "Two", "Three", "Foot", "a.png"):
            self.assertIn(expected, text)

    async def test_oversized_embed_fits(self):
        embed = discord.Embed(title="T" * 256, description="D" * 4096)
        for index in range(25):
            embed.add_field(name=str(index), value="V" * 1024, inline=False)
        embed.set_footer(text="F" * 2048)
        self.assert_valid(layouts("moderation").embed_view(embed))

    async def test_uncoloured_embed_has_no_accent(self):
        view = layouts("banappeals").embed_view(discord.Embed(title="T", color=discord.Color(0)))
        self.assertIsNone(view.to_components()[0]["accent_color"])

    async def test_confirm_view_lifecycle(self):
        module = importlib.import_module("moderation.views")
        for language in ("en", "pt"):
            view = module.ConfirmActionView(1, "ban <@2>", "R" * 2000, "Mod", "https://example.com/a.png", language)
            self.assert_valid(view)
            self.assertNotEqual(view.confirm_button.custom_id, view.cancel_button.custom_id)
            interaction = MagicMock()
            interaction.response.edit_message = AsyncMock()
            await view._confirm(interaction)
            self.assertIs(view.value, True)
            self.assertTrue(view.confirm_button.disabled and view.cancel_button.disabled)
            kwargs = interaction.response.edit_message.await_args.kwargs
            self.assertIs(kwargs["view"], view)
            self.assertNotIn("embed", kwargs)
            self.assert_valid(view)

    async def test_run_slash_blocks_failed_checks(self):
        helpers = layouts("moderation")
        for outcome in (False, commands.CheckFailure("no")):
            command, ctx = MagicMock(), MagicMock()
            command.can_run = AsyncMock(side_effect=[outcome])
            ctx.invoke, ctx.defer = AsyncMock(), AsyncMock()
            bot = types.SimpleNamespace(get_context=AsyncMock(return_value=ctx))
            interaction = MagicMock()
            interaction.response.send_message = AsyncMock()
            await helpers.run_slash(bot, interaction, command, 1)
            ctx.invoke.assert_not_awaited()
            ctx.defer.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_run_slash_runs_with_every_parent_checked(self):
        helpers = layouts("moderation")
        command, ctx = MagicMock(), MagicMock()
        command.can_run = AsyncMock(return_value=True)
        ctx.invoke, ctx.defer = AsyncMock(), AsyncMock()
        bot = types.SimpleNamespace(get_context=AsyncMock(return_value=ctx))
        await helpers.run_slash(bot, MagicMock(), command, 1, ephemeral=True, reason="x")
        command.can_run.assert_awaited_once_with(ctx, check_all_parents=True)
        ctx.defer.assert_awaited_once_with(ephemeral=True)
        ctx.invoke.assert_awaited_once_with(command, 1, reason="x")

    async def test_slash_commands_point_at_real_prefix_commands(self):
        sys.path.insert(0, str(ROOT))
        expected = {
            ("moderation.moderation", "Moderation"): {
                "ban", "kick", "softban", "hackban", "unban", "timeout", "untimeout",
                "warn", "warnings", "clearwarnings", "purge", "userinfo",
                "case", "cases", "reason", "modstats", "status", "help",
                "lockdown", "resetstrikes", "roleoverride", "punish",
            },
            ("banappeals.banappeals", "BanAppeals"): {"settings", "testapi"},
        }
        for (module, name), children in expected.items():
            cls = getattr(importlib.import_module(module), name)
            groups = cls.__cog_app_commands__
            with self.subTest(cog=name):
                self.assertEqual(len(groups), 1)
                self.assertEqual({child.name for child in groups[0].commands}, children)
                self.assertTrue(groups[0].guild_only)
                prefix = {command.callback.__name__ for command in cls.__cog_commands__}
                source = (ROOT / module.replace(".", "/")).with_suffix(".py")
                if not source.exists():
                    source = ROOT / module / "__init__.py"
                for called in re.findall(r"interaction, self\.(\w+)", source.read_text(encoding="utf-8")):
                    self.assertIn(called, prefix)


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class ModerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        self.module = importlib.import_module("moderation.moderation")

    def test_old_cogs_storage_is_reused(self):
        common = importlib.import_module("moderation.common")
        self.assertEqual(common.ADMINHELPER_STORAGE, ("AdminHelper", 911223335))
        self.assertEqual(common.AUTOMOD_STORAGE, ("AntiAbuse", 911223334))
        self.assertEqual(common.HONEYPOT_STORAGE, ("Honeypot", 948372645))

    def test_old_cog_directories_are_gone(self):
        for name in ("adminhelper", "antiabuse", "honeypot"):
            self.assertFalse((ROOT / name / "info.json").exists(), name)

    def test_punish_subgroup_children(self):
        group = self.module.Moderation.__cog_app_commands__[0]
        punish = next(child for child in group.commands if child.name == "punish")
        self.assertEqual({child.name for child in punish.commands}, {"list", "add", "remove", "clear"})

    def test_only_one_top_level_slash_command(self):
        self.assertEqual([group.name for group in self.module.Moderation.__cog_app_commands__], ["mod"])

    def test_listeners_do_not_shadow_each_other(self):
        names = {name for name, _ in self.module.Moderation.__cog_listeners__}
        self.assertIn("on_message", names)
        methods = [method for name, method in self.module.Moderation.__cog_listeners__ if name == "on_message"]
        self.assertEqual(set(methods), {"_automod_on_message", "_honeypot_on_message"})

    def test_control_panel_schema_is_valid(self):
        schema_module = importlib.import_module("control.schema")
        cog = self.module.Moderation.__new__(self.module.Moderation)
        cog.config = cog.automod_config = cog.honeypot_config = MagicMock()
        parsed = schema_module.parse_schema(cog.control_panel_schema())
        self.assertEqual(parsed.sections, ["Moderation", "AutoMod", "AutoMod escalation", "Honeypot"])
        for section in parsed.sections:
            self.assertLessEqual(len(parsed.in_section(section)), schema_module.MAX_SECTION_SETTINGS)
        self.assertIsNotNone(parsed.setting("automod:log_channel_id").getter)
        self.assertEqual(parsed.setting("honeypot:channel_id").access, "admin")
        self.assertIsNone(parsed.setting("log_channel_id").getter)

    def test_natural_parser(self):
        natural = importlib.import_module("moderation.natural")
        request = natural.parse_natural_moderation_request("<@1> mute o <@2> por 10 segundos", 1)
        self.assertEqual((request["action"], request["target_id"], request["duration_seconds"]), ("timeout", 2, 10))
        self.assertEqual(request["language"], "pt")
        request = natural.parse_natural_moderation_request("<@1> ban <@2> 3 days spam", 1)
        self.assertEqual((request["action"], request["delete_days"], request["reason"]), ("ban", 3, "spam"))
        self.assertIsNone(natural.parse_natural_moderation_request("<@2> was banned", 1))

    def test_caps_detection(self):
        automod = importlib.import_module("moderation.automod").AutoModMixin
        self.assertTrue(automod._is_caps_spam("THIS IS ALL CAPS SHOUTING", 0.75, 12))
        self.assertFalse(automod._is_caps_spam("normal message here please", 0.75, 12))

    async def test_strike_summary_mentions_last_violation(self):
        actions = importlib.import_module("moderation.actions").ActionsMixin
        text = actions._strike_summary({"warning_count": 3, "last_violation_at": 5, "last_action_at": 0})
        self.assertIn("AutoMod strikes: 3", text)
        self.assertIn("<t:5:R>", text)
