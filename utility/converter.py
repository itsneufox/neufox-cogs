"""/convert: unit conversion, including fuel economy."""

from __future__ import annotations

from redbot.core import app_commands, commands

from .conversions import UNITS, convert, parse_conversion
from .layouts import NO_PINGS, text_card


def convert_query(query: str) -> str:
    """Return the formatted result. Raises ValueError with a user-facing message."""
    value, source, target = parse_conversion(query)
    result = convert(value, source, target)
    return f"{value:.10g} {source} ≈ **{result:.10g} {target}**"


def units_text(example_command: str) -> str:
    categories: dict[str, list[str]] = {}
    for unit, (category, *_) in UNITS.items():
        categories.setdefault(category, []).append(unit)
    lines = ["## Supported units"]
    lines.extend(f"**{category}:** {', '.join(units)}" for category, units in categories.items())
    lines.append("`mpg` and `gal` mean US units. Use `mpg-UK` and `gal-UK` for imperial units.")
    lines.append(f"-# Example: `{example_command} 15 km/L to L/100km`")
    return "\n".join(lines)


class ConverterMixin:
    @commands.hybrid_command(name="convert")
    @app_commands.describe(query="For example: 15 km/L to mpg. Leave empty to list units.")
    async def convert_command(self, ctx: commands.Context, *, query: str | None = None):
        """Convert units, like `15 km/L to mpg` or `100 km/h mph`.

        Leave the query empty (or use `units`) to list every supported unit.
        MPG and gallons default to US; use mpg-UK or gal-UK for imperial.
        """
        if not query or query.strip().casefold() == "units":
            example = "/convert" if ctx.interaction else f"{ctx.clean_prefix}convert"
            await ctx.send(view=text_card(units_text(example)), ephemeral=True, allowed_mentions=NO_PINGS)
            return
        try:
            text = convert_query(query)
        except ValueError as error:
            await ctx.send(str(error), ephemeral=True)
            return
        await ctx.send(view=text_card(text), allowed_mentions=NO_PINGS)
