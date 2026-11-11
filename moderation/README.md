# Moderation

One cog for server moderation. It replaces three older cogs and keeps all of their saved data:

| Old cog | Now |
| --- | --- |
| AdminHelper | Manual actions, warnings, cases, user info, natural-language moderation |
| AntiAbuse | AutoMod spam detection, strike escalation, lockdowns |
| Honeypot | Trap channel |

## Upgrading from AdminHelper / AntiAbuse / Honeypot

```text
[p]unload adminhelper antiabuse honeypot
[p]load moderation
[p]slash enablecog moderation
[p]slash sync
```

Settings, cases, warnings, warning punishments, name history, AutoMod strikes, role overrides and honeypot settings carry over. The cog reads the old cogs' storage directly, so nothing has to be migrated.

## Commands

Every command is available under `/mod` and as a prefix command.

| Slash | Prefix | What it does |
| --- | --- | --- |
| `/mod ban` `kick` `softban` `hackban` `unban` | `[p]ban` `[p]kick` `[p]softban` `[p]hackban` `[p]unban` | Remove members. Destructive actions ask for confirmation. A ban reason can start with 0-7 to delete that many days of messages. |
| `/mod timeout` `untimeout` | `[p]timeout <member> <minutes>` `[p]untimeout` | Time members out. `/mod timeout` uses the server's default length when none is given. |
| `/mod warn` `warnings` `clearwarnings` | `[p]warn` `[p]warnings` `[p]clearwarnings` | Manual warnings. `warnings` also shows AutoMod strikes; `clearwarnings` clears both. |
| `/mod purge` | `[p]purge <amount> [member]` | Delete recent messages (200 at most by default). |
| `/mod userinfo` | `[p]userinfo` | Account, roles, warnings and name history. |
| `/mod case` `cases` `reason` `modstats` | `[p]mod case` `cases` `reason` `modstats` | The shared case log. |
| `/mod punish list/add/remove/clear` | `[p]mod punish [add/remove/clear]` | Punishments that run when a member reaches a warning count. |
| `/mod lockdown` | `[p]mod lockdown [start [minutes]/stop]` | Stop @everyone from talking for a while. |
| `/mod resetstrikes` | `[p]mod resetstrikes` | Reset everyone's AutoMod strikes. |
| `/mod roleoverride` | `[p]mod roleoverride [role] [key=value ... / clear]` | Per-role AutoMod escalation thresholds. |
| `/mod status` | `[p]mod` | Show every setting. |
| `/mod help` | `[p]mod help` | Command reference. |

You can also mention the bot: `@Bot ban @user spamming`, `@Bot timeout @user 10 minutes`, `@Bot mute o @user por 10 segundos`. English and common Portuguese forms work, with the same checks and confirmation prompts.

## Settings

All settings are in the `/control` panel (Control cog), under **Moderation**:

- **Moderation**: mod-log channel, natural-language moderation, name tracking, require a reason, confirmation prompts and their time limit, default `/mod timeout` length, purge limit (200 by default), ban and softban message cleanup.
- **DM notices**: DM on/off, per-action switches (warn, timeout, kick, ban, softban), a custom DM text with `{server}` `{action}` `{reason}` `{duration}` `{details}` placeholders, and a footer such as an appeal link.
- **AutoMod**: enforcement on/off, log channel, exempt roles, ignored channels and categories, deleting offending messages, public notices and how long they stay.
- **Filter: message rate / commands / mentions / caps / links**: each filter's on switch and limits, plus its own exempt channels and roles. The link filter also takes a list of allowed domains.
- **AutoMod escalation**: strike decay, cooldown, warn/timeout/kick/ban thresholds, and automatic lockdown.
- **Honeypot** (administrators): trap channel, log channel, logging of exempt posts, exempt roles, punishment (ban, kick, timeout or role), ban cleanup days, timeout length, how far back to clean the member's messages, punish role, role stripping, kept roles, and an optional DM before the punishment.

## Case log

Manual actions, AutoMod timeouts/kicks/bans and honeypot punishments all record a case, so `cases` and `modstats` show the full picture. AutoMod and honeypot cases list the bot as moderator.

## Permissions

The bot needs Ban Members, Kick Members, Moderate Members, Manage Messages, Manage Roles (for role punishments) and Manage Channels (for lockdowns).
