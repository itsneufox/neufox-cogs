import importlib
import inspect
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import MagicMock


ROOT = Path(__file__).parents[1]
MAX_TOP_LEVEL = 100
MAX_CHILDREN = 25
MAX_OPTIONS = 25
MAX_DESCRIPTION = 100
NAME = re.compile(r"^[-_\w]{1,32}$")

try:
    from discord import app_commands
    from discord.ext import commands as dpy

    HAS_RED = importlib.util.find_spec("redbot") is not None
except ImportError:
    HAS_RED = False


def cog_classes():
    sys.path.insert(0, str(ROOT))
    found = {}
    for info in sorted(ROOT.glob("*/info.json")):
        name = info.parent.name
        module = importlib.import_module(name)
        modules = [module] + [m for key, m in list(sys.modules.items()) if key.startswith(name + ".")]
        for mod in modules:
            for value in vars(mod).values():
                if inspect.isclass(value) and issubclass(value, dpy.Cog) and value.__module__.startswith(name):
                    found[value] = name
    # A class that is only a base of another cog registers through the subclass.
    return {cls: name for cls, name in found.items() if not any(o is not cls and issubclass(o, cls) for o in found)}


def top_level_commands(cls):
    commands = []
    for command in cls.__cog_commands__:
        if command.parent is None and getattr(command, "app_command", None) is not None:
            commands.append(command.app_command)
    commands.extend(cmd for cmd in getattr(cls, "__cog_app_commands__", []) if getattr(cmd, "parent", None) is None)
    return commands


def check_command(command, path, problems):
    label = f"{path}/{command.name}"
    if not NAME.match(command.name) or command.name != command.name.lower():
        problems.append(f"{label}: invalid name")
    description = getattr(command, "description", "")
    if not 1 <= len(description) <= MAX_DESCRIPTION:
        problems.append(f"{label}: description length {len(description)}")
    if isinstance(command, app_commands.Group):
        if len(command.commands) > MAX_CHILDREN:
            problems.append(f"{label}: {len(command.commands)} children")
        for child in command.commands:
            check_command(child, label, problems)
        return
    parameters = command.parameters
    if len(parameters) > MAX_OPTIONS:
        problems.append(f"{label}: {len(parameters)} options")
    optional_seen = False
    for parameter in parameters:
        if not NAME.match(parameter.name) or parameter.name != parameter.name.lower():
            problems.append(f"{label}: invalid option name {parameter.name!r}")
        if not 1 <= len(parameter.description) <= MAX_DESCRIPTION:
            problems.append(f"{label}: option {parameter.name} description length {len(parameter.description)}")
        if parameter.required and optional_seen:
            problems.append(f"{label}: required option {parameter.name} follows an optional one")
        optional_seen = optional_seen or not parameter.required


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class SlashLimitTests(unittest.TestCase):
    def test_bot_stays_within_discord_limits(self):
        names, problems = {}, []
        for cls, cog in cog_classes().items():
            for command in top_level_commands(cls):
                if command.name in names:
                    problems.append(f"/{command.name}: defined by both {names[command.name]} and {cog}")
                names[command.name] = cog
                check_command(command, cog, problems)
                try:
                    command.to_dict(MagicMock())
                except Exception as error:  # A payload that cannot be built would break the sync.
                    problems.append(f"/{command.name}: cannot build payload: {error!r}")
        self.assertLessEqual(len(names), MAX_TOP_LEVEL, sorted(names))
        self.assertEqual(problems, [], "\n".join(problems))
