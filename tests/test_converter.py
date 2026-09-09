import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "conversions", Path(__file__).parents[1] / "converter" / "conversions.py"
)
conversions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(conversions)


class ConversionTests(unittest.TestCase):
    def test_known_conversions(self):
        cases = [
            (15, "km/L", "L/100km", 100 / 15),
            (5, "L/100km", "km/L", 20),
            (1, "mpg", "km/L", .425143707430272),
            (1, "mpg-UK", "km/L", .354006189934648),
            (1, "mi", "km", 1.609344),
            (100, "km/h", "mph", 62.1371192237),
            (1, "lb", "kg", .45359237),
            (1, "gal-UK", "L", 4.54609),
            (32, "F", "C", 0),
            (-40, "C", "F", -40),
            (0, "K", "C", -273.15),
            (1, "acre", "m²", 4046.8564224),
            (1, "week", "h", 168),
        ]
        for value, source, target, expected in cases:
            with self.subTest(source=source, target=target):
                self.assertAlmostEqual(conversions.convert(value, source, target), expected)

    def test_parser(self):
        for query in ("15 km/L to mpg", "15km/l mpg", "15 km/L in US MPG", "1.5e1 KM/L TO mpg-US"):
            self.assertEqual(conversions.parse_conversion(query), (15, "km/L", "mpg-US"))
        self.assertEqual(conversions.parse_conversion("10 square feet to m²"), (10, "ft2", "m2"))
        self.assertEqual(conversions.parse_conversion("1 m in in"), (1, "m", "in"))

    def test_invalid_values(self):
        for value, source, target in [
            (0, "km/L", "L/100km"), (-1, "mpg", "km/L"),
            (-274, "C", "F"), (-1, "K", "C"),
            (1, "km", "kg"), (1, "bogus", "m"),
            (float("inf"), "m", "km"), (float("nan"), "C", "F"),
            (1e308, "km", "m"), (5e-324, "L/100km", "mpg"),
        ]:
            with self.subTest(value=value, source=source):
                with self.assertRaises(ValueError):
                    conversions.convert(value, source, target)

    def test_invalid_queries(self):
        for query in ("", "km to mi", "1 km", "1 bogus to mi", "nan m to km", "1,000 m to km"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                conversions.parse_conversion(query)

    def test_all_units_round_trip(self):
        for source, (category, *_) in conversions.UNITS.items():
            for target, (other_category, *_) in conversions.UNITS.items():
                if category == other_category:
                    with self.subTest(source=source, target=target):
                        result = conversions.convert(300, source, target)
                        self.assertAlmostEqual(conversions.convert(result, target, source), 300)


if __name__ == "__main__":
    unittest.main()
