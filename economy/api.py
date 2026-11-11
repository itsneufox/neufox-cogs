from __future__ import annotations

import logging
import secrets
from typing import Any

from aiohttp import web

from .errors import EconomyCodeNotFound, EconomyCodeRedeemed, EconomyError

log = logging.getLogger("red.neufox.economy")


class ApiMixin:
    """Token-protected HTTP API."""

    async def _start_api_if_enabled(self):
        await self.bot.wait_until_ready()
        await self._restore_shop_panel_views()
        if await self.config.api_enabled():
            await self._restart_api(await self.config.api_host(), int(await self.config.api_port()))

    async def _restart_api(self, host: str, port: int):
        await self._stop_api()
        app = web.Application()
        app.add_routes(
            [
                web.get("/health", self._api_health),
                web.get("/balance/{user_id}", self._api_get_balance),
                web.post("/balance/{user_id}/add", self._api_add_balance),
                web.post("/balance/{user_id}/remove", self._api_remove_balance),
                web.post("/balance/{user_id}/set", self._api_set_balance),
                web.post("/transfer", self._api_transfer),
                web.post("/redeem-code", self._api_redeem_code),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        log.info("Economy API started on %s:%s", host, port)

    async def _stop_api(self):
        if self._runner is None:
            return
        runner = self._runner
        self._runner = None
        self._site = None
        await runner.cleanup()

    async def _api_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _api_get_balance(self, request: web.Request) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        user_id = self._parse_user_id(request.match_info["user_id"])
        if user_id is None:
            return web.json_response({"error": "invalid user_id"}, status=400)
        return web.json_response({"user_id": user_id, "balances": await self.get_balance(user_id)})

    async def _api_add_balance(self, request: web.Request) -> web.Response:
        return await self._api_adjust(request, "add")

    async def _api_remove_balance(self, request: web.Request) -> web.Response:
        return await self._api_adjust(request, "remove")

    async def _api_set_balance(self, request: web.Request) -> web.Response:
        return await self._api_adjust(request, "set")

    async def _api_adjust(self, request: web.Request, operation: str) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        user_id = self._parse_user_id(request.match_info["user_id"])
        if user_id is None:
            return web.json_response({"error": "invalid user_id"}, status=400)
        payload = await self._json_payload(request)
        if payload is None:
            return web.json_response({"error": "invalid json"}, status=400)
        try:
            if operation == "add":
                balances = await self.add_balance(user_id, payload.get("amount"), actor_id=None, reason=payload.get("reason", "api add"))
            elif operation == "remove":
                balances = await self.remove_balance(user_id, payload.get("amount"), actor_id=None, reason=payload.get("reason", "api remove"))
            else:
                balances = await self.set_balance(user_id, payload.get("amount"), actor_id=None, reason=payload.get("reason", "api set"))
        except EconomyError as error:
            return web.json_response({"error": str(error)}, status=400)
        return web.json_response({"user_id": user_id, "balances": balances})

    async def _api_transfer(self, request: web.Request) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        payload = await self._json_payload(request)
        if payload is None:
            return web.json_response({"error": "invalid json"}, status=400)
        try:
            result = await self.transfer_balance(
                int(payload.get("from_user_id")),
                int(payload.get("to_user_id")),
                payload.get("amount"),
                actor_id=None,
                reason=payload.get("reason", "api transfer"),
            )
        except (TypeError, ValueError, EconomyError) as error:
            return web.json_response({"error": str(error)}, status=400)
        return web.json_response(result)

    async def _api_redeem_code(self, request: web.Request) -> web.Response:
        if not await self._authorized(request):
            return self._unauthorized()
        payload = await self._json_payload(request)
        if payload is None:
            return web.json_response({"error": "invalid json"}, status=400)

        code = payload.get("code")
        if not code:
            return web.json_response({"error": "code is required"}, status=400)
        try:
            entry = await self.redeem_code(
                str(code),
                redeemed_by=str(payload.get("redeemed_by", "api"))[:100],
            )
        except EconomyCodeNotFound as error:
            return web.json_response({"error": str(error)}, status=404)
        except EconomyCodeRedeemed as error:
            return web.json_response({"error": str(error)}, status=409)
        except EconomyError as error:
            return web.json_response({"error": str(error)}, status=400)
        return web.json_response(entry)

    async def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        token = header.removeprefix("Bearer ").strip()
        tokens = await self.config.api_tokens()
        return any(secrets.compare_digest(token, stored) for stored in tokens.values())

    @staticmethod
    def _unauthorized() -> web.Response:
        return web.json_response({"error": "unauthorized"}, status=401)

    @staticmethod
    async def _json_payload(request: web.Request) -> dict[str, Any] | None:
        try:
            payload = await request.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _parse_user_id(value: str) -> int | None:
        try:
            user_id = int(value)
        except (TypeError, ValueError):
            return None
        return user_id if user_id > 0 else None

