# Control

One panel for every cog in this repo, plus a slash `/help`.

Open it with `/control` or `[p]control` (Manage Server, admin role or bot owner). Everything happens in one private message with menus and buttons.

## Settings

Pick a cog from the menu to see and change its server settings, grouped into sections. Each cog decides what it exposes through `control_panel_schema()` (see `schema.py`). Bot-wide settings are shown only to the bot owner, and settings marked admin-only are hidden from members who only have Manage Server.

## Commands

**Commands** lists every loaded cog. Pick one to see its commands with ✅ on, 🚫 off or 🔒 locked, then pick commands to switch them on or off in this server. **Turn all on** re-enables every command you're allowed to change.

This uses the same storage as Red's `[p]command disable server` / `[p]command enable server`, so both always agree. As with those commands, you can't change commands above your own privilege level (for example owner-only commands unless you are the owner), commands Red marks as always available, or Red's `command` manager itself.

## Owner tools

Shown only to the bot owner.

- **Status**: Red and discord.py versions, latency, uptime, server and cog counts.
- **Cogs**: load, unload or reload any installed cog package.
- **Update repo**: pulls a Downloader repo, reinstalls every cog installed from it (pinned cogs are skipped), installs their requirements and reloads the ones that are loaded. Unlike `[p]cog update`, this does not depend on Downloader's saved commit history, which can wrongly report that everything is up to date. Saved cog data is never touched.
- **Sync slash commands**: same as `[p]slash sync`, with a 2-minute cooldown because Discord rate-limits syncing.
- **Last error**: the most recent command error, like `[p]traceback`.

## /help

`/help` lists every loaded cog's slash commands by category, showing each member only the commands they can use. Prefix commands are still listed by `[p]help`.
