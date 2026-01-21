from redbot.core import commands  # isort:skip
from redbot.core.bot import Red  # isort:skip
import discord  # isort:skip
import typing  # isort:skip

import datetime
import io

import chat_exporter

from .locales import get_text
from .utils import utils


class Ticket:
    """Representation of a Ticket."""

    def __init__(
        self,
        bot,
        cog,
        profile,
        id,
        owner,
        guild,
        channel,
        claim,
        created_by,
        opened_by,
        closed_by,
        deleted_by,
        renamed_by,
        locked_by,
        unlocked_by,
        members,
        created_at,
        opened_at,
        closed_at,
        deleted_at,
        renamed_at,
        locked_at,
        unlocked_at,
        status,
        reason,
        logs_messages,
        save_data,
        first_message,
    ):
        self.bot: Red = bot
        self.cog: commands.Cog = cog

        self.profile: str = profile
        self.id: int = id

        self.owner: discord.Member = owner
        self.guild: discord.Guild = guild
        self.channel: typing.Union[discord.TextChannel, discord.Thread] = channel
        self.claim: discord.Member = claim

        self.created_by: discord.Member = created_by
        self.opened_by: discord.Member = opened_by
        self.closed_by: discord.Member = closed_by
        self.deleted_by: discord.Member = deleted_by
        self.renamed_by: discord.Member = renamed_by
        self.locked_by: discord.Member = locked_by
        self.unlocked_by: discord.Member = unlocked_by

        self.members: typing.List[discord.Member] = members

        self.created_at: datetime.datetime = created_at
        self.opened_at: datetime.datetime = opened_at
        self.closed_at: datetime.datetime = closed_at
        self.deleted_at: datetime.datetime = deleted_at
        self.renamed_at: datetime.datetime = renamed_at
        self.locked_at: datetime.datetime = locked_at
        self.unlocked_at: datetime.datetime = unlocked_at

        self.status: str = status
        self.reason: str = reason

        self.first_message: discord.Message = first_message
        self.logs_messages: bool = logs_messages
        self.save_data: bool = save_data

    @staticmethod
    def instance(
        ctx: commands.Context,
        profile: str,
        reason: str = "No reason provided.",
    ) -> typing.Any:
        ticket: Ticket = Ticket(
            bot=ctx.bot,
            cog=ctx.cog,
            profile=profile,
            id=None,
            owner=ctx.author,
            guild=ctx.guild,
            channel=None,
            claim=None,
            created_by=ctx.author,
            opened_by=ctx.author,
            closed_by=None,
            deleted_by=None,
            renamed_by=None,
            locked_by=None,
            unlocked_by=None,
            members=[],
            created_at=datetime.datetime.now(),
            opened_at=None,
            closed_at=None,
            deleted_at=None,
            renamed_at=None,
            locked_at=None,
            unlocked_at=None,
            status="open",
            reason=reason,
            first_message=None,
            logs_messages=True,
            save_data=True,
        )
        return ticket

    @staticmethod
    def from_json(json: dict, bot: Red, cog: commands.Cog) -> typing.Any:
        ticket: Ticket = Ticket(
            bot=bot,
            cog=cog,
            profile=json["profile"],
            id=json["id"],
            owner=json["owner"],
            guild=json["guild"],
            channel=json["channel"],
            claim=json.get("claim"),
            created_by=json["created_by"],
            opened_by=json.get("opened_by"),
            closed_by=json.get("closed_by"),
            deleted_by=json.get("deleted_by"),
            renamed_by=json.get("renamed_by"),
            locked_by=json.get("locked_by"),
            unlocked_by=json.get("unlocked_by"),
            members=json.get("members"),
            created_at=json["created_at"],
            opened_at=json.get("opened_at"),
            closed_at=json.get("closed_at"),
            deleted_at=json.get("deleted_at"),
            renamed_at=json.get("renamed_at"),
            locked_at=json.get("locked_at"),
            unlocked_at=json.get("unlocked_at"),
            status=json["status"],
            reason=json["reason"],
            first_message=json["first_message"],
            logs_messages=json.get("logs_messages", True),
            save_data=json.get("save_data", True),
        )
        return ticket

    async def save(self, clean: bool = True) -> typing.Dict[str, typing.Any]:
        if not self.save_data:
            return
        cog = self.cog
        guild = self.guild
        channel = self.channel
        if self.owner is not None:
            self.owner = int(getattr(self.owner, "id", self.owner))
        if self.guild is not None:
            self.guild = int(self.guild.id)
        if self.channel is not None:
            self.channel = int(self.channel.id)
        if self.claim is not None:
            self.claim = self.claim.id
        if self.created_by is not None:
            self.created_by = int(getattr(self.created_by, "id", self.created_by))
        if self.opened_by is not None:
            self.opened_by = int(getattr(self.opened_by, "id", self.opened_by))
        if self.closed_by is not None:
            self.closed_by = int(getattr(self.closed_by, "id", self.closed_by))
        if self.deleted_by is not None:
            self.deleted_by = int(getattr(self.deleted_by, "id", self.deleted_by))
        if self.renamed_by is not None:
            self.renamed_by = int(getattr(self.renamed_by, "id", self.renamed_by))
        if self.locked_by is not None:
            self.locked_by = int(getattr(self.locked_by, "id", self.locked_by))
        if self.unlocked_by is not None:
            self.unlocked_by = int(getattr(self.unlocked_by, "id", self.unlocked_by))
        members = self.members
        self.members = [int(m.id) for m in members]
        if self.created_at is not None:
            self.created_at = float(datetime.datetime.timestamp(self.created_at))
        if self.opened_at is not None:
            self.opened_at = float(datetime.datetime.timestamp(self.opened_at))
        if self.closed_at is not None:
            self.closed_at = float(datetime.datetime.timestamp(self.closed_at))
        if self.deleted_at is not None:
            self.deleted_at = float(datetime.datetime.timestamp(self.deleted_at))
        if self.renamed_at is not None:
            self.renamed_at = float(datetime.datetime.timestamp(self.renamed_at))
        if self.locked_at is not None:
            self.locked_at = float(datetime.datetime.timestamp(self.locked_at))
        if self.unlocked_at is not None:
            self.unlocked_at = float(datetime.datetime.timestamp(self.unlocked_at))
        if self.first_message is not None:
            self.first_message = int(self.first_message.id)
        json = self.__dict__
        for key in ("bot", "cog"):
            del json[key]
        if clean:
            for key in (
                "claim",
                "opened_by",
                "closed_by",
                "deleted_by",
                "renamed_by",
                "locked_by",
                "unlocked_by",
                "opened_at",
                "closed_at",
                "deleted_at",
                "renamed_at",
                "locked_at",
                "unlocked_at",
            ):
                if json[key] is None:
                    del json[key]
            if json["members"] == []:
                del json["members"]
            for key in ("logs_messages", "save_data"):
                if json[key]:
                    del json[key]
        data = await cog.config.guild(guild).tickets.all()
        data[str(channel.id)] = json
        await cog.config.guild(guild).tickets.set(data)
        return json

    async def create(self) -> typing.Any:
        config = await self.cog.get_config(self.guild, self.profile)
        logschannel = config["logschannel"]
        ping_roles = config["ping_roles"]
        self.id = config["last_nb"] + 1
        lang = await self.cog.get_lang(self.guild)
        _reason = await self.cog.get_audit_reason(
            guild=self.guild,
            profile=self.profile,
            author=self.created_by,
            reason=get_text(lang, "creating_ticket", id=self.id),
        )
        try:
            to_replace = {
                "ticket_id": str(self.id),
                "owner_display_name": self.owner.display_name,
                "owner_name": self.owner.name,
                "owner_id": str(self.owner.id),
                "guild_name": self.guild.name,
                "guild_id": self.guild.id,
                "bot_display_name": self.guild.me.display_name,
                "bot_name": self.bot.user.name,
                "bot_id": str(self.bot.user.id),
                "shortdate": self.created_at.strftime("%m-%d"),
                "longdate": self.created_at.strftime("%m-%d-%Y"),
                "time": self.created_at.strftime("%I-%M-%p"),
                "emoji": config["emoji_open"],
            }
            name = config["dynamic_channel_name"].format(**to_replace).replace(" ", "-")
        except (KeyError, AttributeError):
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "dynamic_name_error")
            )

        view = await self.cog.get_ticket_buttons(self.guild, include_close=True, include_open=False, claim_disabled=False)
        optionnal_ping = (
            f" ||{' '.join(role.mention for role in ping_roles)}||"[:1500] if ping_roles else ""
        )
        embed = await self.cog.get_embed_important(
            self,
            False,
            author=self.created_by,
            title=get_text(lang, "ticket_created"),
            description=get_text(lang, "ticket_created_thanks"),
            reason=self.reason,
        )
        if config["ticket_role"] is not None and self.owner:
            try:
                await self.owner.add_roles(config["ticket_role"], reason=_reason)
            except discord.HTTPException:
                pass
        try:
            if config["forum_channel"] is None:
                overwrites = await utils().get_overwrites(self)
                topic = get_text(
                    lang,
                    "channel_topic",
                    id=self.id,
                    created_by_name=self.created_by.display_name,
                    created_by_id=self.created_by.id,
                    short_reason=(
                        f"{self.reason[:700]}...".replace("\n", " ")
                        if len(self.reason) > 700
                        else self.reason.replace("\n", " ")
                    ),
                )
                self.channel: discord.TextChannel = await self.guild.create_text_channel(
                    name,
                    overwrites=overwrites,
                    category=config["category_open"],
                    topic=topic,
                    reason=_reason,
                )
                await self.channel.edit(topic=topic)
                self.first_message = await self.channel.send(
                    f"{self.created_by.mention}{optionnal_ping}",
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
                self.cog.views[self.first_message] = view
            else:
                if isinstance(config["forum_channel"], discord.ForumChannel):
                    forum_channel: discord.ForumChannel = config["forum_channel"]
                    result: discord.channel.ThreadWithMessage = await forum_channel.create_thread(
                        name=name,
                        content=f"{self.created_by.mention}{optionnal_ping}",
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                        auto_archive_duration=10080,
                        reason=_reason,
                    )
                    self.channel: discord.Thread = result.thread
                    self.first_message: discord.Message = result.message
                else:  # isinstance(config["forum_channel"], discord.TextChannel)
                    forum_channel: discord.TextChannel = config["forum_channel"]
                    self.channel: discord.Thread = await forum_channel.create_thread(
                        name=name,
                        message=None,  # Private thread.
                        type=discord.ChannelType.private_thread,
                        invitable=False,
                        auto_archive_duration=10080,
                        reason=_reason,
                    )
                    self.first_message = await self.channel.send(
                        f"{self.created_by.mention}{optionnal_ping}",
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                    )
                self.cog.views[self.first_message] = view
                members = [self.owner]
                if self.claim is not None:
                    members.append(self.claim)
                if config["admin_roles"]:
                    for role in config["admin_roles"]:
                        members.extend(role.members)
                if config["support_roles"]:
                    for role in config["support_roles"]:
                        members.extend(role.members)
                if config["view_roles"]:
                    for role in config["view_roles"]:
                        members.extend(role.members)
                adding_error = False
                for member in members:
                    try:
                        await self.channel.add_user(member)
                    except (
                        discord.HTTPException
                    ):  # The bot haven't the permission `manage_messages` in the parent text channel.
                        adding_error = True
                if adding_error:
                    await self.channel.send(
                        get_text(lang, "thread_add_error")
                    )
            if config["create_modlog"]:
                await self.cog.create_modlog(self, "ticket_created", _reason)
            if config["custom_message"] is not None:
                try:
                    embed: discord.Embed = discord.Embed()
                    embed.title = "Custom Message"
                    to_replace = {
                        "ticket_id": str(self.id),
                        "owner_display_name": self.owner.display_name,
                        "owner_name": self.owner.name,
                        "owner_id": str(self.owner.id),
                        "guild_name": self.guild.name,
                        "guild_id": self.guild.id,
                        "bot_display_name": self.guild.me.display_name,
                        "bot_name": self.bot.user.name,
                        "bot_id": str(self.bot.user.id),
                        "shortdate": self.created_at.strftime("%m-%d"),
                        "longdate": self.created_at.strftime("%m-%d-%Y"),
                        "time": self.created_at.strftime("%I-%M-%p"),
                        "emoji": config["emoji_open"],
                    }
                    embed.description = config["custom_message"].format(**to_replace)
                    await self.channel.send(embed=embed)
                except (KeyError, AttributeError, discord.HTTPException):
                    pass
            if logschannel is not None:
                embed = await self.cog.get_embed_important(
                    self,
                    True,
                    author=self.created_by,
                    title=get_text(lang, "ticket_created"),
                    description=get_text(lang, "ticket_created_by", created_by=self.created_by),
                    reason=self.reason,
                )
                await logschannel.send(
                    get_text(lang, "report_creation", id=self.id),
                    embed=embed,
                )
        except discord.HTTPException:
            if config["ticket_role"] is not None and self.owner:
                try:
                    await self.owner.remove_roles(config["ticket_role"], reason=_reason)
                except discord.HTTPException:
                    pass
            raise
        await self.cog.config.guild(self.guild).profiles.set_raw(
            self.profile, "last_nb", value=self.id
        )
        await self.save()
        return self

    async def export(self) -> typing.Optional[discord.File]:
        if self.channel:
            transcript = await chat_exporter.export(
                channel=self.channel,
                limit=None,
                tz_info="UTC",
                guild=self.guild,
                bot=self.bot,
            )
            if transcript is not None:
                return discord.File(
                    io.BytesIO(transcript.encode()),
                    filename=f"transcript-ticket-{self.profile}-{self.id}.html",
                )
        return None

    async def open(
        self, author: typing.Optional[discord.Member] = None, reason: typing.Optional[str] = None
    ) -> typing.Any:
        config = await self.cog.get_config(self.guild, self.profile)
        lang = await self.cog.get_lang(self.guild)
        _reason = await self.cog.get_audit_reason(
            guild=self.guild,
            profile=self.profile,
            author=author,
            reason=get_text(lang, "opening_ticket", id=self.id),
        )
        logschannel = config["logschannel"]
        emoji_open = config["emoji_open"]
        emoji_close = config["emoji_close"]
        self.status = "open"
        self.opened_by = author
        self.opened_at = datetime.datetime.now()
        self.closed_by = None
        self.closed_at = None
        new_name = f"{self.channel.name}"
        new_name = new_name.replace(f"{emoji_close}-", "", 1)
        new_name = f"{emoji_open}-{new_name}"
        if isinstance(self.channel, discord.TextChannel):
            members = [self.owner] + self.members
            overwrites = self.channel.overwrites
            for member in members:
                if member in overwrites:
                    overwrites[member].send_messages = True
            await self.channel.edit(
                name=new_name,
                category=config["category_open"],
                overwrites=overwrites,
                reason=_reason,
            )
        else:
            await self.channel.edit(name=new_name, archived=False, reason=_reason)
        if self.logs_messages:
            embed = await self.cog.get_embed_action(
                self, author=self.opened_by, action=get_text(lang, "ticket_opened"), reason=reason
            )
            await self.channel.send(embed=embed)
            if logschannel is not None:
                embed = await self.cog.get_embed_important(
                    self,
                    True,
                    author=self.opened_by,
                    title=get_text(lang, "ticket_opened"),
                    description=get_text(lang, "ticket_opened_by", opened_by=self.opened_by),
                    reason=reason,
                )
                await logschannel.send(
                    get_text(lang, "report_close", id=self.id),
                    embed=embed,
                )
        if self.first_message is not None:
            view = await self.cog.get_ticket_buttons(self.guild, include_close=True, include_open=False, claim_disabled=False)
            try:
                self.first_message = await self.channel.fetch_message(int(self.first_message.id))
                await self.first_message.edit(view=view)
            except discord.HTTPException:
                pass
        if (
            config["ticket_role"] is not None
            and self.owner is not None
            and isinstance(self.owner, discord.Member)
        ):
            try:
                await self.owner.add_roles(config["ticket_role"], reason=_reason)
            except discord.HTTPException:
                pass
        await self.save()
        return self

    async def close(
        self, author: typing.Optional[discord.Member] = None, reason: typing.Optional[str] = None
    ) -> typing.Any:
        config = await self.cog.get_config(self.guild, self.profile)
        lang = await self.cog.get_lang(self.guild)
        _reason = await self.cog.get_audit_reason(
            guild=self.guild,
            profile=self.profile,
            author=author,
            reason=get_text(lang, "closing_ticket", id=self.id),
        )
        logschannel = config["logschannel"]
        emoji_open = config["emoji_open"]
        emoji_close = config["emoji_close"]
        self.status = "close"
        self.closed_by = author
        self.closed_at = datetime.datetime.now()
        new_name = f"{self.channel.name}"
        new_name = new_name.replace(f"{emoji_open}-", "", 1)
        new_name = f"{emoji_close}-{new_name}"
        if self.logs_messages:
            embed = await self.cog.get_embed_action(
                self, author=self.closed_by, action=get_text(lang, "ticket_closed"), reason=reason
            )
            await self.channel.send(embed=embed)
            if logschannel is not None:
                embed = await self.cog.get_embed_important(
                    self,
                    True,
                    author=self.closed_by,
                    title=get_text(lang, "ticket_closed"),
                    description=get_text(lang, "ticket_closed_by", closed_by=self.closed_by),
                    reason=reason,
                )
                await logschannel.send(
                    get_text(lang, "report_close", id=self.id),
                    embed=embed,
                )
        if self.first_message is not None:
            view = await self.cog.get_ticket_buttons(self.guild, include_close=False, include_open=True, claim_disabled=True)
            try:
                self.first_message = await self.channel.fetch_message(int(self.first_message.id))
                await self.first_message.edit(view=view)
            except discord.HTTPException:
                pass
        if isinstance(self.channel, discord.TextChannel):
            allowed_members = []
            if self.claim is not None:
                allowed_members.append(self.claim)
            if config["admin_roles"]:
                for role in config["admin_roles"]:
                    allowed_members.extend(role.members)
            if config["support_roles"]:
                for role in config["support_roles"]:
                    allowed_members.extend(role.members)
            members = filter(
                lambda member: member not in allowed_members, [self.owner] + self.members
            )
            overwrites = self.channel.overwrites
            for member in members:
                if member in overwrites:
                    overwrites[member].send_messages = False
            await self.channel.edit(
                name=new_name,
                category=config["category_close"],
                overwrites=overwrites,
                reason=_reason,
            )
        else:
            await self.channel.edit(name=new_name, archived=True, locked=True, reason=_reason)
        if (
            config["ticket_role"] is not None
            and self.owner is not None
            and isinstance(self.owner, discord.Member)
        ):
            try:
                await self.owner.remove_roles(config["ticket_role"], reason=_reason)
            except discord.HTTPException:
                pass
        await self.save()
        return self

    async def lock(
        self, author: typing.Optional[discord.Member] = None, reason: typing.Optional[str] = None
    ) -> typing.Any:
        lang = await self.cog.get_lang(self.guild)
        if isinstance(self.channel, discord.TextChannel):
            raise commands.UserFeedbackCheckFailure(get_text(lang, "cannot_execute_text"))
        config = await self.cog.get_config(self.guild, self.profile)
        _reason = await self.cog.get_audit_reason(
            guild=self.guild,
            profile=self.profile,
            author=author,
            reason=get_text(lang, "locking_ticket", id=self.id),
        )
        logschannel = config["logschannel"]
        self.locked_by = author
        self.locked_at = datetime.datetime.now()
        if self.logs_messages:
            embed = await self.cog.get_embed_action(
                self, author=self.locked_by, action=get_text(lang, "ticket_locked"), reason=reason
            )
            await self.channel.send(embed=embed)
            if logschannel is not None:
                embed = await self.cog.get_embed_important(
                    self,
                    True,
                    author=self.locked_by,
                    title=get_text(lang, "ticket_locked"),
                    description=get_text(lang, "ticket_locked_by", locked_by=self.locked_by),
                    reason=reason,
                )
                await logschannel.send(
                    get_text(lang, "report_lock", id=self.id),
                    embed=embed,
                )
        await self.channel.edit(locked=True, reason=_reason)
        await self.save()
        return self

    async def unlock(
        self, author: typing.Optional[discord.Member] = None, reason: typing.Optional[str] = None
    ) -> typing.Any:
        lang = await self.cog.get_lang(self.guild)
        if isinstance(self.channel, discord.TextChannel):
            raise commands.UserFeedbackCheckFailure(get_text(lang, "cannot_execute_text"))
        config = await self.cog.get_config(self.guild, self.profile)
        _reason = await self.cog.get_audit_reason(
            guild=self.guild,
            profile=self.profile,
            author=author,
            reason=get_text(lang, "unlocking_ticket", id=self.id),
        )
        logschannel = config["logschannel"]
        self.unlocked_by = author
        self.unlocked_at = datetime.datetime.now()
        if self.logs_messages:
            embed = await self.cog.get_embed_action(
                self, author=self.unlocked_by, action=get_text(lang, "ticket_unlocked")
            )
            await self.channel.send(embed=embed)
            if logschannel is not None:
                embed = await self.cog.get_embed_important(
                    self,
                    True,
                    author=self.unlocked_by,
                    title=get_text(lang, "ticket_unlocked"),
                    description=get_text(lang, "ticket_unlocked_by", unlocked_by=self.unlocked_by),
                    reason=reason,
                )
                await logschannel.send(
                    get_text(lang, "report_unlock", id=self.id),
                    embed=embed,
                )
        await self.channel.edit(locked=False, reason=_reason)
        await self.save()
        return self

    async def rename(
        self,
        new_name: str,
        author: typing.Optional[discord.Member] = None,
        reason: typing.Optional[str] = None,
    ) -> typing.Any:
        lang = await self.cog.get_lang(self.guild)
        _reason = await self.cog.get_audit_reason(
            guild=self.guild,
            profile=self.profile,
            author=author,
            reason=get_text(lang, "renaming_ticket", id=self.id, old_name=self.channel.name, new_name=new_name),
        )
        await self.channel.edit(name=new_name, reason=_reason)
        if author is not None:
            self.renamed_by = author
            self.renamed_at = datetime.datetime.now()
            if self.logs_messages:
                embed = await self.cog.get_embed_action(
                    self, author=self.renamed_by, action=get_text(lang, "ticket_renamed"), reason=reason
                )
                await self.channel.send(embed=embed)
            await self.save()
        return self

    async def delete(
        self, author: typing.Optional[discord.Member] = None, reason: typing.Optional[str] = None
    ) -> typing.Any:
        config = await self.cog.get_config(self.guild, self.profile)
        logschannel = config["logschannel"]
        lang = await self.cog.get_lang(self.guild)
        self.deleted_by = author
        self.deleted_at = datetime.datetime.now()
        if self.logs_messages and logschannel is not None:
            embed = await self.cog.get_embed_important(
                self,
                True,
                author=self.deleted_by,
                title=get_text(lang, "ticket_deleted"),
                description=get_text(lang, "ticket_deleted_by", deleted_by=self.deleted_by),
                reason=reason,
            )
            try:
                transcript = await chat_exporter.export(
                    channel=self.channel,
                    limit=None,
                    tz_info="UTC",
                    guild=self.guild,
                    bot=self.bot,
                )
            except AttributeError:
                transcript = None
            if transcript is not None:
                file = discord.File(
                    io.BytesIO(transcript.encode()),
                    filename=f"transcript-ticket-{self.id}.html",
                )
            else:
                file = None
            message = await logschannel.send(
                get_text(lang, "report_deletion", id=self.id),
                embed=embed,
                file=file,
            )
            embed = discord.Embed(
                title="Transcript Link",
                description=(
                    f"[Click here to view the transcript.](https://mahto.id/chat-exporter?url={message.attachments[0].url})"
                ),
                color=discord.Color.red(),
            )
            await logschannel.send(embed=embed)
        if isinstance(self.channel, discord.TextChannel):
            _reason = await self.cog.get_audit_reason(
                guild=self.guild,
                profile=self.profile,
                author=author,
                reason=get_text(lang, "deleting_ticket", id=self.id),
            )
            await self.channel.delete(reason=_reason)
        else:
            await self.channel.delete()
        data = await self.cog.config.guild(self.guild).tickets.all()
        try:
            del data[str(self.channel.id)]
        except KeyError:
            pass
        await self.cog.config.guild(self.guild).tickets.set(data)
        return self

    async def claim_ticket(
        self,
        member: discord.Member,
        author: typing.Optional[discord.Member] = None,
        reason: typing.Optional[str] = None,
    ) -> typing.Any:
        lang = await self.cog.get_lang(self.guild)
        if self.status != "open":
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "ticket_cannot_claim_closed")
            )
        config = await self.cog.get_config(self.guild, self.profile)
        if member.bot:
            raise commands.UserFeedbackCheckFailure(get_text(lang, "bot_cannot_claim"))
        self.claim = member
        # topic = _(
        #     "🎟️ Ticket ID: {ticket.id}\n"
        #     "🔥 Channel ID: {ticket.channel.id}\n"
        #     "🕵️ Ticket created by: @{ticket.created_by.display_name} ({ticket.created_by.id})\n"
        #     "☢️ Ticket reason: {short_reason}\n"
        #     "👥 Ticket claimed by: @{ticket.claim.display_name} (@{ticket.claim.id})."
        # ).format(ticket=self, short_reason=f"{self.reason[:700]}...".replace("\n", " ") if len(self.reason) > 700 else self.reason.replace("\n", " "))
        if isinstance(self.channel, discord.TextChannel):
            _reason = await self.cog.get_audit_reason(
                guild=self.guild,
                profile=self.profile,
                author=author,
                reason=get_text(lang, "claiming_ticket", id=self.id),
            )
            overwrites = self.channel.overwrites
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                read_message_history=True,
                send_messages=True,
                attach_files=True,
                use_application_commands=True,
            )
            if config["support_roles"]:
                for role in config["support_roles"]:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True,
                        read_messages=True,
                        send_messages=False,
                        read_message_history=True,
                        attach_files=False,
                        use_application_commands=False,
                    )
            await self.channel.edit(overwrites=overwrites, reason=_reason)  # topic=topic,
        if self.first_message is not None:
            view = await self.cog.get_ticket_buttons(self.guild, include_close=True, include_open=False, claim_disabled=True)
            try:
                self.first_message = await self.channel.fetch_message(int(self.first_message.id))
                await self.first_message.edit(view=view)
            except discord.HTTPException:
                pass
        if self.logs_messages:
            embed = await self.cog.get_embed_action(
                self, author=author, action=get_text(lang, "ticket_claimed"), reason=reason
            )
            await self.channel.send(embed=embed)
        await self.save()
        return self

    async def unclaim_ticket(
        self,
        member: discord.Member,
        author: typing.Optional[discord.Member] = None,
        reason: typing.Optional[str] = None,
    ) -> typing.Any:
        lang = await self.cog.get_lang(self.guild)
        if self.status != "open":
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "ticket_cannot_unclaim_closed")
            )
        config = await self.cog.get_config(self.guild, self.profile)
        self.claim = None
        # topic = _(
        #     "🎟️ Ticket ID: {ticket.id}\n"
        #     "🔥 Channel ID: {ticket.channel.id}\n"
        #     "🕵️ Ticket created by: @{ticket.created_by.display_name} ({ticket.created_by.id})\n"
        #     "☢️ Ticket reason: {short_reason}\n"
        #     "👥 Ticket claimed by: Nobody."
        # ).format(ticket=self, short_reason=f"{self.reason[:700]}...".replace("\n", " ") if len(self.reason) > 700 else self.reason.replace("\n", " "))
        if isinstance(self.channel, discord.TextChannel):
            _reason = await self.cog.get_audit_reason(
                guild=self.guild,
                profile=self.profile,
                author=author,
                reason=get_text(lang, "unclaiming_ticket", id=self.id),
            )
            if config["support_roles"]:
                overwrites = self.channel.overwrites
                for role in config["support_roles"]:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True,
                        read_messages=True,
                        read_message_history=True,
                        send_messages=True,
                        attach_files=True,
                        use_application_commands=True,
                    )
                await self.channel.edit(overwrites=overwrites, reason=_reason)
            await self.channel.set_permissions(member, overwrite=None, reason=_reason)
            # await self.channel.edit(topic=topic)
        if self.first_message is not None:
            view = await self.cog.get_ticket_buttons(self.guild, include_close=True, include_open=False, claim_disabled=True)
            try:
                self.first_message = await self.channel.fetch_message(int(self.first_message.id))
                await self.first_message.edit(view=view)
            except discord.HTTPException:
                pass
        if self.logs_messages:
            embed = await self.cog.get_embed_action(
                self, author=author, action=get_text(lang, "ticket_unclaimed"), reason=reason
            )
            await self.channel.send(embed=embed)
        await self.save()
        return self

    async def change_owner(
        self,
        member: discord.Member,
        author: typing.Optional[discord.Member] = None,
        reason: typing.Optional[str] = None,
    ) -> typing.Any:
        lang = await self.cog.get_lang(self.guild)
        if not isinstance(self.channel, discord.TextChannel):
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "cannot_execute_thread")
            )
        config = await self.cog.get_config(self.guild, self.profile)
        _reason = await self.cog.get_audit_reason(
            guild=self.guild,
            profile=self.profile,
            author=author,
            reason=get_text(lang, "changing_owner", id=self.id),
        )
        if member.bot:
            raise commands.UserFeedbackCheckFailure(
                get_text(lang, "cannot_transfer_bot")
            )
        if not isinstance(self.owner, int):
            if config["ticket_role"] is not None:
                try:
                    self.owner.remove_roles(config["ticket_role"], reason=_reason)
                except discord.HTTPException:
                    pass
            self.remove_member(self.owner, author=None)
            self.add_member(self.owner, author=None)
        self.owner = member
        self.remove_member(self.owner, author=None)
        overwrites = self.channel.overwrites
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            read_messages=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            use_application_commands=True,
        )
        await self.channel.edit(overwrites=overwrites, reason=_reason)
        if config["ticket_role"] is not None:
            try:
                self.owner.add_roles(config["ticket_role"], reason=_reason)
            except discord.HTTPException:
                pass
        if self.logs_messages:
            embed = await self.cog.get_embed_action(
                self, author=author, action=get_text(lang, "owner_modified"), reason=reason
            )
            await self.channel.send(embed=embed)
        await self.save()
        return self

    async def add_member(
        self, members: typing.List[discord.Member], author: typing.Optional[discord.Member] = None
    ) -> typing.Any:
        config = await self.cog.get_config(self.guild, self.profile)
        lang = await self.cog.get_lang(self.guild)
        admin_roles_members = []
        if config["admin_roles"]:
            for role in config["admin_roles"]:
                admin_roles_members.extend(role.members)
        if isinstance(self.channel, discord.TextChannel):
            _reason = await self.cog.get_audit_reason(
                guild=self.guild,
                profile=self.profile,
                author=author,
                reason=get_text(lang, "adding_member", id=self.id),
            )
            overwrites = self.channel.overwrites
            for member in members:
                if author is not None:
                    if member.bot:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "cannot_add_bot", member=member)
                        )
                    if not isinstance(self.owner, int) and member == self.owner:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "already_owner", member=member)
                        )
                    if member in admin_roles_members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "is_admin", member=member)
                        )
                    if member in self.members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "already_access", member=member)
                        )
                if member not in self.members:
                    self.members.append(member)
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    read_message_history=True,
                    send_messages=True,
                    attach_files=True,
                    use_application_commands=True,
                )
            await self.channel.edit(overwrites=overwrites, reason=_reason)
        else:
            adding_error = False
            for member in members:
                if author is not None:
                    if member.bot:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "cannot_add_bot", member=member)
                        )
                    if not isinstance(self.owner, int) and member == self.owner:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "already_owner", member=member)
                        )
                    if member in admin_roles_members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "is_admin", member=member)
                        )
                    if member in self.members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "already_access", member=member)
                        )
                    try:
                        await self.channel.add_user(member)
                    except (
                        discord.HTTPException
                    ):  # The bot haven't the permission `manage_messages` in the parent text channel.
                        adding_error = True
                if member not in self.members:
                    self.members.append(member)
            if adding_error:
                await self.channel.send(
                    get_text(lang, "thread_add_error")
                )
        await self.save()
        return self

    async def remove_member(
        self, members: typing.List[discord.Member], author: typing.Optional[discord.Member] = None
    ) -> typing.Any:
        config = await self.cog.get_config(self.guild, self.profile)
        lang = await self.cog.get_lang(self.guild)
        admin_roles_members = []
        if config["admin_roles"]:
            for role in config["admin_roles"]:
                admin_roles_members.extend(role.members)
        support_roles_members = []
        if config["support_roles"]:
            for role in config["support_roles"]:
                support_roles_members.extend(role.members)
        if isinstance(self.channel, discord.TextChannel):
            _reason = await self.cog.get_audit_reason(
                guild=self.guild,
                profile=self.profile,
                author=author,
                reason=get_text(lang, "removing_member", id=self.id),
            )
            for member in members:
                if author is not None:
                    if member.bot:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "cannot_remove_bot", member=member)
                        )
                    if not isinstance(self.owner, int) and member == self.owner:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "cannot_remove_owner", member=member)
                        )
                    if member in admin_roles_members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "is_admin_remove", member=member)
                        )
                    if member not in self.members and member not in support_roles_members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "not_authorized", member=member)
                        )
                    await self.channel.set_permissions(member, overwrite=None, reason=_reason)
                if member in self.members:
                    self.members.remove(member)
        else:
            for member in members:
                if author is not None:
                    if member.bot:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "cannot_remove_bot", member=member)
                        )
                    if not isinstance(self.owner, int) and member == self.owner:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "cannot_remove_owner", member=member)
                        )
                    if member in admin_roles_members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "is_admin_remove", member=member)
                        )
                    if member not in self.members and member not in support_roles_members:
                        raise commands.UserFeedbackCheckFailure(
                            get_text(lang, "not_authorized", member=member)
                        )
                    await self.channel.remove_user(member)
                if member in self.members:
                    self.members.remove(member)
        await self.save()
        return self
