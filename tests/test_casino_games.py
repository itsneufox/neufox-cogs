from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "economy" / "casino_games.py"
SPEC = importlib.util.spec_from_file_location("casino_games", MODULE_PATH)
casino_games = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(casino_games)


class BlackjackHouseRuleTests(unittest.TestCase):
    def test_dealer_natural_redraw_returns_main_wager(self):
        payout = casino_games.calculate_blackjack_dealer_natural_payout(100)
        self.assertEqual(payout, 100)

    def test_dealer_natural_redraw_keeps_standard_insurance_win(self):
        payout = casino_games.calculate_blackjack_dealer_natural_payout(100, 50)
        self.assertEqual(payout, 250)

    def test_action_log_keeps_chronological_order(self):
        entries = ["> Dealt.", "> Hit.", "> Stood."]
        self.assertEqual(
            casino_games.format_blackjack_action_log(entries),
            "> Dealt.\n> Hit.\n> Stood.",
        )

    def test_action_log_trims_oldest_entries_first(self):
        entries = [f"> action {number}" for number in range(1, 20)]
        result = casino_games.format_blackjack_action_log(entries, character_limit=60)
        self.assertTrue(result.startswith("… earlier actions omitted"))
        self.assertIn("> action 19", result)
        self.assertNotIn("> action 1\n", result)

    def test_blackjack_totals_explain_soft_aces_and_adjustments(self):
        self.assertEqual(
            casino_games.format_blackjack_total(13, soft=True),
            "soft 13",
        )
        self.assertEqual(
            casino_games.format_blackjack_total(12, soft=False, ace_adjusted=True),
            "12 (Ace adjusted to 1)",
        )


class HighCardTests(unittest.TestCase):
    def test_player_win_returns_double_wager(self):
        payout, rule = casino_games.calculate_high_card_payout(100, ("9", "C"), ("K", "D"))
        self.assertEqual(payout, 200)
        self.assertIn("player", rule)

    def test_equal_rank_pushes_even_with_different_suits(self):
        payout, rule = casino_games.calculate_high_card_payout(100, ("Q", "C"), ("Q", "S"))
        self.assertEqual(payout, 100)
        self.assertIn("push", rule)

    def test_dealer_win_returns_nothing(self):
        payout, rule = casino_games.calculate_high_card_payout(100, ("A", "H"), ("2", "H"))
        self.assertEqual(payout, 0)
        self.assertIn("dealer", rule)

    def test_draws_distinct_cards(self):
        for _ in range(25):
            dealer, player = casino_games.draw_high_card()
            self.assertNotEqual(dealer, player)


class RouletteTests(unittest.TestCase):
    def test_normalizes_numbers_and_aliases(self):
        self.assertEqual(casino_games.normalize_roulette_bet("17"), "number:17")
        self.assertEqual(casino_games.normalize_roulette_bet("green"), "number:0")
        self.assertEqual(casino_games.normalize_roulette_bet("2nd12"), "dozen2")
        self.assertEqual(casino_games.normalize_roulette_bet("19-36"), "high")

    def test_rejects_invalid_bets(self):
        for choice in ("-1", "37", "blue", "fourth12"):
            with self.subTest(choice=choice):
                self.assertIsNone(casino_games.normalize_roulette_bet(choice))

    def test_straight_number_returns_36x(self):
        self.assertEqual(casino_games.calculate_roulette_payout(10, "number:17", 17)[0], 360)
        self.assertEqual(casino_games.calculate_roulette_payout(10, "number:17", 18)[0], 0)

    def test_rejects_invalid_canonical_number(self):
        with self.assertRaises(ValueError):
            casino_games.calculate_roulette_payout(10, "number:37", 17)

    def test_zero_loses_all_even_money_bets(self):
        for bet in casino_games.ROULETTE_EVEN_MONEY_BETS:
            with self.subTest(bet=bet):
                self.assertEqual(casino_games.calculate_roulette_payout(10, bet, 0)[0], 0)

    def test_even_money_boundaries(self):
        self.assertEqual(casino_games.calculate_roulette_payout(10, "red", 1)[0], 20)
        self.assertEqual(casino_games.calculate_roulette_payout(10, "black", 2)[0], 20)
        self.assertEqual(casino_games.calculate_roulette_payout(10, "low", 18)[0], 20)
        self.assertEqual(casino_games.calculate_roulette_payout(10, "high", 19)[0], 20)

    def test_dozen_boundaries_return_3x(self):
        cases = (
            (1, "dozen1"),
            (12, "dozen1"),
            (13, "dozen2"),
            (24, "dozen2"),
            (25, "dozen3"),
            (36, "dozen3"),
        )
        for result, bet in cases:
            with self.subTest(result=result, bet=bet):
                self.assertEqual(casino_games.calculate_roulette_payout(10, bet, result)[0], 30)


if __name__ == "__main__":
    unittest.main()
