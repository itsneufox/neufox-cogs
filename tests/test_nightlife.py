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

    def test_new_player_can_buy_and_have_an_encounter_immediately(self):
        player, balance, _, _ = nightlife.apply_action(None, 1000, "buy", now=self.now, item="condom")
        player, balance, _, _ = self.act("encounter", player=player, balance=balance, rolls=(0, 99))
        self.assertEqual(balance, 575)
        self.assertEqual(player["encounters"], 1)
        self.assertEqual(player["inventory"]["condom"], 0)

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
        with self.assertRaisesRegex(ValueError, "14,700"):
            self.act("cure", balance=14699)
        player, balance, _, cost = self.act("cure", balance=14700)
        self.assertEqual((player["infections"], balance, cost), ({}, 0, 14700))

    def test_every_disease_can_be_acquired_tested_and_treated(self):
        for index, (key, disease) in enumerate(nightlife.DISEASES.items()):
            with self.subTest(disease=key):
                player, _, _, _ = self.act("encounter", protected=False, rolls=(0, 0, index))
                self.assertEqual(list(player["infections"]), [key])
                player, _, message, _ = self.act("test", player=player, now=self.now + 600)
                self.assertIn(disease["name"], message)
                player, balance, _, cost = self.act("cure", player=player, now=self.now + 600)
                self.assertEqual(player["infections"], {})
                self.assertEqual((balance, cost), (10000 - disease["treatment"], disease["treatment"]))

    def test_every_new_item_can_be_bought_and_used(self):
        for key in ("weed", "cocaine", "ecstasy", "shrooms", "poppers", "champagne", "flowers", "perfume", "chocolate", "coffee", "snack"):
            with self.subTest(item=key):
                player, balance, _, _ = self.act("buy", item=key)
                player["stamina"] = 50
                player, after_use, _, cost = self.act("use", player=player, balance=balance, item=key)
                self.assertEqual((after_use, cost), (balance, 0))
                self.assertEqual(player["inventory"][key], 0)
                if key in ("coffee", "snack"):
                    self.assertGreater(player["stamina"], 50)
                else:
                    self.assertIn(key, player["boosts"])
                    player, _, _, _ = self.act("encounter", player=player, balance=balance, rolls=(0, 99, 99))
                    self.assertGreater(player["satisfaction"], 43)
                    self.assertEqual(player["boosts"], [])

    def test_drug_effect_and_comedown_apply_once(self):
        player, _, _, _ = self.act("buy", item="cocaine", quantity=2)
        player, _, _, _ = self.act("use", player=player, item="cocaine")
        with self.assertRaisesRegex(ValueError, "already ready"):
            self.act("use", player=player, item="cocaine")
        player, _, message, _ = self.act("encounter", player=player, rolls=(0, 99, 99))
        self.assertEqual((player["satisfaction"], player["stamina"]), (73, 55))
        self.assertIn("cocaine rush ended", message)
        self.assertEqual(player["inventory"]["cocaine"], 1)
        with self.assertRaisesRegex(ValueError, "recovering"):
            self.act("encounter", player=player, now=self.now + 300)
        player, _, _, _ = self.act("encounter", player=player, now=self.now + 1800, rolls=(0, 99))
        self.assertEqual((player["satisfaction"], player["stamina"]), (116, 40))

    def test_failed_encounter_preserves_prepared_items(self):
        self.player["boosts"] = ["weed", "cocaine"]
        original = copy.deepcopy(self.player)
        with self.assertRaisesRegex(ValueError, "Insufficient funds"):
            self.act("encounter", balance=0)
        self.assertEqual(self.player, original)

    def test_combined_drug_fatigue_cannot_make_stamina_negative(self):
        self.player["boosts"] = ["weed", "cocaine", "ecstasy", "shrooms", "poppers"]
        self.player["stamina"] = 25
        player, _, _, _ = self.act("encounter", rolls=(0, 99, 99))
        self.assertEqual(player["stamina"], 0)
        self.assertEqual(player["boosts"], [])

    def test_every_substance_reports_and_persists_its_aftermath(self):
        for key in nightlife.SUBSTANCE_CONSEQUENCES:
            with self.subTest(substance=key):
                self.player["boosts"] = [key]
                player, _, message, _ = self.act("encounter", rolls=(0, 99, 99))
                self.assertIn(key, message.lower())
                self.assertGreater(player["recovery_until"], self.now + nightlife.ENCOUNTER_COOLDOWN)
                self.assertEqual(player["medical_bill"], 0)
                refreshed = nightlife.refreshed_profile(player, self.now + 1)
                self.assertEqual(refreshed["recovery_until"], player["recovery_until"])

    def test_hospital_event_creates_debt_without_overdrawing_wallet(self):
        self.player["boosts"] = ["poppers"]
        player, balance, message, cost = self.act("encounter", balance=350, rolls=(0, 99, 0))
        self.assertEqual((balance, cost), (0, 350))
        self.assertEqual((player["stamina"], player["satisfaction"]), (0, 0))
        self.assertEqual((player["medical_bill"], player["hospital_visits"]), (1800, 1))
        self.assertEqual(player["spent"], 350)
        self.assertIn("poppers", message)
        self.assertIn("hospital", message)
        self.assertIn("1,800", message)
        with self.assertRaisesRegex(ValueError, "hospital bills"):
            self.act("encounter", player=player, now=player["recovery_until"])

    def test_poppers_viagra_combination_causes_a_hospital_event(self):
        self.player["boosts"] = ["viagra", "poppers"]
        player, _, message, _ = self.act("encounter", rolls=(0, 99))
        self.assertIn("Poppers and Viagra", message)
        self.assertEqual(player["medical_bill"], 5000)
        self.assertEqual(player["recovery_until"], self.now + 7200)
        self.assertEqual(player["boosts"], [])
        self.assertEqual(player["satisfaction"], 0)

    def test_mixing_substances_can_trigger_hospital_when_single_use_does_not(self):
        self.player["boosts"] = ["weed"]
        single, _, _, _ = self.act("encounter", rolls=(0, 99, 10))
        self.player["boosts"] = ["weed", "champagne"]
        mixed, _, _, _ = self.act("encounter", rolls=(0, 99, 10))
        self.assertEqual(single["medical_bill"], 0)
        self.assertGreater(mixed["medical_bill"], 0)

    def test_paying_bill_is_atomic_and_does_not_remove_recovery(self):
        self.player["medical_bill"] = 1800
        self.player["recovery_until"] = self.now + 3600
        self.player["recovery_reason"] = "Hospital recovery"
        original = copy.deepcopy(self.player)
        with self.assertRaisesRegex(ValueError, "Insufficient funds"):
            self.act("pay", balance=1799)
        self.assertEqual(self.player, original)
        player, balance, message, cost = self.act("pay", balance=2000)
        self.assertEqual((balance, cost, player["medical_bill"], player["spent"]), (200, 1800, 0, 1800))
        self.assertIn("Hospital bill paid", message)
        with self.assertRaisesRegex(ValueError, "recovering"):
            self.act("encounter", player=player)
        with self.assertRaisesRegex(ValueError, "no hospital bills"):
            self.act("pay", player=player)

    def test_stamina_items_cannot_bypass_recovery(self):
        self.player["stamina"] = 0
        self.player["recovery_until"] = self.now + 600
        original = copy.deepcopy(self.player)
        with self.assertRaisesRegex(ValueError, "recovering"):
            self.act("use", item="energy")
        self.assertEqual(self.player, original)
        player, _, _, _ = self.act("use", item="energy", now=self.now + 600)
        self.assertEqual(player["recovery_until"], 0)
        self.assertEqual(player["stamina"], 33)

    def test_old_profiles_gain_consequence_defaults_without_losing_items(self):
        legacy = copy.deepcopy(self.player)
        for key in ("recovery_until", "recovery_reason", "medical_bill", "hospital_visits"):
            legacy.pop(key)
        refreshed = nightlife.refreshed_profile(legacy, self.now)
        self.assertEqual(refreshed, self.player)

    def test_item_aliases_work_for_buy_and_use(self):
        player, _, _, _ = self.act("buy", item=" COKE ")
        player, _, _, _ = self.act("use", player=player, item="coke")
        self.assertEqual(player["inventory"]["cocaine"], 0)
        self.assertEqual(player["boosts"], ["cocaine"])
        self.assertNotIn("coke", player["inventory"])

    def test_old_participation_flags_are_removed_without_resetting_progress(self):
        player, _, _, _ = self.act("encounter", protected=False, rolls=(0, 0, 0))
        original = copy.deepcopy(player)
        for opted_in in (True, False):
            with self.subTest(opted_in=opted_in):
                legacy = dict(player, opted_in=opted_in)
                self.assertEqual(nightlife.refreshed_profile(legacy, self.now), original)
                result, _, _, _ = self.act("buy", player=legacy, item="condom")
                self.assertNotIn("opted_in", result)
                self.assertEqual(result["infections"], original["infections"])
                self.assertEqual(result["last_encounter"], original["last_encounter"])

    def test_maximum_satisfaction_and_vanity_title(self):
        self.player["boosts"] = ["viagra", "lube"]
        self.player["encounters"] = 99
        player, balance, _, cost = self.act("encounter", escort="blair", venue="penthouse", rolls=(25, 99))
        self.assertEqual((player["satisfaction"], balance, cost), (100, 6000, 4000))
        self.assertEqual(nightlife.title_for(player["encounters"]), "Nightlife Legend")


class PlayerEncounterTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000
        self.first = nightlife.new_profile(self.now)
        self.second = nightlife.new_profile(self.now)
        self.first["inventory"] = {"condom": 2}

    def act(self, *, balance=1000, rolls=(0, 0, 99), **kwargs):
        values = iter(rolls)

        def draw(limit):
            value = next(values)
            self.assertTrue(0 <= value < limit)
            return value

        return nightlife.apply_player_encounter(
            self.first, self.second, balance, now=self.now, randbelow=draw, **kwargs,
        )

    def infect(self, player, key):
        player["infections"][key] = {"detectable_at": 0, "diagnosed": True}

    def test_inviter_pays_venue_and_supplies_one_condom_for_both(self):
        originals = copy.deepcopy((self.first, self.second))
        first, second, balance, cost, protection, _, _ = self.act()
        self.assertEqual((balance, cost, first["spent"], second["spent"]), (900, 100, 100, 0))
        self.assertEqual((first["inventory"]["condom"], second["inventory"]), (1, {}))
        for player in (first, second):
            self.assertEqual((player["encounters"], player["last_encounter"], player["stamina"]), (1, self.now, 75))
        self.assertEqual(protection, "Condom held.")
        self.assertEqual((self.first, self.second), originals)

    def test_fresh_partner_needs_no_registration_or_balance(self):
        result = nightlife.apply_player_encounter(self.first, None, 100, now=self.now, randbelow=lambda n: n - 1)
        self.assertEqual((result[1]["encounters"], result[2]), (1, 0))

    def test_both_players_must_be_ready_before_any_state_changes(self):
        for target in (self.first, self.second):
            for key, value in (("recovery_until", self.now + 600), ("medical_bill", 500), ("stamina", 0), ("last_encounter", self.now)):
                with self.subTest(target=target is self.first, key=key):
                    original_value = target[key]
                    target[key] = value
                    original = copy.deepcopy((self.first, self.second))
                    with self.assertRaises(ValueError):
                        self.act(rolls=())
                    self.assertEqual((self.first, self.second), original)
                    target[key] = original_value

    def test_no_funds_no_condom_and_invalid_venue_never_resolve(self):
        with self.assertRaisesRegex(ValueError, "Insufficient funds"):
            self.act(balance=99, rolls=())
        with self.assertRaisesRegex(ValueError, "venue"):
            self.act(venue="unknown", rolls=())
        self.first["inventory"]["condom"] = 0
        with self.assertRaisesRegex(ValueError, "needs a condom"):
            self.act(rolls=())

    def test_healthy_players_do_not_generate_disease(self):
        first, second, *_ = self.act(protected=False, rolls=(0, 0))
        self.assertEqual((first["infections"], second["infections"]), ({}, {}))
        self.assertEqual(first["inventory"]["condom"], 2)

    def test_disease_can_transmit_in_both_directions_after_breakage(self):
        self.infect(self.first, "syphilis")
        self.infect(self.second, "chlamydia")
        first, second, _, _, protection, _, _ = self.act(rolls=(0, 0, 0, 0, 0, 0, 0))
        self.assertIn("broke", protection)
        for player, new_key in ((first, "chlamydia"), (second, "syphilis")):
            self.assertEqual(set(player["infections"]), {"syphilis", "chlamydia"})
            self.assertEqual(player["infections"][new_key], {"detectable_at": self.now + 600, "diagnosed": False})

    def test_intact_condom_blocks_transmission_from_either_player(self):
        self.infect(self.first, "syphilis")
        self.infect(self.second, "chlamydia")
        first, second, *_ = self.act(rolls=(0, 0, 2))
        self.assertEqual(list(first["infections"]), ["syphilis"])
        self.assertEqual(list(second["infections"]), ["chlamydia"])

    def test_unprotected_transmission_does_not_consume_protection(self):
        self.infect(self.first, "hpv")
        first, second, *_ = self.act(protected=False, rolls=(0, 0, 0, 0))
        self.assertIn("hpv", second["infections"])
        self.assertEqual(first["inventory"]["condom"], 2)

    def test_substances_and_hospital_bills_belong_to_the_user_who_used_them(self):
        self.first["boosts"] = ["lube"]
        self.second["boosts"] = ["poppers", "viagra"]
        first, second, _, _, _, first_after, second_after = self.act()
        self.assertEqual((first["medical_bill"], second["medical_bill"]), (0, 5000))
        self.assertEqual((first["stamina"], second["stamina"]), (75, 0))
        self.assertEqual((first["boosts"], second["boosts"]), ([], []))
        self.assertFalse(first_after)
        self.assertIn("Poppers and Viagra", second_after)

    def test_substances_are_not_combined_across_players(self):
        self.first["boosts"] = ["poppers"]
        self.second["boosts"] = ["viagra"]
        first, second, *_ = self.act(rolls=(0, 0, 99, 99))
        self.assertEqual((first["medical_bill"], second["medical_bill"]), (0, 0))
        self.assertGreater(first["recovery_until"], self.now)
        self.assertEqual(second["recovery_until"], 0)


if __name__ == "__main__":
    unittest.main()
