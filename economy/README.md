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
- `[p]eco resets` - show the current UTC time and upcoming claim reset times.
- `[p]eco top` - show the LWD$ leaderboard.
- `[p]eco shop` - show the server shop.
- `[p]eco buy <item> [quantity]` - buy from the server shop. Dedicated shop item messages also have one-click buy buttons.
- `[p]eco gift <member> <item> [quantity]` - gift an allowed inventory item.
- `[p]eco inventory [member]` - show inventory items.
- `[p]eco codes` - DM your unredeemed in-game item codes.
- `[p]eco casino` - show casino games, bet limits, and payout rules.
- `[p]eco casino coinflip <bet> [heads|tails]` - bet on a coin flip.
- `[p]eco casino dice <bet> <1-6>` - guess a six-sided die roll.
- `[p]eco casino highcard <bet>` - draw a card against the dealer.
- `[p]eco casino roulette <bet> <choice>` - spin a European roulette wheel.
- `[p]eco casino slots <bet>` - spin the slot machine.
- `[p]eco casino blackjack <bet>` - play interactive blackjack.

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
- `[p]casino` / `[p]gamble`
- `[p]casino coinflip <bet> [heads|tails]`
- `[p]casino dice <bet> <1-6>`
- `[p]casino highcard <bet>` / `[p]casino war <bet>`
- `[p]casino roulette <bet> <choice>` / `[p]casino wheel <bet> <choice>`
- `[p]casino slots <bet>`
- `[p]casino blackjack <bet>` / `[p]casino bj <bet>`

Casino games use the same global LWD$ balance as the rest of the economy. They use secure random draws, settle each wager atomically, and record the net result in the transaction ledger. The defaults allow bets from 10 to 10,000 LWD$, with a short per-game anti-spam cooldown.

Payouts include the original wager: coin flip returns 1.95x on a win, and an exact dice guess returns 5.7x. High Card returns 2x when your card outranks the dealer and pushes on equal ranks. Animated slots use the machine's printed exact-triple payouts: lemon 4x, cherry 5x, bell 10x, coin 25x, diamond 40x, and seven 80x. Exactly one cherry on an otherwise unmatched spin returns half the wager.

European Roulette uses a single-zero wheel. Straight-up numbers from 0 to 36 return 36x; red/black, odd/even, and low/high return 2x; first, second, and third dozen bets return 3x. Choices can be written as `17`, `red`, `odd`, `low`, `1st12`, `2nd12`, or `3rd12`.

Blackjack supports hit, stand, double down, up to four split hands, late surrender, and insurance. Its live message includes a chronological round log covering player actions, dealer draws, timeouts, and settlement. Completed rounds have player-only Same Bet, Half, Double, and Change Bet replay controls; all replay wagers still respect the casino limits and available balance. The dealer stands on soft 17 and a player natural blackjack pays 3:2. As a house fairness rule, a dealer natural is a redraw: the main wager is returned instead of losing. Insurance still pays 2:1. Wagers and any extra double, split, or insurance stakes are reserved immediately, so funds cannot be moved away while a hand is active. An unanswered hand times out and forfeits after 90 seconds.

Blackjack card/table artwork and slot-machine artwork are used under the bundled Casino Bot MIT notice in `CASINO_BOT_LICENSE.md`.

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
- `[p]eco admin casino show`
- `[p]eco admin casino toggle`
- `[p]eco admin casino limits <minimum> <maximum>`
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
- `[p]eco api token list`
- `[p]eco api token create <name>`
- `[p]eco api token revoke <name>`
- `[p]eco api token revokeall confirm`

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
