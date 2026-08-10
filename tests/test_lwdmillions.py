from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "economy" / "lwdmillions.py"
SPEC = importlib.util.spec_from_file_location("lwdmillions", MODULE_PATH)
lwdmillions = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(lwdmillions)


class TicketParsingTests(unittest.TestCase):
    def test_parses_supported_ticket_formats(self):
        expected = ((1, 2, 3, 4, 5), (1, 2))
        for value in (
            "1 2 3 4 5 | 1 2",
            "1,2,3,4,5 + 1,2",
            "1 2 3 4 5 1 2",
        ):
            with self.subTest(value=value):
                self.assertEqual(lwdmillions.parse_lwdmillions_ticket(value), expected)

    def test_sorts_numbers_into_canonical_order(self):
        self.assertEqual(
            lwdmillions.parse_lwdmillions_ticket("50 4 12 9 31 | 12 3"),
            ((4, 9, 12, 31, 50), (3, 12)),
        )

    def test_rejects_duplicate_out_of_range_or_malformed_numbers(self):
        invalid = (
            "1 1 2 3 4 | 1 2",
            "0 2 3 4 5 | 1 2",
            "1 2 3 4 51 | 1 2",
            "1 2 3 4 5 | 1 13",
            "1 2 three 4 5 | 1 2",
            "1 2 3 4 | 1 2",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    lwdmillions.parse_lwdmillions_ticket(value)

    def test_quick_pick_has_distinct_in_range_numbers(self):
        for _ in range(50):
            main, stars = lwdmillions.random_lwdmillions_ticket()
            self.assertEqual(len(main), 5)
            self.assertEqual(len(set(main)), 5)
            self.assertTrue(all(1 <= number <= 50 for number in main))
            self.assertEqual(len(stars), 2)
            self.assertEqual(len(set(stars)), 2)
            self.assertTrue(all(1 <= number <= 12 for number in stars))


class PrizeTests(unittest.TestCase):
    def test_counts_main_and_star_matches_separately(self):
        matches = lwdmillions.match_lwdmillions_ticket(
            (1, 2, 3, 40, 50),
            (1, 12),
            (1, 2, 3, 4, 5),
            (1, 2),
        )
        self.assertEqual(matches, (3, 1))

    def test_jackpot_uses_supplied_share(self):
        self.assertEqual(
            lwdmillions.calculate_lwdmillions_prize(100, 5, 2, jackpot_share=123_456),
            123_456,
        )

    def test_fixed_tiers_scale_with_ticket_price(self):
        self.assertEqual(lwdmillions.calculate_lwdmillions_prize(100, 5, 1), 250_000)
        self.assertEqual(lwdmillions.calculate_lwdmillions_prize(100, 2, 0), 100)
        self.assertEqual(lwdmillions.calculate_lwdmillions_prize(100, 1, 1), 0)


class ScheduleTests(unittest.TestCase):
    def test_next_draw_is_tuesday_or_friday_at_20_utc(self):
        monday = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
        self.assertEqual(
            lwdmillions.next_lwdmillions_draw(monday),
            datetime(2026, 8, 11, 20, tzinfo=timezone.utc),
        )

    def test_exact_draw_time_moves_to_the_following_draw(self):
        tuesday_draw = datetime(2026, 8, 11, 20, tzinfo=timezone.utc)
        self.assertEqual(
            lwdmillions.next_lwdmillions_draw(tuesday_draw),
            datetime(2026, 8, 14, 20, tzinfo=timezone.utc),
        )


class CommitRevealTests(unittest.TestCase):
    def test_draw_is_reproducible_from_revealed_secret(self):
        secret = "00" * 32
        commitment = lwdmillions.lwdmillions_commitment(secret, 7)
        main, stars = lwdmillions.committed_lwdmillions_draw(secret, 7)
        self.assertEqual(
            commitment,
            "fc8792a188aed9379b3107a9ccfeaafbb08c9e50c5e55239ff6591ac9077af8a",
        )
        self.assertEqual(main, (20, 26, 32, 37, 45))
        self.assertEqual(stars, (1, 11))
        self.assertTrue(
            lwdmillions.verify_lwdmillions_draw(
                secret,
                7,
                commitment,
                main,
                stars,
            )
        )

    def test_verification_rejects_tampering(self):
        secret = "11" * 32
        commitment = lwdmillions.lwdmillions_commitment(secret, 8)
        main, stars = lwdmillions.committed_lwdmillions_draw(secret, 8)
        self.assertFalse(
            lwdmillions.verify_lwdmillions_draw(
                secret,
                8,
                "0" * 64,
                main,
                stars,
            )
        )
        tampered_main = (1, 2, 3, 4, 5)
        self.assertFalse(
            lwdmillions.verify_lwdmillions_draw(
                secret,
                8,
                commitment,
                tampered_main,
                stars,
            )
        )


if __name__ == "__main__":
    unittest.main()
