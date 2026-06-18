# Economy

Global LWD$ economy cog with API access for other bots or future SA-MP integration.

## User commands

- `[p]eco balance [member]` - show a user's LWD$ balance.
- `[p]eco help` - show user economy commands.
- `[p]eco pay <member> <amount>` - transfer LWD$ to another member.
- `[p]eco daily` - claim daily LWD$ once per UTC calendar day.
- `[p]eco weekly` - claim weekly LWD$ once per UTC calendar week.
- `[p]eco monthly` - claim monthly LWD$ once per UTC calendar month.
- `[p]eco annual` - claim annual LWD$ once per UTC calendar year.
- `[p]eco work` - claim hourly LWD$ from a configurable random range.
- `[p]eco top` - show the LWD$ leaderboard.
- `[p]eco shop` - show the server shop.
- `[p]eco buy <item> [quantity]` - buy from the server shop. Dedicated shop item messages also have one-click buy buttons.
- `[p]eco gift <member> <item> [quantity]` - gift an allowed inventory item.
- `[p]eco inventory [member]` - show inventory items.
- `[p]eco codes` - DM your unredeemed in-game item codes.

Shortcut commands are also available for common user actions:

- `[p]balance [member]` / `[p]bal [member]`
- `[p]pay <member> <amount>`
- `[p]daily`, `[p]weekly`, `[p]monthly`, `[p]annual`, `[p]work`
- `[p]shop`
- `[p]buy <item> [quantity]`
- `[p]gift <member> <item> [quantity]`
- `[p]inventory [member]` / `[p]inv [member]`
- `[p]codes`
- `[p]ecotop`

## Owner commands

- `[p]eco admin add <member> <amount> [reason]`
- `[p]eco admin help`
- `[p]eco admin remove <member> <amount> [reason]`
- `[p]eco admin set <member> <amount> [reason]`
- `[p]eco admin claim show`
- `[p]eco admin claim daily <amount> [0 disables]`
- `[p]eco admin claim weekly <amount> [0 disables]`
- `[p]eco admin claim monthly <amount> [0 disables]`
- `[p]eco admin claim annual <amount> [0 disables]`
- `[p]eco admin claim work <amount> [cooldown_seconds]`
- `[p]eco admin claim workrange <minimum> <maximum> [cooldown_seconds]`
- `[p]eco admin logchannel [channel]`
- `[p]eco admin clearlog`
- `[p]eco admin shop add <name> <price> [stock] [description]`
- `[p]eco admin shop remove <name>`
- `[p]eco admin shop role <name> [role]`
- `[p]eco admin shop code <name> [true|false]`
- `[p]eco admin shop stock <name> <stock>`
- `[p]eco admin shop limit <name> <limit>`
- `[p]eco admin shop giftable <name> [true|false]`
- `[p]eco admin shop channel [channel]`
- `[p]eco admin shop post [channel]`
- `[p]eco admin shop clearchannel`

Use `stock -1` for unlimited stock and `limit -1` for unlimited purchases per member. Shop items are server-local because role rewards and log channels are server-local, but user LWD$ balances stay global.
Code-generated, non-role items become giftable by default. Role reward items are locked and cannot be gifted.

`shop channel` creates or updates one dedicated shop message per item, each with a buy button. Each button buys one item. Use the text command for larger quantities:

```text
[p]eco admin shop channel #shop
[p]buy "1 Week VIP" 3
```

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
- `POST /redeem-code`

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

Redeem-code body, for game servers granting purchased in-game items:

```json
{
  "code": "ABCD-EFGH-JKLM",
  "redeemed_by": "samp-server-1"
}
```

Successful redemption returns the Discord user ID, guild ID, item name, quantity, creation timestamp, and redemption timestamp. A code can only be redeemed once.

To make a shop item generate codes after purchase:

```text
[p]eco admin shop add NitroBoost 500 -1 In-game boost item
[p]eco admin shop code NitroBoost true
```

Other Red cogs can call the internal API directly:

```python
economy = bot.get_cog("Economy")
await economy.add_balance(user_id, 100, reason="SA-MP purchase")
balances = await economy.get_balance(user_id)
```

## ActivityStats integration

The `activitystats` cog can automatically pay LWD$ rewards through this cog for these leaderboards:

- `/topmessages` or `[p]activitystats messages`
- `/topvoice` or `[p]activitystats voice`
- `/topreacts` or `[p]activitystats reactiontop`

The reward loop checks once per hour. Default rewards are paid once every 24 hours per server and category:

- 1st place: 100 LWD$
- 2nd place: 50 LWD$
- 3rd place: 25 LWD$

Manage them with:

```text
[p]activitystats rewards show
[p]activitystats rewards logchannel [channel]
[p]activitystats rewards clearlog
[p]activitystats rewards toggle
[p]activitystats rewards cooldown <seconds>
[p]activitystats rewards set <messages|voice|reactions> <rank> <amount>
[p]activitystats rewards run
```
