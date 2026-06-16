# Leveling

Chat and voice XP leveling for Red-DiscordBot.

Defaults are based on the Ree6 leveling behavior:

- Chat XP: random 15-25 XP, once per user every 30 seconds.
- Voice XP: random 5-10 XP per eligible connected minute.
- Level roles: roles are awarded when a member reaches the configured chat or voice level.
- Economy LWD coin rewards: optional rewards can be configured per chat or voice level.

## Commands

- `[p]level [chat|voice] [member]` - show a member's level card.
- `[p]leaderboard [chat|voice]` - show the XP leaderboard.
- `[p]levelrole add <chat|voice> <level> <role>` - add or replace a level role.
- `[p]levelrole remove <chat|voice> <level> [role]` - remove a level role.
- `[p]levelrole list` - show configured level roles.
- `[p]levelcoins add <chat|voice> <level> <amount>` - add or replace an Economy LWD coin reward.
- `[p]levelcoins remove <chat|voice> <level>` - remove an Economy LWD coin reward.
- `[p]levelcoins list` - show configured LWD coin rewards and Economy load status.
- `[p]levelset` - show leveling settings.
- `[p]levelset toggle` - enable or disable tracking.
- `[p]levelset announce` - toggle level-up announcements.
- `[p]levelset channel [channel]` - send level-up messages to a fixed channel.
- `[p]levelset clearchannel` - send chat level-ups in the source channel.
- `[p]levelset cooldown <seconds>` - set the chat XP cooldown.
- `[p]levelset chatxp <min> <max>` - set the chat XP reward range.
- `[p]levelset voicexp <min> <max>` - set the per-minute voice XP reward range.
- `[p]levelset ignorechannel [channel]` - ignore a channel or category.
- `[p]levelset allowchannel <channel_or_category_id>` - stop ignoring a channel or category.
- `[p]levelset ignorerole <role>` - ignore members with a role.
- `[p]levelset allowrole <role>` - stop ignoring a role.
