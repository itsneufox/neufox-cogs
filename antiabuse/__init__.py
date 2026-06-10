from .antiabuse import AntiAbuse


async def setup(bot):
    await bot.add_cog(AntiAbuse(bot))
