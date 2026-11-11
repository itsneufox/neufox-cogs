# Economy

Global LWD$ economy: claims with a daily streak, a server shop, perks and rentals, a market for spending money together, casino games, the LWDMillions lottery, the Nightlife game, and an API for other cogs, bots or an SA-MP server.

Slash commands come first. Everything starts from **`/eco`**, a button panel with Balance, Claim, Shop, Perks, Market, Casino, Lottery, Nightlife and Leaderboard.

On Red installations where third-party slash commands are disabled, the bot owner must run `[p]slash enablecog Economy` and `[p]slash sync` after loading the cog.

## Commands

| Slash | Prefix | What it does |
| --- | --- | --- |
| `/eco [member]` | `[p]eco` | The economy panel. Opens on your profile (or a member's). |
| — | `[p]balance [member]`, `[p]bal` | Balance and profile card. |
| `/claim` | `[p]claim` (also `[p]daily`, `[p]work`, …) | Claims **every** ready reward at once. |
| `/pay <member> <amount>` | `[p]pay` | Pay someone. Shows the transfer tax and asks you to confirm. |
| `/shop …` | `[p]shop …`, `[p]buy <item> [qty]` | Shop, inventory, perks, rentals and the market. |
| `/casino …` | `[p]casino …` | Casino games, Hold'em, player protection and the lottery. |
| `/nightlife …` | `[p]sex …` | The Nightlife game. |

`/shop` subcommands:

- `view` — the server shop · `buy <item> [quantity]` · `inventory [member]` · `gift <member> <item> [quantity]` · `codes` (your redeem codes, privately)
- `perks` — what's on sale and what you own · `perk <kind> [value]` — buy a perk (see below)
- `rent <role> [days]` — rent a role · `cancel <customrole|role name>` — stop a rental or personal role from renewing
- `rain <amount> [people]` — share LWD$ with people who chatted in the channel recently
- `bounty` (list) · `bounty post <amount> <task>` · `bounty fund <id> <amount>` · `bounty award <id> <member>` · `bounty cancel <id>`
- `auction` (list) · `auction sell <item> <starting_bid> [hours] [quantity] [house]` · `auction bid <id> <amount>` · `auction cancel <id>`
- `shoutout <message>` — the bot posts your message in the shoutout channel
- `pin <message link or ID>` — pin one of your own messages for a while

`/casino` subcommands: `games`, `coinflip`, `dice`, `highcard`, `roulette`, `slots`, `mines`, `blackjack`, `holdem open|join|start|cancel|fold|check|call|raise`, `selfexclude`, `exclusion`, `exclude`, `unexclude`, and `lottery status|play|quickpick|tickets|prizes|results|verify`. Prefix aliases such as `[p]casino bj`, `[p]casino lwdm play` and `[p]gamble` still work.

## Claims and the daily streak

`/claim` pays every reward that is ready — daily, weekly, monthly, annual and work — in one receipt that also shows when each one resets (daily, weekly, monthly and annual reset at 00:00 UTC; work is a rolling cooldown). A claim with a 0 amount or a 0 cooldown is switched off.

Claiming the daily reward on consecutive UTC days builds a **streak**. Each day after the first adds a flat bonus (default 25 LWD$ per day, capped at 250 LWD$). Missing a day resets the streak to 1, unless you hold **streak savers**: each one covers one missed day and is used automatically. A claim booster multiplies the whole payout, streak bonus included.

## Money sinks

Everything below takes LWD$ out of circulation (it is burned, not paid to anyone) unless it says otherwise, and every debit is written to the ledger.

### Perks (`/shop perk <kind> [value]`)

| Kind | Default price | Notes |
| --- | --- | --- |
| `title <text>` | 5,000 | Shown under your name on your profile. Up to 32 characters. |
| `badge <emoji>` | 2,500 | Shown before your name. |
| `color <#hex>` | 2,500 | Your profile card colour. |
| `rolecolor <#hex>` / `rolename <text>` | 10,000 per week, changes 1,000 | A personal role the bot creates, places (above the configured anchor role, or just under the bot's top role) and assigns. It renews weekly; if the upkeep cannot be paid, or you `/shop cancel customrole`, the role is deleted when the week ends and you get a DM. **Off by default**; needs Manage Roles. |
| `claimbooster` | 4,000 | x1.5 `/claim` payouts for 24 hours. Buying again adds time. |
| `xpbooster` | 3,000 | x2 activity XP for 24 hours (read by the Activity cog through `get_multiplier`). |
| `streaksaver` | 1,500 | Covers one missed daily claim. Hold up to 5. |

`/shop perk title clear` (also `badge`/`color`) removes a cosmetic for free. A price of 0 stops sales of that perk. Titles, badges, colours and personal roles are per server; boosters and streak savers are global, like balances.

### Rented roles

Server admins choose roles that can be rented and their daily price with `[p]ecoadmin rentals add <role> <price_per_day>` (also `rentals`, `rentals remove <role>`). Members rent with `/shop rent <role> [days]`. Rentals renew automatically for the same number of days; when a renewal cannot be paid, or after `/shop cancel <role>`, the role is removed at the end of the period and the member gets a DM. The bot only offers roles below its own top role, and admins can only offer roles below their own top role.

### Taxes and fees

- **Transfer tax** (per server, default 2%): charged to the sender on top of `/pay`. The confirmation shows the amount, the tax and the total before anything moves. With a 0% tax payments go through immediately.
- **Casino rake** (global, default 0% = off): a share of the *profit* of winning casino results (instant games, blackjack and Mines). Losses and pushes are never raked.
- **Wealth tax** (global, default off): once per UTC day, a percentage of each balance above the tax-free threshold (default 1,000,000), capped per person per day (default 25,000).

### Market

- **Rain**: `/shop rain <amount> [people]` splits the amount evenly between up to 10 members who chatted in that channel in the last 15 minutes (not you, not bots). The house cut (default 5%) and any rounding remainder are burned; you pay exactly the amount you typed.
- **Bounties**: anyone can post a bounty and anyone can add to it with `fund`. The poster (or a server manager) awards it to whoever did the task; the winner receives the pot minus the house cut. Cancelling refunds every funder in full. Unawarded bounties are refunded automatically after 7 days.
- **Auctions**: sell giftable inventory items (items with redeem codes cannot be auctioned). Items are held while the auction runs. Each bid is held from the bidder and refunded when someone outbids them; the next bid must be at least 5% higher. When the auction ends the winner gets the items and the seller gets the bid minus the house cut. Without bids, items go back to the seller. Server managers can run **house auctions** of shop items (`house: true`); their whole price is burned. Sellers can cancel before the first bid; managers can cancel any time, refunding the top bid.
- **Shoutouts**: `/shop shoutout` posts your message (mentions disabled) in the configured channel for a fee (default 2,500; 5-minute cooldown). Off until a channel is set.
- **Paid pins**: `/shop pin` pins one of your own messages in an allowed channel for a fee (default 5,000) and unpins it automatically after the configured time (default 24 hours). Off until channels are set. If Discord refuses the pin you are refunded.

Deadlines for personal roles, rentals, auctions, bounties and pins are stored in Config and checked every minute, so they survive restarts.

## Settings (`/control` → Economy)

Bot owner (global): claim amounts and cooldowns, work range, streak bonus and cap, casino on/off, bet limits and rake, LWDMillions sales, ticket price, jackpot contribution and seed, booster prices/lengths/multipliers, streak saver price, wealth tax rate/threshold/cap, and per-server lottery channel, log channel and Nightlife switch.

Server admins: transfer tax, house cut, perk prices, personal roles on/off with their prices and anchor role, rain limits, shoutout channel and price, paid pin channels, price and duration, auctions on/off and longest auction, and bounty lifetime.

## `[p]ecoadmin`

Only what the panel cannot do:

- Bot owner: `add|remove|set <member> <amount> [reason]`, `jackpot <amount>`, `draw confirm` (force the LWDMillions draw once its beacon exists), `shop add|remove|role|code|stock|limit|giftable|channel|post|clearchannel`, `api status|start [host] [port]|stop`, `api token create|list|revoke|revokeall confirm`.
- Server admins: `rentals`, `rentals add <role> <price_per_day>`, `rentals remove <role>`.

Use `stock -1` for unlimited stock and `limit -1` for unlimited purchases per member. Shop items are server-local because role rewards and log channels are server-local; balances stay global. Code-generated, non-role items become giftable by default; role reward items cannot be gifted. `shop channel` posts one message per item with a buy button (one item per click; use `[p]buy "1 Week VIP" 3` for more).

## Casino

Casino games use the same global LWD$ balance as the rest of the economy. They use secure random draws, settle each wager atomically, and record the net result in the transaction ledger. The defaults allow bets from 10 to 50,000 LWD$, with a short per-game anti-spam cooldown. Players cannot shorten or remove their own self-exclusion; a server administrator can lift either a self-exclusion or an administrator-imposed exclusion with `/casino unexclude <member>`.

Payouts include the original wager: coin flip returns 1.95x on a win, and an exact dice guess returns 5.7x. High Card returns 2x when your card outranks the dealer and pushes on equal ranks. Animated slots use the machine's printed exact-triple payouts: lemon 4x, cherry 5x, bell 10x, coin 25x, diamond 40x, and seven 80x. Exactly one cherry on an otherwise unmatched spin returns half the wager.

European Roulette uses a single-zero wheel. Straight-up numbers from 0 to 36 return 36x; red/black, odd/even, and low/high return 2x; first, second, and third dozen bets return 3x. Choices can be written as `17`, `red`, `odd`, `low`, `1st12`, `2nd12`, or `3rd12`.

Mines uses a 20-tile button board with 1-10 mines (3 by default). Safe reveals build a probability-based cash-out value; hitting a mine loses the reserved wager. Cash-out values target 97% RTP before integer rounding and are capped at 100x. Reaching the cap or clearing every safe tile settles automatically. An unanswered board refunds before the first pick or automatically cashes out its current value after at least one safe pick. Completed games reveal the board and provide player-only replay and setup controls.

Blackjack supports hit, stand, double down, up to four split hands, late surrender, and insurance. Its live message includes a chronological round log. Completed rounds have player-only Same Bet, Half, Double, and Change Bet replay controls. The dealer stands on soft 17 and a player natural blackjack pays 3:2. As a house fairness rule, a dealer natural is a redraw: the main wager is returned instead of losing. Insurance still pays 2:1. Wagers and any extra stakes are reserved immediately. An unanswered hand times out and forfeits after 90 seconds.

Texas Hold'em opens one table per server channel with `/casino holdem open <buyin>`. Up to five others join with the same buy-in, then the host starts one no-limit hand. House players fill empty seats up to six total. Blinds are about 5% and 10% of the buy-in. Hole cards stay private (**Show my cards**); join, start, fold, check, call and raise are table buttons. Side pots and shared pots are handled. A turn times out after 90 seconds and folds; an unstarted lobby expires after three minutes and the host may cancel it, refunding all buy-ins. Interrupted hands refund reserved buy-ins on restart; a saved completed result finishes paying out once.

Blackjack card/table artwork and slot-machine artwork are used under the bundled Casino Bot MIT notice in `CASINO_BOT_LICENSE.md`.

## Nightlife

`/nightlife start` (or `[p]sex`) starts an encounter with an NPC. `[p]sex @member` invites another player. This game is available in server channels (including their threads) and spends the existing global LWD$ balance. NPC fees, venue fees, supplies and clinic bills remove currency; satisfaction and titles never pay currency back. Profiles, cooldowns and supplies are global across servers.

With a `.` prefix, get started using:

```text
.sex buy condoms 5
.sex
```

The default encounter books Alex at the motel for 350 LWD$ and consumes one condom. If you have none, the encounter is refused without charging you. Condoms can break, exposing the player to infection. The receipt reports breakage.

The regular encounter cooldown is 30 seconds for NPC and player encounters.

Commands (every `[p]sex ...` command is also `/nightlife ...`; `[p]nightlife` works as well):

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
- The bot owner can switch Nightlife on or off per server in `/control` → Economy → This server. Enabled by default in server channels.

Player invitations show the venue, condom choice and price. The invited player has 90 seconds to accept or decline; the inviter can cancel. Only acceptance starts the encounter. The inviter pays the venue fee (100 LWD$ for the motel) and supplies one condom for a protected encounter; the partner pays no booking fee. Declining, cancelling, expiry and cog reloads do not charge either player. Each player can have one pending invitation; finish it before starting another encounter or using supplies.

Both players must have enough stamina, be out of recovery and cooldown, and have no unpaid hospital bills. These checks and the inviter's balance are rechecked on acceptance. Both players get their own cooldown, stamina costs and substance consequences. An intact condom protects both; if it breaks or the encounter is unprotected, existing infections can spread in either direction. Newly transmitted conditions require a clinic test to reveal. Each player's prepared substances affect only that player, including their own hospital bills.

The shop stocks condoms, Viagra, lube, energy, weed, cocaine, ecstasy, shrooms, poppers, champagne, flowers, perfume, chocolate, coffee and snacks. Use the single-word item names shown in the shop. Aliases include `condoms`, `cannabis`, `marijuana`, `coke`, `mdma`, `mushrooms`, `chocolates` and `snacks`.

Diseases include chlamydia, gonorrhea, syphilis, genital herpes, HPV, trichomoniasis, pubic lice and scabies. Clinic prices are listed by `[p]sex clinic`. Disease progression, treatment and drug effects are fictional game rules. Energy, coffee and snacks restore stamina; other usable supplies prepare an encounter bonus. Prepared items are consumed only on a successful encounter, and repeated use of the same prepared item is refused. Failed actions do not spend currency or consume supplies. Nightlife spending is recorded in the capped ledger without posting to economy log channels; profiles and receipts are visible in the channel where requested.

Drug items and champagne now report their aftermath in the encounter receipt and require recovery before another encounter or item use. Bad reactions cause hospitalization, empty stamina, award no satisfaction and create a hospital bill. Mixing substances increases the chance of a hospital event. The poppers/Viagra combination triggers a hospital event with a 5,000 LWD$ bill and two-hour recovery. The combination's blood-pressure hazard is described in [FDA prescribing information](https://www.accessdata.fda.gov/drugsatfda_docs/label/2015/020895s045lbl.pdf); event odds, timers and fees here are game settings.

Recovery deadlines and unpaid bills persist across reloads and servers. `[p]sex status` shows recovery, hospital visits and outstanding bills. Hospital bills do not automatically debit the wallet or make it negative: pay them with `[p]sex clinic pay`. Unpaid bills block further encounters even after recovery ends. Payment does not end recovery early, and stamina items cannot bypass it. Buying supplies, earning LWD$, testing and treatment remain available during recovery.

## LWDMillions (`/casino lottery`)

LWDMillions is a global, scheduled lottery using virtual LWD$. Each line contains five distinct main numbers from 1-50 and two distinct Lucky Stars from 1-12. Draws close automatically every Tuesday and Friday at 20:00 UTC. A player may hold up to 50 lines in one draw; a draw accepts up to 10,000 total lines.

The default line price is 100 LWD$. Half of every ticket is added to a rolling 1,000,000 LWD$ jackpot. Lines matching all five main numbers and both Lucky Stars split that jackpot; without a jackpot winner, it rolls over. The other 12 match tiers pay fixed multiples of the price recorded when each line was bought. This means changing the price does not change payouts on existing tickets.

Example entries:

```text
[p]casino lottery play 4 9 12 31 50 | 3 12
[p]casino lottery quickpick 5
[p]casino lottery tickets
```

Every ticket period publishes a SHA-256 commitment that binds a private secret, the draw number, and a specific future League of Entropy drand Quicknet round. That public beacon does not exist when tickets are sold. After its scheduled publication, the bot requires matching responses from at least two relays, verifies the beacon's BLS signature against Quicknet's pinned public key, mixes it with the committed secret, and derives the numbers with unbiased sampling. Settlement fails closed if the beacon cannot be verified. `/casino lottery verify` checks the stored beacon, commitment, and winning numbers and links to the public beacon.

When upgrading while tickets are already sold, nobody must rebuy and no selection, ticket price, or jackpot contribution changes. The in-progress draw keeps its exact original v1 commitment as the secret anchor, deterministically locks the first Quicknet round after its original draw time, and mixes that beacon into the result. Verification checks both the old commitment and the public beacon. Following draws use native v2 commitments. Ticket purchases are blocked for users with an active casino self-exclusion or administrator exclusion.

## HTTP API

Manage it with `[p]ecoadmin api …`. HTTP API requests require `Authorization: Bearer <token>`.

Start the API:

```text
[p]ecoadmin api token create samp
[p]ecoadmin api start 127.0.0.1 8787
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
[p]ecoadmin shop add NitroBoost 500 -1 In-game boost item
[p]ecoadmin shop code NitroBoost true
```

Other Red cogs can call the internal API directly:

```python
economy = bot.get_cog("Economy")
await economy.add_balance(user_id, 100, reason="SA-MP purchase")
balances = await economy.get_balance(user_id)
```

Boosters for other cogs:

```python
economy = bot.get_cog("Economy")
multiplier = await economy.get_multiplier(user_id, "xp")  # or "claim"; 1.0 when none is active
```

`get_multiplier(user_id: int, kind: str) -> float` never raises: unknown kinds, missing data and errors all return 1.0, and active multipliers are capped at 10x.

## Activity integration

The Activity cog (`activity/`) pays LWD$ leaderboard rewards through `add_balance` and multiplies XP with `get_multiplier(user_id, "xp")`, so XP boosters bought here apply there. Its leaderboards are `/leaderboard` and `/rank`, `/activity payout` pays out rewards, and reward amounts and cooldowns are configured in `/control` → Activity.
