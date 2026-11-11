"""Supported sites and fixer services for EmbedFixer. Pure data and URL helpers, no Discord imports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Final
from urllib.parse import urlencode, urlparse, urlunparse


URL_RE = re.compile(r"https?://[^\s<>()|]+", re.IGNORECASE)
MAX_CONTENT = 2000
EMBEDEZ_NAME = "EmbedEZ"
EMBEDEZ_REPO_URL = "https://embedez.com"
FXEMBED_TWITTER_ID = 1
# ISO 639-1 codes FxEmbed accepts for post translation (appended as /<code>).
TRANSLATE_LANGUAGES: Final[dict[str, str]] = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "az": "Azerbaijani", "be": "Belarusian",
    "bg": "Bulgarian", "bn": "Bengali", "bs": "Bosnian", "ca": "Catalan", "cs": "Czech", "cy": "Welsh",
    "da": "Danish", "de": "German", "el": "Greek", "en": "English", "eo": "Esperanto", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fil": "Filipino", "fr": "French",
    "ga": "Irish", "gl": "Galician", "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian", "is": "Icelandic", "it": "Italian",
    "ja": "Japanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer", "kn": "Kannada", "ko": "Korean",
    "lo": "Lao", "lt": "Lithuanian", "lv": "Latvian", "mk": "Macedonian", "ml": "Malayalam",
    "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "my": "Burmese", "ne": "Nepali", "nl": "Dutch",
    "no": "Norwegian", "pa": "Punjabi", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian",
    "ru": "Russian", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian", "sr": "Serbian",
    "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese", "zh": "Chinese",
}


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
    BILIBILI_OPUS = 17
    PINTEREST = 18
    YOUTUBE = 19


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
    enabled_by_default: bool = True

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
                id=1001,
                name="HitlerX",
                fixes=[
                    ReplaceFix(old_domain="twitter.com", new_domain="hitlerx.com"),
                    ReplaceFix(old_domain="x.com", new_domain="hitlerx.com"),
                ],
            ),
            FixMethod(
                id=1002,
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
            Website(r"https://(www\.)?tiktok\.com/(t/\w+|@[\w.]+/(video|photo)/\d+)/?"),
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
            Website(
                r"https://(www\.)?instagram\.com/stories/[\w.]+/\d+/?",
                skip_method_ids=[8, 9, 23, 34, 35],
            ),
        ],
        fix_methods=[
            FixMethod(
                id=23,
                name="KKInstagram",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="kkinstagram.com")],
                repo_url="https://kkscript.com/",
                has_ads=True,
            ),
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
                id=34,
                name="vxinstagram",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="fxig.seria.moe")],
                repo_url="https://github.com/Lainmode/InstagramEmbed-vxinstagram",
            ),
            FixMethod(
                id=35,
                name="InstaEmbedRouter",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="zzinstagram.com")],
                repo_url="https://github.com/Knoppiix/InstaEmbedRouter",
            ),
            FixMethod(
                id=37,
                name="OGInstagram",
                fixes=[ReplaceFix(old_domain="instagram.com", new_domain="oginstagram.com")],
                repo_url="https://github.com/LilasKR/OGInstagram",
                default=True,
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
            Website(r"https://(www\.|web\.)?facebook\.com/share/r/[\w]+/?"),
            Website(r"https://(www\.|web\.)?facebook\.com/reel/\d+/?"),
            Website(r"https://(www\.|web\.)?facebook\.com/share/v/[\w]+/?"),
            Website(r"https://(www\.|web\.)?facebook\.com/marketplace/", skip_method_ids=[15, 25]),
            Website(r"https://(www\.|web\.)?facebook\.com/(.*)", skip_method_ids=[15]),
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
        id=DomainId.BILIBILI_OPUS,
        name="Bilibili Opus",
        websites=[
            Website(r"https://(www\.|m\.)?bilibili\.com/opus/\d+/?"),
            Website(r"https://t\.bilibili\.com/\d+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=36,
                name="BiliFix",
                fixes=[ReplaceFix(old_domain="bilibili.com", new_domain="vxbilibili.com")],
                repo_url="https://vxbilibili.com",
                default=True,
            )
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
            Website(r"https://(www\.)?threads\.(net|com)/share/[\w]+/?"),
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
    Domain(
        id=DomainId.PINTEREST,
        name="Pinterest",
        websites=[Website(r"https://(www\.)?pinterest\.com/pin/\d+/?")],
        fix_methods=[
            FixMethod(
                id=38,
                name=EMBEDEZ_NAME,
                fixes=[ReplaceFix(old_domain="pinterest.com", new_domain="pinterestez.com")],
                repo_url=EMBEDEZ_REPO_URL,
                has_ads=True,
            )
        ],
    ),
    Domain(
        id=DomainId.YOUTUBE,
        name="YouTube",
        websites=[
            Website(r"https://(www\.)?youtube\.com/watch\?v=[\w-]+/?"),
            Website(r"https://(www\.)?youtu\.be/[\w-]+/?"),
        ],
        fix_methods=[
            FixMethod(
                id=39,
                name="Koutube",
                fixes=[
                    ReplaceFix(old_domain="youtube.com", new_domain="koutube.com"),
                    ReplaceFix(old_domain="youtu.be", new_domain="koutu.be"),
                ],
                repo_url="https://github.com/iGerman00/koutube",
                default=True,
            )
        ],
        enabled_by_default=False,
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


def available_methods(match: MatchedURL) -> list[FixMethod]:
    skip = match.website.skip_method_ids or ()
    return [method for method in match.domain.fix_methods if method.id not in skip]


def _method_for_index(match: MatchedURL, index: int, preferred_id: int | None = None) -> FixMethod | None:
    """The method ``index`` steps after the starting one: the preferred method, else the domain default."""
    methods = available_methods(match)
    if not methods:
        return None
    start_method = match.domain.get_fix_method(preferred_id) if preferred_id is not None else None
    if start_method is None or start_method not in methods:
        start_method = match.domain.default_fix_method
    if start_method is not None and start_method in methods:
        start = methods.index(start_method)
        methods = methods[start:] + methods[:start]
    return methods[index % len(methods)]


def fixable_domains() -> list[Domain]:
    """Domains that have at least one fix method, in table order."""
    return [domain for domain in DOMAINS if domain.fix_methods]


def has_alternatives(matches: list[MatchedURL]) -> bool:
    return any(len(available_methods(match)) > 1 for match in matches)


def normalize_language(value: str | None) -> str | None:
    """Return a supported translation code for a code or language name, or None."""
    if not value:
        return None
    value = value.strip().casefold()
    if value in TRANSLATE_LANGUAGES:
        return value
    return next((code for code, name in TRANSLATE_LANGUAGES.items() if name.casefold() == value), None)


def _translate(url: str, language: str) -> str:
    """FxEmbed translates a post when the language code is appended to its path."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=parsed.path.rstrip("/") + "/" + language))


def fixed_links(
    matches: list[MatchedURL],
    rotations: dict[int, int],
    translate_to: str | None = None,
    preferred: dict[int, int] | None = None,
) -> list[str]:
    """Each match's fixed URL, or the original when no method applies.

    ``preferred`` maps a domain id to the fix method id its rotation starts from.
    """
    preferred = preferred or {}
    fixed_urls: list[str] = []
    for match in matches:
        domain_id = int(match.domain.id)
        method = _method_for_index(match, rotations.get(domain_id, 0), _int_or_none(preferred.get(domain_id)))
        fixed = _apply_fix(match.clean, match.domain, method) if method is not None else None
        if fixed and translate_to and method.id == FXEMBED_TWITTER_ID and match.domain.id == DomainId.TWITTER:
            fixed = _translate(fixed, translate_to)
        fixed_urls.append(fixed or match.original)
    return fixed_urls


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def rewrite(
    matches: list[MatchedURL],
    rotations: dict[int, int],
    translate_to: str | None = None,
    preferred: dict[int, int] | None = None,
) -> str:
    """Return the fixed links, one per line, using each domain's rotation index.

    With ``translate_to``, Twitter/X links fixed through FxEmbed are translated to that language.
    """
    return "\n".join(fixed_links(matches, rotations, translate_to, preferred))[:MAX_CONTENT]


def rewrite_inline(
    content: str,
    matches: list[MatchedURL],
    rotations: dict[int, int],
    translate_to: str | None = None,
    preferred: dict[int, int] | None = None,
) -> str:
    """Return the whole message with each matched link replaced by its fixed URL."""
    result = content
    pairs = zip(matches, fixed_links(matches, rotations, translate_to, preferred))
    for match, fixed in sorted(pairs, key=lambda pair: pair[0].start, reverse=True):
        result = result[: match.start] + fixed + result[match.end :]
    return result[:MAX_CONTENT]


def _matching_domain(
    url: str, opted_in: frozenset[DomainId], disabled: frozenset[int] = frozenset()
) -> tuple[Domain | None, Website | None]:
    for domain in DOMAINS:
        if not domain.enabled_by_default and domain.id not in opted_in:
            continue
        if int(domain.id) in disabled:
            continue
        for website in domain.websites:
            if website.match(url):
                return domain, website
    return None, None


def find_matches(
    content: str, opted_in: frozenset[DomainId] = frozenset(), disabled: frozenset[int] = frozenset()
) -> list[MatchedURL]:
    """Find fixable links. ``$link`` and ``<link>`` are left alone.

    Domains that are off by default (YouTube) only match when listed in ``opted_in``.
    Domain ids in ``disabled`` never match.
    """
    matches: list[MatchedURL] = []
    for found in URL_RE.finditer(content):
        raw_url = found.group(0).rstrip(".,!?;:")
        start = found.start()
        end = start + len(raw_url)
        if start > 0 and content[start - 1] == "$":
            continue
        if start > 0 and content[start - 1] == "<" and end < len(content) and content[end] == ">":
            continue
        clean = _clean_url(raw_url)
        domain, website = _matching_domain(clean, opted_in, disabled)
        if domain is None or website is None or not domain.fix_methods:
            continue
        matches.append(MatchedURL(start, end, raw_url, clean, domain, website))
    return matches
