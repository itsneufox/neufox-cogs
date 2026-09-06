from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "economy" / "nightlife.py"
SPEC = importlib.util.spec_from_file_location("nightlife", MODULE_PATH)
nightlife = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(nightlife)


class NightlifeTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000
        self.player = nightlife.new_profile(self.now)
        self.player["opted_in"] = True
        self.player["inventory"] = {"condom": 5, "viagra": 2, "lube": 1, "energy": 2}

    def act(self, action, *, player=None, balance=10_000, now=None, rolls=(), **kwargs):
        values = iter(rolls)

        def draw(limit):
            value = next(values)
            self.assertTrue(0 <= value < limit)
            return value

        return nightlife.apply_action(
            self.player if player is None else player,
            balance, action, now=self.now if now is None else now,
            randbelow=draw, **kwargs,
        )

    def test_opt_in_required_for_all_gameplay(self):
        for action in ("buy", "use", "encounter", "test", "cure"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "Join first"):
                self.act(action, player=nightlife.new_profile(self.now))

    def test_buy_deducts_exact_price_and_preserves_input(self):
        original = copy.deepcopy(self.player)
        player, balance, _, cost = self.act("buy", item="condom", quantity=3)
        self.assertEqual((balance, cost, player["inventory"]["condom"]), (9775, 225, 8))
        self.assertEqual(player["spent"], 225)
        self.assertEqual(self.player, original)

    def test_invalid_quantities_and_inventory_cap(self):
        for quantity in (-5, 0, 101, 96):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.act("buy", item="condom", quantity=quantity)

    def test_insufficient_funds_never_change_supplied_state(self):
        self.player["infections"]["syphilis"] = {"detectable_at": 0, "diagnosed": True}
        original = copy.deepcopy(self.player)
        for action, kwargs in (("buy", {"item": "viagra"}), ("encounter", {}), ("test", {}), ("cure", {})):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "Insufficient funds"):
                self.act(action, balance=0, **kwargs)
            self.assertEqual(self.player, original)

    def test_intact_condom_prevents_infection_and_is_consumed(self):
        # Exactly 2 is outside the break range. No infection roll is requested.
        player, balance, message, cost = self.act("encounter", rolls=(0, 2))
        self.assertEqual(player["infections"], {})
        self.assertEqual(player["inventory"]["condom"], 4)
        self.assertEqual((balance, cost, player["stamina"]), (9650, 350, 75))
        self.assertEqual((player["encounters"], player["satisfaction"]), (1, 43))
        self.assertIn("Condom held", message)

    def test_broken_condom_can_transmit_disease(self):
        player, _, message, _ = self.act("encounter", rolls=(0, 1, 1, 0))
        self.assertIn("condom broke", message)
        self.assertEqual(player["inventory"]["condom"], 4)
        self.assertEqual(player["infections"], {"chlamydia": {"detectable_at": 10600, "diagnosed": False}})
        self.assertNotIn("Chlamydia", message)

    def test_broken_condom_does_not_guarantee_disease(self):
        player, _, message, _ = self.act("encounter", rolls=(0, 0, 2))
        self.assertIn("condom broke", message)
        self.assertEqual(player["infections"], {})

    def test_unprotected_encounter_does_not_consume_condoms(self):
        player, _, _, _ = self.act("encounter", protected=False, rolls=(0, 0, 2))
        self.assertIn("syphilis", player["infections"])
        self.assertEqual(player["inventory"]["condom"], 5)

    def test_protected_does_not_silently_fall_back_without_condom(self):
        self.player["inventory"]["condom"] = 0
        with self.assertRaisesRegex(ValueError, "needs a condom"):
            self.act("encounter")

    def test_cooldown_persists_and_expires_at_boundary(self):
        player, _, _, _ = self.act("encounter", rolls=(0, 99))
        with self.assertRaisesRegex(ValueError, "Rest for 1 more"):
            self.act("encounter", player=player, now=self.now + 299)
        result, _, _, _ = self.act("encounter", player=player, now=self.now + 300, rolls=(0, 99))
        self.assertEqual(result["encounters"], 2)

    def test_stamina_regeneration_preserves_partial_intervals(self):
        self.player["stamina"] = 20
        player = nightlife.refreshed_profile(self.player, self.now + 359)
        self.assertEqual((player["stamina"], player["stamina_at"]), (21, self.now + 180))
        player = nightlife.refreshed_profile(player, self.now + 360)
        self.assertEqual(player["stamina"], 22)
        with self.assertRaisesRegex(ValueError, "25 stamina"):
            self.act("encounter", player=player, now=self.now + 360)

    def test_idle_time_cannot_be_banked_above_full_stamina(self):
        player, _, _, _ = self.act("encounter", now=self.now + 86400, rolls=(0, 99))
        self.assertEqual(player["stamina"], 75)
        self.assertEqual(nightlife.refreshed_profile(player, self.now + 86401)["stamina"], 75)

    def test_boosts_consume_once_and_cannot_stack(self):
        player, _, _, _ = self.act("use", item="viagra")
        with self.assertRaisesRegex(ValueError, "already ready"):
            self.act("use", item="viagra", player=player)
        player, _, _, _ = self.act("use", item="lube", player=player)
        player, _, _, _ = self.act("encounter", player=player, rolls=(0, 99))
        self.assertEqual(player["satisfaction"], 73)
        self.assertEqual(player["boosts"], [])
        self.assertEqual(player["inventory"]["viagra"], 1)

    def test_energy_cannot_be_wasted_at_full_stamina(self):
        with self.assertRaisesRegex(ValueError, "already full"):
            self.act("use", item="energy")
        self.player["stamina"] = 90
        player, _, _, _ = self.act("use", item="energy")
        self.assertEqual((player["stamina"], player["inventory"]["energy"]), (100, 1))

    def test_testing_respects_incubation_and_treatment_requires_diagnosis(self):
        player, _, _, _ = self.act("encounter", protected=False, rolls=(0, 0, 0))
        player, _, message, cost = self.act("test", player=player, now=self.now + 599)
        self.assertEqual(cost, 150)
        self.assertNotIn("Chlamydia", message)
        with self.assertRaisesRegex(ValueError, "No diagnosed"):
            self.act("cure", player=player)
        player, _, message, _ = self.act("test", player=player, now=self.now + 600)
        self.assertIn("Chlamydia", message)
        player["infections"]["syphilis"] = {"detectable_at": 20000, "diagnosed": False}
        player, balance, _, cost = self.act("cure", player=player, now=self.now + 600)
        self.assertEqual((balance, cost), (9400, 600))
        self.assertNotIn("chlamydia", player["infections"])
        self.assertIn("syphilis", player["infections"])

    def test_combined_treatment_is_all_or_nothing(self):
        self.player["infections"] = {key: {"detectable_at": 0, "diagnosed": True} for key in nightlife.DISEASES}
        with self.assertRaisesRegex(ValueError, "5,100"):
            self.act("cure", balance=5099)
        player, balance, _, cost = self.act("cure", balance=5100)
        self.assertEqual((player["infections"], balance, cost), ({}, 0, 5100))

    def test_leaving_and_rejoining_cannot_clear_disease_or_cooldown(self):
        player, _, _, _ = self.act("encounter", protected=False, rolls=(0, 0, 0))
        original = copy.deepcopy(player)
        player, _, _, _ = self.act("leave", player=player)
        player, _, _, _ = self.act("join", player=player)
        self.assertEqual(player, original)

    def test_maximum_satisfaction_and_vanity_title(self):
        self.player["boosts"] = ["viagra", "lube"]
        self.player["encounters"] = 99
        player, balance, message, cost = self.act("encounter", escort="blair", venue="penthouse", rolls=(25, 99))
        self.assertEqual((player["satisfaction"], balance, cost), (100, 6000, 4000))
        self.assertIn("Nightlife Legend", message)


if __name__ == "__main__":
    unittest.main()
