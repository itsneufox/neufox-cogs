from .messagearchive import MessageArchive


async def setup(bot):
    await bot.add_cog(MessageArchive(bot))
