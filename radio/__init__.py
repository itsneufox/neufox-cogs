from .radio import Radio


async def setup(bot):
    await bot.add_cog(Radio(bot))
