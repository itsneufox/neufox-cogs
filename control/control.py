from __future__ import annotations

import discord
from redbot.core import app_commands, commands

from .help import NO_PINGS as HELP_NO_PINGS, HelpView, collect_help
from .panel import NO_PINGS, ControlPanel


class Control(commands.Cog):
    """One panel for every cog's server settings, and a slash /help."""

    def __init__(self, bot):
        self.bot = bot

    async def red_delete_data_for_user(self, **kwargs):
        return

    @commands.hybrid_command(name="control", aliases=["panel"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def control(self, ctx: commands.Context):
        """Open the control panel for this server's settings."""
        panel = ControlPanel(self.bot, ctx.author)
        await panel.render()
        panel.message = await ctx.send(view=panel, ephemeral=True, allowed_mentions=NO_PINGS)

    @app_commands.command(name="help", description="List the bot's slash commands by category.")
    async def help_slash(self, interaction: discord.Interaction):
        in_guild = interaction.guild is not None
        permissions = interaction.permissions if in_guild else None
        commands_ = list(self.bot.tree.get_commands())
        if in_guild:
            commands_ += self.bot.tree.get_commands(guild=interaction.guild)
        descriptions = {name: cog.description or "" for name, cog in self.bot.cogs.items()}
        categories = collect_help(commands_, descriptions, permissions=permissions, in_guild=in_guild)
        prefixes = await self.bot.get_valid_prefixes(interaction.guild)
        prefix = next((p for p in prefixes if not p.startswith("<@")), prefixes[0] if prefixes else "[p]")
        view = HelpView(interaction.user.id, categories, prefix)
        await interaction.response.send_message(
            embed=view.embed(), view=view, ephemeral=True, allowed_mentions=HELP_NO_PINGS
        )
        view.message = await interaction.original_response()
