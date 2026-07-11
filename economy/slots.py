from __future__ import annotations

import secrets
from io import BytesIO
from pathlib import Path

from PIL import Image


ASSETS_PATH = Path(__file__).parent / "assets"
SLOT_SYMBOLS = ("lemon", "seven", "diamond", "coin", "bell", "cherry")
SLOT_EMOJIS = {
    "lemon": "🍋",
    "seven": "7️⃣",
    "diamond": "💎",
    "coin": "🪙",
    "bell": "🔔",
    "cherry": "🍒",
}
SLOT_TRIPLE_MULTIPLIERS = {
    "lemon": 4,
    "seven": 80,
    "diamond": 40,
    "coin": 25,
    "bell": 10,
    "cherry": 5,
}
SLOT_ITEM_HEIGHT = 180
SLOT_REEL_WIDTH = 151
SLOT_REEL_LEFT = 25
SLOT_REEL_TOP = 100
SLOT_FRAME_COUNT = 40
SLOT_FRAME_DURATION_MS = 35
SLOT_REEL_DELAYS = (0.0, 0.10, 0.20)


def _stop_for_symbol(symbol: str) -> int:
    symbol_id = SLOT_SYMBOLS.index(symbol)
    residue = (symbol_id - 1) % len(SLOT_SYMBOLS)
    candidates = [stop for stop in range(43, 60) if stop % 6 == residue]
    return secrets.choice(candidates)


def draw_slot_spin() -> tuple[tuple[str, str, str], tuple[int, int, int]]:
    symbols = tuple(secrets.choice(SLOT_SYMBOLS) for _ in range(3))
    stops = tuple(_stop_for_symbol(symbol) for symbol in symbols)
    return symbols, stops


def calculate_slot_payout(wager: int, symbols: tuple[str, str, str]) -> tuple[int, str]:
    if len(set(symbols)) == 1:
        multiplier = SLOT_TRIPLE_MULTIPLIERS[symbols[0]]
        return wager * multiplier, f"triple {symbols[0]} ({multiplier}x)"
    if len(set(symbols)) == 3 and symbols.count("cherry") == 1:
        return wager // 2, "single cherry refund (0.5x)"
    return 0, "no winning line"


def _eased_progress(raw_progress: float, delay: float) -> float:
    if raw_progress <= delay:
        return 0.0
    scaled = min(1.0, (raw_progress - delay) / (1.0 - delay))
    return 1.0 - ((1.0 - scaled) ** 3)


def render_slot_spin(stops: tuple[int, int, int]) -> BytesIO:
    """Render one non-looping slot spin whose last frame matches the selected symbols."""
    with Image.open(ASSETS_PATH / "slot-face.png") as source:
        facade = source.convert("RGBA")
    with Image.open(ASSETS_PATH / "slot-reel.png") as source:
        reel = source.convert("RGBA")
    base = Image.new("RGBA", facade.size, color=(255, 255, 255, 255))
    frames: list[Image.Image] = []
    try:
        for frame_index in range(SLOT_FRAME_COUNT + 1):
            raw_progress = frame_index / SLOT_FRAME_COUNT
            progress = tuple(
                _eased_progress(raw_progress, delay) for delay in SLOT_REEL_DELAYS
            )
            frame = base.copy()
            for reel_index, (stop, reel_progress) in enumerate(zip(stops, progress)):
                frame.paste(
                    reel,
                    (
                        SLOT_REEL_LEFT + SLOT_REEL_WIDTH * reel_index,
                        SLOT_REEL_TOP - int(SLOT_ITEM_HEIGHT * stop * reel_progress),
                    ),
                )
            frame.alpha_composite(facade)
            frames.append(frame)

        output = BytesIO()
        durations = [SLOT_FRAME_DURATION_MS] * (len(frames) - 1) + [1400]
        frames[0].save(
            output,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            optimize=False,
            disposal=2,
        )
        output.seek(0)
        return output
    finally:
        for frame in frames:
            frame.close()
        base.close()
        reel.close()
        facade.close()
