from .voicechannels import VoiceChannels


async def setup(bot):
    await bot.add_cog(VoiceChannels(bot))
