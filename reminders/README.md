# Reminders

Schedule reminders with slash commands.

## Commands

- `/remindme when message` - remind yourself.
- `/remind user when message` - remind another server member.
- `[p]remindme <when> <message>` - remind yourself with a prefix command.
- `[p]remind <member> <when> <message>` - remind another server member with a prefix command.
- `[p]remindlist [member]` - list active reminders you created (`[p]remindlist @member` for staff/admins).
- `[p]remindcancel <id>` - cancel one active reminder by ID.
- `[p]remindprotectedroles [roles...]` - show current protected roles or replace them; protected roles cannot be reminded by commoners.
- `[p]remindclearprotectedroles` - clear protected roles list.
- `[p]reminderlimit [n]` - show/set max active reminders per target (`0` = unlimited).
- `[p]reminderunlimitedrole <role>` - set the role (and roles above it) that can bypass reminders caps.

`when` accepts compact durations like `10m`, `2h30m`, `1d`, or `1w2d`.

Reminders are stored until delivered, cancelled, or deleted and survive bot restarts.
