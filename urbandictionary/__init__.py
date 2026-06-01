from .urbandictionary import UrbanDictionary


async def setup(bot):
    await bot.add_cog(UrbanDictionary(bot))
