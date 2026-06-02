# Economy

Global cash economy cog with API access for other bots or future SA-MP integration.

## User commands

- `[p]eco balance [member]` - show a user's cash balance.
- `[p]eco pay <member> <amount>` - transfer cash to another member.

## Owner commands

- `[p]eco admin add <member> <amount> [reason]`
- `[p]eco admin remove <member> <amount> [reason]`
- `[p]eco admin set <member> <amount> [reason]`

## API commands

- `[p]eco api status`
- `[p]eco api start [host] [port]`
- `[p]eco api stop`
- `[p]eco api token create <name>`
- `[p]eco api token revoke <name>`

HTTP API requests require `Authorization: Bearer <token>`.

## HTTP API

Start the API:

```text
[p]eco api token create samp
[p]eco api start 127.0.0.1 8787
```

Endpoints:

- `GET /health`
- `GET /balance/{discord_user_id}`
- `POST /balance/{discord_user_id}/add`
- `POST /balance/{discord_user_id}/remove`
- `POST /balance/{discord_user_id}/set`
- `POST /transfer`

Adjustment body:

```json
{
  "amount": 100,
  "reason": "SA-MP purchase"
}
```

Transfer body:

```json
{
  "from_user_id": 111111111111111111,
  "to_user_id": 222222222222222222,
  "amount": 500,
  "reason": "external transfer"
}
```

Other Red cogs can call the internal API directly:

```python
economy = bot.get_cog("Economy")
await economy.add_balance(user_id, 100, reason="SA-MP purchase")
balances = await economy.get_balance(user_id)
```

## ActivityStats integration

The `activitystats` cog can pay `cash` rewards through this cog when these leaderboards are viewed:

- `/topmessages` or `[p]activitystats messages`
- `/topvoice` or `[p]activitystats voice`
- `/topreacts` or `[p]activitystats reactiontop`

Default rewards are once every 24 hours per server and category:

- 1st place: 100 cash
- 2nd place: 50 cash
- 3rd place: 25 cash

Manage them with:

```text
[p]activitystats rewards show
[p]activitystats rewards toggle
[p]activitystats rewards cooldown <seconds>
[p]activitystats rewards set <messages|voice|reactions> <rank> <amount>
```
