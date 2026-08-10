from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence


MAIN_NUMBER_COUNT = 5
MAIN_NUMBER_MAX = 50
LUCKY_STAR_COUNT = 2
LUCKY_STAR_MAX = 12
DRAW_WEEKDAYS = (1, 4)  # Tuesday and Friday, where Monday is 0.
DRAW_HOUR_UTC = 20
LEGACY_COMMITMENT_DOMAIN = "LWDMillions/v1"
COMMITMENT_DOMAIN = "LWDMillions/v2"
DRAND_QUICKNET_CHAIN_HASH = (
    "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
)
DRAND_QUICKNET_PUBLIC_KEY = (
    "83cf0f2896adee7eb8b5f01fcad3912212c437e0073e911fb90022d3e760183c8"
    "c4b450b6a0a6c3ac6a5776a2d1064510d1fec758c921cc22b0e17e63aaf4bcb5"
    "ed66304de9cf809bd274ca73bab4af5a6e9c76a4bc09e76eae8991ef5ece45a"
)
DRAND_QUICKNET_GENESIS_TIME = 1_692_803_367
DRAND_QUICKNET_PERIOD = 3

# Multipliers include the returned value of a winning line. The 5 + 2 tier is
# handled separately because those tickets split the rolling jackpot.
LWD_MILLIONS_PRIZE_MULTIPLIERS: dict[tuple[int, int], int] = {
    (5, 1): 2_500,
    (5, 0): 500,
    (4, 2): 100,
    (4, 1): 15,
    (3, 2): 10,
    (4, 0): 7,
    (2, 2): 5,
    (3, 1): 4,
    (3, 0): 3,
    (1, 2): 2,
    (2, 1): 2,
    (2, 0): 1,
}

LWD_MILLIONS_PRIZE_TIER_ORDER = (
    (5, 2),
    (5, 1),
    (5, 0),
    (4, 2),
    (4, 1),
    (3, 2),
    (4, 0),
    (2, 2),
    (3, 1),
    (3, 0),
    (1, 2),
    (2, 1),
    (2, 0),
)

_ALLOWED_TICKET_CHARACTERS = re.compile(r"[\d\s,|+/;]+")
_SIDE_DELIMITER = re.compile(r"\s*[|+/;]\s*")


def _validated_numbers(
    values: Iterable[int],
    *,
    count: int,
    maximum: int,
    label: str,
) -> tuple[int, ...]:
    numbers = tuple(int(value) for value in values)
    if len(numbers) != count:
        raise ValueError(f"Choose exactly {count} {label}.")
    if len(set(numbers)) != count:
        raise ValueError(f"The {label} must be different from each other.")
    if any(number < 1 or number > maximum for number in numbers):
        raise ValueError(f"Each {label[:-1]} must be from 1 to {maximum}.")
    return tuple(sorted(numbers))


def validate_lwdmillions_ticket(
    main_numbers: Iterable[int],
    lucky_stars: Iterable[int],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Validate and canonicalize one LWDMillions line."""
    main = _validated_numbers(
        main_numbers,
        count=MAIN_NUMBER_COUNT,
        maximum=MAIN_NUMBER_MAX,
        label="main numbers",
    )
    stars = _validated_numbers(
        lucky_stars,
        count=LUCKY_STAR_COUNT,
        maximum=LUCKY_STAR_MAX,
        label="Lucky Stars",
    )
    return main, stars


def parse_lwdmillions_ticket(value: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Parse `1 2 3 4 5 | 1 2`, also accepting commas or seven bare numbers."""
    cleaned = str(value or "").strip()
    if not cleaned or _ALLOWED_TICKET_CHARACTERS.fullmatch(cleaned) is None:
        raise ValueError(
            "Use five main numbers and two Lucky Stars, for example `1 2 3 4 5 | 1 2`."
        )

    sides = _SIDE_DELIMITER.split(cleaned)
    if len(sides) == 1:
        values = [int(number) for number in re.findall(r"\d+", cleaned)]
        if len(values) != MAIN_NUMBER_COUNT + LUCKY_STAR_COUNT:
            raise ValueError(
                "Choose five main numbers followed by two Lucky Stars, or separate them with `|`."
            )
        main_values = values[:MAIN_NUMBER_COUNT]
        star_values = values[MAIN_NUMBER_COUNT:]
    elif len(sides) == 2:
        main_values = [int(number) for number in re.findall(r"\d+", sides[0])]
        star_values = [int(number) for number in re.findall(r"\d+", sides[1])]
    else:
        raise ValueError("Use one `|` between the five main numbers and two Lucky Stars.")

    return validate_lwdmillions_ticket(main_values, star_values)


def random_lwdmillions_ticket() -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return a securely generated Quick Pick line."""
    generator = secrets.SystemRandom()
    main = tuple(sorted(generator.sample(range(1, MAIN_NUMBER_MAX + 1), MAIN_NUMBER_COUNT)))
    stars = tuple(sorted(generator.sample(range(1, LUCKY_STAR_MAX + 1), LUCKY_STAR_COUNT)))
    return main, stars


def format_lwdmillions_numbers(numbers: Sequence[int]) -> str:
    return " ".join(f"{int(number):02d}" for number in numbers)


def format_lwdmillions_ticket(
    main_numbers: Sequence[int],
    lucky_stars: Sequence[int],
) -> str:
    return (
        f"{format_lwdmillions_numbers(main_numbers)} "
        f"| ⭐ {format_lwdmillions_numbers(lucky_stars)}"
    )


def lwdmillions_match_label(main_matches: int, star_matches: int) -> str:
    star_word = "Star" if int(star_matches) == 1 else "Stars"
    return f"{int(main_matches)} + {int(star_matches)} {star_word}"


def match_lwdmillions_ticket(
    ticket_main: Iterable[int],
    ticket_stars: Iterable[int],
    draw_main: Iterable[int],
    draw_stars: Iterable[int],
) -> tuple[int, int]:
    """Return the count of matching main numbers and Lucky Stars."""
    return (
        len(set(ticket_main).intersection(draw_main)),
        len(set(ticket_stars).intersection(draw_stars)),
    )


def calculate_lwdmillions_prize(
    ticket_price: int,
    main_matches: int,
    star_matches: int,
    *,
    jackpot_share: int = 0,
) -> int:
    """Calculate a line's total prize for a match tier."""
    price = int(ticket_price)
    if price <= 0:
        raise ValueError("ticket price must be positive")
    tier = (int(main_matches), int(star_matches))
    if tier == (5, 2):
        return max(0, int(jackpot_share))
    return price * LWD_MILLIONS_PRIZE_MULTIPLIERS.get(tier, 0)


def next_lwdmillions_draw(after: datetime | int | float | None = None) -> datetime:
    """Return the next Tuesday/Friday 20:00 UTC draw strictly after `after`."""
    if after is None:
        current = datetime.now(timezone.utc)
    elif isinstance(after, datetime):
        current = after
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
    else:
        current = datetime.fromtimestamp(float(after), timezone.utc)

    for day_offset in range(8):
        candidate_date = (current + timedelta(days=day_offset)).date()
        if candidate_date.weekday() not in DRAW_WEEKDAYS:
            continue
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            DRAW_HOUR_UTC,
            tzinfo=timezone.utc,
        )
        if candidate > current:
            return candidate
    raise RuntimeError("could not find the next LWDMillions draw")


def drand_round_after(timestamp: datetime | int | float) -> int:
    """Return the first Quicknet round whose publication time is after `timestamp`."""
    if isinstance(timestamp, datetime):
        value = timestamp
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        timestamp_value = int(value.timestamp())
    else:
        timestamp_value = int(timestamp)
    if timestamp_value < DRAND_QUICKNET_GENESIS_TIME:
        raise ValueError("timestamp predates the drand Quicknet chain")
    current_round = (
        (timestamp_value - DRAND_QUICKNET_GENESIS_TIME) // DRAND_QUICKNET_PERIOD
    ) + 1
    return current_round + 1


def drand_round_timestamp(round_number: int) -> int:
    """Return the scheduled Unix timestamp for a Quicknet round."""
    round_value = int(round_number)
    if round_value <= 0:
        raise ValueError("drand round must be positive")
    return DRAND_QUICKNET_GENESIS_TIME + (round_value - 1) * DRAND_QUICKNET_PERIOD


def validate_drand_quicknet_beacon(
    round_number: int,
    randomness: str,
    signature: str,
) -> bool:
    """Validate beacon shape and the protocol's SHA-256 randomness derivation."""
    try:
        if int(round_number) <= 0:
            return False
        randomness_bytes = bytes.fromhex(str(randomness))
        signature_bytes = bytes.fromhex(str(signature))
    except (TypeError, ValueError):
        return False
    if len(randomness_bytes) != 32 or len(signature_bytes) != 48:
        return False
    expected = hashlib.sha256(signature_bytes).hexdigest()
    return secrets.compare_digest(expected, str(randomness).casefold())


def legacy_lwdmillions_commitment(secret: str, draw_number: int) -> str:
    """Reproduce a v1 commitment so already-sold tickets retain their original proof."""
    material = (
        f"{LEGACY_COMMITMENT_DOMAIN}|commit|{int(draw_number)}|{secret}"
    ).encode()
    return hashlib.sha256(material).hexdigest()


def _legacy_committed_sample(
    maximum: int,
    count: int,
    *,
    secret: str,
    draw_number: int,
    pool_name: str,
) -> tuple[int, ...]:
    pool = list(range(1, maximum + 1))
    selected: list[int] = []
    counter = 0
    value_range = 1 << 256
    while len(selected) < count:
        material = (
            f"{LEGACY_COMMITMENT_DOMAIN}|draw|{int(draw_number)}|"
            f"{pool_name}|{counter}|{secret}"
        ).encode()
        value = int.from_bytes(hashlib.sha256(material).digest(), "big")
        counter += 1
        limit = value_range - (value_range % len(pool))
        if value >= limit:
            continue
        selected.append(pool.pop(value % len(pool)))
    return tuple(sorted(selected))


def legacy_committed_lwdmillions_draw(
    secret: str,
    draw_number: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Reproduce the original v1 draw algorithm during a live migration."""
    if not str(secret):
        raise ValueError("draw secret cannot be empty")
    if int(draw_number) <= 0:
        raise ValueError("draw number must be positive")
    main = _legacy_committed_sample(
        MAIN_NUMBER_MAX,
        MAIN_NUMBER_COUNT,
        secret=str(secret),
        draw_number=int(draw_number),
        pool_name="main",
    )
    stars = _legacy_committed_sample(
        LUCKY_STAR_MAX,
        LUCKY_STAR_COUNT,
        secret=str(secret),
        draw_number=int(draw_number),
        pool_name="stars",
    )
    return main, stars


def verify_legacy_lwdmillions_draw(
    secret: str,
    draw_number: int,
    commitment: str,
    main_numbers: Iterable[int],
    lucky_stars: Iterable[int],
) -> bool:
    """Verify a completed v1 draw retained for already-purchased tickets."""
    expected_commitment = legacy_lwdmillions_commitment(secret, draw_number)
    if not secrets.compare_digest(expected_commitment, str(commitment)):
        return False
    expected_main, expected_stars = legacy_committed_lwdmillions_draw(
        secret,
        draw_number,
    )
    try:
        actual_main, actual_stars = validate_lwdmillions_ticket(main_numbers, lucky_stars)
    except (TypeError, ValueError):
        return False
    return expected_main == actual_main and expected_stars == actual_stars


def lwdmillions_commitment(secret: str, draw_number: int, beacon_round: int) -> str:
    """Commit to both the private secret and future public beacon round."""
    if not str(secret):
        raise ValueError("draw secret cannot be empty")
    if int(draw_number) <= 0 or int(beacon_round) <= 0:
        raise ValueError("draw and beacon rounds must be positive")
    material = (
        f"{COMMITMENT_DOMAIN}|commit|{int(draw_number)}|"
        f"{DRAND_QUICKNET_CHAIN_HASH}|{int(beacon_round)}|{secret}"
    ).encode()
    return hashlib.sha256(material).hexdigest()


def detect_lwdmillions_proof_version(
    secret: str,
    draw_number: int,
    commitment: str,
    beacon_round: int = 0,
) -> int | None:
    """Identify a v1 or v2 commitment without trusting newly added config fields."""
    try:
        if secrets.compare_digest(
            legacy_lwdmillions_commitment(str(secret), int(draw_number)),
            str(commitment),
        ):
            return 1
        if int(beacon_round) > 0 and secrets.compare_digest(
            lwdmillions_commitment(str(secret), int(draw_number), int(beacon_round)),
            str(commitment),
        ):
            return 2
    except (TypeError, ValueError):
        return None
    return None


def _committed_sample(
    maximum: int,
    count: int,
    *,
    entropy: str,
    draw_number: int,
    pool_name: str,
) -> tuple[int, ...]:
    pool = list(range(1, maximum + 1))
    selected: list[int] = []
    counter = 0
    value_range = 1 << 256

    while len(selected) < count:
        material = (
            f"{COMMITMENT_DOMAIN}|draw|{int(draw_number)}|{pool_name}|{counter}|{entropy}"
        ).encode()
        value = int.from_bytes(hashlib.sha256(material).digest(), "big")
        counter += 1

        # Rejection sampling avoids modulo bias while choosing from the shrinking pool.
        limit = value_range - (value_range % len(pool))
        if value >= limit:
            continue
        selected.append(pool.pop(value % len(pool)))
    return tuple(sorted(selected))


def committed_lwdmillions_draw(
    secret: str,
    draw_number: int,
    beacon_round: int,
    beacon_randomness: str,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Mix private committed entropy with a fixed future public drand beacon."""
    if not str(secret):
        raise ValueError("draw secret cannot be empty")
    if int(draw_number) <= 0 or int(beacon_round) <= 0:
        raise ValueError("draw and beacon rounds must be positive")
    try:
        randomness_bytes = bytes.fromhex(str(beacon_randomness))
    except ValueError as error:
        raise ValueError("beacon randomness must be hexadecimal") from error
    if len(randomness_bytes) != 32:
        raise ValueError("beacon randomness must contain 32 bytes")
    entropy = hashlib.sha256(
        (
            f"{COMMITMENT_DOMAIN}|entropy|{int(draw_number)}|"
            f"{DRAND_QUICKNET_CHAIN_HASH}|{int(beacon_round)}|{secret}|"
            f"{str(beacon_randomness).casefold()}"
        ).encode()
    ).hexdigest()
    main = _committed_sample(
        MAIN_NUMBER_MAX,
        MAIN_NUMBER_COUNT,
        entropy=entropy,
        draw_number=int(draw_number),
        pool_name="main",
    )
    stars = _committed_sample(
        LUCKY_STAR_MAX,
        LUCKY_STAR_COUNT,
        entropy=entropy,
        draw_number=int(draw_number),
        pool_name="stars",
    )
    return main, stars


def verify_lwdmillions_draw(
    secret: str,
    draw_number: int,
    beacon_round: int,
    beacon_randomness: str,
    beacon_signature: str,
    commitment: str,
    main_numbers: Iterable[int],
    lucky_stars: Iterable[int],
) -> bool:
    """Verify the commitment, beacon hash, and resulting winning numbers."""
    if not validate_drand_quicknet_beacon(
        beacon_round,
        beacon_randomness,
        beacon_signature,
    ):
        return False
    expected_commitment = lwdmillions_commitment(secret, draw_number, beacon_round)
    if not secrets.compare_digest(expected_commitment, str(commitment)):
        return False
    expected_main, expected_stars = committed_lwdmillions_draw(
        secret,
        draw_number,
        beacon_round,
        beacon_randomness,
    )
    try:
        actual_main, actual_stars = validate_lwdmillions_ticket(main_numbers, lucky_stars)
    except (TypeError, ValueError):
        return False
    return expected_main == actual_main and expected_stars == actual_stars


def verify_migrated_lwdmillions_draw(
    secret: str,
    draw_number: int,
    scheduled_for: int,
    beacon_round: int,
    beacon_randomness: str,
    beacon_signature: str,
    legacy_commitment: str,
    main_numbers: Iterable[int],
    lucky_stars: Iterable[int],
) -> bool:
    """Verify a paid v1 draw upgraded to deterministic future drand entropy."""
    try:
        expected_beacon_round = drand_round_after(int(scheduled_for))
        beacon_round_value = int(beacon_round)
    except (TypeError, ValueError):
        return False
    if beacon_round_value != expected_beacon_round:
        return False
    if not secrets.compare_digest(
        legacy_lwdmillions_commitment(secret, draw_number),
        str(legacy_commitment),
    ):
        return False
    if not validate_drand_quicknet_beacon(
        beacon_round_value,
        beacon_randomness,
        beacon_signature,
    ):
        return False
    expected_main, expected_stars = committed_lwdmillions_draw(
        secret,
        draw_number,
        beacon_round_value,
        beacon_randomness,
    )
    try:
        actual_main, actual_stars = validate_lwdmillions_ticket(main_numbers, lucky_stars)
    except (TypeError, ValueError):
        return False
    return expected_main == actual_main and expected_stars == actual_stars
