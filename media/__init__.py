from .media import Media

__red_end_user_data_statement__ = (
    "This cog stores per server: radio stream, status API and channel settings and the radio status message ID; "
    "MusicBoard settings and IDs of nominated messages; EmbedFixer settings (sites, services, ignored channels and roles); "
    "and per member, whether that member opted out of EmbedFixer. It does not store message content or voice audio. "
    "Members' EmbedFixer opt-out is deleted on data deletion requests."
)


async def setup(bot):
    await bot.add_cog(Media(bot))
