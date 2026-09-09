# Converter

A Red-DiscordBot cog for fuel economy, distance, speed, weight, volume,
temperature, area, and time. No additional dependencies or external services.

Load with `[p]load converter` after installation. Replace `[p]` with your bot's prefix.

| Command | Example result (rounded) |
| --- | --- |
| `[p]convert 15 km/L to L/100km` | 6.6667 L/100km |
| `[p]convert 15 km/L to mpg` | 35.2822 mpg-US |
| `[p]convert 15 km/L to mpg-UK` | 42.3721 mpg-UK |
| `[p]convert 100 km/h mph` | 62.1371 mph |
| `[p]convert 32 F to C` | 0 C |
| `[p]convert 10 kg to lb` | 22.0462 lb |
| `[p]convert units` | Lists every supported unit by category |

`conv` is an alias for `convert`. Unit names are case-insensitive; common full
names, plurals, and symbols such as `°C` and `m²` are accepted. Multiword units
work without quotes: `[p]convert 10 square feet to m2`.

Plain `mpg` and `gal` use US gallons. Use `mpg-UK` and `gal-UK` for imperial
gallons. Fuel economy must be positive; temperature cannot be below absolute
zero. Conversions between unrelated categories are rejected. Results use up to
10 significant digits. Use a decimal point without thousands separators.
