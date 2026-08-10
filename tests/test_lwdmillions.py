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


class LegacyMigrationProofTests(unittest.TestCase):
    def test_v1_draw_remains_reproducible_for_already_sold_tickets(self):
        secret = "00" * 32
        commitment = lwdmillions.legacy_lwdmillions_commitment(secret, 7)
        main, stars = lwdmillions.legacy_committed_lwdmillions_draw(secret, 7)
        self.assertEqual(
            commitment,
            "fc8792a188aed9379b3107a9ccfeaafbb08c9e50c5e55239ff6591ac9077af8a",
        )
        self.assertEqual(main, (20, 26, 32, 37, 45))
        self.assertEqual(stars, (1, 11))
        self.assertTrue(
            lwdmillions.verify_legacy_lwdmillions_draw(
                secret,
                7,
                commitment,
                main,
                stars,
            )
        )

    def test_v1_verification_rejects_tampering(self):
        secret = "11" * 32
        commitment = lwdmillions.legacy_lwdmillions_commitment(secret, 8)
        main, stars = lwdmillions.legacy_committed_lwdmillions_draw(secret, 8)
        self.assertFalse(
            lwdmillions.verify_legacy_lwdmillions_draw(
                secret,
                8,
                "0" * 64,
                main,
                stars,
            )
        )
        tampered_main = (1, 2, 3, 4, 5)
        self.assertFalse(
            lwdmillions.verify_legacy_lwdmillions_draw(
                secret,
                8,
                commitment,
                tampered_main,
                stars,
            )
        )

    def test_commitment_detection_preserves_v1_despite_new_config_defaults(self):
        secret = "33" * 32
        commitment = lwdmillions.legacy_lwdmillions_commitment(secret, 12)
        self.assertEqual(
            lwdmillions.detect_lwdmillions_proof_version(
                secret,
                12,
                commitment,
                999_999,
            ),
            1,
        )


class PublicBeaconProofTests(unittest.TestCase):
    # A historical League of Entropy Quicknet beacon makes this test deterministic
    # and exercises the protocol's SHA-256(signature) randomness derivation.
    BEACON_ROUND = 1000
    BEACON_RANDOMNESS = (
        "fe290beca10872ef2fb164d2aa4442de4566183ec51c56ff3cd603d930e54fdd"
    )
    BEACON_SIGNATURE = (
        "b44679b9a59af2ec876b1a6b1ad52ea9b1615fc3982b19576350f93447cb1125"
        "e342b73a8dd2bacbe47e4b6b63ed5e39"
    )

    def test_quicknet_round_is_strictly_after_draw_time(self):
        genesis = lwdmillions.DRAND_QUICKNET_GENESIS_TIME
        self.assertEqual(lwdmillions.drand_round_after(genesis), 2)
        self.assertEqual(
            lwdmillions.drand_round_timestamp(2),
            genesis + lwdmillions.DRAND_QUICKNET_PERIOD,
        )
        self.assertEqual(lwdmillions.drand_round_after(genesis + 3), 3)

    def test_historical_beacon_shape_and_randomness_hash_validate(self):
        self.assertTrue(
            lwdmillions.validate_drand_quicknet_beacon(
                self.BEACON_ROUND,
                self.BEACON_RANDOMNESS,
                self.BEACON_SIGNATURE,
            )
        )
        tampered_signature = "0" + self.BEACON_SIGNATURE[1:]
        self.assertFalse(
            lwdmillions.validate_drand_quicknet_beacon(
                self.BEACON_ROUND,
                self.BEACON_RANDOMNESS,
                tampered_signature,
            )
        )

    def test_v2_draw_is_reproducible_from_secret_and_public_beacon(self):
        secret = "00" * 32
        commitment = lwdmillions.lwdmillions_commitment(
            secret,
            7,
            self.BEACON_ROUND,
        )
        main, stars = lwdmillions.committed_lwdmillions_draw(
            secret,
            7,
            self.BEACON_ROUND,
            self.BEACON_RANDOMNESS,
        )
        self.assertEqual(
            commitment,
            "299412946069cef0c6688c963e97fbe1186fc41abf228ad57a0432fee682161f",
        )
        self.assertEqual(main, (6, 10, 13, 16, 28))
        self.assertEqual(stars, (3, 4))
        self.assertTrue(
            lwdmillions.verify_lwdmillions_draw(
                secret,
                7,
                self.BEACON_ROUND,
                self.BEACON_RANDOMNESS,
                self.BEACON_SIGNATURE,
                commitment,
                main,
                stars,
            )
        )

    def test_v2_commitment_binds_the_future_beacon_round(self):
        secret = "22" * 32
        commitment = lwdmillions.lwdmillions_commitment(
            secret,
            9,
            self.BEACON_ROUND,
        )
        main, stars = lwdmillions.committed_lwdmillions_draw(
            secret,
            9,
            self.BEACON_ROUND,
            self.BEACON_RANDOMNESS,
        )
        self.assertFalse(
            lwdmillions.verify_lwdmillions_draw(
                secret,
                9,
                self.BEACON_ROUND + 1,
                self.BEACON_RANDOMNESS,
                self.BEACON_SIGNATURE,
                commitment,
                main,
                stars,
            )
        )
        self.assertEqual(
            lwdmillions.detect_lwdmillions_proof_version(
                secret,
                9,
                commitment,
                self.BEACON_ROUND,
            ),
            2,
        )

    def test_paid_v1_draw_can_add_drand_without_replacing_its_commitment(self):
        secret = "44" * 32
        draw_number = 10
        scheduled_for = lwdmillions.drand_round_timestamp(self.BEACON_ROUND - 1)
        legacy_commitment = lwdmillions.legacy_lwdmillions_commitment(
            secret,
            draw_number,
        )
        main, stars = lwdmillions.committed_lwdmillions_draw(
            secret,
            draw_number,
            self.BEACON_ROUND,
            self.BEACON_RANDOMNESS,
        )
        self.assertTrue(
            lwdmillions.verify_migrated_lwdmillions_draw(
                secret,
                draw_number,
                scheduled_for,
                self.BEACON_ROUND,
                self.BEACON_RANDOMNESS,
                self.BEACON_SIGNATURE,
                legacy_commitment,
                main,
                stars,
            )
        )
        self.assertFalse(
            lwdmillions.verify_migrated_lwdmillions_draw(
                secret,
                draw_number,
                scheduled_for,
                self.BEACON_ROUND + 1,
                self.BEACON_RANDOMNESS,
                self.BEACON_SIGNATURE,
                legacy_commitment,
                main,
                stars,
            )
        )
        self.assertFalse(
            lwdmillions.verify_migrated_lwdmillions_draw(
                secret,
                draw_number,
                scheduled_for,
                self.BEACON_ROUND,
                self.BEACON_RANDOMNESS,
                self.BEACON_SIGNATURE,
                "0" * 64,
                main,
                stars,
            )
        )


if __name__ == "__main__":
    unittest.main()
