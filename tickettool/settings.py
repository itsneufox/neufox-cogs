import discord
import typing
from copy import deepcopy
from redbot.core import commands

from .layouts import NO_PINGS, info_layout
from .locales import get_text, LANGUAGE_NAMES
from .utils import Emoji, EmojiLabelDescriptionValueConverter, CustomModalConverter


TEMPLATE_VARIABLES = (
    "ticket_id", "owner_display_name", "owner_name", "owner_id", "guild_name", "guild_id",
    "bot_display_name", "bot_name", "bot_id", "shortdate", "longdate", "time", "emoji",
)


def _parse_color(value: typing.Optional[str]) -> int:
    text = str(value or "").strip().lstrip("#")
    if text.lower().startswith("0x"):
        text = text[2:]
    if not 1 <= len(text) <= 6:
        raise ValueError(value)
    return int(text, 16)


class ProfileConverter(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str):
        cog = ctx.bot.get_cog("TicketTool")
        lang = await cog.get_lang(ctx.guild) if cog else "en"
        if len(argument) > 20:
            raise commands.BadArgument(get_text(lang, "profile_not_exist"))
        profiles = await cog.config.guild(ctx.guild).profiles()
        if argument.lower() not in profiles:
            raise commands.BadArgument(get_text(lang, "profile_not_exist"))
        return argument.lower()


class MyMessageConverter(commands.MessageConverter):
    async def convert(self, ctx: commands.Context, argument: str) -> discord.Message:
        message = await super().convert(ctx, argument=argument)
        if message.author != ctx.me:
            cog = ctx.bot.get_cog("TicketTool")
            lang = await cog.get_lang(ctx.guild) if cog else "en"
            raise commands.UserFeedbackCheckFailure(get_text(lang, "author_message"))
        return message


class settings(commands.Cog):
    # ── Control panel ───────────────────────────────────────────────────────

    async def _panel_profile(self, guild: discord.Guild) -> typing.Optional[str]:
        """The profile the control panel edits: the chosen one, else "main", else the first."""
        data = await self.config.guild(guild).all()
        profiles = data.get("profiles") or {}
        chosen = data.get("panel_profile")
        if chosen in profiles:
            return chosen
        if "main" in profiles:
            return "main"
        return sorted(profiles)[0] if profiles else None

    def _profile_default(self, path: typing.Tuple[str, ...]) -> typing.Any:
        value = self.config._defaults[self.config.GUILD]["default_profile_settings"]
        for part in path:
            value = value[part]
        return deepcopy(value)

    def _profile_getter(self, path, read=None):
        async def getter(guild: discord.Guild):
            profile = await self._panel_profile(guild)
            default = self._profile_default(path)
            if profile is None:
                value = default
            else:
                try:
                    value = await self.config.guild(guild).profiles.get_raw(profile, *path)
                except KeyError:
                    value = default
            return read(value) if read else value

        return getter

    def _profile_setter(self, path, write=None):
        async def setter(guild: discord.Guild, value):
            profile = await self._panel_profile(guild)
            if profile is None:
                return
            await self.config.guild(guild).profiles.set_raw(profile, *path, value=write(value) if write else value)

        return setter

    def _profile_validator(self, check=None):
        async def validate(guild: discord.Guild, value):
            if await self._panel_profile(guild) is None:
                return "Create a ticket profile first with /settickettool profile create."
            return check(value) if check else None

        return validate

    async def _get_panel_profile(self, guild: discord.Guild) -> typing.Optional[str]:
        return await self._panel_profile(guild)

    async def _set_panel_profile(self, guild: discord.Guild, value: typing.Optional[str]) -> None:
        await self.config.guild(guild).panel_profile.set(value.strip().lower() if value else None)

    async def _validate_panel_profile(self, guild: discord.Guild, value: typing.Optional[str]) -> typing.Optional[str]:
        if not value:
            return None
        profiles = await self.config.guild(guild).profiles()
        if value.strip().lower() not in profiles:
            names = ", ".join(sorted(profiles)) or "none yet"
            return f"No profile named {value.strip()!r}. Profiles: {names}"[:300]
        return None

    @staticmethod
    def _template_error(template: typing.Optional[str]) -> typing.Optional[str]:
        if not template:
            return None
        sample = {key: "x" for key in TEMPLATE_VARIABLES}
        try:
            template.format(**sample)
        except (KeyError, IndexError, ValueError, AttributeError):
            return "Unknown {variable}. Use: " + ", ".join("{" + key + "}" for key in TEMPLATE_VARIABLES)
        return None

    @staticmethod
    def _url_error(url: typing.Optional[str]) -> typing.Optional[str]:
        if url and not url.startswith(("https://", "http://")):
            return "The link must start with https:// or http://."
        return None

    @staticmethod
    def _color_error(value: typing.Optional[str]) -> typing.Optional[str]:
        try:
            _parse_color(value)
        except ValueError:
            return "Enter a hex colour such as #01D758."
        return None

    def control_panel_schema(self):
        def profile(key, label, kind, section, help, path=None, check=None, read=None, write=None, **extra):
            path = tuple(path or (key,))
            return {
                "key": "profile." + ".".join(path),
                "label": label,
                "type": kind,
                "section": section,
                "help": help,
                "get": self._profile_getter(path, read),
                "set": self._profile_setter(path, write),
                "validate": self._profile_validator(check),
                **extra,
            }

        return {
            "title": "TicketTool",
            "description": "Ticket system. Profile settings apply to the profile chosen under General.",
            "access": "admin",
            "settings": [
                {
                    "key": "language",
                    "label": "Language",
                    "type": "choice",
                    "choices": [{"value": code, "label": LANGUAGE_NAMES.get(code, code)} for code in ("en", "pt-br")],
                    "help": "Language used by this server's ticket system.",
                },
                {
                    "key": "panel_profile",
                    "label": "Profile being edited",
                    "type": "text",
                    "max": 20,
                    "clearable": True,
                    "empty": "No profiles yet",
                    "help": "Name of the profile the other sections change. Create profiles with settickettool.",
                    "get": self._get_panel_profile,
                    "set": self._set_panel_profile,
                    "validate": self._validate_panel_profile,
                },
                profile("enable", "Tickets enabled", "bool", "General", "Let members open tickets with this profile."),
                # Channels
                profile(
                    "logschannel", "Log channel", "channel", "Channels",
                    "Where ticket events and transcripts are sent.", channel_types=["text", "news"],
                ),
                profile(
                    "forum_channel", "Forum or thread channel", "channel", "Channels",
                    "Open tickets as forum posts or private threads here instead of channels.",
                    channel_types=["forum", "text"],
                ),
                profile(
                    "category_open", "Open ticket category", "channel", "Channels",
                    "Category new ticket channels are created in.", channel_types=["category"],
                ),
                profile(
                    "category_close", "Closed ticket category", "channel", "Channels",
                    "Category closed ticket channels are moved to.", channel_types=["category"],
                ),
                # Roles
                profile("admin_roles", "Admin roles", "roles", "Roles", "Full control over every ticket of this profile."),
                profile("support_roles", "Support roles", "roles", "Roles", "Can see, answer and claim tickets."),
                profile("view_roles", "View-only roles", "roles", "Roles", "Can read tickets but not write in them."),
                profile("ping_roles", "Ping roles", "roles", "Roles", "Mentioned when a ticket is opened."),
                profile("ticket_role", "Ticket owner role", "role", "Roles", "Given to members while they have an open ticket."),
                # Behaviour
                profile(
                    "nb_max", "Max open tickets per member", "int", "Behaviour",
                    "How many tickets one member may have open at once.", min=1, max=100, unit="tickets",
                ),
                profile("user_can_close", "Owners can close", "bool", "Behaviour", "Let ticket owners close their own tickets."),
                profile("close_confirmation", "Confirm before closing", "bool", "Behaviour", "Ask for confirmation before a ticket is closed."),
                profile("delete_on_close", "Delete on close", "bool", "Behaviour", "Delete the ticket instead of moving it to closed."),
                profile("close_on_leave", "Close when owner leaves", "bool", "Behaviour", "Close a member's open tickets when they leave the server."),
                profile(
                    "auto_close_hours", "Auto-close after inactivity", "int", "Behaviour",
                    "Close open tickets with no messages for this long. 0 turns it off.", min=0, max=8760, unit="h",
                ),
                profile("transcript", "Save transcripts", "bool", "Behaviour", "Attach an HTML transcript to the log when a ticket is deleted."),
                profile("create_on_react", "Open with 🎟️ reaction", "bool", "Behaviour", "Open a ticket when someone reacts 🎟️ to any message."),
                profile(
                    "rename_channel_dropdown", "Rename to dropdown reason", "bool", "Behaviour",
                    "Rename the ticket after the reason picked from a dropdown.", path=("embed_button", "rename_channel_dropdown"),
                ),
                profile("create_modlog", "Modlog case on open", "bool", "Behaviour", "Create a modlog case for every new ticket."),
                profile("audit_logs", "Name staff in audit log", "bool", "Behaviour", "Put the acting member in Discord audit log reasons."),
                # Appearance
                profile(
                    "dynamic_channel_name", "Ticket name format", "text", "Appearance",
                    "Name of new tickets. Variables include {ticket_id}, {owner_name}, {emoji}.",
                    check=self._template_error, min=1, max=100,
                ),
                profile(
                    "custom_message", "Welcome message", "text", "Appearance",
                    "Sent inside each new ticket. Same variables as the name format. Empty sends none.",
                    check=self._template_error, max=2000, clearable=True, empty="None",
                ),
                profile("emoji_open", "Open emoji", "text", "Appearance", "Emoji used for {emoji} while a ticket is open.", min=1, max=50),
                profile("emoji_close", "Closed emoji", "text", "Appearance", "Emoji put in the name of closed tickets.", min=1, max=50),
                profile(
                    "color", "Embed colour", "text", "Appearance", "Hex colour of ticket embeds, e.g. #01D758.",
                    check=self._color_error, read=lambda value: f"#{int(value):06X}", write=_parse_color, min=1, max=7,
                ),
                profile(
                    "thumbnail", "Embed thumbnail", "text", "Appearance", "Image link shown in the corner of ticket embeds.",
                    check=self._url_error, max=500, clearable=True, empty="None",
                ),
                profile(
                    "title", "Open-ticket message title", "text", "Appearance", "Title of the open-a-ticket message posted with settickettool message.",
                    path=("embed_button", "title"), min=1, max=256,
                ),
                profile(
                    "description", "Open-ticket message text", "text", "Appearance",
                    "Text of the open-a-ticket message. {prefix} is the bot prefix.",
                    path=("embed_button", "description"), min=1, max=2000,
                ),
                profile(
                    "image", "Open-ticket message image", "text", "Appearance", "Image link shown on the open-a-ticket message.",
                    path=("embed_button", "image"), check=self._url_error, max=500, clearable=True, empty="None",
                ),
                profile(
                    "placeholder_dropdown", "Dropdown placeholder", "text", "Appearance",
                    "Hint shown in the reason dropdown of the open-a-ticket message.",
                    path=("embed_button", "placeholder_dropdown"), min=1, max=100,
                ),
            ],
        }

    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    @commands.hybrid_group(name="settickettool", aliases=["tickettoolset"])
    async def configuration(self, ctx: commands.Context) -> None:
        """Configure TicketTool for your server."""
        pass

    @configuration.command(name="help", aliases=["commands"])
    async def configuration_help(self, ctx: commands.Context) -> None:
        """Show TicketTool setup help."""
        prefix = ctx.clean_prefix
        sections = [
            (
                "Profiles",
                "\n".join(
                [
                    f"`{prefix}settickettool profile` - list profiles",
                    f"`{prefix}settickettool profile create <name>` - create a profile",
                    f"`{prefix}settickettool profile delete <profile>` - delete a profile",
                    f"`{prefix}settickettool enable <profile> <true|false>` - enable or disable a profile",
                ]
            ),
            ),
            (
                "Channels",
                "\n".join(
                [
                    f"`{prefix}settickettool logschannel <profile> [channel]`",
                    f"`{prefix}settickettool forumchannel <profile> [channel]`",
                    f"`{prefix}settickettool categoryopen <profile> [category]`",
                    f"`{prefix}settickettool categoryclose <profile> [category]`",
                ]
            ),
            ),
            (
                "Roles",
                "\n".join(
                [
                    f"`{prefix}settickettool adminroles <profile> [roles...]`",
                    f"`{prefix}settickettool supportroles <profile> [roles...]`",
                    f"`{prefix}settickettool viewroles <profile> [roles...]`",
                    f"`{prefix}settickettool pingroles <profile> [roles...]`",
                    f"`{prefix}settickettool ticketrole <profile> [role]`",
                ]
            ),
            ),
            (
                "Behavior And Panel",
                "\n".join(
                [
                    f"`{prefix}settickettool nbmax <profile> <number>`",
                    f"`{prefix}settickettool usercanclose <profile> <true|false>`",
                    f"`{prefix}settickettool closeconfirmation <profile> <true|false>`",
                    f"`{prefix}settickettool closeonleave <profile> <true|false>`",
                    f"`{prefix}settickettool deleteonclose <profile> <true|false>`",
                    f"`{prefix}settickettool message <profile> <channel>`",
                    f"`{prefix}settickettool language <language>`",
                ]
            ),
            ),
        ]
        await ctx.send(
            view=info_layout(
                "TicketTool Setup Help",
                "Admin commands for ticket profiles and settings.",
                sections,
                discord.Color.blue(),
            ),
            allowed_mentions=NO_PINGS,
        )

    # ── Profile management ──────────────────────────────────────────────────

    @configuration.group(name="profile", invoke_without_command=True, fallback="list")
    async def profile_group(self, ctx: commands.Context) -> None:
        """Manage ticket profiles."""
        profiles = await self.config.guild(ctx.guild).profiles()
        if not profiles:
            await ctx.send("No profiles configured. Use `settickettool profile create <name>`.")
            return
        lines = [
            f"**{discord.utils.escape_markdown(name)}**: {'✅ Enabled' if data.get('enable') else '❌ Disabled'}"
            for name, data in profiles.items()
        ]
        await ctx.send(
            view=info_layout("Ticket Profiles", "\n".join(lines)[:3800], color=discord.Color.blue()),
            allowed_mentions=NO_PINGS,
        )

    @profile_group.command(name="create")
    async def profile_create(self, ctx: commands.Context, name: str) -> None:
        """Create a new ticket profile."""
        name = name.lower()
        if len(name) > 20 or not name.isalnum():
            await ctx.send("Profile name must be alphanumeric and 20 characters or less.")
            return
        profiles = await self.config.guild(ctx.guild).profiles()
        if name in profiles:
            await ctx.send(f"Profile `{name}` already exists.")
            return
        defaults = self.config._defaults[self.config.GUILD]["default_profile_settings"].copy()
        await self.config.guild(ctx.guild).profiles.set_raw(name, value=defaults)
        await ctx.send(f"Profile `{name}` created. Use `settickettool` subcommands to configure it.")

    @profile_group.command(name="delete")
    async def profile_delete(self, ctx: commands.Context, profile: ProfileConverter) -> None:
        """Delete a ticket profile."""
        profiles = await self.config.guild(ctx.guild).profiles()
        del profiles[profile]
        await self.config.guild(ctx.guild).profiles.set(profiles)
        await ctx.send(f"Profile `{profile}` deleted.")

    # ── System toggle ───────────────────────────────────────────────────────

    @configuration.command(name="enable")
    async def cmd_enable(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Enable or disable the ticket system for a profile."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "enable", value=value)
        await ctx.send(f"Profile `{profile}` is now {'**enabled**' if value else '**disabled**'}.")

    # ── Channel / category settings ─────────────────────────────────────────

    @configuration.command(name="logschannel")
    async def cmd_logschannel(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        channel: typing.Optional[typing.Union[discord.TextChannel, discord.VoiceChannel, discord.Thread]] = None,
    ) -> None:
        """Set the logs channel. Omit to clear."""
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "logschannel", value=channel.id if channel else None
        )
        if channel:
            await ctx.send(f"Logs channel set to {channel.mention}.")
        else:
            await ctx.send("Logs channel cleared.")

    @configuration.command(name="forumchannel")
    async def cmd_forumchannel(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        channel: typing.Optional[typing.Union[discord.ForumChannel, discord.TextChannel]] = None,
    ) -> None:
        """Set the forum/thread channel. Omit to clear (tickets use categories instead)."""
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "forum_channel", value=channel.id if channel else None
        )
        if channel:
            await ctx.send(f"Forum channel set to {channel.mention}.")
        else:
            await ctx.send("Forum channel cleared. Tickets will use `categoryopen`/`categoryclose`.")

    @configuration.command(name="categoryopen")
    async def cmd_categoryopen(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        category: typing.Optional[discord.CategoryChannel] = None,
    ) -> None:
        """Set the category for open tickets. Omit to clear."""
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "category_open", value=category.id if category else None
        )
        if category:
            await ctx.send(f"Open category set to **{category.name}**.")
        else:
            await ctx.send("Open category cleared.")

    @configuration.command(name="categoryclose")
    async def cmd_categoryclose(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        category: typing.Optional[discord.CategoryChannel] = None,
    ) -> None:
        """Set the category for closed tickets. Omit to clear."""
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "category_close", value=category.id if category else None
        )
        if category:
            await ctx.send(f"Close category set to **{category.name}**.")
        else:
            await ctx.send("Close category cleared.")

    # ── Role settings ───────────────────────────────────────────────────────

    @configuration.command(name="adminroles")
    async def cmd_adminroles(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        roles: commands.Greedy[discord.Role],
    ) -> None:
        """Set admin roles (full ticket permissions). No args to clear."""
        role_ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "admin_roles", value=role_ids)
        if roles:
            await ctx.send(f"Admin roles: {', '.join(r.mention for r in roles)}.", allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send("Admin roles cleared.")

    @configuration.command(name="supportroles")
    async def cmd_supportroles(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        roles: commands.Greedy[discord.Role],
    ) -> None:
        """Set support roles (can participate and claim tickets). No args to clear."""
        role_ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "support_roles", value=role_ids)
        if roles:
            await ctx.send(f"Support roles: {', '.join(r.mention for r in roles)}.", allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send("Support roles cleared.")

    @configuration.command(name="viewroles")
    async def cmd_viewroles(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        roles: commands.Greedy[discord.Role],
    ) -> None:
        """Set view-only roles (read messages, can't send). No args to clear."""
        role_ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "view_roles", value=role_ids)
        if roles:
            await ctx.send(f"View roles: {', '.join(r.mention for r in roles)}.", allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send("View roles cleared.")

    @configuration.command(name="pingroles")
    async def cmd_pingroles(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        roles: commands.Greedy[discord.Role],
    ) -> None:
        """Set roles pinged on ticket creation. No args to clear."""
        role_ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "ping_roles", value=role_ids)
        if roles:
            await ctx.send(f"Ping roles: {', '.join(r.mention for r in roles)}.", allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send("Ping roles cleared.")

    @configuration.command(name="ticketrole")
    async def cmd_ticketrole(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        role: typing.Optional[discord.Role] = None,
    ) -> None:
        """Set a role given to ticket owners. Omit to clear."""
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "ticket_role", value=role.id if role else None
        )
        if role:
            await ctx.send(f"Ticket role set to {role.mention}.", allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send("Ticket role cleared.")

    # ── Behaviour settings ──────────────────────────────────────────────────

    @configuration.command(name="nbmax")
    async def cmd_nbmax(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        number: commands.Range[int, 1, None],
    ) -> None:
        """Set maximum open tickets a user can have at once."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "nb_max", value=number)
        await ctx.send(f"Max open tickets per user set to **{number}**.")

    @configuration.command(name="usercanclose")
    async def cmd_usercanclose(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Allow or prevent ticket owners from closing their own tickets."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "user_can_close", value=value)
        await ctx.send(f"Users {'can' if value else 'cannot'} close their own tickets.")

    @configuration.command(name="closeconfirmation")
    async def cmd_closeconfirmation(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Require confirmation before closing a ticket."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "close_confirmation", value=value)
        await ctx.send(f"Close confirmation {'enabled' if value else 'disabled'}.")

    @configuration.command(name="closeonleave")
    async def cmd_closeonleave(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Close a ticket if its owner leaves the server."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "close_on_leave", value=value)
        await ctx.send(f"Close on leave {'enabled' if value else 'disabled'}.")

    @configuration.command(name="deleteonclose")
    async def cmd_deleteonclose(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Delete the ticket channel/thread immediately when closed."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "delete_on_close", value=value)
        await ctx.send(f"Delete on close {'enabled' if value else 'disabled'}.")

    @configuration.command(name="createonreact")
    async def cmd_createonreact(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Create a ticket when someone reacts with 🎟️ on any message."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "create_on_react", value=value)
        await ctx.send(f"Create on react {'enabled' if value else 'disabled'}.")

    @configuration.command(name="createmodlog")
    async def cmd_createmodlog(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Log ticket creation to the bot modlog."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "create_modlog", value=value)
        await ctx.send(f"Modlog on creation {'enabled' if value else 'disabled'}.")

    @configuration.command(name="auditlogs")
    async def cmd_auditlogs(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Include the acting user's name in Discord audit log reasons."""
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "audit_logs", value=value)
        await ctx.send(f"Audit log attribution {'enabled' if value else 'disabled'}.")

    @configuration.command(name="renamedropdown")
    async def cmd_renamedropdown(
        self, ctx: commands.Context, profile: ProfileConverter, value: bool
    ) -> None:
        """Rename the ticket channel with the chosen dropdown reason."""
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "embed_button", "rename_channel_dropdown", value=value
        )
        await ctx.send(f"Rename channel on dropdown selection {'enabled' if value else 'disabled'}.")

    # ── Channel name / message settings ─────────────────────────────────────

    @configuration.command(name="channelname")
    async def cmd_channelname(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        *,
        template: typing.Optional[str] = None,
    ) -> None:
        """Set the ticket channel name template. Omit to reset to default.

        Variables: {ticket_id}, {owner_display_name}, {owner_name}, {owner_id},
        {guild_name}, {guild_id}, {shortdate}, {longdate}, {time}, {emoji}
        """
        default = "{emoji}-ticket-{ticket_id}"
        value = template or default
        await self.config.guild(ctx.guild).profiles.set_raw(profile, "dynamic_channel_name", value=value)
        await ctx.send(f"Channel name template set to `{value}`.")

    @configuration.command(name="custommessage")
    async def cmd_custommessage(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        *,
        message: typing.Optional[str] = None,
    ) -> None:
        """Set a custom message sent when a ticket opens. Omit to clear."""
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "custom_message", value=message or None
        )
        if message:
            await ctx.send("Custom message set.")
        else:
            await ctx.send("Custom message cleared.")

    @configuration.command(name="customodal")
    async def cmd_customodal(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        *,
        yaml_input: typing.Optional[CustomModalConverter] = None,
    ) -> None:
        """Set a custom modal (up to 5 questions) shown when creating a ticket. Omit to clear.

        Format (YAML):
        ```
        - label: What is the problem?
          style: 2
          required: True
          placeholder: Describe your issue
        ```
        """
        await self.config.guild(ctx.guild).profiles.set_raw(
            profile, "custom_modal", value=yaml_input
        )
        if yaml_input:
            await ctx.send(f"Custom modal set ({len(yaml_input)} question(s)).")
        else:
            await ctx.send("Custom modal cleared.")

    # ── Ticket creation message / button ────────────────────────────────────

    @configuration.command(name="message")
    async def message(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        channel: typing.Optional[discord.TextChannel],
        message: typing.Optional[MyMessageConverter],
        reason_options: commands.Greedy[EmojiLabelDescriptionValueConverter],
        emoji: typing.Optional[Emoji] = "🎟️",
        label: commands.Range[str, 1, 80] = None,
    ) -> None:
        """Send a message with a button or dropdown to open a ticket.

        Examples:
        - `[p]settickettool message <profile> #general`
        - `[p]settickettool message <profile> #general "🐛|Bug Report|Report a bug|bug" "⚠️|User Report|Report a user|user"`
        """
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=True)
        if channel is None:
            channel = message.channel if message is not None else ctx.channel
        channel_permissions = channel.permissions_for(ctx.me)
        if (
            not channel_permissions.view_channel
            or not channel_permissions.read_messages
            or not channel_permissions.read_message_history
            or not channel_permissions.send_messages
        ):
            lang = await self.config.guild(ctx.guild).language()
            raise commands.UserFeedbackCheckFailure(get_text(lang, "no_permissions_channel"))
        if reason_options == []:
            reason_options = None
        lang = await self.config.guild(ctx.guild).language()
        config = await self.get_config(ctx.guild, profile)
        actual_color = config["color"]
        actual_thumbnail = config["thumbnail"]
        embed: discord.Embed = discord.Embed()
        default_button = self.config._defaults[self.config.GUILD]["default_profile_settings"]["embed_button"]
        custom_title = config["embed_button"].get("title")
        custom_description = config["embed_button"].get("description")
        embed.title = (
            custom_title
            if custom_title and custom_title != default_button["title"]
            else get_text(lang, "modal_create_ticket")
        )
        embed.description = (
            custom_description.replace("{prefix}", ctx.prefix)
            if custom_description and custom_description != default_button["description"]
            else get_text(lang, "default_embed_description", prefix=ctx.prefix)
        )
        embed.set_image(url=config["embed_button"]["image"])
        embed.set_thumbnail(url=actual_thumbnail)
        embed.color = actual_color
        embed.set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon)
        if reason_options is None:
            buttons_config = await self.config.guild(ctx.guild).buttons.all()
            view = self.get_buttons(
                buttons=[
                    {
                        "style": discord.ButtonStyle(2),
                        "label": label or get_text(lang, "create_ticket"),
                        "emoji": f"{getattr(emoji, 'id', emoji)}",
                        "custom_id": "create_ticket_button",
                        "disabled": False,
                    }
                ],
            )
            if message is None:
                message = await channel.send(embed=embed, view=view)
            else:
                await message.edit(view=view)
            self.views[message] = view
            buttons_config[f"{message.channel.id}-{message.id}"] = {"profile": profile}
            await self.config.guild(ctx.guild).buttons.set(buttons_config)
        else:
            if len({value for __, __, __, value in reason_options}) != len(
                [value for __, __, __, value in reason_options]
            ):
                raise commands.UserFeedbackCheckFailure(get_text(lang, "dropdown_unique"))
            if ctx.interaction is None and ctx.bot_permissions.add_reactions:
                try:
                    for emoji, label, description, value in reason_options[:19]:
                        await ctx.message.add_reaction(emoji)
                except discord.HTTPException:
                    await ctx.send(get_text(lang, "invalid_emoji"))
                    return
            dropdowns_config = await self.config.guild(ctx.guild).dropdowns.all()
            all_options = [
                {
                    "label": label,
                    "value": value.strip(),
                    "description": description,
                    "emoji": f"{getattr(emoji, 'id', emoji)}",
                    "default": False,
                }
                for emoji, label, description, value in reason_options
            ]
            view = self.get_dropdown(
                placeholder=config["embed_button"]["placeholder_dropdown"],
                options=all_options,
            )
            if message is None:
                message = await channel.send(embed=embed, view=view)
            else:
                message = await message.edit(view=view)
            self.views[message] = view
            dropdowns_config[f"{message.channel.id}-{message.id}"] = [
                {
                    "profile": profile,
                    "emoji": f"{getattr(emoji, 'id', emoji)}",
                    "label": label,
                    "description": description,
                    "value": value.strip(),
                }
                for emoji, label, description, value in reason_options
            ]
            await self.config.guild(ctx.guild).dropdowns.set(dropdowns_config)
        if ctx.interaction is not None:
            await ctx.send(get_text(lang, "slash_done"), ephemeral=True)

    # ── Language ─────────────────────────────────────────────────────────────

    @configuration.command(name="language", aliases=["lang", "idioma"], hidden=True, with_app_command=False)
    async def language(
        self,
        ctx: commands.Context,
        language: typing.Optional[str] = None,
    ) -> None:
        """Set the language for this server's ticket system (en / pt-br)."""
        if language is None:
            current_lang = await self.config.guild(ctx.guild).language()
            lang_name = LANGUAGE_NAMES.get(current_lang, current_lang)
            await ctx.send(get_text(current_lang, "language_current", lang=lang_name))
            return
        language = language.lower()
        if language not in ("en", "pt-br"):
            current_lang = await self.config.guild(ctx.guild).language()
            await ctx.send(get_text(current_lang, "language_invalid"))
            return
        await self.config.guild(ctx.guild).language.set(language)
        await ctx.send(get_text(language, "language_set"))

    # ── Internal helpers (used by the message command) ───────────────────────

    async def check_permissions_in_channel(
        self, permissions: typing.List[str], channel: discord.TextChannel
    ) -> typing.List[str]:
        return [
            permission
            for permission in permissions
            if not getattr(channel.permissions_for(channel.guild.me), permission)
        ]
