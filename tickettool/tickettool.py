from AAA3A_utils import Cog, CogsUtils, Menu, Settings  # isort:skip
from redbot.core import commands, Config  # isort:skip
from redbot.core.bot import Red  # isort:skip
import discord  # isort:skip
import typing  # isort:skip

import asyncio
import datetime
import io
from copy import deepcopy
from functools import partial

import chat_exporter
from redbot.core import modlog
from redbot.core.utils.chat_formatting import pagify

from .dashboard_integration import DashboardIntegration
from .locales import get_text, LANGUAGE_NAMES
from .settings import settings
from .ticket import Ticket
from .utils import CustomModalConverter

# Credits:
# General repo credits.
# Thanks to Yami for the technique in the init file of some cogs to load the interaction client only if it is not already loaded! Before this fix, when a user clicked a button, the actions would be run about 10 times, causing a huge spam and loop in the channel.


class TicketTool(settings, DashboardIntegration, Cog):
    """A cog to manage a Tickets system!"""

    def __init__(self, bot: Red) -> None:
        super().__init__(bot=bot)

        self.config: Config = Config.get_conf(
            self,
            identifier=205192943327321000143939875896557571750,  # 937480369417
            force_registration=True,
        )
        self.CONFIG_SCHEMA: int = 4
        self.config.register_global(CONFIG_SCHEMA=None)
        self.config.register_guild(
            language="en",  # Per-server language: "en" or "pt-br"
            profiles={},
            default_profile_settings={
                "enable": False,
                "logschannel": None,
                "forum_channel": None,
                "category_open": None,
                "category_close": None,
                "admin_roles": [],
                "support_roles": [],
                "view_roles": [],
                "ping_roles": [],
                "ticket_role": None,
                "nb_max": 5,
                "create_modlog": False,
                "close_on_leave": False,
                "create_on_react": False,
                "user_can_close": True,
                "delete_on_close": False,
                "color": 0x01D758,
                "thumbnail": "http://www.quidd.it/wp-content/uploads/2017/10/Ticket-add-icon.png",
                "audit_logs": False,
                "close_confirmation": False,
                "emoji_open": "❓",
                "emoji_close": "🔒",
                "dynamic_channel_name": "{emoji}-ticket-{ticket_id}",
                "last_nb": 0000,
                "custom_message": None,
                "embed_button": {
                    "title": "Create Ticket",
                    "description": (
                        "To get help on this server or to make an order for example, you can create a ticket.\n"
                        "Just use the command `{prefix}ticket create` or click on the button below.\n"
                        "You can then use the `{prefix}ticket` subcommands to manage your ticket."
                    ),
                    "image": None,
                    "placeholder_dropdown": "Choose a reason to open a ticket.",
                    "rename_channel_dropdown": False,
                },
                "custom_modal": None,
            },
            tickets={},
            buttons={},
            dropdowns={},
        )

        _settings: typing.Dict[
            str, typing.Dict[str, typing.Union[typing.List[str], typing.Any, str]]
        ] = {
            "enable": {"converter": bool, "description": "Enable the system.", "no_slash": True},
            "logschannel": {
                "converter": typing.Union[
                    discord.TextChannel, discord.VoiceChannel, discord.Thread
                ],
                "description": "Set the channel where the logs will be sent/saved.",
                "no_slash": True,
            },
            "forum_channel": {
                "converter": typing.Union[discord.ForumChannel, discord.TextChannel],
                "description": "Set the forum channel where the opened tickets will be, or a text channel to use private threads. If it's set, `category_open` and `category_close` will be ignored (except for existing tickets).",
                "no_slash": True,
            },
            "category_open": {
                "converter": discord.CategoryChannel,
                "description": "Set the category where the opened tickets will be.",
                "no_slash": True,
            },
            "category_close": {
                "converter": discord.CategoryChannel,
                "description": "Set the category where the closed tickets will be.",
                "no_slash": True,
            },
            "admin_roles": {
                "converter": commands.Greedy[discord.Role],
                "description": "Users with this role will have full permissions for tickets, but will not be able to set up the cog.",
                "no_slash": True,
            },
            "support_roles": {
                "converter": commands.Greedy[discord.Role],
                "description": "Users with this role will be able to participate and claim the ticket.",
                "no_slash": True,
            },
            "view_roles": {
                "converter": commands.Greedy[discord.Role],
                "description": "Users with this role will only be able to read messages from the ticket, but not send them.",
                "no_slash": True,
            },
            "ping_roles": {
                "converter": commands.Greedy[discord.Role],
                "description": "This role will be pinged automatically when the ticket is created, but does not give any additional permissions.",
                "no_slash": True,
            },
            "ticket_role": {
                "converter": discord.Role,
                "description": "This role will be added automatically to open tickets owners.",
                "no_slash": True,
            },
            "dynamic_channel_name": {
                "converter": str,
                "description": "Set the template that will be used to name the channel when creating a ticket.\n\n`{ticket_id}` - Ticket number\n`{owner_display_name}` - user's nick or name\n`{owner_name}` - user's name\n`{owner_id}` - user's id\n`{guild_name}` - guild's name\n`{guild_id}` - guild's id\n`{bot_display_name}` - bot's nick or name\n`{bot_name}` - bot's name\n`{bot_id}` - bot's id\n`{shortdate}` - mm-dd\n`{longdate}` - mm-dd-yyyy\n`{time}` - hh-mm AM/PM according to bot host system time\n`{emoji}` - The open/closed emoji.",
                "no_slash": True,
            },
            "nb_max": {
                "converter": commands.Range[int, 1, None],
                "description": "Sets the maximum number of open tickets a user can have on the system at any one time (for a profile only).",
                "no_slash": True,
            },
            "custom_message": {
                "converter": str,
                "description": "This message will be sent in the ticket channel when the ticket is opened.\n\n`{ticket_id}` - Ticket number\n`{owner_display_name}` - user's nick or name\n`{owner_name}` - user's name\n`{owner_id}` - user's id\n`{guild_name}` - guild's name\n`{guild_id}` - guild's id\n`{bot_display_name}` - bot's nick or name\n`{bot_name}` - bot's name\n`{bot_id}` - bot's id\n`{shortdate}` - mm-dd\n`{longdate}` - mm-dd-yyyy\n`{time}` - hh-mm AM/PM according to bot host system time\n`{emoji}` - The open/closed emoji.",
                "style": discord.ButtonStyle(2),
                "no_slash": True,
            },
            "user_can_close": {
                "converter": bool,
                "description": "Can the author of the ticket, if he/she does not have a role set up for the system, close the ticket himself?",
                "no_slash": True,
            },
            "close_confirmation": {
                "converter": bool,
                "description": "Should the bot ask for confirmation before closing the ticket (deletion will necessarily have a confirmation)?",
                "no_slash": True,
            },
            "custom_modal": {
                "converter": CustomModalConverter,
                "description": "Ask a maximum of 5 questions to the user who opens a ticket, with a Discord Modal.\n\n**Example:**\n```\n[p]settickettool customodal <profile>\n- label: What is the problem?\n  style: 2 #  short = 1, paragraph = 2\n  required: True\n  default: None\n  placeholder: None\n  min_length: None\n  max_length: None\n```",
                "no_slash": True,
            },
            "close_on_leave": {
                "converter": bool,
                "description": "If a user leaves the server, will all their open tickets be closed?\n\nIf the user then returns to the server, even if their ticket is still open, the bot will not automatically add them to the ticket.",
                "no_slash": True,
            },
            "delete_on_close": {
                "converter": bool,
                "description": "Does closing the ticket directly delete it (with confirmation)?",
                "no_slash": True,
            },
            "modlog": {
                "path": ["create_modlog"],
                "converter": bool,
                "description": "Does the bot create an action in the bot modlog when a ticket is created?",
                "no_slash": True,
            },
            "audit_logs": {
                "converter": bool,
                "description": "On all requests to the Discord api regarding the ticket (channel modification), does the bot send the name and id of the user who requested the action as the reason?",
                "no_slash": True,
            },
            "create_on_react": {
                "converter": bool,
                "description": "Create a ticket when the reaction 🎟️ is set on any message on the server.",
                "no_slash": True,
            },
            "rename_channel_dropdown": {
                "path": ["embed_button", "rename_channel_dropdown"],
                "converter": bool,
                "description": "With Dropdowns feature, rename the ticket channel with chosen reason.",
                "no_slash": True,
            },
        }
        self.settings: Settings = Settings(
            bot=self.bot,
            cog=self,
            config=self.config,
            group=self.config.GUILD,
            settings=_settings,
            global_path=["profiles"],
            use_profiles_system=True,
            can_edit=True,
            commands_group=self.configuration,
        )

    async def cog_load(self) -> None:
        await super().cog_load()
        await self.edit_config_schema()
        await self.settings.add_commands()
        try:
            await modlog.register_casetype(
                "ticket_created", default_setting=True, image="🎟️", case_str="New Ticket"
            )
        except RuntimeError:  # The case is already registered.
            pass
        asyncio.create_task(self.load_buttons())

    async def red_delete_data_for_user(self, *args, **kwargs) -> None:
        """Nothing to delete. Don't delete operational tickets."""
        return

    async def get_lang(self, guild: discord.Guild) -> str:
        """Get the language setting for a guild."""
        return await self.config.guild(guild).language()

    async def t(self, guild: discord.Guild, key: str, **kwargs) -> str:
        """Get a translated string for the guild's language."""
        lang = await self.get_lang(guild)
        return get_text(lang, key, **kwargs)

    async def get_ticket_buttons(self, guild: discord.Guild, include_close: bool = True, include_open: bool = False, claim_disabled: bool = False) -> discord.ui.View:
        """Get the ticket action buttons with translated labels."""
        lang = await self.get_lang(guild)
        buttons = []
        if include_close:
            buttons.append({
                "style": discord.ButtonStyle(2),
                "label": get_text(lang, "close"),
                "emoji": "🔒",
                "custom_id": "close_ticket_button",
                "disabled": False,
            })
        if include_open:
            buttons.append({
                "style": discord.ButtonStyle(2),
                "label": get_text(lang, "re_open"),
                "emoji": "🔓",
                "custom_id": "open_ticket_button",
                "disabled": False,
            })
        buttons.append({
            "style": discord.ButtonStyle(2),
            "label": get_text(lang, "claim"),
            "emoji": "🙋‍♂️",
            "custom_id": "claim_ticket_button",
            "disabled": claim_disabled,
        })
        buttons.append({
            "style": discord.ButtonStyle(2),
            "label": get_text(lang, "delete"),
            "emoji": "⛔",
            "custom_id": "delete_ticket_button",
            "disabled": False,
        })
        return self.get_buttons(buttons)

    # async def red_get_data_for_user(self, *args, **kwargs) -> typing.Dict[str, typing.Any]:
    #     """Nothing to get."""
    #     return {}

    async def edit_config_schema(self):
        CONFIG_SCHEMA = await self.config.CONFIG_SCHEMA()
        if CONFIG_SCHEMA is None:
            CONFIG_SCHEMA = 1
            await self.config.CONFIG_SCHEMA(CONFIG_SCHEMA)
        if CONFIG_SCHEMA == self.CONFIG_SCHEMA:
            return
        if CONFIG_SCHEMA == 1:
            guild_group = self.config._get_base_group(self.config.GUILD)
            async with guild_group.all() as guilds_data:
                _guilds_data = deepcopy(guilds_data)
                for guild in _guilds_data:
                    if "settings" not in _guilds_data[guild]:
                        continue
                    if "main" in _guilds_data[guild].get("panels", []):
                        continue
                    if "panels" not in guilds_data[guild]:
                        guilds_data[guild]["panels"] = {}
                    guilds_data[guild]["panels"]["main"] = self.config._defaults[
                        self.config.GUILD
                    ]["default_profile_settings"]
                    for key, value in _guilds_data[guild]["settings"].items():
                        guilds_data[guild]["panels"]["main"][key] = value
                    del guilds_data[guild]["settings"]
            CONFIG_SCHEMA = 2
            await self.config.CONFIG_SCHEMA.set(CONFIG_SCHEMA)
        if CONFIG_SCHEMA == 2:
            guild_group = self.config._get_base_group(self.config.GUILD)
            async with guild_group.all() as guilds_data:
                _guilds_data = deepcopy(guilds_data)
                for guild in _guilds_data:
                    if "profiles" in guilds_data[guild]:
                        continue
                    if "panels" in guilds_data[guild]:
                        guilds_data[guild]["profiles"] = guilds_data[guild]["panels"]
                        del guilds_data[guild]["panels"]
                    if "tickets" in guilds_data[guild]:
                        for channel_id in guilds_data[guild]["tickets"]:
                            if "panel" not in guilds_data[guild]["tickets"][channel_id]:
                                continue
                            guilds_data[guild]["tickets"][channel_id]["profile"] = guilds_data[
                                guild
                            ]["tickets"][channel_id]["panel"]
                            del guilds_data[guild]["tickets"][channel_id]["panel"]
                    if "buttons" in guilds_data[guild]:
                        for message_id in guilds_data[guild]["buttons"]:
                            if "panel" not in guilds_data[guild]["buttons"][message_id]:
                                continue
                            guilds_data[guild]["buttons"][message_id]["profile"] = guilds_data[
                                guild
                            ]["buttons"][message_id]["panel"]
                            del guilds_data[guild]["buttons"][message_id]["panel"]
                    if "dropdowns" in guilds_data[guild]:
                        for message_id in guilds_data[guild]["dropdowns"]:
                            if "panel" not in guilds_data[guild]["dropdowns"][message_id]:
                                continue
                            guilds_data[guild]["dropdowns"][message_id]["profile"] = guilds_data[
                                guild
                            ]["dropdowns"][message_id]["panel"]
                            del guilds_data[guild]["dropdowns"][message_id]["panel"]
            CONFIG_SCHEMA = 3
            await self.config.CONFIG_SCHEMA.set(CONFIG_SCHEMA)
        if CONFIG_SCHEMA == 3:
            guild_group = self.config._get_base_group(self.config.GUILD)
            async with guild_group.all() as guilds_data:
                _guilds_data = deepcopy(guilds_data)
                for guild in _guilds_data:
                    if "profiles" in guilds_data[guild]:
                        for profile in guilds_data[guild]["profiles"]:
                            if (
                                guilds_data[guild]["profiles"][profile].get("admin_role")
                                is not None
                            ):
                                guilds_data[guild]["profiles"][profile]["admin_roles"] = [
                                    guilds_data[guild]["profiles"][profile]["admin_role"]
                                ]
                                del guilds_data[guild]["profiles"][profile]["admin_role"]
                            if (
                                guilds_data[guild]["profiles"][profile].get("support_role")
                                is not None
                            ):
                                guilds_data[guild]["profiles"][profile]["support_roles"] = [
                                    guilds_data[guild]["profiles"][profile]["support_role"]
                                ]
                                del guilds_data[guild]["profiles"][profile]["support_role"]
                            if (
                                guilds_data[guild]["profiles"][profile].get("view_role")
                                is not None
                            ):
                                guilds_data[guild]["profiles"][profile]["view_roles"] = [
                                    guilds_data[guild]["profiles"][profile]["view_role"]
                                ]
                                del guilds_data[guild]["profiles"][profile]["view_role"]
                            if (
                                guilds_data[guild]["profiles"][profile].get("ping_role")
                                is not None
                            ):
                                guilds_data[guild]["profiles"][profile]["ping_roles"] = [
                                    guilds_data[guild]["profiles"][profile]["ping_role"]
                                ]
                                del guilds_data[guild]["profiles"][profile]["ping_role"]
            CONFIG_SCHEMA = 4
            await self.config.CONFIG_SCHEMA.set(CONFIG_SCHEMA)
        if CONFIG_SCHEMA < self.CONFIG_SCHEMA:
            CONFIG_SCHEMA = self.CONFIG_SCHEMA
            await self.config.CONFIG_SCHEMA.set(CONFIG_SCHEMA)
        self.logger.info(
            f"The Config schema has been successfully modified to {self.CONFIG_SCHEMA} for the {self.qualified_name} cog."
        )

    async def load_buttons(self) -> None:
        await self.bot.wait_until_red_ready()
        try:
            view = self.get_buttons(
                buttons=[
                    {
                        "style": discord.ButtonStyle(2),
                        "label": "Create ticket",
                        "emoji": "🎟️",
                        "custom_id": "create_ticket_button",
                        "disabled": False,
                    }
                ],
            )
            self.bot.add_view(view)
            self.views["New Ticket View"] = view
            view = self.get_buttons(
                buttons=[
                    {
                        "style": discord.ButtonStyle(2),
                        "label": "Close",
                        "emoji": "🔒",
                        "custom_id": "close_ticket_button",
                        "disabled": False,
                    },
                    {
                        "style": discord.ButtonStyle(2),
                        "label": "Re-open",
                        "emoji": "🔓",
                        "custom_id": "open_ticket_button",
                        "disabled": False,
                    },
                    {
                        "style": discord.ButtonStyle(2),
                        "label": "Claim",
                        "emoji": "🙋‍♂️",
                        "custom_id": "claim_ticket_button",
                        "disabled": False,
                    },
                    {
                        "style": discord.ButtonStyle(2),
                        "label": "Delete",
                        "emoji": "⛔",
                        "custom_id": "delete_ticket_button",
                        "disabled": False,
                    },
                ],
            )
            self.bot.add_view(view)
            self.views["Existing Ticket View"] = view
        except Exception as e:
            self.logger.error("The Buttons View could not be added correctly.", exc_info=e)
        all_guilds = await self.config.all_guilds()
        for guild in all_guilds:
            for message in all_guilds[guild]["dropdowns"]:
                channel = self.bot.get_channel(int((str(message).split("-"))[0]))
                if channel is None:
                    continue
                message_id = int((str(message).split("-"))[1])
                try:
                    options = [
                        {
                            "label": reason_option["label"],
                            "value": reason_option.get("value", reason_option["label"]).strip(),
                            "description": reason_option.get("description", None),
                            "emoji": reason_option["emoji"],  # reason_option.get("emoji")
                            "default": False,
                        }
                        for reason_option in all_guilds[guild]["dropdowns"][message]
                    ]
                    view = self.get_dropdown(
                        placeholder="Choose the reason for open a ticket.",
                        options=options,
                    )
                    self.bot.add_view(view, message_id=message_id)
                    self.views[discord.PartialMessage(channel=channel, id=message_id)] = view
                except Exception as e:
                    self.logger.error(
                        f"The Dropdown View could not be added correctly for the `{guild}-{message}` message.",
                        exc_info=e,
                    )

    async def get_config(self, guild: discord.Guild, profile: str) -> typing.Dict[str, typing.Any]:
        config = await self.config.guild(guild).profiles.get_raw(profile)
        for key, value in self.config._defaults[Config.GUILD]["default_profile_settings"].items():
            if key not in config:
                config[key] = value
        if config["logschannel"] is not None:
            config["logschannel"] = guild.get_channel_or_thread(config["logschannel"])
        if config["forum_channel"] is not None:
            config["forum_channel"] = guild.get_channel(config["forum_channel"])
        if config["category_open"] is not None:
            config["category_open"] = guild.get_channel(config["category_open"])
        if config["category_close"] is not None:
            config["category_close"] = guild.get_channel(config["category_close"])
        if config["admin_roles"]:
            config["admin_roles"] = [
                role
                for role_id in config["admin_roles"]
                if (role := guild.get_role(role_id)) is not None
            ]
        if config["support_roles"]:
            config["support_roles"] = [
                role
                for role_id in config["support_roles"]
                if (role := guild.get_role(role_id)) is not None
            ]
        if config["view_roles"]:
            config["view_roles"] = [
                role
                for role_id in config["view_roles"]
                if (role := guild.get_role(role_id)) is not None
            ]
        if config["ping_roles"]:
            config["ping_roles"] = [
                role
                for role_id in config["ping_roles"]
                if (role := guild.get_role(role_id)) is not None
            ]
        if config["ticket_role"] is not None:
            config["ticket_role"] = guild.get_role(config["ticket_role"])
        for key, value in self.config._defaults[self.config.GUILD][
            "default_profile_settings"
        ].items():
            if key not in config:
                config[key] = value
        if len(config["embed_button"]) == 0:
            config["embed_button"] = self.config._defaults[self.config.GUILD][
                "default_profile_settings"
            ]["embed_button"]
        else:
            for key, value in self.config._defaults[self.config.GUILD]["default_profile_settings"][
                "embed_button"
            ].items():
                if key not in config:
                    config[key] = value
        return config

    async def get_ticket(self, channel: discord.TextChannel) -> Ticket:
        tickets = await self.config.guild(channel.guild).tickets.all()
        if str(channel.id) in tickets:
            json = tickets[str(channel.id)]
        else:
            return None
        if "profile" not in json:
            json["profile"] = "main"
        ticket: Ticket = Ticket.from_json(json, self.bot, self)
        ticket.bot = self.bot
        ticket.cog = self
        ticket.guild = ticket.bot.get_guild(ticket.guild) or ticket.guild
        ticket.owner = ticket.guild.get_member(ticket.owner) or ticket.owner
        ticket.channel = channel
        ticket.claim = ticket.guild.get_member(ticket.claim) or ticket.claim
        ticket.created_by = ticket.guild.get_member(ticket.created_by) or ticket.created_by
        ticket.opened_by = ticket.guild.get_member(ticket.opened_by) or ticket.opened_by
        ticket.closed_by = ticket.guild.get_member(ticket.closed_by) or ticket.closed_by
        ticket.deleted_by = ticket.guild.get_member(ticket.deleted_by) or ticket.deleted_by
        ticket.renamed_by = ticket.guild.get_member(ticket.renamed_by) or ticket.renamed_by
        ticket.locked_by = ticket.guild.get_member(ticket.locked_by) or ticket.locked_by
        ticket.unlocked_by = ticket.guild.get_member(ticket.unlocked_by) or ticket.unlocked_by
        members = ticket.members or []
        ticket.members = []
        ticket.members.extend(channel.guild.get_member(m) for m in members)
        if ticket.created_at is not None:
            ticket.created_at = datetime.datetime.fromtimestamp(ticket.created_at)
        if ticket.opened_at is not None:
            ticket.opened_at = datetime.datetime.fromtimestamp(ticket.opened_at)
        if ticket.closed_at is not None:
            ticket.closed_at = datetime.datetime.fromtimestamp(ticket.closed_at)
        if ticket.deleted_at is not None:
            ticket.deleted_at = datetime.datetime.fromtimestamp(ticket.deleted_at)
        if ticket.renamed_at is not None:
            ticket.renamed_at = datetime.datetime.fromtimestamp(ticket.renamed_at)
        if ticket.locked_at is not None:
            ticket.locked_at = datetime.datetime.fromtimestamp(ticket.locked_at)
        if ticket.unlocked_at is not None:
            ticket.unlocked_at = datetime.datetime.fromtimestamp(ticket.unlocked_at)
        if ticket.first_message is not None:
            ticket.first_message = ticket.channel.get_partial_message(ticket.first_message)
        return ticket

    async def get_audit_reason(
        self,
        guild: discord.Guild,
        profile: str,
        author: typing.Optional[discord.Member] = None,
        reason: typing.Optional[str] = None,
    ) -> str:
        if reason is None:
            lang = await self.get_lang(guild)
            reason = get_text(lang, "action_taken")
        config = await self.get_config(guild, profile)
        if author is None or not config["audit_logs"]:
            return f"{reason}"
        else:
            return f"{author.name} ({author.id}) - {reason}"

    async def get_embed_important(
        self,
        ticket,
        more: bool,
        author: discord.Member,
        title: str,
        description: str,
        reason: typing.Optional[str] = None,
    ) -> discord.Embed:
        config = await self.get_config(ticket.guild, ticket.profile)
        actual_color = config["color"]
        actual_thumbnail = config["thumbnail"]
        lang = await self.get_lang(ticket.guild)
        embed: discord.Embed = discord.Embed()
        embed.title = f"{title}"
        embed.description = f"{description}"
        embed.set_thumbnail(url=actual_thumbnail)
        embed.color = actual_color
        embed.timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
        embed.set_author(
            name=author,
            url=author.display_avatar,
            icon_url=author.display_avatar,
        )
        embed.set_footer(
            text=ticket.guild.name,
            icon_url=ticket.guild.icon,
        )
        embed.add_field(inline=True, name=get_text(lang, "ticket_id"), value=f"[{ticket.profile}] {ticket.id}")
        embed.add_field(
            inline=True,
            name=get_text(lang, "owned_by"),
            value=(
                f"<@{ticket.owner}> ({ticket.owner})"
                if isinstance(ticket.owner, int)
                else f"{ticket.owner.mention} ({ticket.owner.id})"
            ),
        )
        if ticket.channel is not None:
            embed.add_field(
                inline=True,
                name=get_text(lang, "channel"),
                value=f"{ticket.channel.mention} - {ticket.channel.name} ({ticket.channel.id})",
            )
        if more:
            if ticket.closed_by is not None:
                embed.add_field(
                    inline=False,
                    name=get_text(lang, "closed_by"),
                    value=(
                        f"<@{ticket.closed_by}> ({ticket.closed_by})"
                        if isinstance(ticket.closed_by, int)
                        else f"{ticket.closed_by.mention} ({ticket.closed_by.id})"
                    ),
                )
            if ticket.deleted_by is not None:
                embed.add_field(
                    inline=True,
                    name=get_text(lang, "deleted_by"),
                    value=(
                        f"<@{ticket.deleted_by}> ({ticket.deleted_by})"
                        if isinstance(ticket.deleted_by, int)
                        else f"{ticket.deleted_by.mention} ({ticket.deleted_by.id})"
                    ),
                )
            if ticket.closed_at is not None:
                embed.add_field(
                    inline=False,
                    name=get_text(lang, "closed_at"),
                    value=f"{ticket.closed_at}",
                )
        if reason is not None:
            reason_pages = list(pagify(reason, page_length=1020))
            embed.add_field(
                inline=False,
                name=get_text(lang, "reason"),
                value=f"{reason_pages[0]}\n..." if len(reason_pages) > 1 else reason,
            )
        return embed

    async def get_embed_action(
        self, ticket, author: discord.Member, action: str, reason: typing.Optional[str] = None
    ) -> discord.Embed:
        config = await self.get_config(ticket.guild, ticket.profile)
        actual_color = config["color"]
        lang = await self.get_lang(ticket.guild)
        embed: discord.Embed = discord.Embed()
        embed.title = get_text(lang, "ticket_action_title", profile=ticket.profile, id=ticket.id)
        embed.description = f"{action}"
        embed.color = actual_color
        embed.timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
        embed.set_author(
            name=author,
            url=author.display_avatar,
            icon_url=author.display_avatar,
        )
        embed.set_footer(
            text=ticket.guild.name,
            icon_url=ticket.guild.icon,
        )
        embed.add_field(inline=False, name=get_text(lang, "reason"), value=f"{reason}")
        return embed

    async def check_limit(self, member: discord.Member, profile: str) -> bool:
        config = await self.get_config(member.guild, profile)
        tickets = await self.config.guild(member.guild).tickets.all()
        to_remove = []
        count = 1
        for id in tickets:
            if config["forum_channel"] is not None:
                channel = config["forum_channel"].get_thread(int(id))
            else:
                channel = member.guild.get_channel(int(id))
            if channel is not None:
                ticket: Ticket = await self.get_ticket(channel)
                if ticket.profile != profile:
                    continue
                if ticket.created_by == member and ticket.status == "open":
                    count += 1
            else:
                to_remove.append(id)
        if to_remove:
            tickets = await self.config.guild(member.guild).tickets.all()
            for id in to_remove:
                del tickets[str(id)]
            await self.config.guild(member.guild).tickets.set(tickets)
        return count <= config["nb_max"]

    async def create_modlog(
        self, ticket, action: str, reason: str
    ) -> typing.Optional[modlog.Case]:
        config = await self.get_config(ticket.guild, ticket.profile)
        if config["create_modlog"]:
            return await modlog.create_case(
                ticket.bot,
                ticket.guild,
                ticket.created_at,
                action_type=action,
                user=ticket.created_by,
                moderator=None,
                reason=reason,
            )
        return

    def decorator(
        ticket_check: typing.Optional[bool] = False,
        status: typing.Optional[str] = None,
        ticket_owner: typing.Optional[bool] = False,
        admin_roles: typing.Optional[bool] = False,
        support_roles: typing.Optional[bool] = False,
        view_roles: typing.Optional[bool] = False,
        ticket_role: typing.Optional[bool] = False,
        guild_owner: typing.Optional[bool] = False,
        claim: typing.Optional[bool] = None,
        claim_staff: typing.Optional[bool] = False,
        members: typing.Optional[bool] = False,
        locked: typing.Optional[bool] = None,
    ) -> None:
        async def pred(ctx: commands.Context) -> bool:
            if not ticket_check:
                return True

            cog = ctx.bot.get_cog("TicketTool")
            ticket: Ticket = await cog.get_ticket(ctx.channel)
            lang = await cog.get_lang(ctx.guild)
            if ticket is None:
                raise commands.CheckFailure(get_text(lang, "not_in_ticket"))
            config = await cog.get_config(ticket.guild, ticket.profile)
            if status is not None and ticket.status != status:
                raise commands.CheckFailure(
                    get_text(lang, "ticket_not_status", status=status)
                )
            if claim is not None:
                if ticket.claim is not None:
                    check = True
                elif ticket.claim is None:
                    check = False
                if check != claim:
                    raise commands.CheckFailure(
                        get_text(lang, "ticket_is_status", status="claimed" if check else "unclaimed")
                    )
            if (
                locked is not None
                and not isinstance(ticket.channel, discord.TextChannel)
                and ticket.channel.locked != locked
            ):
                raise commands.CheckFailure(get_text(lang, "not_allowed_lock"))
            if ctx.author.id in ctx.bot.owner_ids:
                return True
            if (
                ticket_owner
                and not isinstance(ticket.owner, int)
                and ctx.author == ticket.owner
                and (ctx.command.name != "close" or config["user_can_close"])
            ):
                return True
            if (
                admin_roles
                and config["admin_roles"]
                and any(role in ctx.author.roles for role in config["admin_roles"])
            ):
                return True
            if (
                (
                    support_roles
                    or (ctx.command == cog.command_delete and config["delete_on_close"])
                )
                and config["support_roles"]
                and any(role in ctx.author.roles for role in config["support_roles"])
            ):
                return True
            if (
                view_roles
                and config["view_roles"]
                and any(role in ctx.author.roles for role in config["view_roles"])
            ):
                return True
            if (
                ticket_role
                and config["ticket_role"] is not None
                and ctx.author in config["ticket_role"].members
            ):
                return True
            if guild_owner and ctx.author == ctx.guild.owner:
                return True
            if claim_staff and ctx.author == ticket.claim:
                return True
            if members and ctx.author in ticket.members:
                return True
            raise commands.CheckFailure(get_text(lang, "not_allowed_view"))

        return commands.check(pred)

    class ProfileConverter(commands.Converter):
        async def convert(self, ctx: commands.Context, argument: str) -> str:
            cog = ctx.bot.get_cog("TicketTool")
            lang = await cog.get_lang(ctx.guild) if cog else "en"
            if len(argument) > 10:
                raise commands.BadArgument(get_text(lang, "profile_not_exist"))
            profiles = await cog.config.guild(ctx.guild).profiles()
            if argument.lower() not in profiles:
                raise commands.BadArgument(get_text(lang, "profile_not_exist"))
            return argument.lower()

    @commands.guild_only()
    @commands.hybrid_group(name="ticket")
    async def ticket(self, ctx: commands.Context) -> None:
        """Commands for using the Tickets system.

        Many commands to manage tickets appear when you run help in a ticket channel.
        """

    async def create_ticket(
        self,
        ctx: commands.Context,
        profile: typing.Optional[str],
        reason: typing.Optional[str] = None,
        member: typing.Optional[discord.Member] = None,
    ):
        lang = await self.get_lang(ctx.guild)
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        if profile is None:
            profiles = await self.config.guild(ctx.guild).profiles()
            if profiles:
                if len(profiles) == 1:
                    profile = list(profiles)[0]
                else:
                    raise commands.UserFeedbackCheckFailure(get_text(lang, "provide_profile"))
            else:
                raise commands.UserFeedbackCheckFailure(
                    get_text(lang, "no_profile_created")
                )
        config = await self.get_config(ctx.guild, profile)
        forum_channel: typing.Union[discord.ForumChannel, discord.Thread] = config["forum_channel"]
        category_open: discord.CategoryChannel = config["category_open"]
        category_close: discord.CategoryChannel = config["category_close"]
        if not config["enable"]:
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "system_not_enabled", prefix=ctx.prefix)
            )
        if forum_channel is None and (category_open is None or category_close is None):
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "category_not_configured", prefix=ctx.prefix)
            )
        if not await self.check_limit(member or ctx.author, profile):
            limit = config["nb_max"]
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "limit_reached", limit=limit),
                "delete_after",  # An extra arg checked in `commands.Cog.cog_command_error`.
            )
        if forum_channel is None:
            if (
                not category_open.permissions_for(ctx.me).manage_channels
                or not category_close.permissions_for(ctx.me).manage_channels
            ):
                raise commands.UserFeedbackCheckFailure(
                    get_text(lang, "no_manage_channels")
                )
        elif (
            not forum_channel.permissions_for(ctx.me).create_private_threads
            or not forum_channel.permissions_for(ctx.me).create_public_threads
        ):
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "no_manage_forum")
            )
        if config["custom_modal"] is not None:
            if getattr(ctx, "_tickettool_modal_answers", None) is None:
                modal = discord.ui.Modal(
                    title=get_text(lang, "modal_create_ticket"), custom_id="create_ticket_custom_modal"
                )
                modal.on_submit = lambda interaction: interaction.response.defer(ephemeral=True)
                inputs = []
                for _input in config["custom_modal"]:
                    _input["style"] = discord.TextStyle(_input["style"])
                    text_input = discord.ui.TextInput(**_input)
                    text_input.max_length = (
                        1024 if text_input.max_length is None else min(text_input.max_length, 1024)
                    )
                    inputs.append(text_input)
                    modal.add_item(text_input)
                view: discord.ui.View = discord.ui.View()

                async def interaction_check(interaction: discord.Interaction):
                    if interaction.user.id not in [ctx.author.id] + (
                        [member.id] if member is not None else []
                    ) + list(ctx.bot.owner_ids):
                        await interaction.response.send_message(
                            get_text(lang, "not_allowed_interaction"), ephemeral=True
                        )
                        return False
                    return True

                view.interaction_check = interaction_check
                button: discord.ui.Button = discord.ui.Button(
                    label=get_text(lang, "modal_create_ticket"), emoji="🎟️", style=discord.ButtonStyle.secondary
                )

                async def send_modal(_interaction: discord.Interaction) -> None:
                    nonlocal interaction
                    interaction = _interaction
                    await _interaction.response.send_modal(modal)
                    view.stop()

                button.callback = send_modal
                view.add_item(button)
                message = await ctx.send(
                    get_text(lang, "provide_info"),
                    view=view,
                )
                timeout = await view.wait()
                await CogsUtils.delete_message(message)
                if timeout:
                    return  # timeout
                if await modal.wait():
                    return  # timeout
                modal_answers: typing.Dict[str, str] = {
                    _input.label: _input.value.strip() or "Not provided." for _input in inputs
                }
            else:
                interaction = None
                modal_answers: typing.Dict[str, str] = ctx._tickettool_modal_answers
        ticket: Ticket = Ticket.instance(ctx, profile=profile, reason=reason)
        ticket.owner = member or ctx.author
        await ticket.create()
        if config["custom_modal"] is not None:
            embed: discord.Embed = discord.Embed()
            embed.title = get_text(lang, "modal_custom")
            embed.set_author(
                name=(
                    ctx.author.display_name
                    if interaction is None
                    else interaction.user.display_name
                ),
                icon_url=(
                    ctx.author.display_avatar
                    if interaction is None
                    else interaction.user.display_avatar
                ),
            )
            embed.color = await ctx.embed_color()
            for label, value in modal_answers.items():
                embed.add_field(
                    name=label,
                    value=value if len(value) <= 1024 else f"{value[:1021]}...",
                    inline=False,
                )
            if config["forum_channel"] is not None:
                channel = config["forum_channel"].get_thread(int(ticket.channel))
            else:
                channel = ctx.guild.get_channel(int(ticket.channel))
            await channel.send(embed=embed)
        ctx.ticket: Ticket = ticket

    @ticket.command(name="create", aliases=["+"])
    async def command_create(
        self,
        ctx: commands.Context,
        profile: typing.Optional[ProfileConverter] = None,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Create a Ticket.

        If only one profile has been created on this server, you don't need to specify its name.
        """
        await self.create_ticket(ctx, profile, reason)

    @commands.mod_or_permissions(manage_channels=True)
    @ticket.command(name="createfor")
    async def command_createfor(
        self,
        ctx: commands.Context,
        profile: typing.Optional[ProfileConverter],
        member: discord.Member,
        *,
        reason: typing.Optional[str] = None,
    ):
        """Create a Ticket for a member.

        If only one profile has been created on this server, you don't need to specify its name.
        """
        lang = await self.get_lang(ctx.guild)
        if member.bot:
            raise commands.UserFeedbackCheckFailure(get_text(lang, "cannot_create_bot"))
        elif member.top_role >= ctx.author.top_role:
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "cannot_create_higher")
            )
        await self.create_ticket(ctx, profile, reason, member=member)

    @decorator(
        ticket_check=True,
        status=None,
        ticket_owner=True,
        admin_roles=True,
        support_roles=False,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="export")
    async def command_export(self, ctx: commands.Context) -> None:
        """Export all the messages of an existing Ticket in html format.
        Please note: all attachments and user avatars are saved with the Discord link in this file.
        """
        ticket: Ticket = await self.get_ticket(ctx.channel)
        transcript = await chat_exporter.export(
            channel=ticket.channel,
            limit=None,
            tz_info="UTC",
            guild=ticket.guild,
            bot=ticket.bot,
        )
        if transcript is not None:
            file = discord.File(
                io.BytesIO(transcript.encode()),
                filename=f"transcript-ticket-{ticket.profile}-{ticket.id}.html",
            )
        lang = await self.get_lang(ctx.guild)
        message = await ctx.send(
            get_text(lang, "export_message"),
            file=file,
        )
        embed = discord.Embed(
            title=get_text(lang, "modal_transcript"),
            description=(
                f"[{get_text(lang, 'transcript_click')}](https://mahto.id/chat-exporter?url={message.attachments[0].url})"
            ),
            color=await ctx.embed_color(),
        )
        await message.edit(embed=embed)

    @decorator(
        ticket_check=True,
        status="close",
        ticket_owner=True,
        admin_roles=True,
        support_roles=True,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="open", aliases=["reopen"])
    async def command_open(
        self, ctx: commands.Context, *, reason: typing.Optional[str] = None
    ) -> None:
        """Open an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        config = await ctx.bot.get_cog("TicketTool").get_config(ticket.guild, ticket.profile)
        lang = await self.get_lang(ctx.guild)
        if not config["enable"]:
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "system_not_enabled_short")
            )
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.open(ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status="open",
        ticket_owner=True,
        admin_roles=True,
        support_roles=True,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="close")
    async def command_close(
        self,
        ctx: commands.Context,
        confirmation: typing.Optional[bool] = None,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Close an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        config = await self.get_config(ticket.guild, ticket.profile)
        lang = await self.get_lang(ctx.guild)
        if config["delete_on_close"]:
            await self.command_delete(ctx, confirmation=confirmation, reason=reason)
            return
        if confirmation is None:
            config = await self.get_config(ticket.guild, ticket.profile)
            confirmation = not config["close_confirmation"]
        if not confirmation and not ctx.assume_yes:
            embed: discord.Embed = discord.Embed()
            embed.title = get_text(lang, "confirm_close", id=ticket.id)
            embed.color = config["color"]
            embed.set_author(
                name=ctx.author.name,
                url=ctx.author.display_avatar,
                icon_url=ctx.author.display_avatar,
            )
            response = await CogsUtils.ConfirmationAsk(ctx, embed=embed)
            if not response:
                return
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.close(ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status=None,
        ticket_owner=False,
        admin_roles=True,
        support_roles=False,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=False,
    )
    @ticket.command(name="lock")
    async def command_lock(
        self,
        ctx: commands.Context,
        confirmation: typing.Optional[bool] = None,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Lock an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        lang = await self.get_lang(ctx.guild)
        if isinstance(ticket.channel, discord.TextChannel):
            raise commands.UserFeedbackCheckFailure(get_text(lang, "cannot_execute_text"))
        config = await self.get_config(ticket.guild, ticket.profile)
        if not confirmation and not ctx.assume_yes:
            embed: discord.Embed = discord.Embed()
            embed.title = get_text(lang, "confirm_lock", id=ticket.id)
            embed.color = config["color"]
            embed.set_author(
                name=ctx.author.name,
                url=ctx.author.display_avatar,
                icon_url=ctx.author.display_avatar,
            )
            response = await CogsUtils.ConfirmationAsk(ctx, embed=embed)
            if not response:
                return
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.lock(ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status=None,
        ticket_owner=False,
        admin_roles=True,
        support_roles=False,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=True,
    )
    @ticket.command(name="unlock")
    async def command_unlock(
        self,
        ctx: commands.Context,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Unlock an existing locked Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        lang = await self.get_lang(ctx.guild)
        if isinstance(ticket.channel, discord.TextChannel):
            raise commands.UserFeedbackCheckFailure(get_text(lang, "cannot_execute_text"))
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.unlock(ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status=None,
        ticket_owner=True,
        admin_roles=True,
        support_roles=True,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="rename")
    async def command_rename(
        self,
        ctx: commands.Context,
        new_name: str,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Rename an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        lang = await self.get_lang(ctx.guild)
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.rename(new_name, ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status=None,
        ticket_owner=False,
        admin_roles=True,
        support_roles=False,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="delete")
    async def command_delete(
        self,
        ctx: commands.Context,
        confirmation: typing.Optional[bool] = False,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Delete an existing Ticket.

        If a logs channel is defined, an html file containing all the messages of this ticket will be generated.
        (Attachments are not supported, as they are saved with their Discord link)
        """
        ticket: Ticket = await self.get_ticket(ctx.channel)
        config = await self.get_config(ticket.guild, ticket.profile)
        lang = await self.get_lang(ctx.guild)
        if not confirmation and not ctx.assume_yes:
            embed: discord.Embed = discord.Embed()
            embed.title = get_text(lang, "confirm_delete", id=ticket.id)
            if config["logschannel"] is not None:
                embed.description = get_text(lang, "logs_note")
            embed.color = config["color"]
            embed.set_author(
                name=ctx.author.name,
                url=ctx.author.display_avatar,
                icon_url=ctx.author.display_avatar,
            )
            response = await CogsUtils.ConfirmationAsk(ctx, embed=embed)
            if not response:
                return
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.delete(ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status="open",
        ticket_owner=False,
        admin_roles=True,
        support_roles=True,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=False,
        claim_staff=False,
        members=False,
        locked=None,
    )
    @ticket.command(name="claim")
    async def command_claim(
        self,
        ctx: commands.Context,
        member: typing.Optional[discord.Member] = None,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Claim an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        if member is None:
            member = ctx.author
        lang = await self.get_lang(ctx.guild)
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.claim_ticket(member, ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status=None,
        ticket_owner=False,
        admin_roles=True,
        support_roles=True,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=True,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="unclaim")
    async def command_unclaim(
        self, ctx: commands.Context, *, reason: typing.Optional[str] = None
    ) -> None:
        """Unclaim an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        lang = await self.get_lang(ctx.guild)
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.unclaim_ticket(ticket.claim, ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status="open",
        ticket_owner=True,
        admin_roles=True,
        support_roles=False,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=False,
        members=False,
        locked=None,
    )
    @ticket.command(name="owner")
    async def command_owner(
        self,
        ctx: commands.Context,
        new_owner: discord.Member,
        *,
        reason: typing.Optional[str] = None,
    ) -> None:
        """Change the owner of an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        if new_owner is None:
            new_owner = ctx.author
        lang = await self.get_lang(ctx.guild)
        if reason is None:
            reason = get_text(lang, "no_reason_provided")
        await ticket.change_owner(new_owner, ctx.author, reason=reason)

    @decorator(
        ticket_check=True,
        status="open",
        ticket_owner=True,
        admin_roles=True,
        support_roles=False,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="addmember", aliases=["add"])
    async def command_addmember(
        self,
        ctx: commands.Context,
        members: commands.Greedy[discord.Member],
    ) -> None:
        """Add a member to an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        members = list(members)
        await ticket.add_member(members, ctx.author)

    @decorator(
        ticket_check=True,
        status=None,
        ticket_owner=True,
        admin_roles=True,
        support_roles=False,
        view_roles=False,
        ticket_role=False,
        guild_owner=True,
        claim=None,
        claim_staff=True,
        members=False,
        locked=None,
    )
    @ticket.command(name="removemember", aliases=["remove"])
    async def command_removemember(
        self,
        ctx: commands.Context,
        members: commands.Greedy[discord.Member],
    ) -> None:
        """Remove a member to an existing Ticket."""
        ticket: Ticket = await self.get_ticket(ctx.channel)
        members = list(members)
        await ticket.remove_member(members, ctx.author)

    @commands.admin_or_permissions(administrator=True)
    @ticket.command(name="list")
    async def command_list(
        self,
        ctx: commands.Context,
        profile: ProfileConverter,
        status: typing.Optional[typing.Literal["open", "close", "all"]],
        owner: typing.Optional[discord.Member],
    ) -> None:
        """List the existing Tickets for a profile. You can provide a status and/or a ticket owner."""
        if status is None:
            status = "open"
        tickets = await self.config.guild(ctx.guild).tickets.all()
        tickets_to_show = []
        for channel_id in tickets:
            config = await self.get_config(ctx.guild, profile=profile)
            if config["forum_channel"] is not None:
                channel = config["forum_channel"].get_thread(int(channel_id))
            else:
                channel = ctx.guild.get_channel(int(channel_id))
            if channel is None:
                continue
            ticket: Ticket = await self.get_ticket(channel)
            if (
                ticket.profile == profile
                and (owner is None or ticket.owner == owner)
                and (status == "all" or ticket.status == status)
            ):
                tickets_to_show.append(ticket)
        if not tickets_to_show:
            lang = await self.get_lang(ctx.guild)
            raise commands.UserFeedbackCheckFailure(get_text(lang, "no_tickets"))
        BREAK_LINE = "\n"
        description = "\n".join(
            [
                f"• **#{ticket.id}** - {ticket.status} - {ticket.channel.mention} - {ticket.reason.split(BREAK_LINE)[0][:30]}"
                for ticket in sorted(tickets_to_show, key=lambda x: x.id)
            ]
        )
        pages = list(pagify(description, page_length=6000))
        lang = await self.get_lang(ctx.guild)
        embeds = []
        for page in pages:
            embed: discord.Embed = discord.Embed(
                title=get_text(lang, "tickets_list_title", profile=profile)
            )
            embed.description = page
            embeds.append(embed)
        await Menu(pages=embeds).start(ctx)

    async def on_button_interaction(self, interaction: discord.Interaction) -> None:
        permissions = interaction.channel.permissions_for(interaction.user)
        if not permissions.read_messages and not permissions.send_messages:
            return
        permissions = interaction.channel.permissions_for(interaction.guild.me)
        if not permissions.read_messages and not permissions.read_message_history:
            return
        if not interaction.response.is_done() and interaction.data["custom_id"] not in (
            "create_ticket_button",
            "close_ticket_button",
        ):
            await interaction.response.defer(ephemeral=True)
        if interaction.data["custom_id"] == "create_ticket_button":
            buttons = await self.config.guild(interaction.guild).buttons.all()
            if f"{interaction.message.channel.id}-{interaction.message.id}" in buttons:
                profile = buttons[f"{interaction.message.channel.id}-{interaction.message.id}"][
                    "profile"
                ]
            else:
                profile = "main"
            profiles = await self.config.guild(interaction.guild).profiles()
            lang = await self.get_lang(interaction.guild)
            if profile not in profiles:
                await interaction.response.send_message(
                    get_text(lang, "profile_button_not_exist"),
                    ephemeral=True,
                )
                return
            config = await self.get_config(guild=interaction.guild, profile=profile)
            if config["custom_modal"] is None:
                modal = discord.ui.Modal(title=get_text(lang, "modal_create_ticket"), custom_id="create_ticket_modal")
                modal.on_submit = lambda interaction: interaction.response.defer(ephemeral=True)
                # profile_input = discord.ui.TextInput(
                #     label="Profile",
                #     style=discord.TextStyle.short,
                #     default=profile,
                #     max_length=10,
                #     required=True,
                # )
                # modal.add_item(profile_input)
                reason_input = discord.ui.TextInput(
                    label=get_text(lang, "modal_why_create"),
                    style=discord.TextStyle.long,
                    max_length=1000,
                    required=False,
                    placeholder=get_text(lang, "no_reason_provided"),
                )
                modal.add_item(reason_input)
                await interaction.response.send_modal(modal)
                if await modal.wait():
                    return  # timeout
                # profile = profile_input.value
                reason = reason_input.value or ""
                kwargs = {}
            else:
                reason = ""
                modal = discord.ui.Modal(
                    title=get_text(lang, "modal_create_ticket"), custom_id="create_ticket_custom_modal"
                )
                modal.on_submit = lambda interaction: interaction.response.defer(ephemeral=True)
                inputs = []
                for _input in config["custom_modal"]:
                    _input["style"] = discord.TextStyle(_input["style"])
                    text_input = discord.ui.TextInput(**_input)
                    text_input.max_length = (
                        1024 if text_input.max_length is None else min(text_input.max_length, 1024)
                    )
                    inputs.append(text_input)
                    modal.add_item(text_input)
                await interaction.response.send_modal(modal)
                if await modal.wait():
                    return  # timeout
                kwargs = {
                    "_tickettool_modal_answers": {
                        _input.label: _input.value.strip() or get_text(lang, "no_reason_provided") for _input in inputs
                    }
                }
            ctx = await CogsUtils.invoke_command(
                bot=interaction.client,
                author=interaction.user,
                channel=interaction.channel,
                command=f"ticket create {profile}" + (f" {reason}" if reason != "" else ""),
                **kwargs,
            )
            if not await discord.utils.async_all([check(ctx) for check in ctx.command.checks]):
                try:
                    await interaction.response.send_message(
                        get_text(lang, "not_allowed_command"), ephemeral=True
                    )
                except discord.InteractionResponded:
                    await interaction.followup.send(
                        get_text(lang, "chosen_create"), ephemeral=True
                    )
            else:
                try:
                    await interaction.response.send_message(
                        get_text(lang, "chosen_create"), ephemeral=True
                    )
                except discord.InteractionResponded:
                    await interaction.followup.send(
                        get_text(lang, "chosen_create"), ephemeral=True
                    )
        elif interaction.data["custom_id"] == "close_ticket_button":
            lang = await self.get_lang(interaction.guild)
            modal = discord.ui.Modal(
                title=get_text(lang, "modal_close_ticket"), timeout=180, custom_id="close_ticket_modal"
            )
            modal.on_submit = lambda interaction: interaction.response.defer(ephemeral=True)
            reason_input = discord.ui.TextInput(
                label=get_text(lang, "modal_why_close"),
                style=discord.TextStyle.long,
                max_length=1000,
                required=False,
                placeholder=get_text(lang, "no_reason_provided"),
            )
            modal.add_item(reason_input)
            await interaction.response.send_modal(modal)
            timeout = await modal.wait()
            if timeout:
                return
            reason = reason_input.value or ""
            lang = await self.get_lang(interaction.guild)
            try:
                await interaction.followup.send(
                    get_text(lang, "chosen_close"),
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass
            ctx = await CogsUtils.invoke_command(
                bot=interaction.client,
                author=interaction.user,
                channel=interaction.channel,
                command=("ticket close" + (f" {reason}" if reason != "" else "")),
            )
        elif interaction.data["custom_id"] == "open_ticket_button":
            lang = await self.get_lang(interaction.guild)
            ctx = await CogsUtils.invoke_command(
                bot=interaction.client,
                author=interaction.user,
                channel=interaction.channel,
                command="ticket open",
                invoke=False,
            )
            try:
                if not await discord.utils.async_all([check(ctx) for check in ctx.command.checks]):
                    await interaction.followup.send(
                        get_text(lang, "not_allowed_command"), ephemeral=True
                    )
            except commands.CheckFailure:
                await interaction.followup.send(
                    get_text(lang, "not_allowed_command"), ephemeral=True
                )
            await interaction.client.invoke(ctx)
            await interaction.followup.send(
                get_text(lang, "chosen_reopen"),
                ephemeral=True,
            )
        elif interaction.data["custom_id"] == "claim_ticket_button":
            lang = await self.get_lang(interaction.guild)
            ctx = await CogsUtils.invoke_command(
                bot=interaction.client,
                author=interaction.user,
                channel=interaction.channel,
                command="ticket claim",
                invoke=False,
            )
            try:
                if not await discord.utils.async_all([check(ctx) for check in ctx.command.checks]):
                    await interaction.followup.send(
                        get_text(lang, "not_allowed_command"), ephemeral=True
                    )
            except commands.CheckFailure:
                await interaction.followup.send(
                    get_text(lang, "not_allowed_command"), ephemeral=True
                )
            await interaction.client.invoke(ctx)
            await interaction.followup.send(
                get_text(lang, "chosen_claim"),
                ephemeral=True,
            )
        elif interaction.data["custom_id"] == "delete_ticket_button":
            lang = await self.get_lang(interaction.guild)
            ctx = await CogsUtils.invoke_command(
                bot=interaction.client,
                author=interaction.user,
                channel=interaction.channel,
                command="ticket delete",
                invoke=False,
            )
            try:
                if not await discord.utils.async_all([check(ctx) for check in ctx.command.checks]):
                    await interaction.followup.send(
                        get_text(lang, "not_allowed_command"), ephemeral=True
                    )
            except commands.CheckFailure:
                await interaction.followup.send(
                    get_text(lang, "not_allowed_command"), ephemeral=True
                )
            await interaction.client.invoke(ctx)

    async def on_dropdown_interaction(
        self, interaction: discord.Interaction, select_menu: discord.ui.Select
    ) -> None:
        options = select_menu.values
        if len(options) == 0:
            return
        permissions = interaction.channel.permissions_for(interaction.user)
        if not permissions.read_messages and not permissions.send_messages:
            await interaction.response.defer()
            return
        permissions = interaction.channel.permissions_for(interaction.guild.me)
        if not permissions.read_messages and not permissions.read_message_history:
            return
        lang = await self.get_lang(interaction.guild)
        dropdowns = await self.config.guild(interaction.guild).dropdowns()
        if f"{interaction.message.channel.id}-{interaction.message.id}" not in dropdowns:
            await interaction.response.send_message(
                get_text(lang, "not_in_config"), ephemeral=True
            )
            return
        profile = dropdowns[f"{interaction.message.channel.id}-{interaction.message.id}"][0].get(
            "profile", "main"
        )
        profiles = await self.config.guild(interaction.guild).profiles()
        if profile not in profiles:
            await interaction.response.send_message(
                get_text(lang, "profile_dropdown_not_exist"),
                ephemeral=True,
            )
            return
        option = [option for option in select_menu.options if option.value == options[0]][0]
        reason = f"{option.emoji} - {option.label}"
        config = await self.get_config(guild=interaction.guild, profile=profile)
        if config["custom_modal"] is not None:
            modal = discord.ui.Modal(title=get_text(lang, "modal_create_ticket"), custom_id="create_ticket_custom_modal")
            modal.on_submit = lambda interaction: interaction.response.defer(ephemeral=True)
            inputs = []
            for _input in config["custom_modal"]:
                _input["style"] = discord.TextStyle(_input["style"])
                text_input = discord.ui.TextInput(**_input)
                text_input.max_length = (
                    1024 if text_input.max_length is None else min(text_input.max_length, 1024)
                )
                inputs.append(text_input)
                modal.add_item(text_input)
            await interaction.response.send_modal(modal)
            if await modal.wait():
                return  # timeout
            kwargs = {
                "_tickettool_modal_answers": {
                    _input.label: _input.value.strip() or get_text(lang, "no_reason_provided") for _input in inputs
                }
            }
        else:
            kwargs = {}
        ctx = await CogsUtils.invoke_command(
            bot=interaction.client,
            author=interaction.user,
            channel=interaction.channel,
            command=f"ticket create {profile} {reason}",
            **kwargs,
        )
        if not await discord.utils.async_all(
            [check(ctx) for check in ctx.command.checks]
        ) or not hasattr(ctx, "ticket"):
            try:
                await interaction.response.send_message(
                    get_text(lang, "not_allowed_command"), ephemeral=True
                )
            except discord.InteractionResponded:
                await interaction.followup.send(
                    get_text(lang, "not_allowed_command"), ephemeral=True
                )
            return
        config = await self.get_config(interaction.guild, profile)
        if config["embed_button"]["rename_channel_dropdown"]:
            try:
                ticket: Ticket = await self.get_ticket(
                    config["forum_channel"].get_thread(ctx.ticket.channel)
                    if config["forum_channel"] is not None
                    else ctx.guild.get_channel(ctx.ticket.channel)
                )
                if ticket is not None:
                    await ticket.rename(
                        new_name=f"{option.emoji}-{option.value}_{interaction.user.id}".replace(
                            " ", "-"
                        ),
                        author=None,
                    )
            except discord.HTTPException:
                pass
        try:
            await interaction.response.send_message(
                get_text(lang, "chosen_create_reason", reason=reason),
                ephemeral=True,
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                get_text(lang, "chosen_create_reason", reason=reason),
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        member = guild.get_member(payload.user_id)
        if member == guild.me or member.bot:
            return
        if await self.bot.cog_disabled_in_guild(
            cog=self, guild=guild
        ) or not await self.bot.allowed_by_whitelist_blacklist(who=member):
            return
        profile = "main"
        profiles = await self.config.guild(guild).profiles()
        if profile not in profiles:
            return
        config = await self.get_config(guild, profile)
        if config["enable"] and config["create_on_react"] and str(payload.emoji) == "🎟️":
            permissions = channel.permissions_for(member)
            if not permissions.read_messages and not permissions.send_messages:
                return
            permissions = channel.permissions_for(guild.me)
            if not permissions.read_messages and not permissions.read_message_history:
                return
            await CogsUtils.invoke_command(
                bot=self.bot,
                author=member,
                channel=channel,
                command="ticket create",
            )
        return

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        config = await self.config.guild(message.guild).dropdowns.all()
        if f"{message.channel.id}-{message.id}" not in config:
            return
        del config[f"{message.channel.id}-{message.id}"]
        await self.config.guild(message.guild).dropdowns.set(config)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, old_channel: discord.abc.GuildChannel) -> None:
        tickets = await self.config.guild(old_channel.guild).tickets.all()
        if str(old_channel.id) not in tickets:
            return
        try:
            del tickets[str(old_channel.id)]
        except KeyError:
            pass
        await self.config.guild(old_channel.guild).tickets.set(tickets)
        return

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        tickets = await self.config.guild(member.guild).tickets.all()
        for channel_id in tickets:
            config = await self.get_config(member.guild, profile=tickets[channel_id]["profile"])
            if config["forum_channel"] is not None:
                channel = config["forum_channel"].get_thread(int(channel_id))
            else:
                channel = member.guild.get_channel(int(channel_id))
            if channel is None:
                continue
            ticket: Ticket = await self.get_ticket(channel)
            if config["close_on_leave"] and (
                getattr(ticket.owner, "id", ticket.owner) == member.id and ticket.status == "open"
            ):
                await ticket.close(ticket.guild.me)
        return

    def get_buttons(self, buttons: typing.List[dict]) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        for button in buttons:
            if "emoji" in button:
                try:
                    int(button["emoji"])
                except ValueError:
                    pass
                else:
                    button["emoji"] = str(self.bot.get_emoji(int(button["emoji"])))
            button = discord.ui.Button(**button)
            button.callback = self.on_button_interaction
            view.add_item(button)
        return view

    def get_dropdown(self, placeholder: str, options: typing.List[dict]) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        select_menu = discord.ui.Select(
            placeholder=placeholder, custom_id="create_ticket_dropdown", min_values=0, max_values=1
        )
        for option in options:
            if "emoji" in option:
                try:
                    int(option["emoji"])
                except ValueError:
                    pass
                else:
                    option["emoji"] = str(self.bot.get_emoji(int(option["emoji"])))
            option = discord.SelectOption(**option)
            select_menu.append_option(option)
        select_menu.callback = partial(self.on_dropdown_interaction, select_menu=select_menu)
        view.add_item(select_menu)
        return view

    @commands.Cog.listener()
    async def on_assistant_cog_add(
        self, assistant_cog: typing.Optional[commands.Cog] = None
    ) -> None:  # Vert's Assistant integration/third party.
        if assistant_cog is None:
            return self.get_open_tickets_list_in_server_for_assistant
        schema = {
            "name": "get_open_tickets_list_in_server_for_assistant",
            "description": "Get open tickets for the user in this server, and their reason.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
        await assistant_cog.register_function(cog_name=self.qualified_name, schema=schema)

    async def get_open_tickets_list_in_server_for_assistant(
        self, user: discord.Member, *args, **kwargs
    ):
        if not isinstance(user, discord.Member):
            return "The command isn't executed in a server."
        tickets = await self.config.guild(user.guild).tickets.all()
        tickets_to_show = []
        for channel_id in tickets:
            config = await self.get_config(user.guild, profile=tickets[channel_id]["profile"])
            if config["forum_channel"] is not None:
                channel = config["forum_channel"].get_thread(int(channel_id))
            else:
                channel = user.guild.get_channel(int(channel_id))
            if channel is None:
                continue
            ticket: Ticket = await self.get_ticket(channel)
            if (ticket.owner is not None and ticket.owner == user) and ticket.status == "open":
                tickets_to_show.append(ticket)
        if not tickets_to_show:
            lang = await self.get_lang(user.guild)
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "no_open_tickets")
            )
        BREAK_LINE = "\n"
        open_tickets = "\n" + "\n".join(
            [
                f"• #{ticket.id} - {ticket.channel.mention} - {ticket.reason.split(BREAK_LINE)[0][:50]}"
                for ticket in sorted(tickets_to_show, key=lambda x: x.id)
            ]
        )
        data = {
            "Open Tickets": open_tickets,
        }
        return [f"{key}: {value}\n" for key, value in data.items() if value is not None]
