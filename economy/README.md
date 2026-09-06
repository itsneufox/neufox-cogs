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
- `[p]eco lwdmillions` - show the current jackpot, draw time, and lottery commands.
- `[p]eco lwdmillions play <5 numbers> | <2 stars>` - buy a chosen LWDMillions line.
- `[p]eco lwdmillions quickpick [lines]` - buy securely randomized lines.
- `[p]eco lwdmillions tickets` - show your lines in the upcoming draw.
- `[p]eco lwdmillions prizes` - show all 13 match tiers and current payouts.
- `[p]eco lwdmillions results [draw]` - show a recent result.
- `[p]eco lwdmillions verify [draw]` - verify a result against its published commitment.
- `[p]eco casino` - show casino games, bet limits, and payout rules.
- `[p]eco casino coinflip <bet> [heads|tails]` - bet on a coin flip.
- `[p]eco casino dice <bet> <1-6>` - guess a six-sided die roll.
- `[p]eco casino highcard <bet>` - draw a card against the dealer.
- `[p]eco casino roulette <bet> <choice>` - spin a European roulette wheel.
- `[p]eco casino slots <bet>` - spin the slot machine.
- `[p]eco casino mines <bet> [mines]` - reveal safe tiles and cash out before hitting a mine.
- `[p]eco casino blackjack <bet>` - play interactive blackjack.
- `[p]eco casino selfexclude <duration|permanent> confirm` - block your own chance-game access.
- `[p]eco casino exclusion [member]` - show active exclusions.
- `[p]eco casino unexclude <member>` - admins can remove administrator and self-exclusions.
- `[p]sex help` - adult NPC encounters, protection, boosts, and clinic services.

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
- `[p]lwdmillions` / `[p]lwdm` / `[p]millions`
- `[p]casino` / `[p]gamble`
- `[p]casino coinflip <bet> [heads|tails]`
- `[p]casino dice <bet> <1-6>`
- `[p]casino highcard <bet>` / `[p]casino war <bet>`
- `[p]casino roulette <bet> <choice>` / `[p]casino wheel <bet> <choice>`
- `[p]casino slots <bet>`
- `[p]casino mines <bet> [mines]`
- `[p]casino blackjack <bet>` / `[p]casino bj <bet>`

## Nightlife

`[p]sex` starts an encounter with an NPC. `[p]sex @member` invites another player. This game is available in server channels (including their threads) and spends the existing global LWD$ balance. NPC fees, venue fees, supplies and clinic bills remove currency; satisfaction and titles never pay currency back. Profiles, cooldowns and supplies are global across servers.

With a `.` prefix, get started using:

```text
.sex buy condoms 5
.sex
```

The default encounter books Alex at the motel for 350 LWD$ and consumes one condom. If you have none, the encounter is refused without charging you. Condoms can break, exposing the player to infection. The receipt reports breakage.

Commands:

- `[p]sex` - book the default protected encounter.
- `[p]sex @member [true|false]` - invite a player to the motel. Defaults to using a condom; `[p]sex @member false` uses none.
- `[p]sex help` - show setup, commands and game rules.
- `[p]sex escorts` / `[p]sex hookers` - list adult NPCs and venue fees.
- `[p]sex encounter [escort|@member] [venue] [protected]` - customize an NPC or player encounter. Examples: `[p]sex encounter blair penthouse true`, `[p]sex encounter @member hotel false`.
- `[p]sex shop` - view consumables.
- `[p]sex buy <item> [quantity]` - buy supplies; hold up to 100 of each item.
- `[p]sex use <item>` - use supplies.
- `[p]sex status` / `[p]sex inventory` - show your own progress, supplies and diagnosed conditions.
- `[p]sex clinic` - show test and treatment prices.
- `[p]sex clinic test` - pay 150 LWD$ to diagnose detectable conditions.
- `[p]sex clinic cure` - pay the combined treatment fees for all diagnosed conditions.
- `[p]sex clinic pay` - pay outstanding hospital bills.
- `[p]eco admin sex <true|false>` - bot owner can enable or disable the game in the current server. Enabled by default in server channels.

Player invitations show the venue, condom choice and price. The invited player has 90 seconds to accept or decline; the inviter can cancel. Only acceptance starts the encounter. The inviter pays the venue fee (100 LWD$ for the motel) and supplies one condom for a protected encounter; the partner pays no booking fee. Declining, cancelling, expiry and cog reloads do not charge either player. Each player can have one pending invitation; finish it before starting another encounter or using supplies.

Both players must have enough stamina, be out of recovery and cooldown, and have no unpaid hospital bills. These checks and the inviter's balance are rechecked on acceptance. Both players get their own cooldown, stamina costs and substance consequences. An intact condom protects both; if it breaks or the encounter is unprotected, existing infections can spread in either direction. Newly transmitted conditions require a clinic test to reveal. Each player's prepared substances affect only that player, including their own hospital bills.

The shop stocks condoms, Viagra, lube, energy, weed, cocaine, ecstasy, shrooms, poppers, champagne, flowers, perfume, chocolate, coffee and snacks. Use the single-word item names shown in the shop. Aliases include `condoms`, `cannabis`, `marijuana`, `coke`, `mdma`, `mushrooms`, `chocolates` and `snacks`.

Diseases include chlamydia, gonorrhea, syphilis, genital herpes, HPV, trichomoniasis, pubic lice and scabies. Clinic prices are listed by `[p]sex clinic`. Disease progression, treatment and drug effects are fictional game rules. Energy, coffee and snacks restore stamina; other usable supplies prepare an encounter bonus. Prepared items are consumed only on a successful encounter, and repeated use of the same prepared item is refused. Failed actions do not spend currency or consume supplies. Nightlife spending is recorded in the capped ledger without posting to economy log channels; profiles and receipts are visible in the channel where requested.

Drug items and champagne now report their aftermath in the encounter receipt and require recovery before another encounter or item use. Bad reactions cause hospitalization, empty stamina, award no satisfaction and create a hospital bill. Mixing substances increases the chance of a hospital event. The poppers/Viagra combination triggers a hospital event with a 5,000 LWD$ bill and two-hour recovery. The combination's blood-pressure hazard is described in [FDA prescribing information](https://www.accessdata.fda.gov/drugsatfda_docs/label/2015/020895s045lbl.pdf); event odds, timers and fees here are game settings.

Recovery deadlines and unpaid bills persist across reloads and servers. `[p]sex status` shows recovery, hospital visits and outstanding bills. Hospital bills do not automatically debit the wallet or make it negative: pay them with `[p]sex clinic pay`. Unpaid bills block further encounters even after recovery ends. Payment does not end recovery early, and stamina items cannot bypass it. Buying supplies, earning LWD$, testing and treatment remain available during recovery.

## LWDMillions

LWDMillions is a global, scheduled lottery using virtual LWD$. Each line contains five distinct main numbers from 1-50 and two distinct Lucky Stars from 1-12. Draws close automatically every Tuesday and Friday at 20:00 UTC. A player may hold up to 20 lines in one draw; a draw accepts up to 10,000 total lines.

The default line price is 100 LWD$. Half of every ticket is added to a rolling 1,000,000 LWD$ jackpot. Lines matching all five main numbers and both Lucky Stars split that jackpot; without a jackpot winner, it rolls over. The other 12 match tiers pay fixed multiples of the price recorded when each line was bought. This means changing the price does not change payouts on existing tickets.

Example entries:

```text
[p]lwdmillions play 4 9 12 31 50 | 3 12
[p]lwdmillions quickpick 5
[p]lwdmillions tickets
```

Every ticket period publishes a SHA-256 commitment that binds a private secret, the draw number, and a specific future League of Entropy drand Quicknet round. That public beacon does not exist when tickets are sold. After its scheduled publication, the bot requires matching responses from at least two relays, verifies the beacon's BLS signature against Quicknet's pinned public key, mixes it with the committed secret, and derives the numbers with unbiased sampling. Settlement fails closed if the beacon cannot be verified. `[p]lwdmillions verify` checks the stored beacon, commitment, and winning numbers and links to the public beacon.

When upgrading while tickets are already sold, nobody must rebuy and no selection, ticket price, or jackpot contribution changes. The in-progress draw keeps its exact original v1 commitment as the secret anchor, deterministically locks the first Quicknet round after its original draw time, and mixes that beacon into the result. Verification checks both the old commitment and the public beacon. Following draws use native v2 commitments. Ticket purchases are blocked for users with an active casino self-exclusion or administrator exclusion.

Casino games use the same global LWD$ balance as the rest of the economy. They use secure random draws, settle each wager atomically, and record the net result in the transaction ledger. The defaults allow bets from 10 to 10,000 LWD$, with a short per-game anti-spam cooldown.

Players cannot shorten or remove their own self-exclusion. A server administrator can lift either a self-exclusion or an administrator-imposed exclusion with `[p]casino unexclude <member>`.

Payouts include the original wager: coin flip returns 1.95x on a win, and an exact dice guess returns 5.7x. High Card returns 2x when your card outranks the dealer and pushes on equal ranks. Animated slots use the machine's printed exact-triple payouts: lemon 4x, cherry 5x, bell 10x, coin 25x, diamond 40x, and seven 80x. Exactly one cherry on an otherwise unmatched spin returns half the wager.

European Roulette uses a single-zero wheel. Straight-up numbers from 0 to 36 return 36x; red/black, odd/even, and low/high return 2x; first, second, and third dozen bets return 3x. Choices can be written as `17`, `red`, `odd`, `low`, `1st12`, `2nd12`, or `3rd12`.

Mines uses a 20-tile button board with 1-10 mines (3 by default). Safe reveals build a probability-based cash-out value; hitting a mine loses the reserved wager. Cash-out values target 97% RTP before integer rounding and are capped at 100x. Reaching the cap or clearing every safe tile settles automatically. An unanswered board refunds before the first pick or automatically cashes out its current value after at least one safe pick. Completed games reveal the board and provide player-only replay and setup controls.

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
- `[p]eco admin lwdmillions show`
- `[p]eco admin lwdmillions toggle`
- `[p]eco admin lwdmillions ticketprice <amount>`
- `[p]eco admin lwdmillions contribution <0-100>`
- `[p]eco admin lwdmillions seed <amount>`
- `[p]eco admin lwdmillions jackpot <amount>`
- `[p]eco admin lwdmillions channel [channel]`
- `[p]eco admin lwdmillions clearchannel`
- `[p]eco admin lwdmillions draw confirm`
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
