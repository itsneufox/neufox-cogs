from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Final
from urllib.parse import urlencode, urlparse, urlunparse

import discord
from redbot.core import Config, commands


DEFAULT_COLOR = discord.Color.blue()
WEBHOOK_NAME = "EmbedFixer"
URL_RE = re.compile(r"https?://[^\s<>()|]+", re.IGNORECASE)
EMBEDEZ_NAME = "EmbedEZ"
EMBEDEZ_REPO_URL = "https://embedez.com"


class DomainId(IntEnum):
    TWITTER = 1
    PIXIV = 2
    TIKTOK = 3
    REDDIT = 4
    INSTAGRAM = 5
    FURAFFINITY = 6
    TWITCH_CLIPS = 7
    IWARA = 8
    BLUESKY = 9
    KEMONO = 10
    FACEBOOK = 11
    BILIBILI = 12
    TUMBLR = 13
    THREADS = 14
    PTT = 15
    DEVIANTART = 16


@dataclass(frozen=True, kw_only=True)
class ReplaceFix:
    old_domain: str
    new_domain: str


@dataclass(frozen=True, kw_only=True)
class AppendURLFix:
    domain: str


@dataclass(frozen=True, kw_only=True)
class FixMethod:
    id: int
    name: str
    fixes: list[ReplaceFix | AppendURLFix]
    repo_url: str | None = None
    default: bool = False
    has_ads: bool = False


@dataclass(frozen=True)
class Website:
    pattern: str
    skip_method_ids: list[int] | None = None

    def match(self, url: str) -> bool:
        return re.match(self.pattern, url) is not None


@dataclass(frozen=True, kw_only=True)
class Domain:
    id: DomainId
    name: str
    websites: list[Website]
    fix_methods: list[FixMethod]

    @property
    def default_fix_method(self) -> FixMethod | None:
        if not self.fix_methods:
            return None
        return next((method for method in self.fix_methods if method.default), self.fix_methods[0])

    def get_fix_method(self, fix_id: int) -> FixMethod | None:
        return next((method for method in self.fix_methods if method.id == fix_id), None)


@dataclass(frozen=True)
class MatchedURL:
    start: int
    end: int
    original: str
    clean: str
    domain: Domain
    website: Website


DOMAINS: Final[list[Domain]] = [
    Domain(
        id=DomainId.TWITTER,
        name="Twitter/X",
        websites=[
            Website(r"https://(www\.)?twitter\.com/[a-zA-Z0-9_]+/status/\d+(/photo|/video/\d+)?/?"),
            Website(r"https://(www\.)?x\.com/[a-zA-Z0-9_]+/status/\d+(/photo|/video/\d+)?/?"),
        ],
        fix_methods=[
            FixMethod(
                id=1,
                name="FxEmbed",
                fixes=[
                    ReplaceFix(old_domain="twitter.com", new_domain="fxtwitter.com"),
                    ReplaceFix(old_domain="x.com", new_domain="fixupx.com"),
                ],
                repo_url="https://github.com/FxEmbed/FxEmbed",
                default=True,
            ),
            FixMethod(
                id=2,
                name="BetterTwitFix",
                fixes=[
                    ReplaceFix(old_domain="twitter.com", new_domain="vxtwitter.com"),
                    ReplaceFix(old_domain="x.com", new_domain="fixvx.com"),
                ],
                repo_url="https://github.com/dylanpdx/BetterTwitFix",
            ),
            FixMethod(
                id=29,
                name=EMBEDEZ_NAME,
                fixes=[
                    ReplaceFix(old_domain="twitter.com", new_domain="xeezz.com"),
                    ReplaceFix(old_domain="x.com", new_domain="xeezz.com"),
                ],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            ),
            FixMethod(
                id=36,
                name="HitlerX",
                fixes=[
                    ReplaceFix(old_domain="twitter.com", new_domain="hitlerx.com"),
                    ReplaceFix(old_domain="x.com", new_domain="hitlerx.com"),
                ],
            ),
            FixMethod(
                id=37,
                name="GirlCockX",
                fixes=[
                    ReplaceFix(old_domain="twitter.com", new_domain="girlcockx.com"),
                    ReplaceFix(old_domain="x.com", new_domain="girlcockx.com"),
                ],
            ),
        ],
    ),
    Domain(
        id=DomainId.PIXIV,
        name="Pixiv",
        websites=[Website(r"https://(www\.)?pixiv\.net(/[a-zA-Z]+)?/artworks/\d+/?")],
        fix_methods=[
            FixMethod(
                id=3,
                name="Phixiv",
                fixes=[ReplaceFix(old_domain="pixiv.net", new_domain="phixiv.net")],
                repo_url="https://github.com/thelaao/phixiv",
                default=True,
            )
        ],
    ),
    Domain(
        id=DomainId.TIKTOK,
        name="TikTok",
        websites=[
            Website(r"https://(www\.)?tiktok\.com/(t/\w+|@[\w.]+/video/\d+)/?"),
            Website(r"https://vm\.tiktok\.com/\w+/?"),
            Website(r"https://vt\.tiktok\.com/\w+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=4,
                name="fxTikTok",
                fixes=[
                    ReplaceFix(old_domain="tiktok.com", new_domain="tnktok.com"),
                    ReplaceFix(old_domain="vm.tiktok.com", new_domain="tnktok.com"),
                    ReplaceFix(old_domain="vt.tiktok.com", new_domain="tnktok.com"),
                ],
                repo_url="https://github.com/okdargy/fxTikTok",
                default=True,
            ),
            FixMethod(
                id=27,
                name=EMBEDEZ_NAME,
                fixes=[
                    ReplaceFix(old_domain="tiktok.com", new_domain="tiktokez.com"),
                    ReplaceFix(old_domain="vm.tiktok.com", new_domain="tiktokez.com"),
                    ReplaceFix(old_domain="vt.tiktok.com", new_domain="tiktokez.com"),
                ],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            ),
            FixMethod(
                id=31,
                name="KKTikTok",
                fixes=[
                    ReplaceFix(old_domain="tiktok.com", new_domain="kktiktok.com"),
                    ReplaceFix(old_domain="vm.tiktok.com", new_domain="kktiktok.com"),
                    ReplaceFix(old_domain="vt.tiktok.com", new_domain="kktiktok.com"),
                ],
                repo_url="https://kkscript.com/",
                has_ads=True,
            ),
        ],
    ),
    Domain(
        id=DomainId.REDDIT,
        name="Reddit",
        websites=[
            Website(r"https://(www\.|old\.)?reddit\.com/r/[\w]+/comments/[\w]+/[\w]+/?"),
            Website(r"https://(www\.|old\.)?reddit\.com/r/[\w]+/s/[\w]+/?"),
            Website(r"https://(www\.|old\.)?reddit\.com/user/[\w]+/comments/[\w]+/[\w]+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=6,
                name="FixReddit",
                fixes=[ReplaceFix(old_domain="reddit.com", new_domain="fxreddit.seria.moe")],
                repo_url="https://github.com/MinnDevelopment/fxreddit",
                default=True,
            ),
            FixMethod(
                id=7,
                name="vxReddit",
                fixes=[ReplaceFix(old_domain="reddit.com", new_domain="vxreddit.com")],
                repo_url="https://github.com/dylanpdx/vxReddit",
            ),
            FixMethod(
                id=26,
                name=EMBEDEZ_NAME,
                fixes=[ReplaceFix(old_domain="reddit.com", new_domain="redditez.com")],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            ),
        ],
    ),
    Domain(
        id=DomainId.INSTAGRAM,
        name="Instagram",
        websites=[
            Website(r"https://(www\.)?instagram\.com/share/[\w]+/?", skip_method_ids=[8]),
            Website(r"https://(www\.)?instagram\.com/(p|reels?)/[\w]+/?"),
            Website(r"https://(www\.)?instagram\.com/share/(p|reels?)/[\w]+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=8,
                name="InstaFix",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="eeinstagram.com")],
                repo_url="https://github.com/Wikidepia/InstaFix",
            ),
            FixMethod(
                id=9,
                name=EMBEDEZ_NAME,
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="g.embedez.com")],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            ),
            FixMethod(
                id=23,
                name="KKInstagram",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="kkinstagram.com")],
                repo_url="https://kkscript.com/",
                has_ads=True,
            ),
            FixMethod(
                id=34,
                name="vxinstagram",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="fxig.seria.moe")],
                repo_url="https://github.com/Lainmode/InstagramEmbed-vxinstagram",
                default=True,
            ),
            FixMethod(
                id=35,
                name="InstaEmbedRouter",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="zzinstagram.com")],
                repo_url="https://github.com/Knoppiix/InstaEmbedRouter",
            ),
        ],
    ),
    Domain(
        id=DomainId.FURAFFINITY,
        name="FurAffinity",
        websites=[Website(r"https://(www\.)?furaffinity\.net/view/\d+/?")],
        fix_methods=[
            FixMethod(
                id=10,
                name="xfuraffinity",
                fixes=[ReplaceFix(old_domain="furaffinity.net", new_domain="xfuraffinity.net")],
                repo_url="https://github.com/FirraWoof/xfuraffinity",
                default=True,
            ),
            FixMethod(
                id=28,
                name="fxraffinity",
                fixes=[ReplaceFix(old_domain="furaffinity.net", new_domain="fxraffinity.net")],
                repo_url="https://fxraffinity.net/",
            ),
        ],
    ),
    Domain(
        id=DomainId.TWITCH_CLIPS,
        name="Twitch Clips",
        websites=[
            Website(r"https://m\.twitch\.tv/clip/[\w]+/?"),
            Website(r"https://clips\.twitch\.tv/[\w]+/?"),
            Website(r"https://(www\.)?twitch\.tv/[\w]+/clip/[\w]+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=11,
                name="fxtwitch",
                fixes=[
                    ReplaceFix(old_domain="clips.twitch.tv", new_domain="fxtwitch.seria.moe/clip"),
                    ReplaceFix(old_domain="m.twitch.tv", new_domain="fxtwitch.seria.moe"),
                    ReplaceFix(old_domain="twitch.tv", new_domain="fxtwitch.seria.moe"),
                ],
                repo_url="https://github.com/seriaati/fxtwitch",
                default=True,
            )
        ],
    ),
    Domain(
        id=DomainId.IWARA,
        name="Iwara",
        websites=[Website(r"https://(www\.)?iwara\.tv/video/[\w]+/[\w]+/?")],
        fix_methods=[
            FixMethod(
                id=12,
                name="fxiwara",
                fixes=[ReplaceFix(old_domain="iwara.tv", new_domain="fxiwara.seria.moe")],
                repo_url="https://github.com/seriaati/fxiwara",
                default=True,
            )
        ],
    ),
    Domain(
        id=DomainId.BLUESKY,
        name="Bluesky",
        websites=[Website(r"https://(www\.)?bsky\.app/profile/[\w.-]+/post/[\w]+/?")],
        fix_methods=[
            FixMethod(
                id=13,
                name="VixBluesky",
                fixes=[ReplaceFix(old_domain="bsky.app", new_domain="bskx.app")],
                repo_url="https://github.com/Lexedia/VixBluesky",
                default=True,
            ),
            FixMethod(
                id=14,
                name="FxEmbed",
                fixes=[ReplaceFix(old_domain="bsky.app", new_domain="fxbsky.app")],
                repo_url="https://github.com/FxEmbed/FxEmbed",
            ),
        ],
    ),
    Domain(
        id=DomainId.KEMONO,
        name="Kemono",
        websites=[Website(r"https://(www\.)?kemono\.su/[a-zA-Z0-9_]+/user/[\w]+/post/[\w]+/?")],
        fix_methods=[],
    ),
    Domain(
        id=DomainId.FACEBOOK,
        name="Facebook",
        websites=[
            Website(r"https://(www\.)?facebook\.com/share/r/[\w]+/?"),
            Website(r"https://(www\.)?facebook\.com/reel/\d+/?"),
            Website(r"https://(www\.)?facebook\.com/share/v/[\w]+/?"),
            Website(r"https://(www\.)?facebook\.com/(.*)", skip_method_ids=[15]),
        ],
        fix_methods=[
            FixMethod(
                id=15,
                name=EMBEDEZ_NAME,
                fixes=[ReplaceFix(old_domain="facebook.com", new_domain="facebookez.com")],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            ),
            FixMethod(
                id=16,
                name="fxfacebook",
                fixes=[ReplaceFix(old_domain="facebook.com", new_domain="fxfb.seria.moe")],
                repo_url="https://github.com/seriaati/fxfacebook",
            ),
            FixMethod(
                id=25,
                name="facebed",
                fixes=[ReplaceFix(old_domain="facebook.com", new_domain="facebed.seria.moe")],
                repo_url="https://github.com/4pii4/facebed",
                default=True,
            ),
        ],
    ),
    Domain(
        id=DomainId.BILIBILI,
        name="Bilibili",
        websites=[
            Website(r"https://(www\.|m\.)?bilibili\.com/video/[\w]+/?"),
            Website(r"https://(www\.)?b23\.tv/[\w]+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=17,
                name="fxbilibili",
                fixes=[
                    ReplaceFix(old_domain="m.bilibili.com", new_domain="fxbilibili.seria.moe"),
                    ReplaceFix(old_domain="bilibili.com", new_domain="fxbilibili.seria.moe"),
                    ReplaceFix(old_domain="b23.tv", new_domain="fxbilibili.seria.moe/b23"),
                ],
                repo_url="https://github.com/seriaati/fxbilibili",
                default=True,
            ),
            FixMethod(
                id=18,
                name=EMBEDEZ_NAME,
                fixes=[ReplaceFix(old_domain="bilibili.com", new_domain="bilibiliez.com")],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            ),
            FixMethod(
                id=22,
                name="BiliFix",
                fixes=[
                    ReplaceFix(old_domain="m.bilibili.com", new_domain="vxbilibili.com"),
                    ReplaceFix(old_domain="bilibili.com", new_domain="vxbilibili.com"),
                    ReplaceFix(old_domain="b23.tv", new_domain="vxb23.tv"),
                ],
                repo_url="https://vxbilibili.com",
            ),
        ],
    ),
    Domain(
        id=DomainId.TUMBLR,
        name="Tumblr",
        websites=[
            Website(r"https://(www\.)?tumblr\.com/[a-zA-Z0-9_-]+/[0-9]+/?([a-zA-Z0-9_-]+/?)?")
        ],
        fix_methods=[
            FixMethod(
                id=19,
                name="fxtumblr",
                fixes=[ReplaceFix(old_domain="tumblr.com", new_domain="tpmblr.com")],
                repo_url="https://github.com/knuxify/fxtumblr",
                default=True,
            )
        ],
    ),
    Domain(
        id=DomainId.THREADS,
        name="Threads",
        websites=[
            Website(r"https://(www\.)?threads\.(net|com)/@[\w.]+/?"),
            Website(r"https://(www\.)?threads\.(net|com)/@[\w.]+/post/[\w]+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=20,
                name="FixThreads",
                fixes=[
                    ReplaceFix(old_domain="threads.net", new_domain="fixthreads.seria.moe"),
                    ReplaceFix(old_domain="threads.com", new_domain="fixthreads.seria.moe"),
                ],
                repo_url="https://github.com/milanmdev/fixthreads",
                default=True,
            ),
            FixMethod(
                id=21,
                name="vxThreads",
                fixes=[
                    ReplaceFix(old_domain="threads.net", new_domain="vxthreads.net"),
                    ReplaceFix(old_domain="threads.com", new_domain="vxthreads.net"),
                ],
                repo_url="https://github.com/everettsouthwick/vxThreads",
            ),
            FixMethod(
                id=30,
                name=EMBEDEZ_NAME,
                fixes=[
                    ReplaceFix(old_domain="threads.net", new_domain="threadsez.com"),
                    ReplaceFix(old_domain="threads.com", new_domain="threadsez.com"),
                ],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            ),
            FixMethod(
                id=33,
                name="FixEmbed",
                fixes=[AppendURLFix(domain="fixembed.app/embed")],
                repo_url="https://fixembed.app",
            ),
        ],
    ),
    Domain(
        id=DomainId.PTT,
        name="PTT",
        websites=[Website(r"https://(www\.)?ptt\.cc/bbs/[A-Za-z0-9_]+/M\.\d+\.A\.[A-Z0-9]+\.html/?")],
        fix_methods=[
            FixMethod(
                id=24,
                name="fxptt",
                fixes=[ReplaceFix(old_domain="ptt.cc", new_domain="fxptt.seria.moe")],
                repo_url="https://github.com/seriaati/fxptt",
            )
        ],
    ),
    Domain(
        id=DomainId.DEVIANTART,
        name="DeviantArt",
        websites=[Website(r"https://(www\.)?deviantart\.com/[\w.-]+/art/[\w.-]+-\d+/?")],
        fix_methods=[
            FixMethod(
                id=32,
                name="fxdeviantart",
                fixes=[ReplaceFix(old_domain="deviantart.com", new_domain="fixdeviantart.com")],
                repo_url="https://github.com/Tschrock/fixdeviantart",
                default=True,
            )
        ],
    ),
]


def _domain_in_url(url: str, domain: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    domain = domain.lower()
    return host == domain or host.endswith(f".{domain}")


def _split_new_domain(new_domain: str) -> tuple[str, str]:
    if "/" not in new_domain:
        return new_domain, ""
    host, path = new_domain.split("/", 1)
    return host, "/" + path.strip("/")


def _replace_domain(url: str, old_domain: str, new_domain: str) -> str:
    if not _domain_in_url(url, old_domain):
        return url

    parsed = urlparse(url)
    host, prefix = _split_new_domain(new_domain)
    path = parsed.path
    if prefix:
        path = f"{prefix}{path if path.startswith('/') else '/' + path}"
    return urlunparse(parsed._replace(netloc=host, path=path))


def _clean_url(url: str) -> str:
    url = url.rstrip(".,!?;:")
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _apply_fix(url: str, domain: Domain, fix_method: FixMethod) -> str | None:
    for fix in fix_method.fixes:
        if isinstance(fix, AppendURLFix):
            new_url = f"https://{fix.domain}?{urlencode({'url': url})}"
            if domain.id == DomainId.FACEBOOK:
                new_url = new_url.replace("/v/", "/r/")
            return new_url

        if _domain_in_url(url, fix.old_domain):
            return _replace_domain(url, fix.old_domain, fix.new_domain)

    return None


def _method_for_index(domain: Domain, index: int, website: Website) -> FixMethod | None:
    methods = [method for method in domain.fix_methods if not website.skip_method_ids or method.id not in website.skip_method_ids]
    if not methods:
        return None

    default = domain.default_fix_method
    if default is not None and default in methods:
        default_index = methods.index(default)
        methods = methods[default_index:] + methods[:default_index]

    return methods[index % len(methods)]


def _has_alternatives(matches: list[MatchedURL]) -> bool:
    for match in matches:
        methods = [
            method
            for method in match.domain.fix_methods
            if not match.website.skip_method_ids or method.id not in match.website.skip_method_ids
        ]
        if len(methods) > 1:
            return True
    return False


def _rewrite_content(content: str, matches: list[MatchedURL], rotations: dict[int, int]) -> tuple[str, list[str]]:
    fixed_urls: list[str] = []

    for match in matches:
        method = _method_for_index(match.domain, rotations.get(int(match.domain.id), 0), match.website)
        fixed_url = _apply_fix(match.clean, match.domain, method) if method is not None else None
        if fixed_url is None:
            fixed_url = match.original
        fixed_urls.append(fixed_url)

    rewritten = "\n".join(fixed_urls)
    if len(rewritten) <= 2000:
        return rewritten, fixed_urls

    return rewritten[:2000], fixed_urls


class TryOtherView(discord.ui.View):
    def __init__(self, content: str, matches: list[MatchedURL], rotations: dict[int, int] | None = None):
        super().__init__(timeout=86400)
        self.content = content
        self.matches = matches
        self.rotations = rotations or {}
        if not _has_alternatives(matches):
            for child in self.children:
                child.disabled = True

    @discord.ui.button(label="Try other", style=discord.ButtonStyle.secondary)
    async def try_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        for match in self.matches:
            methods = [
                method
                for method in match.domain.fix_methods
                if not match.website.skip_method_ids or method.id not in match.website.skip_method_ids
            ]
            if len(methods) > 1:
                key = int(match.domain.id)
                self.rotations[key] = self.rotations.get(key, 0) + 1

        rewritten, _ = _rewrite_content(self.content, self.matches, self.rotations)
        await interaction.response.edit_message(content=rewritten, view=self)


class EmbedFixer(commands.Cog):
    """Fix social media embeds through webhook reposts."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=728466531)
        self.config.register_guild(enabled=True)
        self.config.register_member(opted_out=False)

    @commands.group(name="embedfixer", aliases=["efix"], invoke_without_command=True)
    @commands.guild_only()
    async def embedfixer(self, ctx: commands.Context):
        """Show EmbedFixer settings."""
        enabled = await self.config.guild(ctx.guild).enabled()
        embed = discord.Embed(title="EmbedFixer", color=DEFAULT_COLOR)
        embed.add_field(name="Automatic fixing", value="Enabled" if enabled else "Disabled", inline=True)
        if isinstance(ctx.author, discord.Member):
            opted_out = await self.config.member(ctx.author).opted_out()
            embed.add_field(name="Your opt-out", value="On" if opted_out else "Off", inline=True)
        embed.add_field(name="Behavior", value="Suppress original embeds and repost only fixed links through a webhook.", inline=False)
        embed.add_field(name="Fallbacks", value="Webhook posts include a Try other button when another service exists.", inline=False)
        embed.add_field(
            name="Opt out",
            value=(
                f"`{ctx.clean_prefix}embedfixer optout` stops automatic fixes for your messages here.\n"
                "For one message, prefix a link with `$` or wrap it in `<...>`."
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @embedfixer.command(name="on")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def embedfixer_on(self, ctx: commands.Context):
        """Enable automatic embed fixing."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("EmbedFixer automatic fixing enabled.")

    @embedfixer.command(name="off")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def embedfixer_off(self, ctx: commands.Context):
        """Disable automatic embed fixing."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("EmbedFixer automatic fixing disabled.")

    @embedfixer.command(name="optout", aliases=["ignoreme"])
    @commands.guild_only()
    async def embedfixer_optout(self, ctx: commands.Context):
        """Opt out of automatic embed fixing for your messages in this server."""
        await self.config.member(ctx.author).opted_out.set(True)
        await ctx.send("EmbedFixer will ignore your messages in this server.")

    @embedfixer.command(name="optin", aliases=["unignoreme"])
    @commands.guild_only()
    async def embedfixer_optin(self, ctx: commands.Context):
        """Opt back in to automatic embed fixing for your messages in this server."""
        await self.config.member(ctx.author).opted_out.set(False)
        await ctx.send("EmbedFixer will fix your supported links again in this server.")

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.guild is None or not message.content:
            return
        if message.author.bot or message.webhook_id is not None:
            return
        if not await self.config.guild(message.guild).enabled():
            return
        if not isinstance(message.author, discord.Member):
            return
        if await self.config.member(message.author).opted_out():
            return

        matches = self._find_matches(message.content)
        if not matches:
            return

        rewritten, _ = _rewrite_content(message.content, matches, {})
        if rewritten == message.content:
            return

        view = TryOtherView(message.content, matches) if _has_alternatives(matches) else None
        sent = await self._send_webhook_copy(message, rewritten, view=view)
        if sent:
            await self._suppress_original_embeds(message)

    def _find_matches(self, content: str) -> list[MatchedURL]:
        matches: list[MatchedURL] = []

        for match in URL_RE.finditer(content):
            raw_url = match.group(0).rstrip(".,!?;:")
            start = match.start()
            end = match.start() + len(raw_url)

            if start > 0 and content[start - 1] == "$":
                continue
            if start > 0 and content[start - 1] == "<" and end < len(content) and content[end] == ">":
                continue

            clean_url = _clean_url(raw_url)
            domain, website = self._matching_domain(clean_url)
            if domain is None or website is None or not domain.fix_methods:
                continue

            matches.append(MatchedURL(start, end, raw_url, clean_url, domain, website))

        return matches

    @staticmethod
    def _matching_domain(url: str) -> tuple[Domain | None, Website | None]:
        for domain in DOMAINS:
            for website in domain.websites:
                if website.match(url):
                    return domain, website
        return None, None

    async def _send_webhook_copy(
        self,
        message: discord.Message,
        content: str,
        *,
        view: discord.ui.View | None = None,
    ) -> bool:
        channel = message.channel
        me = message.guild.me
        if me is None or not hasattr(channel, "permissions_for"):
            return False

        permissions = channel.permissions_for(me)
        if not permissions.send_messages:
            return False

        username = getattr(message.author, "display_name", str(message.author))[:80]
        avatar_url = message.author.display_avatar.url
        allowed_mentions = discord.AllowedMentions.none()

        webhook, thread = await self._get_webhook_for_channel(channel)
        if webhook is not None:
            try:
                kwargs = {
                    "content": content,
                    "username": username,
                    "avatar_url": avatar_url,
                    "allowed_mentions": allowed_mentions,
                    "view": view,
                    "wait": True,
                }
                if thread is not None:
                    kwargs["thread"] = thread
                await webhook.send(**kwargs)
                return True
            except (discord.Forbidden, discord.HTTPException, TypeError):
                pass

        try:
            await channel.send(content=content, allowed_mentions=allowed_mentions, view=view)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _get_webhook_for_channel(
        self, channel: discord.abc.Messageable
    ) -> tuple[discord.Webhook | None, discord.Thread | None]:
        thread = channel if isinstance(channel, discord.Thread) else None
        webhook_channel = channel.parent if thread is not None else channel

        if (
            webhook_channel is None
            or not hasattr(webhook_channel, "webhooks")
            or not hasattr(webhook_channel, "create_webhook")
            or not hasattr(webhook_channel, "permissions_for")
        ):
            return None, thread

        me = webhook_channel.guild.me
        if me is None:
            return None, thread

        permissions = webhook_channel.permissions_for(me)
        if not permissions.manage_webhooks:
            return None, thread

        try:
            webhooks = await webhook_channel.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            return None, thread

        for webhook in webhooks:
            if webhook.name == WEBHOOK_NAME and webhook.user == self.bot.user:
                return webhook, thread

        try:
            webhook = await webhook_channel.create_webhook(name=WEBHOOK_NAME, reason="EmbedFixer repost webhook")
        except (discord.Forbidden, discord.HTTPException):
            return None, thread
        return webhook, thread

    @staticmethod
    async def _suppress_original_embeds(message: discord.Message) -> None:
        me = message.guild.me
        if me is None or not hasattr(message.channel, "permissions_for"):
            return

        permissions = message.channel.permissions_for(me)
        if not permissions.manage_messages:
            return

        try:
            await message.edit(suppress=True)
        except (discord.Forbidden, discord.HTTPException):
            return
