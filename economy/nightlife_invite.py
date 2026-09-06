from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import time

import discord


log = logging.getLogger("red.neufox.economy.nightlife")
INVITE_TIMEOUT = 90


class NightlifeInviteView(discord.ui.View):
    def __init__(self, cog, ctx, partner, venue: str, protected: bool):
        super().__init__(timeout=INVITE_TIMEOUT)
        self.cog = cog
        self.ctx = ctx
        self.partner = partner
        self.venue = venue
        self.protected = protected
        self.deadline = time.monotonic() + INVITE_TIMEOUT
        self.message = None
        self.closed = False
        self._lock = asyncio.Lock()

    @property
    def participant_ids(self):
        return (self.ctx.author.id, self.partner.id)

    def close(self):
        self.closed = True
        self.stop()
        for user_id in self.participant_ids:
            if self.cog._nightlife_invites.get(user_id) is self:
                self.cog._nightlife_invites.pop(user_id, None)

    async def edit_message(self, content: str):
        if self.message is not None:
            with suppress(discord.HTTPException):
                await self.message.edit(content=content, view=None, allowed_mentions=discord.AllowedMentions.none())

    async def on_timeout(self):
        async with self._lock:
            if self.closed:
                return
            self.close()
            await self.edit_message("Invitation expired.")

    async def respond(self, interaction: discord.Interaction, action: str):
        permitted_id = self.ctx.author.id if action == "cancel" else self.partner.id
        if interaction.user.id != permitted_id:
            await interaction.response.send_message("This button isn't for you.", ephemeral=True)
            return
        await interaction.response.defer()
        async with self._lock:
            if self.closed:
                await interaction.followup.send("This invitation has ended.", ephemeral=True)
                return
            if time.monotonic() >= self.deadline:
                self.close()
                await self.edit_message("Invitation expired.")
                return
            try:
                if action == "accept":
                    content = await self.cog._accept_nightlife_invite(self)
                else:
                    content = "Invitation cancelled." if action == "cancel" else "Invitation declined."
            except ValueError as error:
                content = str(error).replace("`sex", f"`{self.ctx.clean_prefix}sex")
            except Exception:
                log.exception("Failed to finish a Nightlife invitation")
                content = "The encounter hit an error. Check your balance and profile before trying again."
            finally:
                self.close()
            # Settlement is complete before editing; a failed edit must never replay it.
            try:
                await interaction.edit_original_response(
                    content=content, view=None, allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                with suppress(discord.HTTPException):
                    await interaction.followup.send(content, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.respond(interaction, "accept")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.respond(interaction, "decline")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.respond(interaction, "cancel")
