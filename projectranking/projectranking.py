from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from redbot.core import Config, commands


UPVOTE = "\U0001f44d"
DOWNVOTE = "\U0001f44e"
VOTE_EMOJIS = {UPVOTE, DOWNVOTE}
SCHEDULER_INTERVAL = 15 * 60

log = logging.getLogger("red.neufox.projectranking")


class FavoriteTagMissing(Exception):
    """Raised when the configured forum tag no longer exists."""


def _week_key(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    iso = value.astimezone(timezone.utc).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _week_order(value: str) -> tuple[int, int]:
    year, week = value.split("-W", maxsplit=1)
    return int(year), int(week)


def _week_range(value: str) -> str:
    year, week = _week_order(value)
    monday = datetime.fromisocalendar(year, week, 1).date()
    sunday = datetime.fromisocalendar(year, week, 7).date()
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {sunday:%b %Y}"
    return f"{monday.day} {monday:%b}–{sunday.day} {sunday:%b %Y}"


def _clean_emoji(value: str) -> str:
    return value.replace("\ufe0f", "")


class ProjectRanking(commands.Cog):
    """Weekly voting and promotion for projects posted in a forum channel."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=739201846)
        self.config.register_guild(
            forum_channel_id=None,
            best_creations_channel_id=None,
            favorite_tag_id=None,
            eligible_from_week=None,
            paused=False,
            posts={},
            weekly_results={},
        )
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._scheduler_task = self.bot.loop.create_task(self._weekly_scheduler())

    def cog_unload(self):
        self._scheduler_task.cancel()

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = self._guild_locks[guild_id] = asyncio.Lock()
        return lock

    @commands.group(name="projectrank", aliases=["projectranking"], invoke_without_command=True)
    @commands.guild_only()
    async def projectrank(self, ctx: commands.Context):
        """Show the project voting commands."""
        await ctx.send_help()

    @projectrank.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def projectrank_setup(
        self,
        ctx: commands.Context,
        forum_id: int,
        best_creations_id: int,
        *,
        tag_name: str = "Community Favorite",
    ):
        """Set the forum and promotion channel by ID, plus the favorite tag."""
        try:
            forum = ctx.guild.get_channel(forum_id) or await ctx.guild.fetch_channel(forum_id)
            best_creations = ctx.guild.get_channel(best_creations_id) or await ctx.guild.fetch_channel(
                best_creations_id
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await ctx.send("I couldn't find one of those channel IDs in this server.")
            return
        if not isinstance(forum, discord.ForumChannel):
            await ctx.send(f"Channel ID `{forum_id}` is not a forum channel.")
            return
        if not isinstance(best_creations, discord.TextChannel):
            await ctx.send(f"Channel ID `{best_creations_id}` is not a text channel.")
            return
        if forum.guild.id != ctx.guild.id or best_creations.guild.id != ctx.guild.id:
            await ctx.send("Both channel IDs must belong to this server.")
            return

        tag = None
        if tag_name.isdecimal():
            tag_id = int(tag_name)
            tag = next((item for item in forum.available_tags if item.id == tag_id), None)
        else:
            tag = next(
                (item for item in forum.available_tags if item.name.casefold() == tag_name.casefold()),
                None,
            )
        if tag is None:
            available = ", ".join(f"`{item.name}`" for item in forum.available_tags) or "none"
            await ctx.send(
                f"I couldn't find the tag `{tag_name}` in {forum.mention}. "
                f"Create that forum tag first, then run setup again. Available tags: {available}."
            )
            return

        guild_config = self.config.guild(ctx.guild)
        data = await guild_config.all()
        same_forum = data["forum_channel_id"] == forum.id
        await guild_config.forum_channel_id.set(forum.id)
        await guild_config.best_creations_channel_id.set(best_creations.id)
        await guild_config.favorite_tag_id.set(tag.id)
        if not same_forum or not data["eligible_from_week"]:
            await guild_config.eligible_from_week.set(_week_key())

        scanned, reacted, failed = await self._sync_current_week(ctx.guild, forum)
        missing = self._missing_permissions(ctx.guild, forum, best_creations)
        note = f"\nMissing bot permissions: {', '.join(missing)}." if missing else ""
        await ctx.send(
            f"Project voting is set up for {forum.mention}. Weekly winners will be posted in "
            f"{best_creations.mention} and receive `{tag.name}`. I checked {scanned} current-week "
            f"posts and added or confirmed the voting reactions on {reacted}; {failed} could not be updated."
            f"\nWeeks run Monday to Sunday in UTC. The winner is the post with the most counted 👍 votes."
            f"{note}"
        )

    @projectrank.command(name="sync")
    @commands.admin_or_permissions(manage_guild=True)
    async def projectrank_sync(self, ctx: commands.Context):
        """Add the two voting reactions to this week's existing forum posts."""
        data = await self.config.guild(ctx.guild).all()
        forum = ctx.guild.get_channel(data["forum_channel_id"]) if data["forum_channel_id"] else None
        if not isinstance(forum, discord.ForumChannel):
            await ctx.send("Set a project forum first with `[p]projectrank setup`.")
            return
        scanned, reacted, failed = await self._sync_current_week(ctx.guild, forum)
        await ctx.send(
            f"Checked {scanned} current-week posts, updated reactions on {reacted}, "
            f"and couldn't update {failed}."
        )

    @projectrank.command(name="pause")
    @commands.admin_or_permissions(manage_guild=True)
    async def projectrank_pause(self, ctx: commands.Context):
        """Pause weekly promotions while leaving voting active."""
        async with self._lock_for(ctx.guild.id):
            await self.config.guild(ctx.guild).paused.set(True)
        await ctx.send("Weekly Best Creations promotions are paused. Voting and vote tracking remain active.")

    @projectrank.command(name="resume")
    @commands.admin_or_permissions(manage_guild=True)
    async def projectrank_resume(self, ctx: commands.Context):
        """Resume weekly promotions; weeks skipped while paused stay skipped."""
        lock = self._lock_for(ctx.guild.id)
        async with lock:
            guild_config = self.config.guild(ctx.guild)
            data = await guild_config.all()
            if not data["paused"]:
                await ctx.send("Weekly Best Creations promotions are already active.")
                return
            # Close completed weeks as skipped before unpausing so a restart or
            # delayed scheduler cannot publish a backlog of old winners.
            await self._mark_closed_weeks_skipped(ctx.guild, data)
            await guild_config.paused.set(False)
        await ctx.send("Weekly Best Creations promotions resumed. The current week's winner will be promoted at its end.")

    @projectrank.command(name="status")
    @commands.guild_only()
    async def projectrank_status(self, ctx: commands.Context):
        """Show the project voting configuration and current week's leading posts."""
        data = await self.config.guild(ctx.guild).all()
        forum = ctx.guild.get_channel(data["forum_channel_id"]) if data["forum_channel_id"] else None
        destination = (
            ctx.guild.get_channel(data["best_creations_channel_id"])
            if data["best_creations_channel_id"]
            else None
        )
        posts = self._current_week_posts(data)
        leader = self._sort_posts(posts)[:1]
        leader_text = "No counted votes yet."
        if leader and leader[0]["upvotes"] > 0:
            winner = leader[0]
            leader_text = (
                f"[{discord.utils.escape_markdown(winner['title'])}]({winner['jump_url']}) — "
                f"👍 {winner['upvotes']} · 👎 {winner['downvotes']}"
            )
        tag = (
            next((item for item in forum.available_tags if item.id == data["favorite_tag_id"]), None)
            if isinstance(forum, discord.ForumChannel)
            else None
        )
        await ctx.send(
            f"Forum: {forum.mention if forum else 'not set'} `ID: {data['forum_channel_id'] or '—'}`\n"
            f"Best Creations: {destination.mention if destination else 'not set'} `ID: {data['best_creations_channel_id'] or '—'}`\n"
            f"Favorite tag: {tag.name if tag else 'not available'} `ID: {data['favorite_tag_id'] or '—'}`\n"
            f"Weekly promotions: {'paused' if data['paused'] else 'active'}\n"
            f"Current week ({_week_key()}): {len(posts)} tracked project(s)\n"
            f"Leader: {leader_text}"
        )

    @projectrank.command(name="ranking", aliases=["rank", "top"])
    @commands.guild_only()
    async def projectrank_ranking(self, ctx: commands.Context):
        """Show this week's projects ranked by counted upvotes."""
        data = await self.config.guild(ctx.guild).all()
        posts = self._sort_posts(self._current_week_posts(data))[:10]
        if not posts:
            await ctx.send("There are no tracked projects for this week yet.")
            return
        lines = []
        for index, post in enumerate(posts, start=1):
            title = discord.utils.escape_markdown(post["title"])
            score = post["upvotes"] - post["downvotes"]
            lines.append(
                f"**{index}.** [{title}]({post['jump_url']}) — "
                f"👍 {post['upvotes']} · 👎 {post['downvotes']} · net {score:+d}"
            )
        embed = discord.Embed(
            title=f"Project ranking · {_week_range(_week_key())}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Project authors and bot accounts do not count as voters. Only 👍 decides the weekly winner.")
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if thread.guild is None:
            return
        data = await self.config.guild(thread.guild).all()
        if data["forum_channel_id"] != thread.parent_id:
            return
        try:
            await self._register_and_react(thread)
        except discord.HTTPException:
            log.exception("Unable to prepare new project thread %s in guild %s", thread.id, thread.guild.id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.message_id != payload.channel_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or self.bot.user is None or payload.user_id == self.bot.user.id:
            return

        guild_config = self.config.guild(guild)
        data = await guild_config.all()
        if not data["forum_channel_id"]:
            return

        emoji = _clean_emoji(str(payload.emoji))
        thread = await self._get_thread(guild, payload.channel_id)
        if thread is None or thread.parent_id != data["forum_channel_id"]:
            return

        if emoji not in VOTE_EMOJIS:
            await self._remove_unapproved_reaction(payload, guild, thread)
            return

        lock = self._lock_for(guild.id)
        async with lock:
            try:
                current = await guild_config.all()
                post = current["posts"].get(str(thread.id))
                if post is None:
                    post = await self._register_and_react(thread, current, lock_already_held=True)
                if post is None or not post["eligible"] or post["week"] != _week_key():
                    return
                message = await thread.fetch_message(thread.id)
                counts = await self._count_votes(message, post["author_id"])
                post.update(counts)
                current["posts"][str(thread.id)] = post
                await guild_config.posts.set(current["posts"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.exception("Unable to update votes for project thread %s", thread.id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.message_id != payload.channel_id:
            return
        emoji = _clean_emoji(str(payload.emoji))
        if emoji not in VOTE_EMOJIS:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        guild_config = self.config.guild(guild)
        data = await guild_config.all()
        if data["forum_channel_id"] is None:
            return
        thread = await self._get_thread(guild, payload.channel_id)
        if thread is None or thread.parent_id != data["forum_channel_id"]:
            return

        lock = self._lock_for(guild.id)
        async with lock:
            try:
                current = await guild_config.all()
                post = current["posts"].get(str(thread.id))
                if post is None or not post["eligible"] or post["week"] != _week_key():
                    return
                message = await thread.fetch_message(thread.id)
                counts = await self._count_votes(message, post["author_id"])
                post.update(counts)
                current["posts"][str(thread.id)] = post
                await guild_config.posts.set(current["posts"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.exception("Unable to refresh votes for project thread %s", thread.id)

    async def _weekly_scheduler(self):
        await self.bot.wait_until_ready()
        # Recover reactions and vote counts for this week's posts after a restart.
        for guild in list(self.bot.guilds):
            try:
                data = await self.config.guild(guild).all()
                forum = guild.get_channel(data["forum_channel_id"]) if data["forum_channel_id"] else None
                if isinstance(forum, discord.ForumChannel):
                    await self._sync_current_week(guild, forum)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Unable to sync current-week project posts in guild %s", guild.id)
        while True:
            for guild in list(self.bot.guilds):
                try:
                    await self._finalize_closed_weeks(guild)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Weekly project promotion failed for guild %s", guild.id)
            await asyncio.sleep(SCHEDULER_INTERVAL)

    async def _finalize_closed_weeks(self, guild: discord.Guild):
        lock = self._lock_for(guild.id)
        async with lock:
            guild_config = self.config.guild(guild)
            data = await guild_config.all()
            if not data["forum_channel_id"]:
                return
            current_week = _week_key()
            closed_weeks = sorted(
                {
                    post["week"]
                    for post in data["posts"].values()
                    if post.get("eligible")
                    and post.get("forum_id") == data["forum_channel_id"]
                    and _week_order(post["week"]) < _week_order(current_week)
                    and post["week"] not in data["weekly_results"]
                },
                key=_week_order,
            )
            if data["paused"]:
                await self._mark_closed_weeks_skipped(guild, data, closed_weeks)
                return

            forum = guild.get_channel(data["forum_channel_id"])
            if not isinstance(forum, discord.ForumChannel):
                return
            for week in closed_weeks:
                try:
                    await self._finalize_week(guild, forum, week, data)
                    data = await guild_config.all()
                except FavoriteTagMissing:
                    log.error("Favorite tag is missing from forum %s in guild %s", forum.id, guild.id)
                    return
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Unable to promote project winner for %s in guild %s", week, guild.id)
                    return

    async def _finalize_week(
        self,
        guild: discord.Guild,
        forum: discord.ForumChannel,
        week: str,
        data: dict,
    ):
        guild_config = self.config.guild(guild)
        posts = data["posts"]
        candidates = [
            (post_id, post)
            for post_id, post in posts.items()
            if post.get("eligible")
            and post.get("forum_id") == forum.id
            and post.get("week") == week
            and not post.get("deleted")
        ]

        for post_id, post in candidates:
            thread = await self._get_thread(guild, int(post_id))
            if thread is None or thread.parent_id != forum.id:
                post["deleted"] = True
                continue
            try:
                message = await thread.fetch_message(int(post_id))
            except discord.NotFound:
                post["deleted"] = True
                continue
            counts = await self._count_votes(message, post["author_id"])
            post.update(counts)

        await guild_config.posts.set(posts)
        candidates = [(post_id, post) for post_id, post in candidates if not post.get("deleted")]
        candidates.sort(
            key=lambda item: (
                -item[1]["upvotes"],
                item[1]["downvotes"],
                int(item[0]),
            )
        )
        winner = candidates[0] if candidates and candidates[0][1]["upvotes"] > 0 else None
        result = {
            "winner_post_id": winner[0] if winner else None,
            "upvotes": winner[1]["upvotes"] if winner else 0,
            "downvotes": winner[1]["downvotes"] if winner else 0,
            "status": "promoted" if winner else "no_votes",
            "finalized_at": datetime.now(timezone.utc).isoformat(),
        }

        if winner:
            post_id, post = winner
            thread = await self._get_thread(guild, int(post_id))
            if thread is None:
                raise RuntimeError("Project thread disappeared during promotion")
            await self._add_favorite_tag(thread, forum, data["favorite_tag_id"])
            destination = guild.get_channel(data["best_creations_channel_id"])
            if not isinstance(destination, discord.TextChannel):
                raise RuntimeError("Best Creations channel is unavailable")
            starter_message = await thread.fetch_message(int(post_id))
            promo_message = await self._publish_winner(
                destination, forum, thread, starter_message, post, week
            )
            post["promotion_message_id"] = promo_message.id
            post["featured_at"] = result["finalized_at"]
            result["promotion_message_id"] = promo_message.id
            await guild_config.posts.set(posts)

        weekly_results = data["weekly_results"]
        weekly_results[week] = result
        await guild_config.weekly_results.set(weekly_results)

    async def _publish_winner(
        self,
        destination: discord.TextChannel,
        forum: discord.ForumChannel,
        thread: discord.Thread,
        starter_message: discord.Message,
        post: dict,
        week: str,
    ) -> discord.Message:
        safe_title = discord.utils.escape_markdown(post["title"])
        description = (
            f"🏆 **Community Favorite · {_week_range(week)} (UTC)**\n"
            f"{forum.mention} · **Winner:** [{safe_title}]({self._thread_jump_url(thread)})"
        )
        if starter_message.content:
            available = max(0, 4096 - len(description) - 2)
            if len(starter_message.content) > available:
                body = starter_message.content[: max(0, available - 1)].rstrip() + "…"
            else:
                body = starter_message.content
            description += f"\n\n{body}"

        author = starter_message.author
        embed = discord.Embed(
            description=description,
            color=discord.Color.gold(),
            timestamp=starter_message.created_at,
        )
        embed.set_author(name=author.display_name[:256], icon_url=author.display_avatar.url)
        embed.add_field(name="👍 Upvotes", value=str(post["upvotes"]), inline=True)
        embed.add_field(name="👎 Downvotes", value=str(post["downvotes"]), inline=True)
        embed.add_field(name="Net votes", value=f"{post['upvotes'] - post['downvotes']:+d}", inline=True)

        image = next(
            (
                attachment
                for attachment in starter_message.attachments
                if attachment.content_type and attachment.content_type.startswith("image/")
            ),
            None,
        )
        if image is not None:
            embed.set_image(url=image.url)
        elif starter_message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join(attachment.url for attachment in starter_message.attachments[:3]),
                inline=False,
            )
        else:
            for original_embed in starter_message.embeds:
                if original_embed.image and original_embed.image.url:
                    embed.set_image(url=original_embed.image.url)
                    break
        embed.set_footer(text=f"Posted in #{forum.name} · author and bot votes are excluded")
        allowed_mentions = discord.AllowedMentions.none()

        if post.get("promotion_message_id"):
            try:
                message = await destination.fetch_message(post["promotion_message_id"])
                await message.edit(embed=embed, allowed_mentions=allowed_mentions)
                return message
            except discord.NotFound:
                pass
        return await destination.send(embed=embed, allowed_mentions=allowed_mentions)

    async def _add_favorite_tag(
        self,
        thread: discord.Thread,
        forum: discord.ForumChannel,
        tag_id: int | None,
    ):
        tag = next((item for item in forum.available_tags if item.id == tag_id), None)
        if tag is None:
            raise FavoriteTagMissing
        applied = list(thread.applied_tags)
        if all(item.id != tag.id for item in applied):
            await thread.edit(applied_tags=[*applied, tag], reason="Weekly community project favorite")

    async def _sync_current_week(self, guild: discord.Guild, forum: discord.ForumChannel) -> tuple[int, int, int]:
        scanned = reacted = failed = 0
        current_week = _week_key()
        threads_by_id = {thread.id: thread for thread in forum.threads}
        try:
            async for thread in forum.archived_threads(limit=100):
                threads_by_id.setdefault(thread.id, thread)
        except discord.HTTPException:
            log.exception("Unable to enumerate archived project threads in forum %s", forum.id)

        for thread in threads_by_id.values():
            if _week_key(thread.created_at) != current_week:
                continue
            scanned += 1
            try:
                await self._register_and_react(thread)
                reacted += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                failed += 1
                log.exception("Unable to add voting reactions to project thread %s", thread.id)
        return scanned, reacted, failed

    async def _register_and_react(
        self,
        thread: discord.Thread,
        data: dict | None = None,
        *,
        lock_already_held: bool = False,
    ) -> dict | None:
        guild = thread.guild
        if guild is None:
            return None
        lock = self._lock_for(guild.id)
        if not lock_already_held:
            async with lock:
                return await self._register_and_react(thread, data, lock_already_held=True)

        guild_config = self.config.guild(guild)
        data = data or await guild_config.all()
        if data["forum_channel_id"] != thread.parent_id:
            return None
        message = None
        for attempt in range(3):
            try:
                message = await thread.fetch_message(thread.id)
                break
            except discord.NotFound:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
        if message is None:
            return None

        await self._ensure_vote_reactions(message)
        post_id = str(thread.id)
        posts = data["posts"]
        post = posts.get(post_id)
        week = _week_key(thread.created_at)
        if post is None:
            post = {
                "forum_id": thread.parent_id,
                "week": week,
                "eligible": bool(data["eligible_from_week"])
                and _week_order(week) >= _week_order(data["eligible_from_week"]),
                "author_id": message.author.id,
                "title": thread.name,
                "jump_url": self._thread_jump_url(thread),
                "created_at": thread.created_at.isoformat(),
                "upvotes": 0,
                "downvotes": 0,
                "deleted": False,
            }
        else:
            post["title"] = thread.name
            post["jump_url"] = self._thread_jump_url(thread)
            post["created_at"] = thread.created_at.isoformat()
            post["author_id"] = message.author.id
            post["deleted"] = False
        post.update(await self._count_votes(message, post["author_id"]))
        posts[post_id] = post
        await guild_config.posts.set(posts)
        return post

    async def _ensure_vote_reactions(self, message: discord.Message):
        existing = {_clean_emoji(str(reaction.emoji)) for reaction in message.reactions}
        for emoji in (UPVOTE, DOWNVOTE):
            if emoji not in existing:
                await message.add_reaction(emoji)

        # Remove legacy or user-added reactions so each submission only offers
        # the two reactions used by this cog.
        for reaction in list(message.reactions):
            if _clean_emoji(str(reaction.emoji)) in VOTE_EMOJIS:
                continue
            users = [user async for user in reaction.users(limit=None)]
            for user in users:
                try:
                    await message.remove_reaction(reaction.emoji, user)
                except discord.HTTPException:
                    log.exception("Unable to remove an unapproved reaction from message %s", message.id)

    async def _count_votes(self, message: discord.Message, author_id: int) -> dict[str, int]:
        counts = {"upvotes": 0, "downvotes": 0}
        for reaction in message.reactions:
            emoji = _clean_emoji(str(reaction.emoji))
            if emoji not in VOTE_EMOJIS:
                continue
            key = "upvotes" if emoji == UPVOTE else "downvotes"
            async for user in reaction.users(limit=None):
                if user.bot or user.id == author_id:
                    continue
                counts[key] += 1
        return counts

    @staticmethod
    def _thread_jump_url(thread: discord.Thread) -> str:
        return f"https://discord.com/channels/{thread.guild.id}/{thread.id}/{thread.id}"

    async def _remove_unapproved_reaction(
        self,
        payload: discord.RawReactionActionEvent,
        guild: discord.Guild,
        thread: discord.Thread,
    ):
        try:
            message = await thread.fetch_message(thread.id)
            member = payload.member or guild.get_member(payload.user_id)
            user = member or await self.bot.fetch_user(payload.user_id)
            await message.remove_reaction(payload.emoji, user)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.exception("Unable to remove an unapproved reaction from project thread %s", thread.id)

    async def _get_thread(self, guild: discord.Guild, thread_id: int) -> discord.Thread | None:
        thread = guild.get_thread(thread_id)
        if thread is not None:
            return thread
        try:
            channel = await guild.fetch_channel(thread_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        return channel if isinstance(channel, discord.Thread) else None

    def _current_week_posts(self, data: dict) -> list[dict]:
        return [
            post
            for post in data["posts"].values()
            if post.get("eligible")
            and post.get("forum_id") == data["forum_channel_id"]
            and post.get("week") == _week_key()
            and not post.get("deleted")
        ]

    @staticmethod
    def _sort_posts(posts: list[dict]) -> list[dict]:
        return sorted(
            posts,
            key=lambda post: (-post["upvotes"], post["downvotes"], post.get("created_at", "")),
        )

    async def _mark_closed_weeks_skipped(self, guild: discord.Guild, data: dict, weeks: list[str] | None = None):
        if weeks is None:
            current_week = _week_key()
            weeks = sorted(
                {
                    post["week"]
                    for post in data["posts"].values()
                    if post.get("eligible")
                    and post.get("forum_id") == data["forum_channel_id"]
                    and _week_order(post["week"]) < _week_order(current_week)
                    and post["week"] not in data["weekly_results"]
                },
                key=_week_order,
            )
        if not weeks:
            return
        results = data["weekly_results"]
        now = datetime.now(timezone.utc).isoformat()
        for week in weeks:
            results[week] = {"winner_post_id": None, "status": "paused", "finalized_at": now}
        await self.config.guild(guild).weekly_results.set(results)

    @staticmethod
    def _missing_permissions(
        guild: discord.Guild,
        forum: discord.ForumChannel,
        destination: discord.TextChannel,
    ) -> list[str]:
        bot_member = guild.me
        if bot_member is None:
            return []
        forum_permissions = forum.permissions_for(bot_member)
        destination_permissions = destination.permissions_for(bot_member)
        missing = []
        for attribute, label in (
            ("add_reactions", "Add Reactions in the forum"),
            ("read_message_history", "Read Message History in the forum"),
            ("manage_messages", "Manage Messages in the forum (remove other emojis)"),
            ("manage_threads", "Manage Threads in the forum (apply the favorite tag)"),
        ):
            if not getattr(forum_permissions, attribute):
                missing.append(label)
        if not destination_permissions.send_messages:
            missing.append("Send Messages in Best Creations")
        if not destination_permissions.embed_links:
            missing.append("Embed Links in Best Creations")
        return missing
