"""Command toggles and owner tools for the control panel.

Command toggles use the same storage as Red's ``[p]command disable server``, so the
panel and the commands always agree. Owner tools wrap Red's own load/unload/reload
and Downloader helpers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timezone
from types import SimpleNamespace
from typing import Any

import discord

log = logging.getLogger("red.neufox.control")

SYNC_COOLDOWN = 120
MAX_ERROR_CHARS = 3500
_last_sync = 0.0


# ----- commands -----------------------------------------------------------------


async def member_level(bot, member: discord.Member):
    """The member's Red privilege level, computed the way Red does for a command context."""
    from redbot.core.commands.requires import PrivilegeLevel

    return await PrivilegeLevel.from_ctx(SimpleNamespace(bot=bot, author=member, guild=member.guild))


def _is_command_manager(bot, command) -> bool:
    core = bot.get_cog("Core")
    manager = getattr(core, "command_manager", None)
    if manager is None:
        return False
    return command is manager or manager in getattr(command, "parents", ())


def lock_reason(bot, command, level) -> str | None:
    """Why this member can't toggle the command, or None when they can."""
    from redbot.core.commands.commands import _RuleDropper

    if _is_command_manager(bot, command):
        return "Red's command manager can't be disabled."
    if isinstance(command, _RuleDropper):
        return "Always available."
    required = getattr(getattr(command, "requires", None), "privilege_level", None)
    if required is not None and required > level:
        return f"Needs {required.name.replace('_', ' ').title()}."
    return None


def cog_commands(cog) -> list:
    """Every prefix/hybrid command of a cog, groups and subcommands included, sorted by name."""
    return sorted(cog.walk_commands(), key=lambda c: c.qualified_name)


def is_disabled(command, guild: discord.Guild) -> bool:
    return not command.is_enabled(guild)


async def set_command_enabled(bot, guild: discord.Guild, command, enabled: bool) -> bool:
    """Enable or disable a command in one server. Returns whether anything changed."""
    name = command.qualified_name
    async with bot._config.guild(guild).disabled_commands() as disabled:
        if enabled:
            while name in disabled:
                disabled.remove(name)
        elif name not in disabled:
            disabled.append(name)
    return command.enable_in(guild) if enabled else command.disable_in(guild)


# ----- owner tools --------------------------------------------------------------


def top_package(module: str) -> str:
    return module.split(".", 1)[0]


async def package_names(bot) -> tuple[list[str], list[str]]:
    """Loaded packages and installed-but-unloaded packages, each sorted."""
    loaded = sorted({top_package(name) for name in bot.extensions})
    available: set[str] = set()
    manager = getattr(bot, "_cog_mgr", None)
    if manager is not None:
        try:
            available = set(await manager.available_modules())
        except Exception:
            log.exception("Could not list available cog packages.")
    return loaded, sorted(available - set(loaded))


def _summarize(result: dict[str, Any]) -> str:
    labels = {
        "loaded_packages": "Loaded",
        "unloaded_packages": "Unloaded",
        "alreadyloaded_packages": "Already loaded",
        "notloaded_packages": "Not loaded",
        "notfound_packages": "Not found",
        "invalid_pkg_names": "Invalid name",
        "failed_packages": "Failed (see Last error)",
    }
    parts = [f"{label}: {', '.join(result[key])}" for key, label in labels.items() if result.get(key)]
    for name, reason in (result.get("failed_with_reason_packages") or {}).items():
        parts.append(f"Failed {name}: {reason}")
    return "\n".join(parts) or "Nothing changed."


async def manage_package(bot, action: str, package: str) -> str:
    """Run Red's own load/unload/reload for one package and describe the outcome."""
    core = bot.get_cog("Core")
    if core is None:
        return "Red's Core cog isn't available."
    if action == "load":
        result = await core._load([package])
    elif action == "unload":
        result = await core._unload([package])
    elif action == "reload":
        result = await core._reload([package])
    else:
        raise ValueError(action)
    return _summarize(result)


def sync_wait(now: float | None = None) -> int:
    """Seconds left before another sync is allowed, or 0."""
    now = time.monotonic() if now is None else now
    return max(0, int(_last_sync + SYNC_COOLDOWN - now)) if _last_sync else 0


async def sync_commands(bot) -> str:
    global _last_sync
    wait = sync_wait()
    if wait:
        return f"Synced recently. Discord rate-limits syncing, so try again in {wait}s."
    _last_sync = time.monotonic()
    commands = await bot.tree.sync()
    return f"Synced {len(commands)} slash commands. Restart Discord (Ctrl+R) to see changes."


def last_error(bot) -> str:
    text = getattr(bot, "_last_exception", None)
    if not text:
        return "No errors recorded since the bot started."
    text = text if len(text) <= MAX_ERROR_CHARS else "…" + text[-MAX_ERROR_CHARS:]
    return f"```py\n{text}\n```"


def repo_names(bot) -> list[str]:
    downloader = bot.get_cog("Downloader")
    if downloader is None:
        return []
    return sorted(repo.name for repo in downloader._repo_manager.repos)


@dataclass
class UpdateReport:
    updated: list[str] = field(default_factory=list)
    skipped_pinned: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reload_summary: str = ""
    error: str = ""

    def text(self) -> str:
        if self.error:
            return f"⚠️ {self.error}"
        lines = [f"Reinstalled: {', '.join(self.updated) or 'nothing'}"]
        if self.skipped_pinned:
            lines.append(f"Pinned, skipped: {', '.join(self.skipped_pinned)}")
        if self.failed:
            lines.append(f"Failed: {', '.join(self.failed)}")
        if self.reload_summary:
            lines.append(self.reload_summary)
        return "\n".join(lines)


async def force_update_repo(bot, repo_name: str) -> UpdateReport:
    """Pull a repo, reinstall every installed cog from it and reload the loaded ones.

    Unlike ``[p]cog update`` this does not rely on Downloader's saved commit history,
    which can wrongly report "already up to date". Pinned cogs are left alone.
    """
    report = UpdateReport()
    downloader = bot.get_cog("Downloader")
    if downloader is None:
        report.error = "The Downloader cog isn't loaded."
        return report
    repo = downloader._repo_manager.get_repo(repo_name)
    if repo is None:
        report.error = f"No repo named {repo_name}."
        return report
    try:
        await repo.update()
    except Exception as error:
        log.exception("Could not update repo %s.", repo_name)
        report.error = f"Could not pull {repo_name}: {error}"
        return report

    installed = [cog for cog in await downloader.installed_cogs() if cog.repo_name == repo_name]
    report.skipped_pinned = sorted(cog.name for cog in installed if cog.pinned)
    wanted = {cog.name for cog in installed if not cog.pinned}
    fresh = [module for module in repo.available_cogs if module.name in wanted]
    report.failed = sorted(wanted - {module.name for module in fresh})

    failed_reqs = await downloader._install_requirements(fresh)
    if failed_reqs:
        report.error = f"Could not install requirements: {', '.join(failed_reqs)}"
        return report
    done, failed = await downloader._install_cogs(fresh)
    await downloader._save_to_installed(done)
    report.updated = sorted(module.name for module in done)
    report.failed = sorted(set(report.failed) | {module.name for module in failed})

    to_reload = [name for name in report.updated if name in bot.extensions]
    if to_reload:
        core = bot.get_cog("Core")
        report.reload_summary = _summarize(await core._reload(to_reload)) if core else ""
    return report


def bot_status(bot) -> str:
    try:
        import redbot

        red_version = getattr(redbot, "__version__", "?")
    except Exception:
        red_version = "?"
    latency = getattr(bot, "latency", float("nan"))
    latency_text = f"{latency * 1000:.0f} ms" if latency == latency else "?"
    uptime = getattr(bot, "uptime", None)  # Red stores a naive UTC datetime
    started = f"<t:{int(uptime.replace(tzinfo=timezone.utc).timestamp())}:R>" if uptime else "?"
    return "\n".join(
        [
            f"**Red** {red_version} · **discord.py** {discord.__version__}",
            f"**Latency:** {latency_text} · **Started:** {started}",
            f"**Servers:** {len(bot.guilds)} · **Cogs loaded:** {len(bot.cogs)}",
        ]
    )
