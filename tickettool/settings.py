import discord
import typing
from redbot.core import commands

from .locales import get_text, LANGUAGE_NAMES
from .utils import Emoji, EmojiLabelDescriptionValueConverter, CustomModalConverter


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
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    @commands.hybrid_group(name="settickettool", aliases=["tickettoolset"])
    async def configuration(self, ctx: commands.Context) -> None:
        """Configure TicketTool for your server."""
        pass

    # ── Profile management ──────────────────────────────────────────────────

    @configuration.group(name="profile", invoke_without_command=True)
    async def profile_group(self, ctx: commands.Context) -> None:
        """Manage ticket profiles."""
        profiles = await self.config.guild(ctx.guild).profiles()
        if not profiles:
            await ctx.send("No profiles configured. Use `settickettool profile create <name>`.")
            return
        embed = discord.Embed(title="Ticket Profiles", color=discord.Color.blue())
        for name, data in profiles.items():
            status = "✅ Enabled" if data.get("enable") else "❌ Disabled"
            embed.add_field(name=name, value=status, inline=True)
        await ctx.send(embed=embed)

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
        embed.title = get_text(lang, "modal_create_ticket")
        embed.description = get_text(lang, "default_embed_description", prefix=ctx.prefix)
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

    # ── Language ─────────────────────────────────────────────────────────────

    @configuration.command(name="language", aliases=["lang", "idioma"])
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
