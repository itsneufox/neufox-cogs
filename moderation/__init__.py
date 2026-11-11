from .moderation import Moderation

__red_end_user_data_statement__ = (
    "This cog stores moderation cases (action, target and moderator IDs and names, reasons, timestamps), "
    "manual warnings, AutoMod strike counters and timestamps per member, and previous usernames, global "
    "display names and server nicknames observed while name tracking is enabled."
)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
