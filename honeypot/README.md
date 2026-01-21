# Honeypot Cog for Red Bot

A lightweight moderation cog that lets you mark a single channel per guild as a honeypot. Anyone who sends a message in that channel has the message deleted and receives the configured punishment automatically, with Discord optionally cleaning up to a day of message history when banning.

## Features

- Configure a honeypot channel per guild with a simple admin command.
- Automatically deletes the triggering message in the honeypot channel.
- Ban action prunes up to one day of message history through Discord's ban endpoint.
- Choose between banning, kicking, or applying a custom role to offenders.
- Optional role stripping so offenders keep only the configured punish role, with per-role exceptions.
- Kick and role punishments generate a log embed with a Ban button so moderators can manually review/escalate.
- Optional exemption list so trusted roles can speak in the honeypot without punishment.
- Optional log channel to receive embeds whenever someone trips (or is exempt from) the honeypot.
- Fail-safe handling so missing permissions never crash your bot.

## Installation

### Using Red's Downloader (recommended)

```text
[p]repo add neufox-honeypot https://github.com/itsneufox/neufox-honeypot-cog
[p]cog install neufox-honeypot honeypot
[p]load honeypot
```

### Manual installation

1. Download or clone this repository.
2. Copy the `honeypot` folder into your Red bot's `cogs` directory.
3. Install requirements: `pip install -r requirements.txt`.
4. Load with `[p]load honeypot`.

## Configuration

Grant your bot `Manage Messages` in the honeypot channel and `Ban Members` server-wide. Then select the honeypot channel:

```text
[p]honeypot set #trap-channel
```

Optionally configure a log channel:

```text
[p]honeypot log #mod-logs
```

You can re-run the command at any time to move the trap to another text channel.

## Usage

- `[p]honeypot` — View current status/configuration.
- `[p]honeypot set <channel>` — Save the honeypot channel for the guild.
- `[p]honeypot log <channel>` — Save the log channel (omit the channel to disable logging).
- `[p]honeypot action <ban|kick|role>` — Choose how offenders are punished.
- `[p]honeypot punishrole [role]` — Set or clear the punish role used when the action is `role`.
- `[p]honeypot striproles <true|false>` — Toggle whether existing roles are removed before the punish role is applied.
- `[p]honeypot stripexception add/remove/list <role>` — Keep specific roles when stripping is enabled.
- `[p]honeypot exempt` or `[p]honeypot exempt list` — Show roles currently exempt from the trap.
- `[p]honeypot exempt add <role>` — Add a role to the exempt list.
- `[p]honeypot exempt remove <role>` — Remove a role from the exempt list.
- Once configured, the cog watches all messages. If a non-exempt member speaks in the honeypot channel their message is deleted and the chosen punishment (ban, kick, or role assignment) is applied automatically. When banning, Discord can also remove up to a day of message history via `delete_message_days=1`.
- Kick/role punishments additionally post to the log channel with a Ban button so moderators with `Ban Members` can quickly escalate after reviewing the situation.

## Permissions & Behavior

- The bot needs `Manage Messages` in the honeypot channel and `Ban Members` server-wide.
- Message cleanup relies on Discord's native ban option `delete_message_days=1`, which deletes up to 24 hours of the member's history.
- Logging is optional but requires `Send Messages`/`Embed Links` in the channel you configure with `[p]honeypotlog`.

## Troubleshooting

1. **Bot isn't deleting messages** — confirm it has `Manage Messages` in that channel.
2. **Bot isn't banning** — verify it has `Ban Members` and sits higher in the role hierarchy than the offender.
3. **No log messages** — ensure `[p]honeypot log` is set and the bot can send embeds there.
4. **Trusted members getting banned** — add their role to `[p]honeypot exempt add <role>`.
5. **Multiple honeypot channels?** — currently only one channel per guild can be configured; rerun `[p]honeypot set …` to change it.

## Contributing

Issues and pull requests are welcome. Please describe the scenario you are solving, include reproduction steps, and test your changes on a Red 3.5+ instance when possible.

## License

Released under the MIT License.
