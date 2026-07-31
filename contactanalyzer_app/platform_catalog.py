from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RELATIONS = ("followers", "following", "friends")

# Proven adapters must never be overridden by browser-learned routes.
PROTECTED_CORE_PLATFORMS = frozenset({
    "instagram", "x", "bluesky", "github",
    "soundcloud", "pinterest", "depop", "poshmark", "disqus",
})

# These modes are recorded as profiles but skipped by the graph collector.
NON_ENUMERABLE_MODES = frozenset({"private", "none"})


@dataclass(frozen=True)
class PlatformDefinition:
    name: str
    hosts: tuple[str, ...]
    relations: tuple[str, ...]
    graph_mode: str = "enumerable"  # enumerable | conditional | private | none
    profile_patterns: tuple[str, ...] = ()
    route_templates: dict[str, tuple[str, ...]] = field(default_factory=dict)
    row_selectors: tuple[str, ...] = (
        "[role='dialog'] [role='listitem'] a[href]",
        "[role='dialog'] a[href]",
        "main [role='listitem'] a[href]",
        "main li a[href]",
        "main a[href]",
    )
    next_selectors: tuple[str, ...] = (
        "a[rel='next']",
        "a.next_page:not(.disabled)",
        "button[aria-label*='Next' i]:not([disabled])",
        "a[aria-label*='Next' i]",
        "button[data-testid*='next' i]:not([disabled])",
    )
    network_keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: str = ""


# This catalog intentionally separates sites that expose a rendered relationship list
# from sites that merely host a public profile. Unknown sites are still inspected by the
# browser discovery engine before they are rejected.
PLATFORMS: dict[str, PlatformDefinition] = {
    "instagram": PlatformDefinition(
        "instagram", ("instagram.com",), ("followers", "following"),
        profile_patterns=(r"^/[^/]+/?$",),
        network_keywords={"followers": ("friendships", "followers"), "following": ("friendships", "following")},
    ),
    "facebook": PlatformDefinition(
        "facebook", ("facebook.com",), ("friends", "followers", "following"), "conditional",
        profile_patterns=(r"^/profile\.php$", r"^/people/[^/]+/\d+/?$", r"^/[^/]+/?$"),
        route_templates={
            "friends": ("/{username}/friends", "/{username}/friends_all", "/profile.php?id={username}&sk=friends", "/profile.php?id={username}&sk=friends_all"),
            "followers": ("/{username}/followers", "/profile.php?id={username}&sk=followers"),
            "following": ("/{username}/following", "/profile.php?id={username}&sk=following"),
        },
        row_selectors=("[role='main'] a[role='link'][href]", "[role='dialog'] a[role='link'][href]", "main a[href]"),
        network_keywords={},
        notes="Friends, followers, and following are collected only when each exact relationship control is visible and verifiable on the rendered profile.",
    ),
    "x": PlatformDefinition(
        "x", ("x.com", "twitter.com"), ("followers", "following"),
        profile_patterns=(r"^/[^/]+/?$",),
        route_templates={"followers": ("/{username}/followers", "/{username}/verified_followers"), "following": ("/{username}/following",)},
        row_selectors=(
            "main [aria-label='Timeline: Followers'] [data-testid='UserCell'] a[href]",
            "main [aria-label='Timeline: Following'] [data-testid='UserCell'] a[href]",
        ),
        network_keywords={"followers": ("followers", "blueverifiedfollowers"), "following": ("following",)},
    ),
    "tiktok": PlatformDefinition(
        "tiktok", ("tiktok.com",), ("followers", "following"), "conditional",
        profile_patterns=(r"^/@[^/]+/?$",),
        row_selectors=("[role='dialog'] a[href*='/@']", "main a[href*='/@']"),
        network_keywords={"followers": ("follower/list", "followers"), "following": ("following/list", "following")},
    ),
    "threads": PlatformDefinition(
        "threads", ("threads.net", "threads.com"), ("followers", "following"), "conditional",
        profile_patterns=(r"^/@[^/]+/?$",),
        row_selectors=("[role='dialog'] a[href*='/@']", "main a[href*='/@']"),
        network_keywords={"followers": ("followers", "graphql"), "following": ("following", "graphql")},
    ),
    "bluesky": PlatformDefinition(
        "bluesky", ("bsky.app",), ("followers", "following"),
        profile_patterns=(r"^/profile/[^/]+/?$",),
        route_templates={"followers": ("/profile/{username}/followers",), "following": ("/profile/{username}/follows",)},
        row_selectors=(
            "main [data-testid='profileFollowersScreen'] a[href*='/profile/']",
            "main [data-testid='profileFollowsScreen'] a[href*='/profile/']",
        ),
        network_keywords={"followers": ("getfollowers",), "following": ("getfollows",)},
    ),
    "mastodon": PlatformDefinition(
        "mastodon", tuple(), ("followers", "following"),
        profile_patterns=(r"^/@[^/]+/?$",),
        route_templates={"followers": ("/@{username}/followers",), "following": ("/@{username}/following",)},
        row_selectors=("main .account a[href*='/@']", "main a[href*='/@']"),
        network_keywords={"followers": ("/api/v1/accounts/", "followers"), "following": ("/api/v1/accounts/", "following")},
        notes="Applies to arbitrary ActivityPub instances using the common /@user route; browser discovery validates the instance.",
    ),
    "linkedin": PlatformDefinition(
        "linkedin", ("linkedin.com",), ("friends", "followers"), "conditional",
        profile_patterns=(r"^/in/[^/]+/?$", r"^/company/[^/]+/?$", r"^/school/[^/]+/?$"),
        row_selectors=("main a.app-aware-link[href*='/in/']", "main a[href*='/in/']"),
        notes="Connections/followers are collected only when the authenticated web UI renders the list.",
    ),
    "github": PlatformDefinition(
        "github", ("github.com",), ("followers", "following"),
        profile_patterns=(r"^/[^/]+/?$",),
        route_templates={"followers": ("/{username}?tab=followers",), "following": ("/{username}?tab=following",)},
        row_selectors=("main .d-table a[data-hovercard-type='user'][href]",),
        next_selectors=("a.next_page:not(.disabled)", "a[rel='next']"),
    ),
    "strava": PlatformDefinition(
        "strava", ("strava.com",), ("followers", "following"), "conditional",
        profile_patterns=(r"^/athletes/[^/]+/?$", r"^/pros/[^/]+/?$"),
        route_templates={"followers": ("/athletes/{username}/followers",), "following": ("/athletes/{username}/following",)},
        row_selectors=("main a[href*='/athletes/']", "[role='dialog'] a[href*='/athletes/']"),
    ),
    "youtube": PlatformDefinition(
        "youtube", ("youtube.com", "youtu.be"), ("followers", "following"), "conditional",
        profile_patterns=(r"^/@[^/]+/?$", r"^/channel/[^/]+/?$", r"^/user/[^/]+/?$", r"^/c/[^/]+/?$"),
        row_selectors=("main a[href^='/@']", "main a[href*='/channel/']", "ytd-channel-renderer a[href]"),
        notes="Subscribers/subscriptions are mapped to followers/following only when the browser exposes an enumerable list.",
    ),
    "soundcloud": PlatformDefinition(
        "soundcloud", ("soundcloud.com",), ("followers", "following"), "enumerable",
        profile_patterns=(r"^/[^/]+/?$",),
        route_templates={"followers": ("/{username}/followers",), "following": ("/{username}/following",)},
        row_selectors=(".userBadgeListItem a.userBadgeListItem__heading[href]",),
    ),
    "spotify": PlatformDefinition(
        "spotify", ("open.spotify.com", "spotify.com"), tuple(), "none",
        profile_patterns=(r"^/user/[^/]+/?$",),
        notes="The web profile does not expose another user's enumerable followers/following directory.",
    ),
    "pinterest": PlatformDefinition(
        "pinterest", ("pinterest.com",), ("followers", "following"), "conditional",
        profile_patterns=(r"^/[^/]+/?$",),
        row_selectors=("[role='dialog'] a[href]",),
        notes="Followers/following are collected only from the exact profile-count modal.",
    ),
    "tumblr": PlatformDefinition(
        "tumblr", ("tumblr.com",), tuple(), "none",
        profile_patterns=(r"^/[^/]+/?$",),
        notes="Public Tumblr profiles expose Follow actions, not an enumerable followers/following directory.",
    ),
    "myspace": PlatformDefinition("myspace", ("myspace.com",), ("friends",), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "reddit": PlatformDefinition(
        "reddit", ("reddit.com",), tuple(), "none",
        profile_patterns=(r"^/(?:user|u)/[^/]+/?$",),
        notes="Another user's Reddit followers are not exposed as a public enumerable directory.",
    ),
    "twitch": PlatformDefinition("twitch", ("twitch.tv",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "rumble": PlatformDefinition("rumble", ("rumble.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/user/[^/]+/?$", r"^/c/[^/]+/?$")),
    "gab": PlatformDefinition("gab", ("gab.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "parler": PlatformDefinition("parler", ("parler.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "kick": PlatformDefinition("kick", ("kick.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "flickr": PlatformDefinition("flickr", ("flickr.com",), ("friends", "followers", "following"), "conditional", profile_patterns=(r"^/people/[^/]+/?$",)),
    "vk": PlatformDefinition("vk", ("vk.com",), ("friends", "followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "weibo": PlatformDefinition("weibo", ("weibo.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "steam": PlatformDefinition("steam", ("steamcommunity.com",), ("friends",), "conditional", profile_patterns=(r"^/(?:id|profiles)/[^/]+/?$",)),
    "roblox": PlatformDefinition("roblox", ("roblox.com",), ("friends", "followers", "following"), "conditional", profile_patterns=(r"^/users/\d+/profile/?$",)),
    "letterboxd": PlatformDefinition("letterboxd", ("letterboxd.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "goodreads": PlatformDefinition("goodreads", ("goodreads.com",), ("friends", "followers", "following"), "conditional", profile_patterns=(r"^/user/show/[^/]+/?$",)),
    "deviantart": PlatformDefinition("deviantart", ("deviantart.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "medium": PlatformDefinition("medium", ("medium.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/@[^/]+/?$", r"^/[^/]+/?$")),
    "quora": PlatformDefinition("quora", ("quora.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/profile/[^/]+/?$",)),
    "poshmark": PlatformDefinition(
        "poshmark", ("poshmark.com",), ("followers", "following"), "enumerable",
        profile_patterns=(r"^/closet/[^/]+/?$",),
        route_templates={
            "followers": ("/user/{username}/followers",),
            "following": ("/user/{username}/following",),
        },
        row_selectors=("main a[href^='/closet/']", "main a[href*='poshmark.com/closet/']"),
        network_keywords={
            "followers": ("/followers?",),
            "following": ("/following?",),
        },
        notes="Exact profile counts link to dedicated, scrollable relationship routes.",
    ),
    "depop": PlatformDefinition(
        "depop", ("depop.com",), ("followers", "following"), "conditional",
        profile_patterns=(r"^/[^/]+/?$",),
        row_selectors=("[role='dialog'] a[href]",),
        notes="Followers/following are collected only from the exact profile-count modal.",
    ),
    "etsy": PlatformDefinition(
        "etsy", ("etsy.com",), ("followers", "following"), "conditional",
        profile_patterns=(r"^/people/[^/]+/?$",),
        notes="Collected only when Etsy renders an exact enumerable people relationship list; otherwise skipped.",
    ),
    "ebay": PlatformDefinition(
        "ebay", ("ebay.com",), tuple(), "none",
        profile_patterns=(r"^/usr/[^/]+/?$",),
        notes="Seller profiles do not expose the authenticated social relationship directories collected by Contact Analyzer.",
    ),
    "yelp": PlatformDefinition(
        "yelp", ("yelp.com",), ("friends", "followers"), "conditional",
        profile_patterns=(r"^/user_details$",),
        notes="Add Friend and Follow actions alone are not relationship lists; collection requires a verified enumerable directory.",
    ),
    "tripadvisor": PlatformDefinition("tripadvisor", ("tripadvisor.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/Profile/[^/]+/?$",)),
    "venmo": PlatformDefinition("venmo", ("venmo.com", "account.venmo.com"), ("friends",), "conditional", profile_patterns=(r"^/u/[^/]+/?$",)),
    "google_maps": PlatformDefinition(
        "google_maps", ("google.com",), tuple(), "none",
        profile_patterns=(r"^/maps/contrib/\d+",),
        notes="Google Maps contributor and review pages do not expose an enumerable people relationship directory.",
    ),
    "vimeo": PlatformDefinition("vimeo", ("vimeo.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/user\d+/?$", r"^/[^/]+/?$")),
    "smule": PlatformDefinition("smule", ("smule.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "pandora": PlatformDefinition("pandora", ("pandora.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/content/mobile/profile\.vm$",)),
    "disqus": PlatformDefinition(
        "disqus", ("disqus.com",), ("followers", "following"), "enumerable",
        profile_patterns=(r"^/by/[^/]+/?$",),
        route_templates={
            "followers": ("/by/{username}/followers/",),
            "following": ("/by/{username}/following/",),
        },
        row_selectors=("main a[href*='/by/']", "a[href*='/by/']"),
    ),
    "untappd": PlatformDefinition("untappd", ("untappd.com",), ("friends",), "conditional", profile_patterns=(r"^/user/[^/]+/?$",)),
    "beatstars": PlatformDefinition("beatstars", ("beatstars.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "hudl": PlatformDefinition("hudl", ("hudl.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/profile/[^/]+",)),
    "fitbit": PlatformDefinition("fitbit", ("fitbit.com",), ("friends",), "conditional", profile_patterns=(r"^/user/[^/]+/?$",)),
    "quizlet": PlatformDefinition("quizlet", ("quizlet.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "soundbetter": PlatformDefinition(
        "soundbetter", ("soundbetter.com",), tuple(), "none",
        profile_patterns=(r"^/profiles/[^/]+",),
        notes="Provider profiles do not expose an enumerable social relationship directory.",
    ),
    "iheart": PlatformDefinition("iheart", ("iheart.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/artist/[^/]+",)),
    "amazon_music": PlatformDefinition("amazon_music", ("music.amazon.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/artists/[^/]+",)),
    "apple_music": PlatformDefinition("apple_music", ("music.apple.com",), ("followers", "following"), "conditional"),
    "bandlab": PlatformDefinition("bandlab", ("bandlab.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "mixcloud": PlatformDefinition("mixcloud", ("mixcloud.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/[^/]+/?$",)),
    "picsart": PlatformDefinition("picsart", ("picsart.com",), ("followers", "following"), "conditional", profile_patterns=(r"^/u/[^/]+/?$", r"^/user/[^/]+/?$")),
    "myfitnesspal": PlatformDefinition("myfitnesspal", ("myfitnesspal.com",), ("friends",), "conditional"),
    "discogs": PlatformDefinition("discogs", ("discogs.com",), ("friends", "followers", "following"), "conditional"),
    "lastfm": PlatformDefinition("lastfm", ("last.fm",), ("followers", "following"), "conditional", profile_patterns=(r"^/user/[^/]+/?$",)),
    "foursquare": PlatformDefinition("foursquare", ("foursquare.com",), ("friends", "followers", "following"), "conditional"),
    "patreon": PlatformDefinition("patreon", ("patreon.com",), ("followers", "following"), "conditional"),

    # Private-contact or profile-only services. They are accepted as profile URLs but no
    # follower/friend extraction is attempted unless discovery finds a real rendered list.
    "snapchat": PlatformDefinition("snapchat", ("snapchat.com",), tuple(), "private", profile_patterns=(r"^/(?:@|add/)[^/]+/?$",), notes="Friend lists are private to the account/app."),
    "telegram": PlatformDefinition("telegram", ("t.me", "telegram.me"), tuple(), "private", profile_patterns=(r"^/[^/]+/?$",), notes="Ignored: user contacts are private; channel members/subscribers are a different entity type."),
    "whatsapp": PlatformDefinition("whatsapp", ("wa.me", "whatsapp.com"), tuple(), "private", notes="Contacts are private."),
    "kik": PlatformDefinition("kik", ("kik.me",), tuple(), "private", notes="Ignored: Kik contacts are private and no public relationship directory is exposed."),
    "discord": PlatformDefinition("discord", ("discord.com", "discord.gg"), tuple(), "private"),
    "cashapp": PlatformDefinition("cashapp", ("cash.app",), tuple(), "none"),
    "paypal": PlatformDefinition("paypal", ("paypal.com",), tuple(), "none"),
    "gravatar": PlatformDefinition("gravatar", ("gravatar.com",), tuple(), "none"),
    "linktree": PlatformDefinition("linktree", ("linktr.ee",), tuple(), "none"),
    "allmylinks": PlatformDefinition("allmylinks", ("allmylinks.com",), tuple(), "none"),
    "aboutme": PlatformDefinition("aboutme", ("about.me",), tuple(), "none"),
    "gofundme": PlatformDefinition("gofundme", ("gofundme.com",), tuple(), "none"),
    "plex": PlatformDefinition("plex", ("app.plex.tv",), tuple(), "private"),
    "ifttt": PlatformDefinition("ifttt", ("ifttt.com",), tuple(), "none"),
    "codecademy": PlatformDefinition("codecademy", ("codecademy.com",), tuple(), "none"),
    "periscope": PlatformDefinition("periscope", ("pscp.tv",), tuple(), "none"),
    "tracker_profile": PlatformDefinition("tracker_profile", ("tracker.gg", "fortnitetracker.com", "apex.tracker.gg"), tuple(), "none"),
    "xbox_profile": PlatformDefinition("xbox_profile", ("xboxgamertag.com",), tuple(), "none"),
    "trello": PlatformDefinition("trello", ("trello.com",), tuple(), "none"),
    "khanacademy": PlatformDefinition("khanacademy", ("khanacademy.org",), tuple(), "none"),
    "maxpreps": PlatformDefinition("maxpreps", ("maxpreps.com",), tuple(), "none"),
}


NAV_SEGMENTS = {
    "about", "account", "accounts", "activity", "admin", "ads", "api", "apps", "auth", "blog",
    "business", "channels", "community", "compose", "contact", "create", "dashboard", "developer",
    "developers", "directory", "discover", "download", "events", "explore", "feed", "followers",
    "following", "friends", "help", "home", "jobs", "legal", "login", "logout", "marketplace",
    "messages", "notifications", "privacy", "reels", "search", "settings", "share", "signup",
    "stories", "support", "terms", "topics", "trending", "upload", "watch", "posts", "status",
}


def _host_matches(host: str, known: str) -> bool:
    host = host.lower().split(":", 1)[0]
    known = known.lower()
    return host == known or host.endswith("." + known)


def definition_for_name(name: str) -> PlatformDefinition:
    # Unknown non-social domains are analyzed by the bounded Codex website-person
    # adapter. They are never sent through generic relationship discovery.
    return PLATFORMS.get(name, PlatformDefinition(name or "generic", tuple(), tuple(), "codex"))


def platform_for_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    for name, definition in PLATFORMS.items():
        if definition.hosts and any(_host_matches(host, known) for known in definition.hosts):
            # Google is broad; only classify contributor profiles as Google Maps.
            if name == "google_maps" and not path.startswith("/maps/contrib/"):
                continue
            return name
    if re.match(r"^/@[^/]+/?$", path):
        return "mastodon"
    return "generic"


def default_relations(platform: str) -> tuple[str, ...]:
    return definition_for_name(platform).relations


def graph_mode(platform: str) -> str:
    return definition_for_name(platform).graph_mode


def coverage_rows() -> list[dict[str, Any]]:
    return [
        {
            "platform": item.name,
            "hosts": list(item.hosts),
            "relations": list(item.relations),
            "mode": item.graph_mode,
            "notes": item.notes,
        }
        for item in sorted(PLATFORMS.values(), key=lambda row: row.name)
    ]


def learned_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "contactanalyzer" / "learned_adapters.json"


def load_learned() -> dict[str, Any]:
    path = learned_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_learned(data: dict[str, Any]) -> None:
    path = learned_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _source_identity(platform: str, source_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(source_url)
    host = (parsed.hostname or "").lower()
    segments = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
    query = urllib.parse.parse_qs(parsed.query)
    if platform == "facebook" and parsed.path.endswith("profile.php") and query.get("id"):
        return query["id"][0], host
    if platform == "bluesky" and len(segments) >= 2 and segments[0].casefold() == "profile":
        return segments[1], host
    if platform in {"tiktok", "threads", "mastodon"} and segments:
        return segments[0].lstrip("@"), host
    if platform == "spotify" and len(segments) >= 2:
        return segments[1], host
    if platform == "linkedin" and len(segments) >= 2:
        return segments[1], host
    if platform in {"poshmark", "etsy", "disqus"} and len(segments) >= 2:
        return segments[1], host
    if platform == "yelp" and query.get("userid"):
        return query["userid"][0], host
    if platform in {"strava", "roblox", "goodreads", "flickr", "steam", "quora", "youtube"} and len(segments) >= 2:
        return segments[-1].lstrip("@"), host
    return (segments[0].lstrip("@") if segments else host), host


def _route_from_template(source_url: str, template: str, username: str) -> str:
    parsed = urllib.parse.urlparse(source_url)
    path = template.format(username=username)
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urllib.parse.urlunparse((parsed.scheme or "https", parsed.netloc, path.split("?", 1)[0], "", (path.split("?", 1)[1] if "?" in path else ""), ""))


def _catalog_relation_url(platform: str, source_url: str, relation: str) -> str | None:
    learned = load_learned()
    host = (urllib.parse.urlparse(source_url).hostname or "").lower()
    relation_data = learned.get(host, {}).get("relations", {}).get(relation, {})
    if relation_data.get("verified") is True and relation_data.get("url_template"):
        username, _ = _source_identity(platform, source_url)
        return _route_from_template(source_url, str(relation_data["url_template"]), username)
    definition = definition_for_name(platform)
    templates = definition.route_templates.get(relation) or tuple()
    if not templates:
        return None
    username, _ = _source_identity(platform, source_url)
    return _route_from_template(source_url, templates[0], username)


def _catalog_fragments(platform: str, source_url: str, relation: str) -> tuple[str, ...]:
    # These platforms render exact count controls without relationship hrefs.
    # Their list route/modal is still platform-specific, but count matching must
    # be based on the compact visible label rather than a nonexistent href.
    if platform in {"pinterest", "poshmark", "depop"}:
        return tuple()
    learned = load_learned()
    host = (urllib.parse.urlparse(source_url).hostname or "").lower()
    relation_data = learned.get(host, {}).get("relations", {}).get(relation, {})
    values = relation_data.get("href_fragments") if relation_data.get("verified") is True else None
    if isinstance(values, list) and values:
        return tuple(str(value) for value in values)
    definition = definition_for_name(platform)
    username, _ = _source_identity(platform, source_url)
    return tuple(template.format(username=username) for template in definition.route_templates.get(relation, tuple()))


def _same_site(a: str, b: str) -> bool:
    return bool(a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)))


def _canonicalize_known(platform: str, href: str, source_url: str) -> tuple[str, str, str | None] | None:
    try:
        parsed = urllib.parse.urlparse(href)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    source_host = (urllib.parse.urlparse(source_url).hostname or "").lower()
    definition = definition_for_name(platform)
    if definition.hosts and not any(_host_matches(host, known) for known in definition.hosts):
        return None
    if platform == "mastodon" and not _same_site(host, source_host):
        # Federated list rows often point to remote instances. Accept remote /@user URLs.
        pass
    elif not definition.hosts and not _same_site(host, source_host):
        return None

    path = re.sub(r"/+", "/", parsed.path or "/")
    lower_path = path.casefold()
    segments = [urllib.parse.unquote(x) for x in path.split("/") if x]
    if not segments:
        return None
    if any(segment.casefold() in NAV_SEGMENTS for segment in segments[:2]):
        return None

    patterns = definition.profile_patterns
    if patterns and not any(re.match(pattern, path, re.I) for pattern in patterns):
        return None

    username = ""
    platform_id: str | None = None
    canonical = ""

    if platform == "threads":
        username = segments[0].lstrip("@")
        canonical = f"https://www.threads.com/@{username}"
    elif platform == "roblox" and len(segments) >= 2:
        username = segments[1]
        platform_id = username if username.isdigit() else None
        canonical = f"https://www.roblox.com/users/{username}/profile"
    elif platform == "poshmark" and len(segments) >= 2:
        username = segments[1]
        canonical = f"https://poshmark.com/closet/{username}"
    elif platform == "depop":
        username = segments[0]
        canonical = f"https://www.depop.com/{username}/"
    elif platform == "etsy" and len(segments) >= 2:
        username = segments[1]
        canonical = f"https://www.etsy.com/people/{username}"
    elif platform == "ebay" and len(segments) >= 2:
        username = segments[1]
        canonical = f"https://www.ebay.com/usr/{username}"
    elif platform == "venmo" and len(segments) >= 2:
        username = segments[1]
        canonical = f"https://account.venmo.com/u/{username}"
    elif platform == "google_maps" and len(segments) >= 3:
        username = segments[2]
        platform_id = username if username.isdigit() else None
        canonical = f"https://www.google.com/maps/contrib/{username}"
    elif platform == "vimeo":
        username = segments[0]
        canonical = f"https://vimeo.com/{username}"
    elif platform == "disqus" and len(segments) >= 2:
        username = segments[1]
        canonical = f"https://disqus.com/by/{username}/"
    elif platform == "untappd" and len(segments) >= 2:
        username = segments[1]
        canonical = f"https://untappd.com/user/{username}"
    elif platform == "mastodon":
        if not segments[0].startswith("@"):
            return None
        username = segments[0][1:]
        canonical = f"https://{host}/@{username}"
    elif platform == "generic":
        learned = load_learned().get(source_host, {})
        learned_patterns = learned.get("profile_patterns") or []
        if learned_patterns and not any(re.match(pattern, path, re.I) for pattern in learned_patterns):
            return None
        if len(segments) != 1 or lower_path.strip("/") in NAV_SEGMENTS:
            return None
        username = segments[0].lstrip("@")
        canonical = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/" + segments[0], "", "", ""))
    else:
        # Known platforms already handled by the original adapter generally fall through
        # only for newly cataloged one- or two-segment profile forms.
        username = segments[-1].lstrip("@")
        canonical = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/") or "/", "", "", ""))

    source_username, _ = _source_identity(platform, source_url)
    if not username or username.casefold() == source_username.casefold():
        return None
    return username, canonical, platform_id


def install_into_adapters(namespace: dict[str, Any]) -> None:
    """Extend the installed adapters module without overwriting proven platform code."""
    PlatformSpec = namespace.get("PlatformSpec")
    SPECS = namespace.get("SPECS")
    if PlatformSpec is None or not isinstance(SPECS, dict):
        return

    original_platform_for = namespace.get("platform_for")
    original_relations_for = namespace.get("relations_for")
    original_source_identity = namespace.get("source_identity")
    original_relation_url = namespace.get("relation_url")
    original_fragments = namespace.get("count_href_fragments")
    original_normalize = namespace.get("normalize_profile_link")
    original_canonical = namespace.get("canonical_from_network")
    original_keywords = namespace.get("network_keywords")

    # Add missing specs only. Existing Instagram/X/Bluesky/GitHub selectors remain intact.
    for name, definition in PLATFORMS.items():
        if name in SPECS:
            continue
        SPECS[name] = PlatformSpec(
            name=name,
            hosts=definition.hosts,
            relations=definition.relations,
            row_selectors=definition.row_selectors,
            next_selectors=definition.next_selectors,
            network_keywords=definition.network_keywords,
        )

    def platform_for(url: str) -> str:
        detected = platform_for_url(url)
        if detected != "generic":
            return detected
        return original_platform_for(url) if callable(original_platform_for) else "generic"

    def relations_for(platform: str) -> tuple[str, ...]:
        definition = PLATFORMS.get(platform)
        if definition is not None:
            return definition.relations
        return original_relations_for(platform) if callable(original_relations_for) else RELATIONS

    def source_identity(platform: str, source_url: str) -> tuple[str, str]:
        if platform in PLATFORMS and platform not in {"instagram", "facebook", "x", "tiktok", "bluesky", "threads", "github", "strava", "youtube", "soundcloud", "spotify", "mastodon", "linkedin", "reddit", "steam", "flickr", "goodreads", "quora"}:
            return _source_identity(platform, source_url)
        return original_source_identity(platform, source_url) if callable(original_source_identity) else _source_identity(platform, source_url)

    def relation_url(platform: str, source_url: str, relation: str) -> str | None:
        # Keep the Codex-tested core adapters authoritative. Learned routes are
        # allowed only for new/conditional platforms after browser verification.
        if platform in PROTECTED_CORE_PLATFORMS | {"facebook"} and callable(original_relation_url):
            original = original_relation_url(platform, source_url, relation)
            if original:
                return original
        learned_or_catalog = _catalog_relation_url(platform, source_url, relation)
        if learned_or_catalog:
            return learned_or_catalog
        return original_relation_url(platform, source_url, relation) if callable(original_relation_url) else None

    def count_href_fragments(platform: str, source_url: str, relation: str) -> tuple[str, ...]:
        if platform in PROTECTED_CORE_PLATFORMS | {"facebook"} and callable(original_fragments):
            original = original_fragments(platform, source_url, relation)
            if original:
                return original
        values = _catalog_fragments(platform, source_url, relation)
        if values:
            return values
        return original_fragments(platform, source_url, relation) if callable(original_fragments) else tuple()

    def normalize_profile_link(platform: str, href: str, source_url: str):
        if callable(original_normalize):
            result = original_normalize(platform, href, source_url)
            if result:
                return result
            # A rejection by a dedicated normalizer is authoritative.  Falling
            # through to the catalog's generic path rules can turn navigation
            # endpoints such as Facebook /photo.php into fake contacts.
            if platform in {
                "instagram", "facebook", "x", "tiktok", "bluesky", "threads",
                "github", "strava", "youtube", "soundcloud", "pinterest",
                "depop", "spotify", "mastodon", "linkedin", "reddit", "steam",
                "flickr", "goodreads", "quora",
                "hudl",
            }:
                return None
        return _canonicalize_known(platform, href, source_url)

    def canonical_from_network(platform: str, username: str, source_url: str, platform_id: str | None = None) -> str | None:
        if callable(original_canonical):
            result = original_canonical(platform, username, source_url, platform_id)
            if result:
                return result
        parsed = urllib.parse.urlparse(source_url)
        base = urllib.parse.urlunparse((parsed.scheme or "https", parsed.netloc, "", "", "", ""))
        definition = definition_for_name(platform)
        for pattern in definition.profile_patterns:
            if "@" in pattern:
                return f"{base}/@{username}"
        return f"{base}/{username}"

    def network_keywords(platform: str, relation: str) -> tuple[str, ...]:
        if platform in PROTECTED_CORE_PLATFORMS and callable(original_keywords):
            original = original_keywords(platform, relation)
            if original:
                return original
        definition = PLATFORMS.get(platform)
        if definition and definition.network_keywords.get(relation):
            return definition.network_keywords[relation]
        return original_keywords(platform, relation) if callable(original_keywords) else tuple()

    namespace.update({
        "platform_for": platform_for,
        "relations_for": relations_for,
        "source_identity": source_identity,
        "relation_url": relation_url,
        "count_href_fragments": count_href_fragments,
        "normalize_profile_link": normalize_profile_link,
        "canonical_from_network": canonical_from_network,
        "network_keywords": network_keywords,
    })
