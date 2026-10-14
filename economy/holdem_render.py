"""Render Hold'em cards and player seats with the bundled casino artwork."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ASSETS = Path(__file__).parent / "assets"
CARDS = ASSETS / "cards"
CARD_SIZE = (90, 135)
SEATS = ((15, 565), (775, 565), (15, 30), (775, 30), (15, 300), (775, 300))


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _card_name(code: str) -> str:
    return "red_back.png" if code == "back" else f"{'10' if code[0] == 'T' else code[0]}{code[1]}.png"


def _paste_card(canvas: Image.Image, code: str, x: int, y: int):
    with Image.open(CARDS / _card_name(code)) as source:
        card = source.convert("RGBA")
        if card.size != CARD_SIZE:
            resized = card.resize(CARD_SIZE, Image.Resampling.LANCZOS)
            card.close()
            card = resized
        try:
            canvas.alpha_composite(card, (x, y))
        finally:
            card.close()


def _paste_avatar(canvas: Image.Image, raw: bytes | None, x: int, y: int, name: str):
    if raw:
        try:
            with Image.open(BytesIO(raw)) as source:
                avatar = ImageOps.fit(source.convert("RGBA"), (44, 44), method=Image.Resampling.LANCZOS)
            mask = Image.new("L", (44, 44), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 43, 43), fill=255)
            canvas.paste(avatar, (x, y), mask)
            avatar.close()
            mask.close()
            return
        except (OSError, ValueError):
            pass
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((x, y, x + 43, y + 43), fill="#315e50", outline="#cfbc86", width=2)
    draw.text((x + 14, y + 10), (name[:1] or "?").upper(), fill="white", font=_font(18))


def render_holdem_table(players, board: list[str], avatars: dict[int, bytes], *, active_id: int | None = None, finished: bool = False, showdown: bool = False) -> BytesIO:
    """Show the board and seats; only reveal live hole cards at showdown."""
    with Image.open(ASSETS / "table.png") as source:
        background = source.convert("RGBA")
    canvas = Image.new("RGBA", (1000, 760), "#103b2f")
    try:
        canvas.alpha_composite(background, (0, 180))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((230, 292, 770, 463), radius=18, fill=(5, 35, 25, 210), outline="#d5b568", width=3)
        for index in range(5):
            _paste_card(canvas, board[index] if index < len(board) else "back", 255 + index * 98, 310)
        draw.text((390, 255), "COMMUNITY CARDS", fill="white", font=_font(19))
        for index, player in enumerate(players):
            x, y = SEATS[index]
            is_active = player.user_id == active_id
            outline = "#ffd54f" if is_active else "#789c8d"
            draw.rounded_rectangle((x - 10, y - 25, x + 210, y + 175), radius=15, fill=(5, 28, 22, 230), outline=outline, width=4 if is_active else 2)
            _paste_avatar(canvas, avatars.get(player.user_id), x, y - 27, player.name)
            name = player.name[:14] + ("…" if len(player.name) > 14 else "")
            draw.text((x + 50, y - 15), name, fill="white", font=_font(16))
            for card_index, card in enumerate(player.cards):
                visible = finished and showdown and not player.folded
                _paste_card(canvas, card if visible else "back", x + card_index * 100, y + 18)
            state = "FOLDED" if player.folded else ("ALL IN" if not player.stack and not finished else "")
            draw.text((x, y + 154), f"{player.stack:,} chips {state}", fill="#ffe7a5", font=_font(15))
        output = BytesIO()
        canvas.convert("RGB").save(output, format="PNG", optimize=True)
        output.seek(0)
        return output
    finally:
        background.close()
        canvas.close()


def render_hole_cards(cards: list[str]) -> BytesIO:
    """Render only the requesting player's two private cards."""
    if len(cards) != 2:
        raise ValueError("Hole cards must contain exactly two cards.")
    canvas = Image.new("RGBA", (260, 195), "#103b2f")
    try:
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((8, 8, 252, 185), radius=15, outline="#d5b568", width=3)
        _paste_card(canvas, cards[0], 33, 20)
        _paste_card(canvas, cards[1], 137, 20)
        draw.text((77, 162), "YOUR HOLE CARDS", fill="white", font=_font(13))
        output = BytesIO()
        canvas.convert("RGB").save(output, format="PNG", optimize=True)
        output.seek(0)
        return output
    finally:
        canvas.close()
