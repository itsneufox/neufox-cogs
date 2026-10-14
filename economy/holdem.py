"""One-hand, no-limit Texas Hold'em rules for the casino table."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "CDHS"
STREETS = ("Preflop", "Flop", "Turn", "River")
HAND_NAMES = (
    "High card", "Pair", "Two pair", "Three of a kind", "Straight",
    "Flush", "Full house", "Four of a kind", "Straight flush",
)


def shuffled_deck() -> list[str]:
    deck = [rank + suit for rank in RANKS for suit in SUITS]
    secrets.SystemRandom().shuffle(deck)
    return deck


def hand_rank(cards: list[str]) -> tuple[int, ...]:
    """Compare any five to seven cards; higher tuples win."""
    if not 5 <= len(cards) <= 7 or len(set(cards)) != len(cards):
        raise ValueError("A hand needs five to seven distinct cards.")
    if any(len(card) != 2 or card[0] not in RANKS or card[1] not in SUITS for card in cards):
        raise ValueError("Invalid card.")
    return max(_five_card_rank(hand) for hand in combinations(cards, 5))


def _five_card_rank(cards: tuple[str, ...]) -> tuple[int, ...]:
    values = [RANKS.index(card[0]) + 2 for card in cards]
    counts = {value: values.count(value) for value in set(values)}
    groups = sorted(((count, value) for value, count in counts.items()), reverse=True)
    flush = len({card[1] for card in cards}) == 1
    unique = set(values)
    if 14 in unique:
        unique.add(1)
    straight = max((top for top in range(5, 15) if all(top - step in unique for step in range(5))), default=0)
    if flush and straight:
        return (8, straight)
    if groups[0][0] == 4:
        return (7, groups[0][1], groups[1][1])
    if groups[0][0] == 3 and groups[1][0] == 2:
        return (6, groups[0][1], groups[1][1])
    if flush:
        return (5, *sorted(values, reverse=True))
    if straight:
        return (4, straight)
    if groups[0][0] == 3:
        return (3, groups[0][1], *sorted((v for v in values if v != groups[0][1]), reverse=True))
    if groups[0][0] == 2 and groups[1][0] == 2:
        pairs = sorted((groups[0][1], groups[1][1]), reverse=True)
        return (2, *pairs, max(v for v in values if v not in pairs))
    if groups[0][0] == 2:
        return (1, groups[0][1], *sorted((v for v in values if v != groups[0][1]), reverse=True))
    return (0, *sorted(values, reverse=True))


def split_pots(contributions: dict[int, int], contenders: set[int], ranks: dict[int, tuple[int, ...]], order: list[int]) -> dict[int, int]:
    """Return each player's winnings, including any uncontested side pots."""
    payouts = {player: 0 for player in contributions}
    previous = 0
    for level in sorted(set(contributions.values())):
        eligible = [player for player, amount in contributions.items() if amount >= level]
        amount = (level - previous) * len(eligible)
        if len(eligible) == 1:
            # Nobody matched the excess. Return it, even if that player folded.
            payouts[eligible[0]] += amount
            previous = level
            continue
        candidates = [player for player in eligible if player in contenders]
        if not candidates:
            raise ValueError("A pot has no eligible contender.")
        best = max(ranks[player] for player in candidates)
        winners = [player for player in order if player in candidates and ranks[player] == best]
        each, remainder = divmod(amount, len(winners))
        for player in winners:
            payouts[player] += each
        for player in winners[:remainder]:
            payouts[player] += 1
        previous = level
    return payouts


@dataclass
class Player:
    user_id: int
    name: str
    stack: int
    cards: list[str] = field(default_factory=list)
    contributed: int = 0
    street_bet: int = 0
    folded: bool = False
    acted: bool = False


class HoldemHand:
    def __init__(self, players: list[Player], buyin: int):
        if not 2 <= len(players) <= 6:
            raise ValueError("Hold'em needs two to six players.")
        self.players = players
        self.buyin = buyin
        self.deck = shuffled_deck()
        self.board: list[str] = []
        self.street = 0
        self.current_bet = 0
        self.minimum_raise = max(1, buyin // 10)
        self.turn = 0
        self.finished = False
        self.showdown = False
        self.payouts: dict[int, int] | None = None
        self.action_log: list[str] = []
        for player in players:
            player.cards = [self.deck.pop(), self.deck.pop()]
        small = max(1, buyin // 20)
        big = max(2, buyin // 10)
        self._commit(players[0], min(small, players[0].stack))
        self._commit(players[1], min(big, players[1].stack))
        self.current_bet = max(player.street_bet for player in players)
        self.minimum_raise = big
        self.turn = 0 if len(players) == 2 else 2
        self.action_log.append(f"{players[0].name} posts {small:,}; {players[1].name} posts {big:,}.")

    def _commit(self, player: Player, amount: int):
        player.stack -= amount
        player.street_bet += amount
        player.contributed += amount

    @property
    def actor(self) -> Player:
        return self.players[self.turn]

    @property
    def pot(self) -> int:
        return sum(player.contributed for player in self.players)

    def act(self, user_id: int, action: str, amount: int = 0):
        if self.finished or self.actor.user_id != user_id:
            raise ValueError("It is not your turn.")
        player = self.actor
        to_call = self.current_bet - player.street_bet
        if action == "fold":
            player.folded = True
            self.action_log.append(f"{player.name} folds.")
        elif action == "check":
            if to_call:
                raise ValueError(f"Call {to_call:,} or fold.")
            player.acted = True
            self.action_log.append(f"{player.name} checks.")
        elif action == "call":
            if not to_call:
                raise ValueError("There is no bet to call; check instead.")
            paid = min(player.stack, to_call)
            self._commit(player, paid)
            player.acted = True
            self.action_log.append(f"{player.name} calls {paid:,}" + (" (all in)." if not player.stack else "."))
        elif action == "raise":
            # amount is the new total bet on this street.
            if player.acted:
                raise ValueError("Betting has not been reopened; call or fold.")
            if amount <= self.current_bet:
                raise ValueError("Raise to an amount above the current bet.")
            needed = amount - player.street_bet
            if needed > player.stack:
                raise ValueError("You do not have enough chips; raise to your all-in total instead.")
            increase = amount - self.current_bet
            if increase < self.minimum_raise and needed != player.stack:
                raise ValueError(f"Minimum raise is {self.current_bet + self.minimum_raise:,}, unless all in.")
            self._commit(player, needed)
            if increase >= self.minimum_raise:
                self.minimum_raise = increase
                for other in self.players:
                    if not other.folded and other.stack:
                        other.acted = False
            self.current_bet = amount
            player.acted = True
            self.action_log.append(f"{player.name} raises to {amount:,}" + (" (all in)." if not player.stack else "."))
        else:
            raise ValueError("Use fold, check, call, or raise.")
        self._advance()

    def _advance(self):
        live = [player for player in self.players if not player.folded]
        if len(live) == 1:
            self._finish()
            return
        pending = [player for player in live if player.stack and (not player.acted or player.street_bet != self.current_bet)]
        if pending:
            for offset in range(1, len(self.players) + 1):
                index = (self.turn + offset) % len(self.players)
                if self.players[index] in pending:
                    self.turn = index
                    return
        if self.street == 3 or sum(bool(player.stack) for player in live) <= 1:
            while len(self.board) < 5:
                self.board.append(self.deck.pop())
            self._finish()
            return
        self.street += 1
        self.board.extend(self.deck.pop() for _ in range(3 if self.street == 1 else 1))
        self.current_bet = 0
        self.minimum_raise = max(2, self.buyin // 10)
        for player in live:
            player.street_bet = 0
            player.acted = False
        self.action_log.append(f"{STREETS[self.street]}: {' '.join(self.board)}")
        first = 1 if len(self.players) == 2 else 0
        for offset in range(len(self.players)):
            index = (first + offset) % len(self.players)
            player = self.players[index]
            if not player.folded and player.stack:
                self.turn = index
                break

    def _finish(self):
        self.finished = True
        contenders = {player.user_id for player in self.players if not player.folded}
        self.showdown = len(contenders) > 1
        ranks = {player.user_id: hand_rank(player.cards + self.board) for player in self.players if player.user_id in contenders} if len(contenders) > 1 else {next(iter(contenders)): (0,)}
        self.payouts = split_pots(
            {player.user_id: player.contributed for player in self.players},
            contenders, ranks, [player.user_id for player in self.players],
        )
        for player in self.players:
            player.stack += self.payouts[player.user_id]
