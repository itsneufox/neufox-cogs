import importlib.util
from pathlib import Path
import sys
import unittest

try:
    import discord
    from discord import app_commands

    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False

ROOT = Path(__file__).parents[1]


def load_help():
    spec = importlib.util.spec_from_file_location("control_help", ROOT / "control" / "help.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["control_help"] = module
    spec.loader.exec_module(module)
    return module


class FakeCog:
    def __init__(self, name):
        self.qualified_name = name


def bound(command, cog):
    for leaf in [command] + (list(command.walk_commands()) if isinstance(command, app_commands.Group) else []):
        if isinstance(leaf, app_commands.Command):
            leaf.binding = cog
    return command


@unittest.skipUnless(HAS_DISCORD, "needs discord.py installed")
class HelpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.help = load_help()

        async def callback(interaction):
            pass

        economy, mod = FakeCog("Economy"), FakeCog("Moderation")
        self.claim = bound(app_commands.Command(name="claim", description="Claim rewards.", callback=callback), economy)
        shop = app_commands.Group(name="shop", description="Shop.")
        shop.add_command(app_commands.Command(name="buy", description="Buy an item.", callback=callback))
        shop.add_command(app_commands.Command(name="view", description="View the shop.", callback=callback))
        self.shop = bound(shop, economy)
        modgroup = app_commands.Group(
            name="mod", description="Moderation.", default_permissions=discord.Permissions(ban_members=True), guild_only=True
        )
        modgroup.add_command(app_commands.Command(name="ban", description="Ban someone.", callback=callback))
        self.mod = bound(modgroup, mod)
        self.commands = [self.mod, self.shop, self.claim]

    def collect(self, permissions, in_guild=True):
        return self.help.collect_help(
            self.commands, {"Economy": "Money things."}, permissions=permissions, in_guild=in_guild
        )

    def test_groups_by_cog_and_lists_subcommands(self):
        categories = self.collect(discord.Permissions.all())
        self.assertEqual([c.name for c in categories], ["Economy", "Moderation"])
        self.assertEqual([e.name for e in categories[0].entries], ["claim", "shop buy", "shop view"])
        self.assertEqual(categories[0].description, "Money things.")
        self.assertEqual(categories[1].entries[0].line(), "`/mod ban` - Ban someone.")

    def test_hides_commands_member_cannot_use(self):
        self.assertEqual([c.name for c in self.collect(discord.Permissions.none())], ["Economy"])
        admin = discord.Permissions(administrator=True)
        self.assertIn("Moderation", [c.name for c in self.collect(admin)])

    def test_hides_guild_only_commands_in_dms(self):
        self.assertEqual([c.name for c in self.collect(None, in_guild=False)], ["Economy"])

    def test_paginate_respects_limit(self):
        lines = [f"`/cmd{i}` - " + "x" * 90 for i in range(200)]
        pages = self.help.paginate(lines)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= self.help.MAX_PAGE_CHARS for page in pages))
        self.assertEqual(sum(page.count("\n") + 1 for page in pages), 200)

    async def test_view_switches_category_and_pages(self):
        categories = self.collect(discord.Permissions.all())
        view = self.help.HelpView(1, categories, ".")
        self.assertIn("**Economy** (3) - Money things.", view.embed().description)
        self.assertIn("`.help`", view.embed().description)
        view.show("Economy")
        self.assertEqual(view.embed().title, "Help: Economy")
        self.assertIn("`/shop buy` - Buy an item.", view.embed().description)
        self.assertTrue(view.previous.disabled and view.next.disabled)
        view.show("")
        self.assertEqual(view.embed().title, "Help")
        for option in view.picker.options:  # Discord requires 1-100 character option values
            self.assertTrue(1 <= len(option.value) <= 100, option)
        view.show(self.help.OVERVIEW_VALUE)
        self.assertEqual(view.embed().title, "Help")

    async def test_whole_repo_fits_embed_limits(self):
        if importlib.util.find_spec("redbot") is None:
            self.skipTest("needs Red-DiscordBot installed")
        from test_slash_limits import cog_classes, top_level_commands

        commands = [command for cls in cog_classes() for command in top_level_commands(cls)]
        categories = self.help.collect_help(commands, {}, permissions=discord.Permissions.all(), in_guild=True)
        view = self.help.HelpView(1, categories, ".")
        names = {entry.name for category in categories for entry in category.entries}
        self.assertIn("claim", names)
        self.assertIn("help", names)
        for category in [""] + [c.name for c in view.categories]:
            view.show(category)
            for page in view.pages:
                self.assertLessEqual(len(page), 4096)


if __name__ == "__main__":
    unittest.main()
