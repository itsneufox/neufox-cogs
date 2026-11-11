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
| `/mod timeout` `untimeout` | `[p]timeout <member> <minutes>` `[p]untimeout` | Time members out. |
| `/mod warn` `warnings` `clearwarnings` | `[p]warn` `[p]warnings` `[p]clearwarnings` | Manual warnings. `warnings` also shows AutoMod strikes; `clearwarnings` clears both. |
| `/mod purge` | `[p]purge <1-200> [member]` | Delete recent messages. |
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

- **Moderation**: mod-log channel, DM notices, natural-language moderation, name tracking, ban and softban message cleanup.
- **AutoMod**: enforcement on/off, log channel, exempt roles, ignored channels and categories, and detection limits (message rate, command spam, mentions, links, caps).
- **AutoMod escalation**: strike decay, cooldown, warn/timeout/kick/ban thresholds, and automatic lockdown.
- **Honeypot** (administrators): trap channel, log channel, exempt roles, punishment (ban, kick or role), punish role, role stripping and kept roles.

## Case log

Manual actions, AutoMod timeouts/kicks/bans and honeypot punishments all record a case, so `cases` and `modstats` show the full picture. AutoMod and honeypot cases list the bot as moderator.

## Permissions

The bot needs Ban Members, Kick Members, Moderate Members, Manage Messages, Manage Roles (for role punishments) and Manage Channels (for lockdowns).
