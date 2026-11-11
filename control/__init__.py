from .control import Control


async def setup(bot):
    await bot.add_cog(Control(bot))
