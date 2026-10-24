from .projectranking import ProjectRanking


async def setup(bot):
    await bot.add_cog(ProjectRanking(bot))
