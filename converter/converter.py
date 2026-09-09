from redbot.core import commands

from .conversions import UNITS, convert, parse_conversion


class Converter(commands.Cog):
    """Convert common measurements, including fuel economy."""

    @commands.group(name="convert", aliases=["conv"], invoke_without_command=True)
    async def convert_command(self, ctx: commands.Context, *, query: str = None):
        """Convert units: `[p]convert 15 km/L to mpg`.

        Also accepts `[p]convert 100 km/h mph`. MPG and gallons default to US.
        Use mpg-UK or gal-UK for imperial units. Run `[p]convert units` for a list.
        """
        if not query:
            await ctx.send_help()
            return
        try:
            value, source, target = parse_conversion(query)
            result = convert(value, source, target)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"{value:.10g} {source} ≈ {result:.10g} {target}")

    @convert_command.command(name="units")
    async def units(self, ctx: commands.Context):
        """List supported units by category."""
        categories = {}
        for unit, (category, *_) in UNITS.items():
            categories.setdefault(category, []).append(unit)
        lines = [f"**{category}:** {', '.join(units)}" for category, units in categories.items()]
        lines.append("`mpg` and `gal` mean US units. Use `mpg-UK` and `gal-UK` for imperial units.")
        lines.append(f"Example: `{ctx.clean_prefix}convert 15 km/L to L/100km`")
        await ctx.send("\n".join(lines))
