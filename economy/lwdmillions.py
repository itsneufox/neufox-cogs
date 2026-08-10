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
COMMITMENT_DOMAIN = "LWDMillions/v1"

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


def lwdmillions_commitment(secret: str, draw_number: int) -> str:
    """Commit to a draw secret before tickets close."""
    material = f"{COMMITMENT_DOMAIN}|commit|{int(draw_number)}|{secret}".encode()
    return hashlib.sha256(material).hexdigest()


def _committed_sample(
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
            f"{COMMITMENT_DOMAIN}|draw|{int(draw_number)}|{pool_name}|{counter}|{secret}"
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
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Derive an independently reproducible draw from a previously committed secret."""
    if not str(secret):
        raise ValueError("draw secret cannot be empty")
    if int(draw_number) <= 0:
        raise ValueError("draw number must be positive")
    main = _committed_sample(
        MAIN_NUMBER_MAX,
        MAIN_NUMBER_COUNT,
        secret=str(secret),
        draw_number=int(draw_number),
        pool_name="main",
    )
    stars = _committed_sample(
        LUCKY_STAR_MAX,
        LUCKY_STAR_COUNT,
        secret=str(secret),
        draw_number=int(draw_number),
        pool_name="stars",
    )
    return main, stars


def verify_lwdmillions_draw(
    secret: str,
    draw_number: int,
    commitment: str,
    main_numbers: Iterable[int],
    lucky_stars: Iterable[int],
) -> bool:
    """Verify both a revealed commitment and its resulting draw."""
    expected_commitment = lwdmillions_commitment(secret, draw_number)
    if not secrets.compare_digest(expected_commitment, str(commitment)):
        return False
    expected_main, expected_stars = committed_lwdmillions_draw(secret, draw_number)
    try:
        actual_main, actual_stars = validate_lwdmillions_ticket(main_numbers, lucky_stars)
    except (TypeError, ValueError):
        return False
    return expected_main == actual_main and expected_stars == actual_stars
