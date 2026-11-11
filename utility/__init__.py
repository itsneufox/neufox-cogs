from .utility import Utility


__red_end_user_data_statement__ = (
    "This cog stores reminders (creator and target user IDs, server and channel IDs, timestamps, and reminder "
    "text) until they are delivered, cancelled, or deleted, plus per-server settings. Search terms are sent to "
    "Urban Dictionary and the open.mp documentation search only when a lookup command is used."
)


async def setup(bot):
    await bot.add_cog(Utility(bot))
