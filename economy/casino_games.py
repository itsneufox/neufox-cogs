from __future__ import annotations

import secrets


HIGH_CARD_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
HIGH_CARD_SUITS = ("C", "D", "H", "S")
HIGH_CARD_RANK_VALUES = {rank: value for value, rank in enumerate(HIGH_CARD_RANKS, start=2)}
HIGH_CARD_RANK_NAMES = {"J": "Jack", "Q": "Queen", "K": "King", "A": "Ace"}
HIGH_CARD_SUIT_NAMES = {"C": "Clubs", "D": "Diamonds", "H": "Hearts", "S": "Spades"}

ROULETTE_RED_NUMBERS = frozenset(
    {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
)
ROULETTE_EVEN_MONEY_BETS = frozenset({"red", "black", "odd", "even", "low", "high"})
ROULETTE_DOZEN_BETS = frozenset({"dozen1", "dozen2", "dozen3"})
ROULETTE_BET_ALIASES = {
    "r": "red",
    "red": "red",
    "b": "black",
    "black": "black",
    "o": "odd",
    "odd": "odd",
    "e": "even",
    "even": "even",
    "low": "low",
    "1-18": "low",
    "high": "high",
    "19-36": "high",
    "1st12": "dozen1",
    "first12": "dozen1",
    "dozen1": "dozen1",
    "d1": "dozen1",
    "2nd12": "dozen2",
    "second12": "dozen2",
    "dozen2": "dozen2",
    "d2": "dozen2",
    "3rd12": "dozen3",
    "third12": "dozen3",
    "dozen3": "dozen3",
    "d3": "dozen3",
    "green": "number:0",
    "zero": "number:0",
}


def calculate_blackjack_dealer_natural_payout(wager: int, insurance_bet: int = 0) -> int:
    """Apply the house redraw rule while preserving the standard 2:1 insurance win."""
    return wager + insurance_bet * 3


def format_blackjack_action_log(entries: list[str], character_limit: int = 1024) -> str:
    """Fit the newest chronological blackjack actions into one Discord embed field."""
    if not entries:
        return "No actions yet."
    complete = "\n".join(entries)
    if len(complete) <= character_limit:
        return complete

    omission = "… earlier actions omitted\n"
    available = character_limit - len(omission)
    kept: list[str] = []
    used = 0
    for entry in reversed(entries):
        added = len(entry) + (1 if kept else 0)
        if used + added > available:
            break
        kept.append(entry)
        used += added
    if not kept:
        return complete[-character_limit:]
    return omission + "\n".join(reversed(kept))


def draw_high_card() -> tuple[tuple[str, str], tuple[str, str]]:
    """Draw distinct dealer and player cards from one securely shuffled deck."""
    deck = [(rank, suit) for rank in HIGH_CARD_RANKS for suit in HIGH_CARD_SUITS]
    secrets.SystemRandom().shuffle(deck)
    return deck.pop(), deck.pop()


def high_card_label(card: tuple[str, str]) -> str:
    rank, suit = card
    return f"{HIGH_CARD_RANK_NAMES.get(rank, rank)} of {HIGH_CARD_SUIT_NAMES[suit]}"


def calculate_high_card_payout(
    wager: int,
    dealer_card: tuple[str, str],
    player_card: tuple[str, str],
) -> tuple[int, str]:
    dealer_value = HIGH_CARD_RANK_VALUES[dealer_card[0]]
    player_value = HIGH_CARD_RANK_VALUES[player_card[0]]
    if player_value > dealer_value:
        return wager * 2, "player card is higher"
    if player_value == dealer_value:
        return wager, "equal ranks - push"
    return 0, "dealer card is higher"


def normalize_roulette_bet(choice: str) -> str | None:
    normalized = choice.strip().casefold().replace("_", "").replace(" ", "")
    if normalized in ROULETTE_BET_ALIASES:
        return ROULETTE_BET_ALIASES[normalized]
    try:
        number = int(normalized)
    except ValueError:
        return None
    if 0 <= number <= 36:
        return f"number:{number}"
    return None


def roulette_number_color(number: int) -> str:
    if number == 0:
        return "green"
    return "red" if number in ROULETTE_RED_NUMBERS else "black"


def roulette_bet_label(bet: str) -> str:
    if bet.startswith("number:"):
        return bet.partition(":")[2]
    labels = {
        "low": "low (1-18)",
        "high": "high (19-36)",
        "dozen1": "1st dozen (1-12)",
        "dozen2": "2nd dozen (13-24)",
        "dozen3": "3rd dozen (25-36)",
    }
    return labels.get(bet, bet)


def calculate_roulette_payout(wager: int, bet: str, result: int) -> tuple[int, str]:
    """Return the total roulette payout (including stake) and the matching rule."""
    if result < 0 or result > 36:
        raise ValueError("roulette result must be between 0 and 36")

    if bet.startswith("number:"):
        selected_number = int(bet.partition(":")[2])
        if selected_number < 0 or selected_number > 36:
            raise ValueError("roulette number bet must be between 0 and 36")
        won = result == selected_number
        multiplier = 36
        rule = "straight-up number (36x)"
    elif bet in ROULETTE_EVEN_MONEY_BETS:
        color = roulette_number_color(result)
        won = (
            (bet in {"red", "black"} and color == bet)
            or (bet == "odd" and result != 0 and result % 2 == 1)
            or (bet == "even" and result != 0 and result % 2 == 0)
            or (bet == "low" and 1 <= result <= 18)
            or (bet == "high" and 19 <= result <= 36)
        )
        multiplier = 2
        rule = "even-money bet (2x)"
    elif bet in ROULETTE_DOZEN_BETS:
        dozen = int(bet[-1])
        won = 1 + (result - 1) // 12 == dozen if result else False
        multiplier = 3
        rule = "dozen bet (3x)"
    else:
        raise ValueError("unknown roulette bet")

    return (wager * multiplier if won else 0), rule
