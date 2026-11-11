"""Layout and slash checks for Economy. No money logic is exercised through a fake here."""

import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock


ROOT = Path(__file__).parents[1]

try:
    import discord
    from discord import app_commands
    from redbot.core import commands

    HAS_RED = True
except ImportError:
    HAS_RED = False


def modules():
    sys.path.insert(0, str(ROOT))
    economy = importlib.import_module("economy.economy")
    return economy, importlib.import_module("economy.layouts")


def slash_tree(cls):
    top = {}
    for command in cls.__cog_commands__:
        if command.parent is None and getattr(command, "app_command", None) is not None:
            top[command.app_command.name] = command.app_command
    for command in cls.__cog_app_commands__:
        if command.parent is None:
            top[command.name] = command
    return top


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class EconomyLayoutTests(unittest.IsolatedAsyncioTestCase):
    def assert_valid(self, view):
        self.assertTrue(view.to_components())
        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 4000)

    async def test_embed_keeps_fields_and_attachment_image(self):
        _, layouts = modules()
        embed = discord.Embed(title="Casino - Slots", color=discord.Color.green())
        embed.set_author(name="Player: Someone")
        embed.add_field(name="Reels", value="a | b | c", inline=False)
        embed.add_field(name="Result", value="You won", inline=False)
        embed.set_footer(text="Balance: 5 LWD$")
        embed.set_image(url="attachment://slots.gif")
        view = layouts.embed_view(embed)
        self.assert_valid(view)
        text = str(view.to_components())
        for expected in ("Reels", "You won", "Balance: 5", "attachment://slots.gif"):
            self.assertIn(expected, text)

    async def test_oversized_embed_stays_in_limits(self):
        _, layouts = modules()
        embed = discord.Embed(title="T", description="x" * 4096, color=discord.Color.gold())
        for index in range(25):
            embed.add_field(name=f"n{index}", value="y" * 1024, inline=False)
        embed.set_footer(text="z" * 2048)
        self.assert_valid(layouts.embed_view(embed))

    async def test_pages_move_and_lock_at_ends(self):
        _, layouts = modules()
        pages = [discord.Embed(title=f"Page {index}", color=discord.Color.gold()) for index in range(3)]
        view = layouts.EmbedPages(1, pages)
        self.assert_valid(view)

        def buttons():
            return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]

        self.assertTrue(buttons()[0].disabled)
        interaction = MagicMock(response=MagicMock(edit_message=AsyncMock()))
        await buttons()[1].callback(interaction)
        self.assertEqual(view.page, 1)
        await buttons()[1].callback(interaction)
        self.assertEqual(view.page, 2)
        self.assertTrue(buttons()[1].disabled)
        self.assert_valid(view)

    async def test_pages_reject_other_users(self):
        _, layouts = modules()
        view = layouts.EmbedPages(1, [discord.Embed(title="a")])
        interaction = MagicMock(user=MagicMock(id=2), response=MagicMock(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))

    def bot(self, ctx):
        bot = MagicMock()
        bot.get_context = AsyncMock(return_value=ctx)
        bot.get_valid_prefixes = AsyncMock(return_value=["<@1> ", "!"])
        return bot

    def ctx(self, allowed=True):
        ctx = MagicMock(guild=MagicMock())
        ctx.defer = AsyncMock()
        ctx.invoke = AsyncMock()
        return ctx

    def command(self, allowed=True):
        command = MagicMock()
        command.can_run = AsyncMock(return_value=allowed)
        return command

    def interaction(self):
        return MagicMock(response=MagicMock(send_message=AsyncMock()))

    async def test_run_slash_blocks_failed_checks(self):
        _, layouts = modules()
        ctx, command, interaction = self.ctx(), self.command(False), self.interaction()
        await layouts.run_slash(self.bot(ctx), interaction, command)
        ctx.invoke.assert_not_awaited()
        ctx.defer.assert_not_awaited()
        command.can_run.assert_awaited_once_with(ctx, check_all_parents=True)

    async def test_run_slash_applies_cooldown(self):
        _, layouts = modules()
        ctx, command, interaction = self.ctx(), self.command(), self.interaction()
        cooldown = commands.CommandOnCooldown(MagicMock(rate=1, per=3), 2.0, commands.BucketType.user)
        command._prepare_cooldowns = MagicMock(side_effect=cooldown)
        await layouts.run_slash(self.bot(ctx), interaction, command)
        ctx.invoke.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_run_slash_runs_once_after_acknowledging(self):
        _, layouts = modules()
        ctx, command, interaction = self.ctx(), self.command(), self.interaction()
        order = []
        ctx.defer.side_effect = lambda **kwargs: order.append("defer")
        ctx.invoke.side_effect = lambda *args, **kwargs: order.append("invoke")
        await layouts.run_slash(self.bot(ctx), interaction, command, amount=5)
        self.assertEqual(order, ["defer", "invoke"])
        ctx.invoke.assert_awaited_once_with(command, amount=5)
        self.assertIs(ctx.command, command)
        self.assertEqual(ctx.prefix, "!")

    async def test_expired_interaction_never_runs_command(self):
        _, layouts = modules()
        ctx, command, interaction = self.ctx(), self.command(), self.interaction()
        ctx.defer.side_effect = discord.NotFound(MagicMock(status=404, reason="x"), "Unknown interaction")
        with self.assertRaises(discord.NotFound):
            await layouts.run_slash(self.bot(ctx), interaction, command)
        ctx.invoke.assert_not_awaited()

    def test_slash_map(self):
        economy, _ = modules()
        tree = slash_tree(economy.Economy)
        self.assertEqual(set(tree), {"eco", "claim", "pay", "shop", "casino", "nightlife"})
        children = lambda group: {child.name for child in group.commands}
        self.assertEqual(
            children(tree["shop"]),
            {"view", "buy", "gift", "codes", "inventory", "perks", "perk", "rent", "cancel", "rain", "bounty",
             "auction", "shoutout", "pin"},
        )
        casino = tree["casino"]
        self.assertIn("lottery", children(casino))
        lottery = next(child for child in casino.commands if child.name == "lottery")
        self.assertEqual(children(lottery), {"status", "play", "quickpick", "tickets", "prizes", "results", "verify"})
        self.assertIn("clinic", children(tree["nightlife"]))
        bounty = next(child for child in tree["shop"].commands if child.name == "bounty")
        self.assertEqual(children(bounty), {"list", "post", "fund", "award", "cancel"})
        for group in (tree["shop"], casino, tree["nightlife"]):
            self.assertLessEqual(len(group.commands), 25)

    def test_prefix_surface(self):
        economy, _ = modules()
        names = {command.qualified_name for command in economy.Economy.__cog_commands__}
        for expected in ("eco", "balance", "pay", "claim", "shop", "buy", "casino", "casino lottery play",
                         "nightlife", "nightlife clinic pay", "ecoadmin add", "ecoadmin api token create",
                         "ecoadmin draw", "ecoadmin rentals add", "ecoadmin shop add"):
            self.assertIn(expected, names)
        for removed in ("eco admin add", "daily", "work", "lwdmillions", "gift", "inventory", "topbalance"):
            self.assertNotIn(removed, names)
        by_name = {command.qualified_name: command for command in economy.Economy.__cog_commands__}
        self.assertEqual(list(by_name["balance"].aliases), ["bal"])
        self.assertIn("sex", by_name["nightlife"].aliases)
        self.assertIn("daily", by_name["claim"].aliases)
        self.assertIn("lwdmillions", by_name["casino lottery"].aliases)

    def test_control_panel_schema_parses(self):
        economy, _ = modules()
        sys.path.insert(0, str(ROOT))
        schema_module = importlib.import_module("control.schema")
        cog = economy.Economy.__new__(economy.Economy)
        cog.config = MagicMock()
        schema = schema_module.parse_schema(cog.control_panel_schema())
        keys = {setting.key for setting in schema.settings}
        for key in ("pay_tax_percent", "house_cut_percent", "casino_rake_percent", "wealth_tax_percent",
                    "custom_roles_enabled", "pin_channels", "xp_booster_multiplier", "streak_bonus_per_day"):
            self.assertIn(key, keys)
        self.assertEqual(schema.setting("pay_tax_percent").scope, "guild")
        self.assertEqual(schema.setting("wealth_tax_percent").scope, "global")

    async def test_hub_panel_fits_and_guards_owner(self):
        sys.path.insert(0, str(ROOT))
        hub = importlib.import_module("economy.hub")
        owner = MagicMock(id=1)
        view = hub.EconomyHub(MagicMock(), owner, MagicMock(), owner)
        view.render(discord.Embed(title="Profile", description="x" * 3000, color=discord.Color.gold()))
        self.assert_valid(view)
        labels = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        for expected in ("Balance", "Claim", "Shop", "Perks", "Market", "Casino", "Lottery", "Nightlife", "Leaderboard"):
            self.assertIn(expected, labels)
        interaction = MagicMock(user=MagicMock(id=2), response=MagicMock(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))
        dm_view = hub.EconomyHub(MagicMock(), owner, None, owner)
        dm_view.render(discord.Embed(title="Profile"))
        dm_labels = [item.label for item in dm_view.walk_children() if isinstance(item, discord.ui.Button)]
        self.assertNotIn("Shop", dm_labels)

    async def test_hub_button_switches_page(self):
        sys.path.insert(0, str(ROOT))
        hub = importlib.import_module("economy.hub")
        owner = MagicMock(id=1)
        cog = MagicMock()
        cog._hub_page = AsyncMock(return_value=discord.Embed(title="Casino"))
        view = hub.EconomyHub(cog, owner, MagicMock(), owner)
        view.render(discord.Embed(title="Profile"))
        button = next(item for item in view.walk_children()
                      if isinstance(item, discord.ui.Button) and item.label == "Casino")
        interaction = MagicMock(response=MagicMock(edit_message=AsyncMock()))
        await button.callback(interaction)
        self.assertEqual(view.page, "casino")
        cog._hub_page.assert_awaited_once()
        interaction.response.edit_message.assert_awaited_once()
