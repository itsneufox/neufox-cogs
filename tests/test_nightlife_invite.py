"""Exercise invitation authorization and lifecycle without requiring a Discord connection."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class FakeView:
    def __init__(self, *, timeout):
        self.timeout = timeout

    def stop(self):
        pass


class HTTPException(Exception):
    pass


discord_stub = SimpleNamespace(
    ui=SimpleNamespace(View=FakeView, button=lambda **kwargs: lambda callback: callback),
    ButtonStyle=SimpleNamespace(success=1, secondary=2, danger=3),
    AllowedMentions=SimpleNamespace(none=lambda: None),
    HTTPException=HTTPException,
)
SPEC = importlib.util.spec_from_file_location("nightlife_invite", Path(__file__).parents[1] / "economy" / "nightlife_invite.py")
invites = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"discord": discord_stub}):
    SPEC.loader.exec_module(invites)


class InvitationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = SimpleNamespace(_nightlife_invites={}, _accept_nightlife_invite=AsyncMock(return_value="Encounter completed."))
        self.ctx = SimpleNamespace(author=SimpleNamespace(id=1), clean_prefix=".")
        self.partner = SimpleNamespace(id=2)
        self.view = invites.NightlifeInviteView(self.cog, self.ctx, self.partner, "motel", True)
        self.view.message = SimpleNamespace(edit=AsyncMock())
        self.cog._nightlife_invites = {1: self.view, 2: self.view}

    def interaction(self, user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
        )

    async def test_only_invited_player_can_accept(self):
        for user_id in (1, 3):
            interaction = self.interaction(user_id)
            await self.view.respond(interaction, "accept")
            interaction.response.send_message.assert_awaited_once()
        self.cog._accept_nightlife_invite.assert_not_awaited()
        self.assertFalse(self.view.closed)

    async def test_simultaneous_accepts_settle_once(self):
        await asyncio.gather(*(self.view.respond(self.interaction(2), "accept") for _ in range(2)))
        self.cog._accept_nightlife_invite.assert_awaited_once_with(self.view)
        self.assertTrue(self.view.closed)
        self.assertEqual(self.cog._nightlife_invites, {})

    async def test_decline_and_cancel_do_not_settle(self):
        for action, user_id in (("decline", 2), ("cancel", 1)):
            self.setUp()
            await self.view.respond(self.interaction(user_id), action)
            self.cog._accept_nightlife_invite.assert_not_awaited()
            self.assertTrue(self.view.closed)
            self.assertEqual(self.cog._nightlife_invites, {})

    async def test_outsider_cannot_decline_or_cancel(self):
        for action in ("decline", "cancel"):
            await self.view.respond(self.interaction(3), action)
        self.assertFalse(self.view.closed)
        self.cog._accept_nightlife_invite.assert_not_awaited()

    async def test_expired_invitation_cannot_settle(self):
        self.view.deadline = 0
        await self.view.respond(self.interaction(2), "accept")
        self.cog._accept_nightlife_invite.assert_not_awaited()
        self.assertEqual(self.cog._nightlife_invites, {})

    async def test_timeout_releases_both_players_without_settlement(self):
        await self.view.on_timeout()
        self.cog._accept_nightlife_invite.assert_not_awaited()
        self.assertEqual(self.cog._nightlife_invites, {})
        self.view.message.edit.assert_awaited_once()

    async def test_failed_revalidation_closes_invitation(self):
        self.cog._accept_nightlife_invite.side_effect = ValueError("Insufficient funds.")
        interaction = self.interaction(2)
        await self.view.respond(interaction, "accept")
        self.assertIn("Insufficient funds", interaction.edit_original_response.call_args.kwargs["content"])
        self.assertEqual(self.cog._nightlife_invites, {})

    async def test_message_failure_after_settlement_cannot_replay(self):
        interaction = self.interaction(2)
        interaction.edit_original_response.side_effect = HTTPException()
        await self.view.respond(interaction, "accept")
        await self.view.respond(self.interaction(2), "accept")
        self.cog._accept_nightlife_invite.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

    async def test_closed_old_invitation_does_not_remove_a_new_one(self):
        replacement = object()
        self.cog._nightlife_invites[1] = replacement
        self.view.close()
        self.assertIs(self.cog._nightlife_invites[1], replacement)
        self.assertNotIn(2, self.cog._nightlife_invites)


if __name__ == "__main__":
    unittest.main()
