import re

import discord
from redbot.core import commands, Config

YT_PATTERN = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^&\s]*&)*v=|youtu\.be/)[\w-]+'
)

LINK_EMOJI = "\U0001f517"  # 🔗
CHECK_EMOJI = "✅"     # ✅


class MusicBoard(commands.Cog):
    """Post YouTube links to a dedicated music channel via 🔗 reaction."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=728463910)
        self.config.register_guild(
            music_channel_id=None,
            posted_message_ids=[],
        )
        self._processing: set[int] = set()

    @commands.group(name="musicboard", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def musicboard(self, ctx: commands.Context):
        """Manage MusicBoard settings."""
        await ctx.invoke(self.musicboard_show)

    @musicboard.command(name="channel")
    @commands.admin_or_permissions(manage_guild=True)
    async def musicboard_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where music links get posted."""
        await self.config.guild(ctx.guild).music_channel_id.set(channel.id)
        embed = discord.Embed(
            title="MusicBoard",
            description=f"Music channel set to {channel.mention}.\nReact with 🔗 on any YouTube link to nominate it.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)

    @musicboard.command(name="show")
    @commands.admin_or_permissions(manage_guild=True)
    async def musicboard_show(self, ctx: commands.Context):
        """Show current MusicBoard configuration."""
        data = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data["music_channel_id"]) if data["music_channel_id"] else None
        posted_count = len(data["posted_message_ids"])

        embed = discord.Embed(title="MusicBoard Configuration", color=discord.Color.red())
        embed.add_field(
            name="Music Channel",
            value=channel.mention if channel else "*Not set*",
            inline=True,
        )
        embed.add_field(name="Tracks Posted", value=str(posted_count), inline=True)
        embed.set_footer(text="React with 🔗 on any YouTube link to nominate it.")
        await ctx.send(embed=embed)

    @musicboard.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    async def musicboard_clear(self, ctx: commands.Context):
        """Clear the list of already-posted message IDs (resets dedup)."""
        await self.config.guild(ctx.guild).posted_message_ids.set([])
        embed = discord.Embed(
            title="MusicBoard",
            description="Dedup list cleared. Previously posted links can be nominated again.",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != LINK_EMOJI:
            return
        if payload.guild_id is None:
            return
        if payload.user_id == self.bot.user.id:
            return

        if payload.message_id in self._processing:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        music_channel_id = await self.config.guild(guild).music_channel_id()
        if not music_channel_id:
            return

        music_channel = guild.get_channel(music_channel_id)
        if not music_channel or not isinstance(music_channel, discord.TextChannel):
            return

        posted = await self.config.guild(guild).posted_message_ids()
        if payload.message_id in posted:
            return

        self._processing.add(payload.message_id)
        try:
            channel = guild.get_channel(payload.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            try:
                message = await channel.fetch_message(payload.message_id)
            except (discord.NotFound, discord.Forbidden):
                return

            url = self._extract_youtube_url(message.content)
            if not url:
                return

            nominator = guild.get_member(payload.user_id)
            if not nominator:
                try:
                    nominator = await guild.fetch_member(payload.user_id)
                except (discord.NotFound, discord.HTTPException):
                    return

            embed = self._build_embed(nominator, message)
            await music_channel.send(content=url, embed=embed)

            async with self.config.guild(guild).posted_message_ids() as id_list:
                id_list.append(payload.message_id)

            try:
                await message.add_reaction(CHECK_EMOJI)
            except (discord.Forbidden, discord.HTTPException):
                pass
        finally:
            self._processing.discard(payload.message_id)

    def _extract_youtube_url(self, content: str) -> str | None:
        match = YT_PATTERN.search(content)
        return match.group(0) if match else None

    def _build_embed(self, nominator: discord.Member, source_message: discord.Message) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.red())
        embed.set_author(
            name=f"Nominated by {nominator.display_name}",
            icon_url=nominator.display_avatar.url,
        )
        embed.add_field(
            name="",
            value=f"[Jump to original message]({source_message.jump_url})",
            inline=False,
        )
        embed.timestamp = discord.utils.utcnow()
        return embed

