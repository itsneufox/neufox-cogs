from .activitystats import ActivityStats


async def setup(bot):
    await bot.add_cog(ActivityStats(bot))
