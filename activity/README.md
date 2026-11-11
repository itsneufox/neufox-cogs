# Activity

Server activity for Red-DiscordBot in one cog: chat and voice XP with levels, message, reaction and
voice-time stats, one leaderboard, one rank card and LWD$ rewards through the Economy cog.

Activity replaces the old **Leveling** and **ActivityStats** cogs and keeps all of their data: it opens
both old Config namespaces directly, so XP, levels, level roles, stats and reward settings carry over.
Unload the old cogs before loading this one.

## How tracking works

One set of listeners feeds everything:

- **Messages** count towards the message stats. Non-command messages also earn chat XP
  (random 15-25 XP, once every 30 seconds per member by default).
- **Voice**: time in a voice channel counts towards voice time. Unmuted, undeafened time outside the
  AFK channel also earns voice XP (random 5-10 XP per minute by default).
- **Reactions** that other people add to a member's messages count as received reactions.
- Ignored channels, categories and roles are excluded from everything.

XP boosters sold by the Economy cog multiply all XP while they are active. The rank card shows an
active boost.

## Commands

- `/rank [member]` (`[p]rank`, `[p]level`) - levels, progress, next reward, message, voice and reaction
  stats, and any XP boost.
- `/leaderboard [board] [emoji]` (`[p]leaderboard`, `[p]lb`) - one leaderboard with a picker for
  Chat XP, Voice XP, Messages, Voice time and Reactions. `emoji` limits the reactions board to one emoji.
- `/activity overview` - server totals, level roles, top-message roles and rewards.
- `/activity announce [enabled]` - opt in or out of public level-up messages for yourself.
- `/activity levelrole <chat|voice> <level> [role]` - set the role awarded at a level, or remove it by
  leaving `role` empty. Needs Manage Roles.

Admin maintenance (Manage Server):

- `/activity backfill [channel] [limit] [everywhere]` - import old messages and reactions.
- `/activity purge <channel>` - forget stats from a channel, thread or category.
- `/activity recount` - rebuild message and reaction totals from known messages.
- `/activity sources [member]` - where a member's counted messages came from.
- `/activity payout` - pay any leaderboard rewards that are due now.
- `/activity reset confirm:True` - delete all stats. XP and levels are kept.

## Settings

Everything else is in the `/control` panel from the Control cog, under **Activity**:

- **Tracking** - on/off, ignored channels and ignored roles.
- **XP** - level curve, chat cooldown, chat and voice XP ranges, level-up messages, how often they
  are posted and where, DM delivery, pinging, the message text (placeholders `{member}` `{name}`
  `{kind}` `{level}` `{old_level}` `{cash}` `{server}`), keeping or replacing lower level roles,
  voice XP while muted, and bonus XP channels and roles with their multipliers.
- **Stats** - roles for 1st, 2nd and 3rd place on the message leaderboard and how often they refresh.
- **Rewards** - level cash (every 10 levels pays `level x 100` LWD$ by default), automatic leaderboard
  payouts, their cooldown, log channel and the amounts for the top three of each board.

Level-up messages do not ping unless that is turned on. By default they are posted every 5 levels and
on levels that pay cash or give a role.
