"""Money flows of the Economy cog against a real (temporary) Red Config."""

import importlib
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock


ROOT = Path(__file__).parents[1]

try:
    from redbot.core import _drivers as drivers
    from redbot.core import data_manager

    HAS_RED = True
except ImportError:
    HAS_RED = False


def user(user_id):
    return SimpleNamespace(id=user_id, display_name=f"user{user_id}", bot=False)


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class EconomyFlowTests(unittest.IsolatedAsyncioTestCase):
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
        bot = MagicMock()
        bot.loop.create_task = lambda coro: (coro.close(), MagicMock())[1]
        bot.get_guild.return_value = None
        self.cog = self.module.Economy(bot)
        # A unique identifier per test keeps the JSON driver's cache from leaking between tests.
        self.cog.config = self.module.Config.get_conf(None, identifier=hash(self.id()) & 0xFFFFFFF, cog_name="EcoTest")
        constants = importlib.import_module("economy.constants")
        self.cog.config.register_global(**constants.GLOBAL_DEFAULTS)
        self.cog.config.register_guild(**constants.GUILD_DEFAULTS)
        self.cog.config.register_member(**constants.MEMBER_DEFAULTS)
        self.cog.config.register_user(**constants.USER_DEFAULTS)

    async def asyncTearDown(self):
        await drivers.get_driver_class().teardown()
        self.tmp.cleanup()

    async def balance(self, user_id):
        return (await self.cog.get_balance(user_id))["cash"]

    async def ledger_types(self):
        return [entry["type"] for entry in await self.cog.config.ledger()]

    async def test_claim_all_then_nothing_then_streak(self):
        await self.cog.config.work_min.set(50)
        await self.cog.config.work_max.set(50)
        player = user(1)
        receipt = await self.cog._claim_all(player, None)
        self.assertEqual(await self.balance(1), 250 + 1000 + 2500 + 12000 + 50)
        self.assertIn("15,800", receipt.title)
        again = await self.cog._claim_all(player, None)
        self.assertEqual(again.title, "Nothing to claim yet")
        self.assertEqual(await self.balance(1), 15800)

        async with self.cog.config.claims() as claims:
            claims["1"]["daily"] -= 86400
            claims["1"]["work"] -= 3600
        await self.cog._claim_all(player, None)
        claims = await self.cog.config.claims()
        self.assertEqual(claims["1"]["streak"], 2)
        self.assertEqual(await self.balance(1), 15800 + 250 + 25 + 50)

    async def test_streak_saver_and_claim_booster(self):
        player = user(2)
        await self.cog.add_balance(2, 100_000)
        await self.cog._buy_streak_saver(player)
        await self.cog._buy_booster(player, "claim")
        self.assertEqual(await self.cog.get_multiplier(2, "claim"), 1.5)
        self.assertEqual(await self.cog.get_multiplier(2, "xp"), 1.0)
        self.assertEqual(await self.cog.get_multiplier(2, "nonsense"), 1.0)
        await self.cog.config.work_max.set(0)
        await self.cog.config.weekly_amount.set(0)
        await self.cog.config.monthly_amount.set(0)
        await self.cog.config.annual_amount.set(0)
        before = await self.balance(2)
        await self.cog._claim_all(player, None)
        self.assertEqual(await self.balance(2), before + 375)
        async with self.cog.config.claims() as claims:
            claims["2"]["daily"] -= 2 * 86400
        await self.cog._claim_all(player, None)
        self.assertEqual((await self.cog.config.claims())["2"]["streak"], 2)
        self.assertEqual(await self.cog.config.user(player).streak_savers(), 0)
        self.assertIn("booster", await self.ledger_types())

    async def test_pay_tax_is_burned(self):
        await self.cog.add_balance(3, 1000)
        await self.cog.transfer_with_tax(3, 4, 500, 10, guild_id=None, reason="test")
        self.assertEqual(await self.balance(3), 490)
        self.assertEqual(await self.balance(4), 500)
        with self.assertRaises(self.module.EconomyError):
            await self.cog.transfer_with_tax(3, 4, 490, 10, guild_id=None, reason="test")
        tax = [entry for entry in await self.cog.config.ledger() if entry["type"] == "tax"]
        self.assertEqual(tax[0]["amount"], 10)
        self.assertIsNone(tax[0]["to_user_id"])

    async def test_self_transfer_cannot_create_money(self):
        await self.cog.add_balance(6, 1000)
        with self.assertRaises(self.module.EconomyError):
            await self.cog.transfer_with_tax(6, 6, 500, 10, guild_id=None, reason="test")
        with self.assertRaises(self.module.EconomyError):
            await self.cog.transfer_balance(6, 6, 500)
        self.assertEqual(await self.balance(6), 1000)

    async def test_pay_out_charges_exact_total(self):
        await self.cog.add_balance(5, 1000)
        await self.cog._pay_out(5, {6: 300, 7: 300}, 400, tx_type="rain", guild_id=None, reason="r")
        self.assertEqual(await self.balance(5), 0)
        self.assertEqual(await self.balance(6), 300)
        with self.assertRaises(self.module.EconomyError):
            await self.cog._pay_out(5, {6: 1}, 0, tx_type="rain", guild_id=None, reason="r")
        self.assertIn("rain_cut", await self.ledger_types())

    async def test_wealth_tax_runs_once_per_day(self):
        await self.cog.add_balance(8, 1_101_000)
        await self.cog.add_balance(9, 500)
        await self.cog.config.wealth_tax_percent.set(1.0)
        now = int(time.time())
        await self.cog._process_wealth_tax(now)
        await self.cog._process_wealth_tax(now)
        self.assertEqual(await self.balance(8), 1_101_000 - 1_010)
        self.assertEqual(await self.balance(9), 500)

    async def test_auction_sale_moves_items_and_burns_cut(self):
        guild = SimpleNamespace(id=50)
        await self.cog._move_inventory(50, None, 10, "Gem", 2)
        await self.cog._move_inventory(50, 10, None, "Gem", 2)
        self.assertEqual(await self.cog._get_inventory(50, 10), {})
        await self.cog.add_balance(11, 1000)
        await self.cog._charge(11, 1000, tx_type="auction_bid", reason="bid")
        auction = {"id": 1, "seller": 10, "item": "Gem", "quantity": 2, "start": 100,
                   "top_bid": 1000, "top_bidder": 11, "ends": 0, "channel_id": 0}
        async with self.cog.config.guild(guild).auctions() as auctions:
            auctions["1"] = auction
        await self.cog._close_auction(guild, auction, cancelled=False)
        self.assertEqual(await self.cog._get_inventory(50, 11), {"Gem": 2})
        self.assertEqual(await self.balance(10), 950)
        self.assertEqual(await self.cog.config.guild(guild).auctions(), {})

    async def test_bounty_cancel_refunds_contributors(self):
        guild = SimpleNamespace(id=60)
        bounty = {"id": 3, "poster": 12, "task": "x", "amount": 300, "contributors": {"12": 200, "13": 100},
                  "created": 0, "expires": 0, "channel_id": 0}
        async with self.cog.config.guild(guild).bounties() as bounties:
            bounties["3"] = bounty
        await self.cog._close_bounty(guild, bounty, "cancelled")
        self.assertEqual(await self.balance(12), 200)
        self.assertEqual(await self.balance(13), 100)
        self.assertEqual(await self.cog.config.guild(guild).bounties(), {})

    async def test_casino_rake_only_on_profit(self):
        await self.cog.config.casino_rake_percent.set(10.0)
        await self.cog.add_balance(14, 1000)
        account = await self.cog._settle_casino_wager(14, 100, 300, game="dice", outcome="x", guild_id=None)
        self.assertEqual(account["rake"], 20)
        self.assertEqual(await self.balance(14), 1180)
        account = await self.cog._settle_casino_wager(14, 100, 0, game="dice", outcome="x", guild_id=None)
        self.assertEqual(account["rake"], 0)
        self.assertEqual(await self.balance(14), 1080)


if __name__ == "__main__":
    unittest.main()
