from .musicboard import MusicBoard


async def setup(bot):
    await bot.add_cog(MusicBoard(bot))
