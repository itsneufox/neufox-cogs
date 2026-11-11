"""Interactive views: the last-check confirmation prompt and the honeypot review button."""

from __future__ import annotations

import discord

from .common import DEFAULT_REASON, audit_reason
from .layouts import NO_PINGS, embed_container


HONEYPOT_REASON = "Triggered honeypot channel"


class ConfirmActionView(discord.ui.LayoutView):
    def __init__(
        self,
        author_id: int,
        action_text: str,
        reason: str,
        moderator_name: str,
        thumbnail_url: str,
        language: str = "en",
        timeout: float = 30,
    ):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.action_text = action_text
        self.reason = reason or DEFAULT_REASON
        self.moderator_name = moderator_name
        self.thumbnail_url = thumbnail_url
        self.language = language
        self.value: bool | None = None
        self.message: discord.Message | None = None
        portuguese = self.language == "pt"
        self.confirm_button = discord.ui.Button(
            label="Confirmar" if portuguese else "Confirm",
            style=discord.ButtonStyle.danger,
        )
        self.confirm_button.callback = self._confirm
        self.cancel_button = discord.ui.Button(
            label="Cancelar" if portuguese else "Cancel",
            style=discord.ButtonStyle.secondary,
        )
        self.cancel_button.callback = self._cancel
        self._show(self.prompt_embed())

    def _show(self, embed: discord.Embed):
        self.clear_items()
        self.add_item(embed_container(embed, discord.ui.ActionRow(self.confirm_button, self.cancel_button)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        message = (
            "Somente o moderador que executou este comando pode usar esta confirmação."
            if self.language == "pt"
            else "Only the moderator who ran this command can use this prompt."
        )
        await interaction.response.send_message(message, ephemeral=True)
        return False

    async def _confirm(self, interaction: discord.Interaction):
        self.value = True
        self._disable_buttons()
        self._show(self._result_embed("Confirmed", discord.Color.green()))
        await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)
        self.stop()

    async def _cancel(self, interaction: discord.Interaction):
        self.value = False
        self._disable_buttons()
        self._show(self._result_embed("Cancelled", discord.Color.light_grey()))
        await interaction.response.edit_message(view=self, allowed_mentions=NO_PINGS)
        self.stop()

    async def on_timeout(self):
        self._disable_buttons()
        if self.message is not None:
            try:
                self._show(self._result_embed("Expired", discord.Color.light_grey()))
                await self.message.edit(view=self, allowed_mentions=NO_PINGS)
            except (discord.Forbidden, discord.HTTPException):
                return

    def _disable_buttons(self):
        self.confirm_button.disabled = True
        self.cancel_button.disabled = True

    def prompt_embed(self) -> discord.Embed:
        if self.language == "pt":
            embed = discord.Embed(
                title="Última confirmação",
                description="Esta ação de moderação está prestes a ser executada. Confira uma última vez.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Ação", value=self._embed_value(self.action_text), inline=False)
            embed.add_field(name="Motivo", value=self._embed_value(self.reason), inline=False)
            embed.add_field(name="Moderador", value=self.moderator_name, inline=True)
            embed.add_field(name="Expira em", value="30 segundos", inline=True)
            embed.set_footer(text="Confirmar executa a ação. Cancelar não altera nada.")
            if self.thumbnail_url:
                embed.set_thumbnail(url=self.thumbnail_url)
            return embed

        embed = discord.Embed(
            title="Last Check",
            description="This moderation action is about to go through. Give it one clean look first.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Action", value=self._embed_value(self.action_text), inline=False)
        embed.add_field(name="Reason", value=self._embed_value(self.reason), inline=False)
        embed.add_field(name="Moderator", value=self.moderator_name, inline=True)
        embed.add_field(name="Expires", value="30 seconds", inline=True)
        embed.set_footer(text="Confirm runs it. Cancel keeps the paperwork clean.")
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        return embed

    def _result_embed(self, status: str, color: discord.Color) -> discord.Embed:
        if self.language == "pt":
            descriptions = {
                "Confirmed": "Tudo certo. A ação está sendo executada.",
                "Cancelled": "Cancelado. Nada foi alterado.",
                "Expired": "Sem confirmação, sem ação. O pedido expirou.",
            }
            translated_status = {
                "Confirmed": "confirmada",
                "Cancelled": "cancelada",
                "Expired": "expirada",
            }.get(status, status)
            embed = discord.Embed(
                title=f"Ação de moderação {translated_status}",
                description=descriptions.get(status, ""),
                color=color,
            )
            embed.add_field(name="Ação", value=self._embed_value(self.action_text), inline=False)
            embed.add_field(name="Motivo", value=self._embed_value(self.reason), inline=False)
            embed.add_field(name="Moderador", value=self.moderator_name, inline=True)
            if self.thumbnail_url:
                embed.set_thumbnail(url=self.thumbnail_url)
            return embed

        descriptions = {
            "Confirmed": "Locked in. The action is being carried out.",
            "Cancelled": "Called off. Nothing was changed.",
            "Expired": "No click, no action. The prompt expired.",
        }
        embed = discord.Embed(
            title=f"Moderation Action {status}",
            description=descriptions.get(status, ""),
            color=color,
        )
        embed.add_field(name="Action", value=self._embed_value(self.action_text), inline=False)
        embed.add_field(name="Reason", value=self._embed_value(self.reason), inline=False)
        embed.add_field(name="Moderator", value=self.moderator_name, inline=True)
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        return embed

    @staticmethod
    def _embed_value(value: str) -> str:
        if len(value) <= 1024:
            return value
        return value[:1021] + "..."



class BanReviewView(discord.ui.View):
    """Ban button attached to honeypot kick/role log entries."""

    def __init__(self, cog, guild_id: int, target_id: int, target_name: str):
        super().__init__(timeout=86400)
        self.cog = cog
        self.guild_id = guild_id
        self.target_id = target_id
        self.target_name = target_name

    @discord.ui.button(label="Ban User", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = self.cog.bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message("Guild is unavailable. Try again later.", ephemeral=True)
            return

        perms = getattr(interaction.user, "guild_permissions", None)
        if not perms or not perms.ban_members:
            await interaction.response.send_message(
                "You need the **Ban Members** permission to use this button.", ephemeral=True
            )
            return

        target = discord.Object(id=self.target_id)
        try:
            await guild.ban(target, reason=audit_reason(interaction.user, HONEYPOT_REASON), delete_message_days=1)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "I couldn't ban that user. Check my permissions and role hierarchy.", ephemeral=True
            )
            return

        button.disabled = True
        button.label = "User Banned"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"{self.target_name} has been banned.", ephemeral=True)
        self.stop()
        await self.cog._create_case(
            guild, interaction.user, target, "Honeypot Ban", HONEYPOT_REASON, target_name=self.target_name
        )
