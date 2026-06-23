from .embedfixer import EmbedFixer


async def setup(bot):
    await bot.add_cog(EmbedFixer(bot))
