# Utility

Everyday tools in one cog. Replaces the old Converter, UrbanDictionary, Wiki,
Reminders and SixSeven cogs and keeps their saved reminders and settings.

Every command below works as a slash command and as a prefix command
(`[p]convert`, `[p]urban`, ...). Enable the slash versions with
`[p]slash enablecog utility` and `[p]slash sync`.

## Commands

| Command | What it does |
| --- | --- |
| `/convert <query>` | Convert units: `15 km/L to mpg`, `100 km/h mph`, `32 F to C`. Leave empty (or `units`) to list every supported unit. |
| `/urban [term]` | Urban Dictionary definitions with page buttons. Leave empty for a random entry. Prefix aliases: `[p]ud`, `[p]define`; `[p]urban random` also gives a random entry. |
| `/wiki <search term>` | Search the open.mp documentation and open a result with the numbered buttons. Leave empty for help. |
| `/remind me <when> <message>` | Remind yourself. |
| `/remind member <member> <when> <message>` | Remind another member. |
| `/remind list [member]` | List active reminders you created. Staff can list anyone's or filter by member. |
| `/remind cancel <id>` | Cancel a reminder you created or that is for you. Staff can cancel any. |
| `[p]sixseven [en\|pt]` | Admins: list the built-in six seven replies for a language. |

`when` accepts compact durations like `10m`, `2h30m`, `1d` or `1w2d`, up to 366
days. Reminders are delivered by DM, falling back to the original channel, and
survive restarts.

Converter notes: plain `mpg` and `gal` use US gallons; use `mpg-UK` and
`gal-UK` for imperial. Unit names are case-insensitive and accept common full
names, plurals and symbols such as `°C` and `m²`. Results use up to 10
significant digits.

## Settings

Server settings live in the `/control` panel (Control cog), under **Utility**:

- **Reminders**: limit per target member (0 = unlimited), the role at or above
  which members are staff for reminders, and roles protected from reminders by
  non-staff. Administrators are always staff.
- **SixSeven**: replies on/off, cooldown, a fixed reply text, the default
  language, and English/Portuguese channel and category overrides. SixSeven
  replies to `six seven`, `six-seven`, `6 7`, `67` and similar.

## Data

Reminders store creator and target user IDs, server and channel IDs,
timestamps and the reminder text until they are delivered, cancelled or
deleted. Data deletion requests remove every reminder a user created or is the
target of. Search terms go to Urban Dictionary and the open.mp search only when
a lookup command runs. Requires `beautifulsoup4`.
