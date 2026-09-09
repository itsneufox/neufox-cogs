from .converter import Converter


async def setup(bot):
    await bot.add_cog(Converter())
