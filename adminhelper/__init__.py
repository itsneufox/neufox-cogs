from .adminhelper import AdminHelper

async def setup(bot):
    await bot.add_cog(AdminHelper(bot))
