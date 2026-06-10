from __future__ import annotations

import asyncio
import json
import time
import sqlite3
from pathlib import Path
from typing import Any

import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.blue()


class MessageArchive(commands.Cog):
    """Save guild messages to a local archive file."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=999000101)
        self.config.register_guild(message_archive_enabled=True)

        self._archive_dir = Path(__file__).with_name("data")
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._initialized_db: set[int] = set()
        self._locks: dict[int, asyncio.Lock] = {}

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def _guild_db(self, guild_id: int) -> Path:
        return self._archive_dir / f"{guild_id}.db"

    @staticmethod
    def _serialize_message(message: discord.Message) -> dict[str, Any]:
        return {
            "message_id": message.id,
            "guild_id": message.guild.id if message.guild is not None else 0,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "author_name": str(getattr(message.author, "global_name", None) or message.author.name),
            "display_name": getattr(message.author, "display_name", str(message.author)),
            "created_at": int(message.created_at.timestamp()),
            "content": message.content,
            "clean_content": message.clean_content,
            "system_content": message.system_content or "",
            "is_webhook": 1 if message.webhook_id is not None else 0,
            "is_bot": 1 if message.author.bot else 0,
            "reference_message_id": message.reference.message_id if message.reference else None,
            "attachments": json.dumps([attachment.url for attachment in message.attachments], ensure_ascii=False),
            "jump_url": message.jump_url,
        }

    @staticmethod
    def _initialize_db(path: Path):
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT,
                    display_name TEXT,
                    created_at INTEGER NOT NULL,
                    content TEXT,
                    clean_content TEXT,
                    system_content TEXT,
                    is_webhook INTEGER NOT NULL,
                    is_bot INTEGER NOT NULL,
                    reference_message_id INTEGER,
                    attachments TEXT,
                    jump_url TEXT,
                    created_in_seconds INTEGER NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_guild_author ON messages(guild_id, author_id);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_guild_created_at ON messages(guild_id, created_at DESC);")
            connection.commit()

    @staticmethod
    def _insert_message(path: Path, data: dict[str, Any]):
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_id,
                    guild_id,
                    channel_id,
                    author_id,
                    author_name,
                    display_name,
                    created_at,
                    content,
                    clean_content,
                    system_content,
                    is_webhook,
                    is_bot,
                    reference_message_id,
                    attachments,
                    jump_url,
                    created_in_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["message_id"],
                    data["guild_id"],
                    data["channel_id"],
                    data["author_id"],
                    data["author_name"],
                    data["display_name"],
                    data["created_at"],
                    data["content"],
                    data["clean_content"],
                    data["system_content"],
                    data["is_webhook"],
                    data["is_bot"],
                    data["reference_message_id"],
                    data["attachments"],
                    data["jump_url"],
                    int(time.time()),
                ),
            )
            connection.commit()

    @staticmethod
    def _insert_messages(path: Path, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0

        values = [
            (
                data["message_id"],
                data["guild_id"],
                data["channel_id"],
                data["author_id"],
                data["author_name"],
                data["display_name"],
                data["created_at"],
                data["content"],
                data["clean_content"],
                data["system_content"],
                data["is_webhook"],
                data["is_bot"],
                data["reference_message_id"],
                data["attachments"],
                data["jump_url"],
                int(time.time()),
            )
            for data in records
        ]

        with sqlite3.connect(path) as connection:
            pre_count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            connection.executemany(
                """
                INSERT OR IGNORE INTO messages (
                    message_id,
                    guild_id,
                    channel_id,
                    author_id,
                    author_name,
                    display_name,
                    created_at,
                    content,
                    clean_content,
                    system_content,
                    is_webhook,
                    is_bot,
                    reference_message_id,
                    attachments,
                    jump_url,
                    created_in_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            connection.commit()
            post_count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return max(0, int(post_count - pre_count))

    @staticmethod
    def _db_stats(path: Path) -> tuple[int, int]:
        if not path.exists():
            return 0, 0
        with sqlite3.connect(path) as connection:
            cursor = connection.execute("SELECT COUNT(*) FROM messages")
            count = int(cursor.fetchone()[0] or 0)
        return count, path.stat().st_size

    @staticmethod
    def _clear_db(path: Path):
        if path.exists():
            path.unlink()

    def _ensure_guild_db(self, guild_id: int):
        path = self._guild_db(guild_id)
        if guild_id in self._initialized_db:
            if path.exists():
                return
            self._initialized_db.discard(guild_id)
        self._initialize_db(path)
        self._initialized_db.add(guild_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if not await self.config.guild(message.guild).message_archive_enabled():
            return

        data = self._serialize_message(message)
        path = self._guild_db(message.guild.id)
        self._ensure_guild_db(message.guild.id)
        lock = self._guild_lock(message.guild.id)
        async with lock:
            await asyncio.to_thread(self._insert_message, path, data)

    @commands.group(name="messagearchive", aliases=["msgarchive"], invoke_without_command=True)
    @commands.guild_only()
    async def messagearchive(self, ctx: commands.Context):
        """Save every guild message to local archive files."""
        await ctx.invoke(self.messagearchive_status)

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @messagearchive.command(name="on")
    async def messagearchive_on(self, ctx: commands.Context):
        """Enable message archiving in this server."""
        await self.config.guild(ctx.guild).message_archive_enabled.set(True)
        await ctx.send("Message archiving enabled for this server.")

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @messagearchive.command(name="off")
    async def messagearchive_off(self, ctx: commands.Context):
        """Disable message archiving in this server."""
        await self.config.guild(ctx.guild).message_archive_enabled.set(False)
        await ctx.send("Message archiving disabled for this server.")

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @messagearchive.command(name="backfill")
    async def messagearchive_backfill(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | discord.Thread | None = None,
        limit: int = 0,
    ):
        """Backfill existing messages into the archive (0 = no limit / all history)."""
        channels = [channel] if channel is not None else list(ctx.guild.text_channels) + list(ctx.guild.threads)
        if not channels:
            await ctx.send("No text channels found in this server.")
            return

        if limit < 0:
            await ctx.send("Limit must be 0 or a positive number.")
            return

        archive_path = self._guild_db(ctx.guild.id)
        self._ensure_guild_db(ctx.guild.id)
        lock = self._guild_lock(ctx.guild.id)
        total_scanned = 0
        total_added = 0
        batch_size = 250
        unavailable_channels: list[str] = []

        for target in channels:
            perms = target.permissions_for(ctx.guild.me)
            if not perms.read_message_history:
                unavailable_channels.append(f"{target.mention} (no read history permission)")
                continue

            scan_limit = None if limit == 0 else limit
            buffer: list[dict[str, Any]] = []
            try:
                async for message in target.history(limit=scan_limit, oldest_first=True):
                    data = self._serialize_message(message)
                    total_scanned += 1
                    buffer.append(data)

                    if len(buffer) >= batch_size:
                        async with lock:
                            total_added += await asyncio.to_thread(self._insert_messages, archive_path, buffer)
                        buffer = []

                if buffer:
                    async with lock:
                        total_added += await asyncio.to_thread(self._insert_messages, archive_path, buffer)
            except (discord.Forbidden, discord.HTTPException):
                unavailable_channels.append(f"{target.mention} (history request failed)")

        message_parts = [f"Backfill complete. Scanned {total_scanned} messages, inserted {total_added} new messages."]
        if unavailable_channels:
            message_parts.append("Skipped channels: " + ", ".join(unavailable_channels))
        await ctx.send(" ".join(message_parts))

    @commands.guild_only()
    @messagearchive.command(name="status")
    async def messagearchive_status(self, ctx: commands.Context):
        """Show archiving status for this server."""
        enabled = await self.config.guild(ctx.guild).message_archive_enabled()
        path = self._guild_db(ctx.guild.id)
        rows, size_bytes = await asyncio.to_thread(self._db_stats, path)
        if path.exists():
            size_label = f"{size_bytes:,} bytes"
        else:
            size_label = "0 bytes"

        embed = discord.Embed(title="Message Archive", color=DEFAULT_COLOR)
        embed.add_field(name="Status", value="Enabled" if enabled else "Disabled", inline=True)
        embed.add_field(name="Archive File", value=f"`{path.name}`", inline=True)
        embed.add_field(name="Messages", value=f"{rows:,}", inline=True)
        embed.add_field(name="Size", value=size_label, inline=True)
        await ctx.send(embed=embed)

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @messagearchive.command(name="clear")
    async def messagearchive_clear(self, ctx: commands.Context):
        """Delete the current guild archive file."""
        path = self._guild_db(ctx.guild.id)
        if not path.exists():
            await ctx.send("No archive file found for this server.")
            return

        lock = self._guild_lock(ctx.guild.id)
        async with lock:
            await asyncio.to_thread(self._clear_db, path)
            self._initialized_db.discard(ctx.guild.id)
        await ctx.send("Message archive cleared for this server.")
