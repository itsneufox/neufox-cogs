from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "economy" / "sinks.py"
SPEC = importlib.util.spec_from_file_location("economy_sinks", MODULE_PATH)
sinks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sinks)


def ts(year, month, day, hour=12):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp())


class FeeTests(unittest.TestCase):
    def test_transfer_tax_floors_and_clamps(self):
        self.assertEqual(sinks.transfer_tax(1000, 2), 20)
        self.assertEqual(sinks.transfer_tax(49, 2), 0)
        self.assertEqual(sinks.transfer_tax(1000, 2.5), 25)
        self.assertEqual(sinks.transfer_tax(1000, 0), 0)
        self.assertEqual(sinks.transfer_tax(1000, -5), 0)
        self.assertEqual(sinks.transfer_tax(1000, 500), 1000)
        self.assertEqual(sinks.transfer_tax(1000, "nonsense"), 0)
        self.assertEqual(sinks.transfer_tax(1000, float("nan")), 0)

    def test_casino_rake_only_on_profit(self):
        self.assertEqual(sinks.casino_rake(300, 100, 10), 20)
        self.assertEqual(sinks.casino_rake(100, 100, 10), 0)
        self.assertEqual(sinks.casino_rake(50, 100, 10), 0)
        self.assertEqual(sinks.casino_rake(300, 100, 0), 0)

    def test_wealth_tax(self):
        self.assertEqual(sinks.wealth_tax(1_500_000, 1_000_000, 1, 0), 5_000)
        self.assertEqual(sinks.wealth_tax(1_500_000, 1_000_000, 1, 2_000), 2_000)
        self.assertEqual(sinks.wealth_tax(900_000, 1_000_000, 1, 0), 0)
        self.assertEqual(sinks.wealth_tax(1_500_000, 1_000_000, 0, 0), 0)

    def test_rain_split_burns_cut_and_remainder(self):
        plan = sinks.split_rain(1000, [1, 2, 3, 3], 5)
        self.assertEqual(plan["cut"], 50)
        self.assertEqual(plan["share"], 316)
        self.assertEqual(plan["payouts"], {1: 316, 2: 316, 3: 316})
        self.assertEqual(plan["burned"], 1000 - 3 * 316)
        self.assertEqual(sum(plan["payouts"].values()) + plan["burned"], 1000)

    def test_rain_split_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            sinks.split_rain(1000, [], 5)
        with self.assertRaises(ValueError):
            sinks.split_rain(2, [1, 2, 3], 0)
        with self.assertRaises(ValueError):
            sinks.split_rain(0, [1], 0)

    def test_multiplier_bounds(self):
        self.assertEqual(sinks.apply_multiplier(100, 1.5), 150)
        self.assertEqual(sinks.apply_multiplier(100, 0.5), 100)
        self.assertEqual(sinks.apply_multiplier(100, 50), 1000)
        self.assertEqual(sinks.apply_multiplier(100, "x"), 100)


class ClaimTests(unittest.TestCase):
    SETTINGS = {
        "daily": {"amount": 250, "cooldown": 86400},
        "weekly": {"amount": 1000, "cooldown": 604800},
        "monthly": {"amount": 0, "cooldown": 2592000},
        "annual": {"amount": 12000, "cooldown": 0},
        "work": {"amount": 60, "cooldown": 3600},
    }

    def test_first_claim_collects_every_enabled_reward(self):
        plan = sinks.plan_claims(ts(2026, 11, 11), {}, self.SETTINGS)
        self.assertEqual(plan["ready"], {"daily": 250, "weekly": 1000, "work": 60})
        self.assertEqual(set(plan["disabled"]), {"monthly", "annual"})
        self.assertEqual(plan["waiting"], {})

    def test_calendar_and_rolling_cooldowns(self):
        now = ts(2026, 11, 11, 23)
        last = {"daily": ts(2026, 11, 11, 1), "weekly": ts(2026, 11, 9, 1), "work": now - 1800}
        plan = sinks.plan_claims(now, last, self.SETTINGS)
        self.assertEqual(plan["ready"], {})
        self.assertEqual(plan["waiting"]["daily"], ts(2026, 11, 12, 0))
        self.assertEqual(plan["waiting"]["weekly"], ts(2026, 11, 16, 0))
        self.assertEqual(plan["waiting"]["work"], now + 1800)
        later = sinks.plan_claims(ts(2026, 11, 12, 0) + 1, last, self.SETTINGS)
        self.assertEqual(set(later["ready"]), {"daily", "work"})

    def test_streak_continues_breaks_and_uses_savers(self):
        today = ts(2026, 11, 11)
        self.assertEqual(sinks.advance_streak(0, 0, today, 0), (1, 0))
        self.assertEqual(sinks.advance_streak(4, ts(2026, 11, 10, 23), today, 0), (5, 0))
        self.assertEqual(sinks.advance_streak(4, ts(2026, 11, 8), today, 0), (1, 0))
        self.assertEqual(sinks.advance_streak(4, ts(2026, 11, 8), today, 2), (5, 2))
        self.assertEqual(sinks.advance_streak(4, ts(2026, 11, 8), today, 1), (1, 0))
        self.assertEqual(sinks.advance_streak(4, today, today, 0), (4, 0))
        self.assertEqual(sinks.streak_at_risk(ts(2026, 11, 9), today), 1)
        self.assertEqual(sinks.streak_at_risk(ts(2026, 11, 10), today), 0)

    def test_streak_bonus(self):
        self.assertEqual(sinks.streak_bonus(1, 25, 250), 0)
        self.assertEqual(sinks.streak_bonus(3, 25, 250), 50)
        self.assertEqual(sinks.streak_bonus(40, 25, 250), 250)
        self.assertEqual(sinks.streak_bonus(40, 25, 0), 975)


class UpkeepTests(unittest.TestCase):
    def test_upkeep_decisions(self):
        self.assertEqual(sinks.upkeep_decision(200, 100, True, 0, 50), sinks.UPKEEP_ACTIVE)
        self.assertEqual(sinks.upkeep_decision(100, 100, True, 50, 50), sinks.UPKEEP_RENEW)
        self.assertEqual(sinks.upkeep_decision(100, 100, True, 49, 50), sinks.UPKEEP_LAPSE)
        self.assertEqual(sinks.upkeep_decision(100, 100, False, 999, 50), sinks.UPKEEP_LAPSE)
        self.assertEqual(sinks.upkeep_decision(100, 100, True, 999, 0), sinks.UPKEEP_LAPSE)

    def test_extend_and_expiry(self):
        self.assertEqual(sinks.extend_until(0, 1000, 60), 1060)
        self.assertEqual(sinks.extend_until(2000, 1000, 60), 2060)
        self.assertTrue(sinks.is_expired(1000, 1000))
        self.assertFalse(sinks.is_expired(1001, 1000))

    def test_active_multiplier(self):
        boosters = {"xp": {"until": 200, "multiplier": 2.0}, "claim": {"until": 50, "multiplier": 1.5}}
        self.assertEqual(sinks.active_multiplier(boosters, "xp", 100), 2.0)
        self.assertEqual(sinks.active_multiplier(boosters, "claim", 100), 1.0)
        self.assertEqual(sinks.active_multiplier(None, "xp", 100), 1.0)
        self.assertEqual(sinks.active_multiplier({"xp": {"until": "bad"}}, "xp", 100), 1.0)
        self.assertEqual(sinks.active_multiplier({"xp": {"until": 200, "multiplier": 99}}, "xp", 100), 10.0)

    def test_bids(self):
        self.assertEqual(sinks.min_next_bid(100, 0, False), 100)
        self.assertEqual(sinks.min_next_bid(100, 1000, True), 1050)
        self.assertEqual(sinks.min_next_bid(100, 10, True), 11)


class TextTests(unittest.TestCase):
    def test_colors_and_text(self):
        self.assertEqual(sinks.parse_hex_color("#ff8800"), 0xFF8800)
        self.assertEqual(sinks.parse_hex_color("FF8800"), 0xFF8800)
        self.assertIsNone(sinks.parse_hex_color("orange"))
        self.assertEqual(sinks.clean_text("  hi\n there  ", 32), "hi there")
        self.assertNotIn("@everyone", sinks.clean_text("ping @everyone", 32))
        self.assertEqual(len(sinks.clean_text("x" * 100, 10)), 10)


if __name__ == "__main__":
    unittest.main()
