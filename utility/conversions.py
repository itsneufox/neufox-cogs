"""Dependency-free unit definitions, parsing, and conversion."""

import math
import re


# Each unit maps to (category, scale, offset, reciprocal). Base = value * scale
# + offset, or scale / value for reciprocal fuel-consumption units.
UNITS = {}
ALIASES = {}


def _add(category, name, scale, *aliases, offset=0.0, reciprocal=False):
    UNITS[name] = (category, scale, offset, reciprocal)
    for alias in (name, *aliases):
        ALIASES[_normalize(alias)] = name


def _normalize(unit):
    return re.sub(r"[\s_]+", "", unit.casefold()).replace("²", "2").replace("°", "")


_add("Fuel economy", "km/L", 1, "kpl", "kmpl")
_add("Fuel economy", "L/100km", 100, "l/100 km", reciprocal=True)
_add("Fuel economy", "mpg-US", 1.609344 / 3.785411784, "mpg", "mpg us", "us mpg")
_add("Fuel economy", "mpg-UK", 1.609344 / 4.54609, "mpg uk", "uk mpg", "mpg imperial")

_add("Distance", "mm", .001, "millimeter", "millimeters", "millimetre", "millimetres")
_add("Distance", "cm", .01, "centimeter", "centimeters", "centimetre", "centimetres")
_add("Distance", "m", 1, "meter", "meters", "metre", "metres")
_add("Distance", "km", 1000, "kilometer", "kilometers", "kilometre", "kilometres")
_add("Distance", "in", .0254, "inch", "inches")
_add("Distance", "ft", .3048, "foot", "feet")
_add("Distance", "yd", .9144, "yard", "yards")
_add("Distance", "mi", 1609.344, "mile", "miles")

_add("Speed", "km/h", 1 / 3.6, "kph", "kmh")
_add("Speed", "mph", .44704, "mi/h")
_add("Speed", "m/s", 1, "mps")
_add("Speed", "kn", 1852 / 3600, "knot", "knots", "kt")

_add("Weight", "mg", .000001, "milligram", "milligrams")
_add("Weight", "g", .001, "gram", "grams")
_add("Weight", "kg", 1, "kilogram", "kilograms")
_add("Weight", "oz", .028349523125, "ounce", "ounces")
_add("Weight", "lb", .45359237, "lbs", "pound", "pounds")
_add("Weight", "st", 6.35029318, "stone", "stones")
_add("Weight", "t", 1000, "tonne", "tonnes", "metric ton")

_add("Volume", "mL", .001, "milliliter", "milliliters", "millilitre", "millilitres")
_add("Volume", "L", 1, "liter", "liters", "litre", "litres")
_add("Volume", "gal-US", 3.785411784, "gal", "gallon", "gallons", "us gallon", "us gal")
_add("Volume", "gal-UK", 4.54609, "uk gallon", "uk gal", "imperial gallon")

_add("Temperature", "C", 1, "celsius", offset=273.15)
_add("Temperature", "F", 5 / 9, "fahrenheit", offset=273.15 - 32 * 5 / 9)
_add("Temperature", "K", 1, "kelvin")

_add("Area", "m2", 1, "sqm", "square meters", "square metres")
_add("Area", "km2", 1_000_000, "sqkm", "square kilometers", "square kilometres")
_add("Area", "ft2", .09290304, "sqft", "square feet")
_add("Area", "ha", 10000, "hectare", "hectares")
_add("Area", "acre", 4046.8564224, "acres")

_add("Time", "s", 1, "sec", "second", "seconds")
_add("Time", "min", 60, "minute", "minutes")
_add("Time", "h", 3600, "hr", "hour", "hours")
_add("Time", "day", 86400, "days", "d")
_add("Time", "week", 604800, "weeks", "w")


def resolve_unit(unit):
    """Return a canonical unit name, without echoing untrusted input in errors."""
    try:
        return ALIASES[_normalize(unit)]
    except KeyError:
        raise ValueError("Unknown unit. Use the units command to see supported units.") from None


def convert(value, source, target):
    source, target = resolve_unit(source), resolve_unit(target)
    category, scale, offset, reciprocal = UNITS[source]
    target_category, target_scale, target_offset, target_reciprocal = UNITS[target]
    if category != target_category:
        raise ValueError("Those units measure different things. Choose units from the same category.")
    if not math.isfinite(value):
        raise ValueError("Enter a finite number.")
    if category == "Fuel economy" and value <= 0:
        raise ValueError("Fuel economy values must be greater than zero.")
    base = scale / value if reciprocal else value * scale + offset
    if category == "Temperature" and base < -1e-10:
        raise ValueError("Temperature cannot be below absolute zero.")
    if target_reciprocal and base == 0:
        raise ValueError("That value is too small to convert.")
    result = target_scale / base if target_reciprocal else (base - target_offset) / target_scale
    if not math.isfinite(base) or not math.isfinite(result):
        raise ValueError("That value is too large to convert.")
    return result


def parse_conversion(query):
    """Accept '15 km/L to mpg', '15 km/L mpg', and attached units like '15km/L'."""
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.+?)\s*", query)
    if not match:
        raise ValueError("Use: convert <number> <from unit> to <to unit>.")
    value = float(match[1])
    units = re.split(r"\s+(?:to|in)\s+", match[2], maxsplit=1, flags=re.IGNORECASE)
    if len(units) == 2:
        return value, resolve_unit(units[0]), resolve_unit(units[1])
    # Try whitespace boundaries so multiword aliases also work without 'to'.
    for boundary in re.finditer(r"\s+", match[2]):
        try:
            source = resolve_unit(match[2][:boundary.start()])
            target = resolve_unit(match[2][boundary.end():])
            return value, source, target
        except ValueError:
            continue
    raise ValueError("Use: convert <number> <from unit> to <to unit>. See convert units for supported units.")
