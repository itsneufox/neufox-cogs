"""Economy's /control panel settings, round-tripped through a real (temporary) Red Config."""

import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock


ROOT = Path(__file__).parents[1]

try:
    from redbot.core import _drivers as drivers
    from redbot.core import data_manager

    HAS_RED = True
except ImportError:
    HAS_RED = False


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class EconomyPanelTests(unittest.IsolatedAsyncioTestCase):
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
        self.module = importlib.import_module("economy.economy")
        self.schema_module = importlib.import_module("control.schema")
        self.panel = importlib.import_module("control.panel")
        self.nightlife = importlib.import_module("economy.nightlife")
        bot = MagicMock()
        bot.loop.create_task = lambda coro: (coro.close(), MagicMock())[1]
        bot.get_guild.return_value = None
        self.cog = self.module.Economy(bot)
        self.cog.config = self.module.Config.get_conf(None, identifier=hash(self.id()) & 0xFFFFFFF, cog_name="EcoPanel")
        constants = importlib.import_module("economy.constants")
        self.constants = constants
        self.cog.config.register_global(**constants.GLOBAL_DEFAULTS)
        self.cog.config.register_guild(**constants.GUILD_DEFAULTS)
        self.cog.config.register_member(**constants.MEMBER_DEFAULTS)
        self.cog.config.register_user(**constants.USER_DEFAULTS)
        self.guild = SimpleNamespace(id=4242)
        self.schema = self.schema_module.parse_schema(self.cog.control_panel_schema())

    async def asyncTearDown(self):
        await drivers.get_driver_class().teardown()
        self.tmp.cleanup()

    async def read(self, key):
        return await self.panel.read_value(self.cog, self.schema.setting(key), self.guild)

    async def write(self, key, value):
        await self.panel.write_value(self.cog, self.schema.setting(key), self.guild, value)

    def test_schema_limits(self):
        self.assertLessEqual(len(self.schema.sections), 25)
        for section in self.schema.sections:
            self.assertLessEqual(len(self.schema.in_section(section)), self.schema_module.MAX_SECTION_SETTINGS, section)
        for setting in self.schema.settings:
            self.assertLessEqual(len(setting.label), 100, setting.key)
            self.assertLessEqual(len(setting.help), 100, setting.key)
            if setting.type in {"int", "float"} and setting.minimum is not None and setting.maximum is not None:
                self.assertLessEqual(setting.minimum, setting.maximum, setting.key)
        for section in ("Casino games", "Casino tables", "Casino in this server", "Rain", "Shoutouts & pins",
                        "Auctions & bounties", "Nightlife prices", "Nightlife clinic"):
            self.assertIn(section, self.schema.sections)

    def test_new_settings_scopes(self):
        for game in self.constants.CASINO_GAMES:
            self.assertEqual(self.schema.setting(f"casino_{game}_enabled").scope, "global")
            server = self.schema.setting(f"casino_{game}_allowed")
            self.assertEqual((server.scope, server.access), ("guild", "admin"))
        for key in ("rental_max_days", "shoutout_max_length", "bounty_max_open", "auction_max_open"):
            self.assertEqual(self.schema.setting(key).scope, "guild")
        for key in ("casino_dice_payout_percent", "blackjack_decks", "lwdmillions_max_lines",
                    "streak_saver_max", "nightlife_price.item.condom"):
            self.assertEqual(self.schema.setting(key).scope, "global")

    async def test_defaults_match_old_constants(self):
        expected = {
            "casino_coinflip_payout_percent": 195,
            "casino_dice_payout_percent": 570,
            "blackjack_decks": 6,
            "holdem_lobby_seconds": 180,
            "holdem_turn_seconds": 90,
            "lwdmillions_max_lines": 50,
            "lwdmillions_max_tickets": 10_000,
            "streak_saver_max": 5,
            "rental_max_days": 30,
            "shoutout_max_length": 500,
            "bounty_max_open": 25,
            "auction_max_open": 25,
            "casino_mines_enabled": True,
            "casino_holdem_allowed": True,
            "nightlife_price.escort.alex": 250,
            "nightlife_price.item.condom": 75,
            "nightlife_price.test": 150,
            "nightlife_price.treatment.syphilis": 3000,
        }
        for key, value in expected.items():
            self.assertEqual(await self.read(key), value, key)

    async def test_round_trips(self):
        samples = {
            "casino_dice_payout_percent": 580,
            "blackjack_decks": 2,
            "holdem_turn_seconds": 45,
            "lwdmillions_max_tickets": 20_000,
            "streak_saver_max": 9,
            "rental_max_days": 7,
            "auction_max_open": 3,
            "casino_slots_enabled": False,
            "casino_roulette_allowed": False,
            "nightlife_price.venue.penthouse": 3500,
        }
        for key, value in samples.items():
            await self.write(key, value)
            self.assertEqual(await self.read(key), value, key)
        self.assertEqual(await self.cog.config.nightlife_prices(), {"venue.penthouse": 3500})
        # Setting a Nightlife price back to its default drops the override.
        await self.write("nightlife_price.venue.penthouse", 2000)
        self.assertEqual(await self.cog.config.nightlife_prices(), {})

    async def test_game_toggles_close_games(self):
        Error = self.module.EconomyError
        await self.cog._validate_casino_wager(100, game="slots", guild_id=self.guild.id)
        await self.write("casino_slots_enabled", False)
        with self.assertRaises(Error):
            await self.cog._validate_casino_wager(100, game="slots", guild_id=None)
        await self.write("casino_slots_enabled", True)
        await self.write("casino_dice_allowed", False)
        with self.assertRaises(Error):
            await self.cog._validate_casino_wager(100, game="dice", guild_id=self.guild.id)
        await self.cog._validate_casino_wager(100, game="dice", guild_id=self.guild.id + 1)
        await self.cog._validate_casino_wager(100, game="coinflip", guild_id=self.guild.id)

    async def test_nightlife_price_override_is_charged(self):
        await self.write("nightlife_price.item.condom", 10)
        prices = await self.cog.config.nightlife_prices()
        _, balance, _, cost = self.nightlife.apply_action(None, 1000, "buy", now=0, item="condom", quantity=3,
                                                         prices=prices)
        self.assertEqual((balance, cost), (970, 30))
        _, _, _, default_cost = self.nightlife.apply_action(None, 1000, "buy", now=0, item="condom")
        self.assertEqual(default_cost, 75)

    async def test_streak_saver_limit_applies(self):
        await self.write("streak_saver_max", 1)
        member = SimpleNamespace(id=7)
        await self.cog.config.user(member).streak_savers.set(1)
        await self.cog.config.streak_saver_price.set(10)
        with self.assertRaises(self.module.EconomyError) as raised:
            await self.cog._buy_streak_saver(member)
        self.assertIn("maximum of 1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
