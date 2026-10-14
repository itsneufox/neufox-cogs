from __future__ import annotations

import importlib.util
import sys
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "economy" / "holdem.py"
SPEC = importlib.util.spec_from_file_location("holdem", MODULE_PATH)
holdem = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = holdem
SPEC.loader.exec_module(holdem)
RENDER_PATH = Path(__file__).parents[1] / "economy" / "holdem_render.py"
RENDER_SPEC = importlib.util.spec_from_file_location("holdem_render", RENDER_PATH)
holdem_render = importlib.util.module_from_spec(RENDER_SPEC)
RENDER_SPEC.loader.exec_module(holdem_render)
ESCROW_PATH = Path(__file__).parents[1] / "economy" / "holdem_escrow.py"
ESCROW_SPEC = importlib.util.spec_from_file_location("holdem_escrow", ESCROW_PATH)
holdem_escrow = importlib.util.module_from_spec(ESCROW_SPEC)
ESCROW_SPEC.loader.exec_module(holdem_escrow)


class HoldemRulesTests(unittest.TestCase):
    def test_hand_rank_wheel_and_straight_flush(self):
        wheel = holdem.hand_rank(["AS", "2D", "3C", "4H", "5S", "KH", "QC"])
        six_high = holdem.hand_rank(["2S", "3D", "4C", "5H", "6S", "KH", "QC"])
        straight_flush = holdem.hand_rank(["9H", "TH", "JH", "QH", "KH", "2C", "2D"])
        self.assertEqual(wheel, (4, 5))
        self.assertGreater(six_high, wheel)
        self.assertEqual(straight_flush, (8, 13))

    def test_full_house_uses_best_three_and_pair(self):
        rank = holdem.hand_rank(["AS", "AD", "AC", "KH", "KD", "KC", "2S"])
        self.assertEqual(rank, (6, 14, 13))

    def test_side_pots_and_odd_chip(self):
        payout = holdem.split_pots(
            {1: 50, 2: 100, 3: 100},
            {1, 2, 3},
            {1: (8, 14), 2: (2, 10), 3: (2, 10)},
            [1, 2, 3],
        )
        self.assertEqual(payout, {1: 150, 2: 50, 3: 50})
        self.assertEqual(holdem.split_pots({1: 1, 2: 1, 3: 1}, {1, 2, 3}, {1: (1,), 2: (1,), 3: (0,)}, [2, 1, 3]), {1: 1, 2: 2, 3: 0})
        self.assertEqual(holdem.split_pots({1: 100, 2: 50}, {2}, {2: (0,)}, [1, 2]), {1: 50, 2: 100})

    def test_betting_to_showdown_preserves_chips(self):
        players = [holdem.Player(1, "A", 100), holdem.Player(2, "B", 100)]
        hand = holdem.HoldemHand(players, 100)
        hand.act(1, "call")
        hand.act(2, "check")
        self.assertEqual(hand.actor.user_id, 2)
        for _ in range(3):
            hand.act(2, "check")
            hand.act(1, "check")
        self.assertTrue(hand.finished)
        self.assertEqual(len(hand.board), 5)
        self.assertEqual(sum(p.stack for p in players), 200)

    def test_fold_winner_does_not_show_cards(self):
        hand = holdem.HoldemHand([holdem.Player(1, "A", 100), holdem.Player(2, "B", 100)], 100)
        hand.act(1, "fold")
        self.assertTrue(hand.finished)
        self.assertFalse(hand.showdown)
        winner = hand.players[1]
        before = holdem_render.render_holdem_table(hand.players, hand.board, {}, finished=True, showdown=hand.showdown)
        winner.cards = ["AS", "KS"]
        after = holdem_render.render_holdem_table(hand.players, hand.board, {}, finished=True, showdown=hand.showdown)
        self.assertEqual(before.getvalue(), after.getvalue())

    def test_six_human_seats_can_finish_a_hand(self):
        players = [holdem.Player(number, f"Player {number}", 100) for number in range(1, 7)]
        hand = holdem.HoldemHand(players, 100)
        while not hand.finished:
            actor = hand.actor
            hand.act(actor.user_id, "call" if hand.current_bet > actor.street_bet else "check")
        self.assertTrue(hand.showdown)
        self.assertEqual(sum(player.stack for player in players), 600)

    def test_short_all_in_does_not_reopen_betting(self):
        players = [holdem.Player(1, "A", 100), holdem.Player(2, "B", 100), holdem.Player(3, "C", 100)]
        hand = holdem.HoldemHand(players, 100)
        hand.act(3, "call")
        hand.act(1, "raise", 25)
        hand.act(2, "call")
        hand.act(3, "call")
        self.assertEqual(hand.street, 1)


class HoldemImageTests(unittest.TestCase):
    def test_public_table_keeps_hole_cards_hidden_until_showdown(self):
        player = holdem.Player(1, "Alice", 100, ["AS", "KH"])
        image_before = holdem_render.render_holdem_table([player], ["2C", "3D", "4H"], {})
        player.cards = ["QC", "JD"]
        image_after = holdem_render.render_holdem_table([player], ["2C", "3D", "4H"], {})
        self.assertEqual(image_before.getvalue(), image_after.getvalue())
        showdown = holdem_render.render_holdem_table([player], ["2C", "3D", "4H", "5S", "6C"], {}, finished=True, showdown=True)
        self.assertNotEqual(image_after.getvalue(), showdown.getvalue())

    def test_private_cards_and_avatar_render(self):
        private = holdem_render.render_hole_cards(["AS", "TH"])
        with Image.open(private) as image:
            self.assertEqual(image.size, (260, 195))
        avatar = BytesIO()
        Image.new("RGB", (64, 64), "red").save(avatar, format="PNG")
        player = holdem.Player(1, "Alice", 100, ["AS", "TH"])
        table = holdem_render.render_holdem_table([player], [], {1: avatar.getvalue()})
        with Image.open(table) as image:
            self.assertEqual(image.size, (1000, 760))
            self.assertEqual(image.getpixel((37, 560)), (255, 0, 0))


class HoldemEscrowTests(unittest.TestCase):
    def test_interrupted_hand_refunds_once(self):
        balances = {"1": {"cash": 1000}}
        holdem_escrow.reserve_buyin(balances, 1, "table", 100)
        self.assertEqual(balances["1"]["cash"], 900)
        with self.assertRaises(ValueError):
            holdem_escrow.reserve_buyin(balances, 1, "second", 100)
        account = holdem_escrow.account_from_mapping(balances, 1)
        account["cash"] += 50
        balances["1"] = account
        self.assertEqual(holdem_escrow.recover_escrows(balances, {}), [(1, 100, "table")])
        self.assertEqual(balances["1"], {"cash": 1050})
        self.assertEqual(holdem_escrow.recover_escrows(balances, {}), [])

    def test_saved_payout_finishes_partial_settlement_once(self):
        balances = {"1": {"cash": 1000}, "2": {"cash": 1000}}
        holdem_escrow.reserve_buyin(balances, 1, "table", 100)
        holdem_escrow.reserve_buyin(balances, 2, "table", 100)
        self.assertTrue(holdem_escrow.resolve_escrow(balances, 1, "table", 150))
        self.assertEqual(holdem_escrow.recover_escrows(balances, {"table": {"1": 150, "2": 50}}), [(2, 50, "table")])
        self.assertEqual(balances, {"1": {"cash": 1050}, "2": {"cash": 950}})
        self.assertEqual(holdem_escrow.recover_escrows(balances, {"table": {"1": 150, "2": 50}}), [])


if __name__ == "__main__":
    unittest.main()
