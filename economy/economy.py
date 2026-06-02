from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

from aiohttp import web
import discord
from redbot.core import Config, commands


log = logging.getLogger("red.neufox.economy")

CASH = "cash"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8787
MAX_LEDGER_ENTRIES = 500
MAX_AMOUNT = 10**15


class Economy(commands.Cog):
    """Global economy with API access."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=830271946)
        self.config.register_global(
            balances={},
            ledger=[],
            next_tx=1,
            api_enabled=False,
            api_host=DEFAULT_API_HOST,
            api_port=DEFAULT_API_PORT,
            api_tokens={},
        )
        self._lock = asyncio.Lock()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._startup_task = self.bot.loop.create_task(self._start_api_if_enabled())

    def cog_unload(self):
        self._startup_task.cancel()
        self.bot.loop.create_task(self._stop_api())

    @commands.group(name="eco", aliases=["economy"], invoke_without_command=True)
    async def economy(self, ctx: commands.Context):
        """View economy commands."""
        await ctx.invoke(self.economy_balance)

    @economy.command(name="balance", aliases=["bal"])
    async def economy_balance(self, ctx: commands.Context, member: discord.Member | None = None):
        """Show your balance, or another member's balance."""
        member = member or ctx.author
        balances = await self.get_balance(member.id)
        embed = discord.Embed(title=f"{member.display_name}'s Balance", color=discord.Color.gold())
        embed.add_field(name="Cash", value=f"{balances[CASH]:,}", inline=True)
        await ctx.send(embed=embed)

    @economy.command(name="pay")
    async def economy_pay(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Pay cash to another member."""
        if member.bot:
            await ctx.send("You cannot pay bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("You cannot pay yourself.")
            return
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        try:
            await self.transfer_balance(
                ctx.author.id,
                member.id,
                amount,
                actor_id=ctx.author.id,
                reason=f"Discord pay command in guild {ctx.guild.id if ctx.guild else 'dm'}",
            )
        except EconomyError as error:
            await ctx.send(str(error))
            return

        await ctx.send(f"Paid {member.mention} {amount:,} cash.", allowed_mentions=discord.AllowedMentions(users=True))

    @economy.group(name="admin", invoke_without_command=True)
    @commands.is_owner()
    async def economy_admin(self, ctx: commands.Context):
        """Owner-only economy management."""
        await ctx.send_help()

    @economy_admin.command(name="add")
    @commands.is_owner()
    async def economy_admin_add(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "owner adjustment",
    ):
        """Add cash to a user."""
        await self._owner_adjust(ctx, member, amount, "add", reason)

    @economy_admin.command(name="remove")
    @commands.is_owner()
    async def economy_admin_remove(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "owner adjustment",
    ):
        """Remove cash from a user."""
        await self._owner_adjust(ctx, member, amount, "remove", reason)

    @economy_admin.command(name="set")
    @commands.is_owner()
    async def economy_admin_set(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "owner set",
    ):
        """Set a user's cash balance."""
        await self._owner_adjust(ctx, member, amount, "set", reason)

    @economy.group(name="api", invoke_without_command=True)
    @commands.is_owner()
    async def economy_api(self, ctx: commands.Context):
        """Owner-only API management."""
        await ctx.invoke(self.economy_api_status)

    @economy_api.command(name="status")
    @commands.is_owner()
    async def economy_api_status(self, ctx: commands.Context):
        """Show API status."""
        host = await self.config.api_host()
        port = await self.config.api_port()
        enabled = await self.config.api_enabled()
        tokens = await self.config.api_tokens()
        runtime = "running" if self._runner is not None else "stopped"
        await ctx.send(
            f"API configured: {'enabled' if enabled else 'disabled'}\n"
            f"Runtime: {runtime}\n"
            f"Bind: `{host}:{port}`\n"
            f"Tokens: {len(tokens)}"
        )

    @economy_api.command(name="start")
    @commands.is_owner()
    async def economy_api_start(self, ctx: commands.Context, host: str = DEFAULT_API_HOST, port: int = DEFAULT_API_PORT):
        """Start the HTTP API."""
        if port < 1 or port > 65535:
            await ctx.send("Port must be between 1 and 65535.")
            return

        await self.config.api_host.set(host)
        await self.config.api_port.set(port)
        await self.config.api_enabled.set(True)
        try:
            await self._restart_api(host, port)
        except OSError as error:
            await ctx.send(f"Could not start API: {error}")
            return
        await ctx.send(f"Economy API started on `{host}:{port}`.")

    @economy_api.command(name="stop")
    @commands.is_owner()
    async def economy_api_stop(self, ctx: commands.Context):
        """Stop the HTTP API."""
        await self.config.api_enabled.set(False)
        await self._stop_api()
        await ctx.send("Economy API stopped.")

    @economy_api.group(name="token", invoke_without_command=True)
    @commands.is_owner()
    async def economy_api_token(self, ctx: commands.Context):
        """Manage API tokens."""
        await ctx.send_help()

    @economy_api_token.command(name="create")
    @commands.is_owner()
    async def economy_api_token_create(self, ctx: commands.Context, name: str):
        """Create or replace an API token."""
        token = secrets.token_urlsafe(32)
        async with self.config.api_tokens() as tokens:
            tokens[name] = token
        await ctx.send(f"Token `{name}` created. Copy it now:\n`{token}`")

    @economy_api_token.command(name="revoke")
    @commands.is_owner()
    async def economy_api_token_revoke(self, ctx: commands.Context, name: str):
        """Revoke an API token."""
        async with self.config.api_tokens() as tokens:
            existed = tokens.pop(name, None) is not None
        await ctx.send(f"Token `{name}` {'revoked' if existed else 'was not found'}.")

    async def _owner_adjust(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        operation: str,
        reason: str,
    ):
        if amount < 0:
            await ctx.send("Amount cannot be negative.")
            return

        try:
            if operation == "add":
                balances = await self.add_balance(member.id, amount, actor_id=ctx.author.id, reason=reason)
            elif operation == "remove":
                balances = await self.remove_balance(member.id, amount, actor_id=ctx.author.id, reason=reason)
            else:
                balances = await self.set_balance(member.id, amount, actor_id=ctx.author.id, reason=reason)
        except EconomyError as error:
            await ctx.send(str(error))
            return

        await ctx.send(f"{member.mention} now has {balances[CASH]:,} cash.", allowed_mentions=discord.AllowedMentions(users=True))

    async def get_balance(self, user_id: int) -> dict[str, int]:
        """Public cog API: return a user's balances."""
        balances = await self.config.balances()
        return self._account_from_mapping(balances, user_id)

    async def add_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        reason: str = "api add",
    ) -> dict[str, int]:
        """Public cog API: add cash to a user."""
        return await self._adjust_balance(user_id, amount, actor_id=actor_id, reason=reason, operation="add")

    async def remove_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        reason: str = "api remove",
    ) -> dict[str, int]:
        """Public cog API: remove cash from a user."""
        return await self._adjust_balance(user_id, amount, actor_id=actor_id, reason=reason, operation="remove")

    async def set_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        reason: str = "api set",
    ) -> dict[str, int]:
        """Public cog API: set a user's cash balance."""
        return await self._adjust_balance(user_id, amount, actor_id=actor_id, reason=reason, operation="set")

    async def transfer_balance(
        self,
        from_user_id: int,
        to_user_id: int,
        amount: int,
        *,
        actor_id: int | None = None,
        reason: str = "api transfer",
    ) -> dict[str, dict[str, int]]:
        """Public cog API: transfer cash between users."""
        amount = self._require_amount(amount, allow_zero=False)
        async with self._lock:
            async with self.config.balances() as balances:
                source = self._account_from_mapping(balances, from_user_id)
                target = self._account_from_mapping(balances, to_user_id)
                if source[CASH] < amount:
                    raise EconomyError("Insufficient funds.")
                source[CASH] -= amount
                target[CASH] += amount
                balances[str(from_user_id)] = source
                balances[str(to_user_id)] = target
            await self._append_ledger(
                "transfer",
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                amount=amount,
                actor_id=actor_id,
                reason=reason,
            )
        return {"from": source, "to": target}

    async def _adjust_balance(
        self,
        user_id: int,
        amount: int,
        *,
        actor_id: int | None,
        reason: str,
        operation: str,
    ) -> dict[str, int]:
        amount = self._require_amount(amount, allow_zero=True)
        async with self._lock:
            async with self.config.balances() as balances:
                account = self._account_from_mapping(balances, user_id)
                if operation == "add":
                    account[CASH] += amount
                elif operation == "remove":
                    if account[CASH] < amount:
                        raise EconomyError("Insufficient funds.")
                    account[CASH] -= amount
                elif operation == "set":
                    account[CASH] = amount
                else:
                    raise EconomyError("Unknown operation.")
                balances[str(user_id)] = account
            await self._append_ledger(
                operation,
                from_user_id=None,
                to_user_id=user_id,
                amount=amount,
                actor_id=actor_id,
                reason=reason,
            )
        return account

    async def _append_ledger(
        self,
        tx_type: str,
        *,
        from_user_id: int | None,
        to_user_id: int | None,
        amount: int,
        actor_id: int | None,
        reason: str,
    ):
        tx_id = int(await self.config.next_tx())
        await self.config.next_tx.set(tx_id + 1)
        entry = {
            "id": tx_id,
            "type": tx_type,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "amount": amount,
            "actor_id": actor_id,
            "reason": str(reason or "")[:250],
            "created_at": int(time.time()),
        }
        async with self.config.ledger() as ledger:
            ledger.append(entry)
            del ledger[:-MAX_LEDGER_ENTRIES]

    async def _start_api_if_enabled(self):
        await self.bot.wait_until_ready()
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

    @staticmethod
    def _account_from_mapping(balances: dict[str, Any], user_id: int) -> dict[str, int]:
        account = balances.get(str(user_id), {})
        return {
            CASH: int(account.get(CASH, 0)),
        }

    @staticmethod
    def _require_amount(amount: Any, *, allow_zero: bool) -> int:
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise EconomyError("Amount must be an integer.")
        if amount < 0 or (amount == 0 and not allow_zero):
            raise EconomyError("Amount must be positive.")
        if amount > MAX_AMOUNT:
            raise EconomyError(f"Amount cannot exceed {MAX_AMOUNT:,}.")
        return amount


class EconomyError(Exception):
    pass
