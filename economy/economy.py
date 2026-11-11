"""The Economy cog: global LWD$ balances, claims, shop, casino, lottery, Nightlife and money sinks.

The cog is assembled from feature mixins so each area lives in its own module:

- ``core``      balances, the ledger, charges/credits and economy logs (public API)
- ``hub``       ``/eco`` panel, ``/pay`` with transfer tax, ``[p]balance`` and the leaderboard
- ``claims``    ``/claim`` (every ready reward at once) and the daily streak
- ``shop``      ``/shop`` items, inventory, gifts and redeem codes
- ``perks``     cosmetics, personal roles, rented roles, boosters, streak savers, ``get_multiplier``
- ``social``    rain, bounties, auctions, shoutouts, pins, wealth tax and the upkeep loop
- ``casino``    ``/casino`` games, Hold'em and player protection
- ``lottery``   ``/casino lottery`` (LWDMillions)
- ``nightlife_commands`` ``/nightlife``
- ``admin``     ``/control`` panel schema and ``[p]ecoadmin``
- ``api``       token-protected HTTP API
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from aiohttp import web
from redbot.core import Config, commands

from .admin import AdminMixin
from .api import ApiMixin
from .blackjack import BlackjackShoe, BlackjackView
from .casino import CasinoMixin
from .claims import ClaimsMixin
from .constants import CONFIG_IDENTIFIER, GLOBAL_DEFAULTS, GUILD_DEFAULTS, MEMBER_DEFAULTS, USER_DEFAULTS
from .core import CoreMixin
from .errors import EconomyCodeNotFound, EconomyCodeRedeemed, EconomyError
from .holdem_table import HoldemTable
from .hub import HubMixin
from .layouts import use_real_prefix
from .lottery import LotteryMixin
from .mines import MinesView
from .nightlife_commands import NightlifeMixin
from .nightlife_invite import NightlifeInviteView
from .perks import PerksMixin
from .shop import ShopMixin
from .social import SocialMixin

__all__ = ["Economy", "EconomyError", "EconomyCodeNotFound", "EconomyCodeRedeemed"]

# Slash commands whose replies only the caller should see.
PRIVATE_SLASH = {
    "shop codes",
    "casino holdem join",
    "casino holdem start",
    "casino holdem cancel",
    "casino holdem fold",
    "casino holdem check",
    "casino holdem call",
    "casino holdem raise",
}


class Economy(
    HubMixin,
    ClaimsMixin,
    PerksMixin,
    SocialMixin,
    ShopMixin,
    LotteryMixin,
    CasinoMixin,
    NightlifeMixin,
    AdminMixin,
    ApiMixin,
    CoreMixin,
    commands.Cog,
):
    """Global economy with claims, a shop, money sinks, casino games and API access."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER)
        self.config.register_global(**GLOBAL_DEFAULTS)
        self.config.register_guild(**GUILD_DEFAULTS)
        self.config.register_member(**MEMBER_DEFAULTS)
        self.config.register_user(**USER_DEFAULTS)
        self._lock = asyncio.Lock()
        self._social_lock = asyncio.Lock()
        self._recent_chatters: dict[int, OrderedDict[int, float]] = {}
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._blackjack_players: set[int] = set()
        self._blackjack_views: dict[int, BlackjackView] = {}
        self._blackjack_shoes: dict[int, BlackjackShoe] = {}
        self._holdem_tables: dict[int, HoldemTable] = {}
        self._holdem_lock = asyncio.Lock()
        self._mines_players: set[int] = set()
        self._mines_views: dict[int, MinesView] = {}
        self._nightlife_invites: dict[int, NightlifeInviteView] = {}
        self._casino_exclusion_lock = asyncio.Lock()
        self._slot_players: set[int] = set()
        self._slot_render_semaphore = asyncio.Semaphore(2)
        self._lwdmillions_draw_lock = asyncio.Lock()
        self._lwdmillions_notification_tasks: set[asyncio.Task] = set()
        self._startup_task = self.bot.loop.create_task(self._start_api_if_enabled())
        self._casino_limit_upgrade_task = self.bot.loop.create_task(self._upgrade_casino_limit())
        self._holdem_recovery_task = self.bot.loop.create_task(self._recover_holdem_escrows())
        self._lwdmillions_task = self.bot.loop.create_task(self._lwdmillions_draw_loop())
        self._sinks_task = self.bot.loop.create_task(self._sinks_loop())

    async def cog_before_invoke(self, ctx: commands.Context):
        """Acknowledge slash commands before any money moves, and fix prefixes in help text."""
        if ctx.interaction is None:
            return
        await use_real_prefix(self.bot, ctx)
        if not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=ctx.command.qualified_name in PRIVATE_SLASH)

    def cog_unload(self):
        for view in set(self._nightlife_invites.values()):
            view.close()
            self.bot.loop.create_task(view.edit_message("Invitation cancelled because Economy was unloaded."))
        self._startup_task.cancel()
        self._casino_limit_upgrade_task.cancel()
        self._holdem_recovery_task.cancel()
        self._lwdmillions_task.cancel()
        self._sinks_task.cancel()
        for task in list(self._lwdmillions_notification_tasks):
            task.cancel()
        for view in list(self._blackjack_views.values()):
            self.bot.loop.create_task(view.cancel_and_refund())
        for view in list(self._mines_views.values()):
            self.bot.loop.create_task(view.cancel_and_refund())
        for table in list(self._holdem_tables.values()):
            self.bot.loop.create_task(table.cancel(0, force=True))
        self.bot.loop.create_task(self._stop_api())
