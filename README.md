# neufox's Cogs
Cogs for Red-DiscordBot.

| Cog | What it does |
| --- | --- |
| `economy` | Global LWD$ balances, `/claim`, the `/eco` hub, shop, perks, casino, LWDMillions lottery, nightlife, money sinks and an HTTP API. |
| `activity` | XP levels, message, voice and reaction stats, `/rank`, `/leaderboard`, LWD$ activity rewards. |
| `moderation` | Manual moderation and cases (`/mod`), AutoMod and the honeypot trap channel. |
| `utility` | `/convert`, `/urban`, `/wiki`, `/remind` and the six-seven joke responder. |
| `media` | Radio streaming, MusicBoard and EmbedFixer (`/media`). |
| `control` | One `/control` panel for every cog's settings, and `/help` listing every slash command by category. |
| `tickettool` | Ticket system. |
| `voicechannels` | On-demand temporary voice channels. |
| `banappeals` | Ban appeal moderation in a forum or text channel. |
| `messagearchive` | Local SQLite archive of guild messages. |
| `projectranking` | Weekly community voting for forum projects. |

## Upgrading from the old cogs

Several cogs were merged. The merged cogs read the old cogs' saved data, so nothing has to be moved. **Do not** uninstall the old cogs with any "clear data" option: that deletes the data the new cogs use.

| Old cogs | New cog |
| --- | --- |
| `adminhelper`, `antiabuse`, `honeypot` | `moderation` |
| `leveling`, `activitystats` | `activity` |
| `converter`, `urbandictionary`, `wiki`, `reminders`, `sixseven` | `utility` |
| `radio`, `musicboard`, `embedfixer` | `media` |

```text
[p]unload adminhelper antiabuse honeypot leveling activitystats converter urbandictionary wiki reminders sixseven radio musicboard embedfixer
[p]repo update neufox-s-Cogs
[p]cog install neufox-s-Cogs moderation activity utility media
[p]cog update economy
[p]load moderation activity utility media
[p]reload economy
[p]slash enablecog economy moderation activity utility media control
[p]slash sync
```

Old cogs left loaded next to the merged cogs would act on the same messages and data twice, so unload them first. Settings that used to be hidden prefix commands are now in `[p]control`.
