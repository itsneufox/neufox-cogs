from .sixseven import SixSeven


async def setup(bot):
    await bot.add_cog(SixSeven(bot))
