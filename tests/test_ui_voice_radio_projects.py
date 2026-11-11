import importlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock


ROOT = Path(__file__).parents[1]
HAS_RED = importlib.util.find_spec("discord") is not None and importlib.util.find_spec("redbot") is not None


def load(name):
    sys.path.insert(0, str(ROOT))
    return importlib.import_module(f"{name}.{name}")


def fake_cog(data):
    cog = MagicMock()
    cog.config.guild.return_value.all = AsyncMock(return_value=data)
    return cog


def fake_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.defer = AsyncMock()
    ctx.clean_prefix = "!"
    ctx.interaction = None
    return ctx


@unittest.skipUnless(HAS_RED, "needs Red-DiscordBot installed")
class MemberLayoutTests(unittest.IsolatedAsyncioTestCase):
    def assert_valid(self, view):
        self.assertTrue(view.to_components())
        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 4000)

    def sent_view(self, ctx):
        kwargs = ctx.send.await_args.kwargs
        self.assertNotIn("embed", kwargs)
        self.assertNotIn("content", kwargs)
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        view = kwargs["view"]
        self.assert_valid(view)
        return view

    def app_children(self, cls_command):
        return {child.name for child in cls_command.app_command.commands}

    async def test_layout_helpers_trim_to_limit(self):
        for name in ("messagearchive", "projectranking", "voicechannels"):
            with self.subTest(cog=name):
                self.assert_valid(load(name)._layout("a" * 3000, "b" * 3000, "c"))

    def test_slash_groups(self):
        expected = {
            ("messagearchive", "MessageArchive", "messagearchive"): {"backfill", "status", "clear"},
            ("projectranking", "ProjectRanking", "projectrank"): {"setup", "sync", "status", "ranking"},
            ("voicechannels", "VoiceChannels", "voicechannels"): {"help", "setup", "dashboard", "show", "cleanup", "history"},
        }
        for (module, cls, attr), children in expected.items():
            with self.subTest(cog=module):
                command = getattr(getattr(load(module), cls), attr)
                self.assertEqual(self.app_children(command), children)
                self.assertTrue(command.app_command.guild_only)

    def test_staff_only_groups_default_to_manage_server(self):
        for module, cls, attr in (("voicechannels", "VoiceChannels", "voicechannels"),):
            with self.subTest(cog=module):
                group = getattr(getattr(load(module), cls), attr).app_command
                self.assertTrue(group.default_permissions.manage_guild)

    def test_staff_subcommands_keep_their_own_checks(self):
        cases = {
            ("messagearchive", "MessageArchive"): ("messagearchive_backfill", "messagearchive_clear"),
            ("projectranking", "ProjectRanking"): ("projectrank_setup", "projectrank_sync"),
            ("voicechannels", "VoiceChannels"): ("voicechannels_setup", "voicechannels_cleanup"),
        }
        for (module, cls), names in cases.items():
            for name in names:
                with self.subTest(command=name):
                    command = getattr(getattr(load(module), cls), name)
                    self.assertTrue(command.requires.user_perms.manage_guild)

    def test_project_setup_uses_channel_options(self):
        # Slash integer options cannot hold a Discord ID.
        names = {p.name: p.type.name for p in load("projectranking").ProjectRanking.projectrank_setup.app_command.parameters}
        self.assertEqual(names, {"forum": "channel", "best_creations": "channel", "tag_name": "string"})

    async def test_status_commands_render(self):
        archive = load("messagearchive")
        cog = MagicMock()
        cog.config.guild.return_value.message_archive_enabled = AsyncMock(return_value=True)
        cog._guild_db.return_value = Path("/nonexistent/1.db")
        cog._db_stats = lambda path: (5, 10)
        ctx = fake_ctx()
        await archive.MessageArchive.messagearchive_status.callback(cog, ctx)
        self.sent_view(ctx)

    async def test_project_ranking_fits_with_long_titles(self):
        ranking = load("projectranking")
        week = ranking._week_key()
        posts = {
            str(i): {
                "eligible": True, "forum_id": 1, "week": week, "deleted": False, "upvotes": i, "downvotes": 0,
                "title": "*_" * 50, "jump_url": "https://discord.com/channels/" + "1" * 60, "created_at": "",
            }
            for i in range(25)
        }
        data = {"posts": posts, "forum_channel_id": 1}
        cog = fake_cog(data)
        cog._current_week_posts = lambda d: list(d["posts"].values())
        cog._sort_posts = lambda items: items
        ctx = fake_ctx()
        await ranking.ProjectRanking.projectrank_ranking.callback(cog, ctx)
        self.sent_view(ctx)

    async def test_dashboard_layout_keeps_persistent_button_ids(self):
        voice = load("voicechannels")
        classic = voice.VoiceDashboardView(MagicMock())
        layout = voice.VoiceDashboardLayout(MagicMock(), voice.VoiceChannels._dashboard_text())
        self.assert_valid(layout)
        ids = lambda view: {item.custom_id for item in view.walk_children() if hasattr(item, "custom_id")} if hasattr(view, "walk_children") else set()
        classic_ids = {item.custom_id for item in classic.children}
        self.assertEqual(ids(layout), classic_ids)
        self.assertEqual(len(classic_ids), 3)

    async def test_dashboard_buttons_gate_admin_action(self):
        voice = load("voicechannels")
        cog = MagicMock()
        cog._can_admin_manage.return_value = False
        cog.send_admin_channel_picker = AsyncMock()
        interaction = MagicMock()
        interaction.user = MagicMock(spec=voice.discord.Member)
        interaction.response.send_message = AsyncMock()
        await voice._run_dashboard_action(cog, interaction, "admin")
        cog.send_admin_channel_picker.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_owner_dashboard_renders(self):
        voice = load("voicechannels")
        channel = MagicMock()
        channel.mention = "<#1>"
        channel.user_limit = 0
        record = {"owner_id": 2, "trusted_ids": [], "blocked_ids": [3]}
        body = voice.VoiceChannels._owner_dashboard_text(channel, record, admin_viewer=MagicMock(mention="<@9>"))
        view = voice.VoiceOwnerDashboardView(MagicMock(), 2, 1, body, controller_id=9, admin_mode=True)
        self.assert_valid(view)
        self.assertEqual(sum(1 for item in view.walk_children() if isinstance(item, voice.discord.ui.Button)), 7)

    async def _panel_message(self, voice, is_v2):
        channel = MagicMock()
        channel.id = 1
        channel.guild = MagicMock()
        message = MagicMock()
        message.flags.components_v2 = is_v2
        message.edit = AsyncMock()
        message.delete = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
        cog = MagicMock()
        cog._channel_record = AsyncMock(return_value={"panel_message_id": 5, "owner_id": 2})
        cog._send_voice_channel_dashboard_message = AsyncMock(return_value=True)
        cog._owner_dashboard_view = MagicMock()
        await voice.VoiceChannels._refresh_voice_channel_dashboard_message(cog, channel)
        return cog, message

    async def test_classic_owner_panel_is_replaced_not_edited(self):
        voice = load("voicechannels")
        cog, message = await self._panel_message(voice, False)
        message.edit.assert_not_awaited()
        cog._send_voice_channel_dashboard_message.assert_awaited_once()
        message.delete.assert_awaited_once()

    async def test_new_owner_panel_is_edited_in_place(self):
        voice = load("voicechannels")
        cog, message = await self._panel_message(voice, True)
        message.edit.assert_awaited_once()
        cog._send_voice_channel_dashboard_message.assert_not_awaited()
