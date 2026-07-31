from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

CANONICAL_RELATIONS = ("followers", "following", "friends")

RELATION_LABELS: dict[str, list[str]] = {
    "followers": ["followers", "follower", "subscribers", "subscriber", "fans", "watchers"],
    "following": ["following", "follows", "subscriptions", "subscribed", "watching"],
    "friends": ["friends", "friend", "connections", "connection", "contacts"],
}


def threads_display_name(username: str, value: Any) -> str | None:
    """Remove relationship-row control text from a rendered Threads name."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None
    row_control = re.search(r"\s+Follow(?:ing)?\s*$", text, flags=re.I)
    text = re.sub(r"\s+Follow(?:ing)?\s*$", "", text, flags=re.I).strip()
    handle = str(username or "").strip().lstrip("@")
    if handle and row_control:
        text = re.sub(
            rf"^@?{re.escape(handle)}(?:\s+|$)",
            "",
            text,
            count=1,
            flags=re.I,
        ).strip()
    return text or handle or None


@dataclass(frozen=True)
class PlatformSpec:
    name: str
    hosts: tuple[str, ...]
    relations: tuple[str, ...]
    row_selectors: tuple[str, ...] = ("main a[href]", "[role='dialog'] a[href]")
    next_selectors: tuple[str, ...] = (
        "a[rel='next']",
        "a.next_page:not(.disabled)",
        "button[aria-label*='Next']:not([disabled])",
        "a[aria-label*='Next']",
    )
    control_selectors: tuple[str, ...] = ("a[href]", "button", "[role='link']", "[role='button']")
    network_keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)


SPECS: dict[str, PlatformSpec] = {
    "instagram": PlatformSpec(
        "instagram", ("instagram.com",), ("followers", "following"),
        ("[role='dialog'] a[href]",),
        control_selectors=("main section a[href]",),
        network_keywords={
            "followers": ("friendships", "followers"),
            "following": ("friendships", "following"),
        },
    ),
    "facebook": PlatformSpec(
        "facebook", ("facebook.com",), ("friends", "followers", "following"),
        ("[role='main'] a[role='link'][href]", "[role='dialog'] a[role='link'][href]", "main a[href]"),
        # Facebook multiplexes unrelated feed, recommendation, and navigation
        # entities through GraphQL. Until a response can be proven to belong to
        # the active profile relationship directory, use the rendered list only.
        network_keywords={},
    ),
    "x": PlatformSpec(
        "x", ("x.com", "twitter.com"), ("followers", "following"),
        (
            "main [aria-label='Timeline: Followers'] [data-testid='UserCell'] a[href][tabindex='-1']:not(:has(img))",
            "main [aria-label='Timeline: Following'] [data-testid='UserCell'] a[href][tabindex='-1']:not(:has(img))",
        ),
        control_selectors=("main a[href]",),
        network_keywords={
            "followers": ("followers", "blueverifiedfollowers"),
            "following": ("following",),
        },
    ),
    "tiktok": PlatformSpec(
        "tiktok", ("tiktok.com",), ("followers", "following"),
        ("[role='dialog'] a[href*='/@']", "main a[href*='/@']"),
        network_keywords={
            "followers": ("follower/list", "followers"),
            "following": ("following/list", "following"),
        },
    ),
    "bluesky": PlatformSpec(
        "bluesky", ("bsky.app",), ("followers", "following"),
        (
            "main [data-testid='profileFollowersScreen'] a[href*='/profile/']",
            "main [data-testid='profileFollowsScreen'] a[href*='/profile/']",
        ),
        control_selectors=("main a[href]",),
        network_keywords={
            "followers": ("getfollowers",),
            "following": ("getfollows",),
        },
    ),
    "threads": PlatformSpec(
        "threads", ("threads.com", "threads.net"), ("followers", "following"),
        ("[role='dialog'] a[href*='/@']", "main a[href*='/@']"),
        network_keywords={
            "followers": ("followers", "graphql"),
            "following": ("following", "graphql"),
        },
    ),
    "pinterest": PlatformSpec(
        "pinterest", ("pinterest.com",), ("followers", "following"),
        ("[role='dialog'] a[href]",),
        # Pinterest's localized profile shell does not consistently render a
        # semantic <main>.  The count scanner still requires an exact visible
        # relationship label/count, so document-wide controls remain scoped
        # safely without depending on that missing ancestor.
        control_selectors=("[role='button']", "button", "a[href]"),
    ),
    "github": PlatformSpec(
        "github", ("github.com",), ("followers", "following"),
        ("main .d-table a[data-hovercard-type='user'][href]",),
        ("a.next_page:not(.disabled)", "a[rel='next']"),
        control_selectors=("main a[href]",),
    ),
    "strava": PlatformSpec(
        "strava", ("strava.com",), ("followers", "following"),
        ("main a[href*='/athletes/']", "[role='dialog'] a[href*='/athletes/']"),
    ),
    "youtube": PlatformSpec(
        "youtube", ("youtube.com", "youtu.be"), ("followers", "following"),
        ("main a[href^='/@']", "main a[href*='/channel/']", "ytd-channel-renderer a[href]"),
    ),
    "soundcloud": PlatformSpec(
        "soundcloud", ("soundcloud.com",), ("followers", "following"),
        # SoundCloud's current network pages also omit <main>.  Restrict rows
        # to the user-card heading so follower-count links and Follow buttons
        # are never interpreted as account records.
        (".userBadgeListItem a.userBadgeListItem__heading[href]",),
        control_selectors=("a[href]",),
    ),
    "spotify": PlatformSpec(
        "spotify", ("open.spotify.com", "spotify.com"), ("followers", "following"),
        ("main a[href*='/user/']", "[role='dialog'] a[href*='/user/']"),
    ),
    "mastodon": PlatformSpec(
        "mastodon", tuple(), ("followers", "following"),
        ("main a[href*='/@']", ".account a[href*='/@']"),
        network_keywords={
            "followers": ("followers", "api/v1/accounts"),
            "following": ("following", "api/v1/accounts"),
        },
    ),
    "gab": PlatformSpec("gab", ("gab.com",), ("followers", "following"), ("main a[href]",)),
    "parler": PlatformSpec("parler", ("parler.com",), ("followers", "following"), ("main a[href]",)),
    "linkedin": PlatformSpec(
        "linkedin", ("linkedin.com",), ("friends", "followers"),
        ("main a.app-aware-link[href]", "main a[href*='/in/']"),
    ),
    "twitch": PlatformSpec(
        "twitch", ("twitch.tv",), ("followers", "following"),
        ("main a[href]", "[data-a-target*='follow'] a[href]"),
    ),
    "rumble": PlatformSpec("rumble", ("rumble.com",), ("followers", "following"), ("main a[href]",)),
    "tumblr": PlatformSpec("tumblr", ("tumblr.com",), ("followers", "following"), ("main a[href]",)),
    "myspace": PlatformSpec("myspace", ("myspace.com",), ("friends",), ("main a[href]",)),
    "reddit": PlatformSpec("reddit", ("reddit.com",), ("followers",), ("main a[href*='/user/']",)),
    "flickr": PlatformSpec("flickr", ("flickr.com",), ("friends", "followers", "following"), ("main a[href*='/people/']",)),
    "vk": PlatformSpec("vk", ("vk.com",), ("friends", "followers"), ("main a[href]",)),
    "kick": PlatformSpec("kick", ("kick.com",), ("followers", "following"), ("main a[href]",)),
    "steam": PlatformSpec("steam", ("steamcommunity.com",), ("friends",), ("main a[href*='/id/']", "main a[href*='/profiles/']")),
    "letterboxd": PlatformSpec("letterboxd", ("letterboxd.com",), ("followers", "following"), ("main a[href]",)),
    "goodreads": PlatformSpec("goodreads", ("goodreads.com",), ("friends", "followers", "following"), ("main a[href*='/user/show/']",)),
    "deviantart": PlatformSpec("deviantart", ("deviantart.com",), ("followers", "following"), ("main a[href]",)),
    "medium": PlatformSpec("medium", ("medium.com",), ("followers", "following"), ("main a[href]",)),
    # Quora's profile relationship tabs are outside a semantic <main>.  The
    # collector additionally requires the source-scoped /followers route and
    # filters rows below the exact follower tab before these links are used.
    "quora": PlatformSpec("quora", ("quora.com",), ("followers", "following"), ("a[href*='/profile/']",)),
    "poshmark": PlatformSpec(
        "poshmark", ("poshmark.com",), ("followers", "following"),
        ("main a[href^='/closet/']", "main a[href*='poshmark.com/closet/']"),
        control_selectors=("main div", "main a[href]", "main button"),
        network_keywords={
            "followers": ("/followers?",),
            "following": ("/following?",),
        },
    ),
    "depop": PlatformSpec(
        "depop", ("depop.com",), ("followers", "following"),
        ("[role='dialog'] a[href]",),
        control_selectors=("main button", "main [role='button']"),
    ),
    "disqus": PlatformSpec(
        "disqus", ("disqus.com",), ("followers", "following"),
        ("main a[href*='/by/']", "a[href*='/by/']"),
        control_selectors=("main a[href]", "a[href]"),
    ),
    "weibo": PlatformSpec("weibo", ("weibo.com",), ("followers", "following"), ("main a[href]",)),
    "generic": PlatformSpec("generic", tuple(), ("followers", "following", "friends"), ("main a[href]", "[role='dialog'] a[href]")),
}

GENERIC_EXCLUDED = {
    "about", "account", "accounts", "activity", "admin", "ads", "api", "apps", "auth", "blog",
    "business", "channels", "community", "compose", "contact", "create", "dashboard", "developer",
    "developers", "directory", "discover", "download", "events", "explore", "feed", "followers",
    "following", "friends", "help", "home", "jobs", "legal", "login", "logout", "marketplace",
    "messages", "notifications", "privacy", "reels", "search", "settings", "share", "signup",
    "stories", "support", "terms", "topics", "trending", "upload", "watch",
}


def host_matches(host: str, known: str) -> bool:
    host = host.lower().split(":", 1)[0]
    known = known.lower()
    return host == known or host.endswith("." + known)


def platform_for(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    for name, spec in SPECS.items():
        if name == "generic":
            continue
        if spec.hosts and any(host_matches(host, known) for known in spec.hosts):
            return name
    if re.match(r"^/@[^/]+/?$", path):
        return "mastodon"
    return "generic"


def relations_for(platform: str) -> tuple[str, ...]:
    return SPECS.get(platform, SPECS["generic"]).relations


def tiktok_username(value: str) -> str | None:
    """Return a safe TikTok username from a rendered row value."""
    value = str(value or "").strip().lstrip("@")
    if not value or not re.fullmatch(r"[A-Za-z0-9._~-]+", value):
        return None
    return value


def tiktok_canonical_url(value: str, source_url: str) -> str | None:
    username = tiktok_username(value)
    if not username:
        return None
    source_username, _ = source_identity("tiktok", source_url)
    if username.casefold() == source_username.casefold():
        return None
    return f"https://www.tiktok.com/@{username}"


def source_identity(platform: str, source_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(source_url)
    host = (parsed.hostname or "").lower()
    segments = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
    query = urllib.parse.parse_qs(parsed.query)
    username = segments[-1] if segments else host
    if platform in {"instagram", "x", "github", "soundcloud", "pinterest", "gab", "twitch", "kick", "letterboxd"}:
        username = segments[0] if segments else host
    elif platform in {"tiktok", "threads", "mastodon"}:
        username = (segments[0] if segments else host).lstrip("@")
    elif platform == "bluesky" and len(segments) >= 2 and segments[0].lower() == "profile":
        username = segments[1]
    elif platform == "facebook" and segments and segments[0].lower() == "profile.php" and query.get("id"):
        username = query["id"][0]
    elif platform == "youtube" and segments:
        username = segments[-1].lstrip("@")
    elif platform == "spotify" and len(segments) >= 2:
        username = segments[1]
    return username, host


def relation_url(platform: str, source_url: str, relation: str) -> str | None:
    parsed = urllib.parse.urlparse(source_url)
    base = source_url.split("#", 1)[0].rstrip("/")
    username, _ = source_identity(platform, source_url)

    if platform == "x":
        return f"https://x.com/{username}/{relation}"
    if platform == "bluesky":
        suffix = "followers" if relation == "followers" else "follows"
        return f"https://bsky.app/profile/{username}/{suffix}"
    if platform == "github":
        return f"https://github.com/{username}?tab={relation}"
    if platform == "facebook":
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.endswith("profile.php") and query.get("id"):
            suffix = "friends_all" if relation == "friends" else relation
            return f"https://www.facebook.com/profile.php?id={query['id'][0]}&sk={suffix}"
        # Vanity-profile friend counts link to the dedicated All friends
        # directory. The shorter /friends route can render only the profile's
        # small friend-preview card followed by unrelated photos/check-ins.
        suffix = "friends_all" if relation == "friends" else relation
        return f"{base}/{suffix}"
    if platform in {"soundcloud", "letterboxd", "gab", "mastodon"}:
        suffix = relation
        return f"{base}/{suffix}/" if platform in {"pinterest", "letterboxd"} else f"{base}/{suffix}"
    if platform == "strava":
        return f"{base}/{relation}"
    if platform == "quora" and relation == "followers":
        return f"{base}/followers"
    return None


def count_href_fragments(platform: str, source_url: str, relation: str) -> tuple[str, ...]:
    username, _ = source_identity(platform, source_url)
    if platform == "instagram":
        # Instagram's rendered profile-stat controls currently use a same-page '#'
        # href. The platform-specific control selector and exact label identify them.
        return tuple()
    if platform == "x":
        if relation == "followers":
            return (f"/{username}/followers", f"/{username}/verified_followers")
        return (f"/{username}/following",)
    if platform == "bluesky":
        suffix = "followers" if relation == "followers" else "follows"
        return (f"/profile/{username}/{suffix}",)
    if platform == "github":
        return (f"/{username}?tab={relation}", f"?tab={relation}")
    if platform == "facebook":
        parsed = urllib.parse.urlparse(source_url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.rstrip("/").endswith("profile.php") and query.get("id"):
            fragments = [f"/profile.php?id={query['id'][0]}&sk={relation}"]
            if relation == "friends":
                fragments.append(f"/profile.php?id={query['id'][0]}&sk=friends_all")
            return tuple(fragments)
        username, _ = source_identity(platform, source_url)
        fragments = [f"/{username}/{relation}"]
        if relation == "friends":
            fragments.append(f"/{username}/friends_all")
        return tuple(fragments)
    if platform == "soundcloud":
        return (f"/{username}/{relation}",)
    if platform in {"letterboxd", "gab", "mastodon"}:
        return (f"/{relation}",)
    return tuple()


def _same_site(a: str, b: str) -> bool:
    return bool(a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)))


def facebook_friend_filter_route(source_url: str, target_url: str) -> str | None:
    """Return a verified source-scoped Facebook friend-subset route.

    Facebook can expose ``Current city``, ``Hometown``, and similar tabs below
    ``All friends``.  They are safe supplemental friend directories only when
    their URL belongs to the same source profile and the section starts with
    ``friends_``.  ``All friends`` and the separate ``Following`` relation are
    deliberately excluded.
    """
    try:
        source = urllib.parse.urlparse(source_url)
        target = urllib.parse.urlparse(target_url)
    except ValueError:
        return None
    source_host = (source.hostname or "").casefold()
    target_host = (target.hostname or "").casefold()
    if target.scheme not in {"http", "https"} or not _same_site(source_host, target_host):
        return None

    source_path = source.path.rstrip("/")
    target_path = target.path.rstrip("/")
    source_query = urllib.parse.parse_qs(source.query)
    target_query = urllib.parse.parse_qs(target.query)
    if source_path.casefold().endswith("/profile.php"):
        source_ids = source_query.get("id") or []
        target_ids = target_query.get("id") or []
        section = str((target_query.get("sk") or [""])[0]).casefold()
        valid = bool(
            source_ids
            and target_ids == source_ids
            and target_path.casefold().endswith("/profile.php")
            and section.startswith("friends_")
            and section != "friends_all"
        )
    else:
        prefix = source_path + "/"
        section = target_path[len(prefix):].casefold() if target_path.startswith(prefix) else ""
        valid = bool(section.startswith("friends_") and section != "friends_all" and "/" not in section)
    if not valid:
        return None
    return urllib.parse.urlunparse((
        target.scheme,
        target.netloc,
        target.path,
        "",
        target.query,
        "",
    ))


def normalize_profile_link(platform: str, href: str, source_url: str) -> tuple[str, str, str | None] | None:
    try:
        parsed = urllib.parse.urlparse(href)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    source_host = (urllib.parse.urlparse(source_url).hostname or "").lower()
    segments = [urllib.parse.unquote(x) for x in re.sub(r"/+", "/", parsed.path).split("/") if x]
    lower = [x.casefold() for x in segments]
    query = urllib.parse.parse_qs(parsed.query)
    if not segments:
        return None

    username = ""
    canonical = ""
    platform_id: str | None = None

    if platform == "instagram":
        if not host_matches(host, "instagram.com") or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED:
            return None
        username = segments[0].lstrip("@")
        canonical = f"https://www.instagram.com/{username}/"
    elif platform == "x":
        if not (host_matches(host, "x.com") or host_matches(host, "twitter.com")) or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED | {"i", "intent"}:
            return None
        username = segments[0].lstrip("@")
        canonical = f"https://x.com/{username}"
    elif platform == "tiktok":
        if not host_matches(host, "tiktok.com") or len(segments) != 1 or not segments[0].startswith("@") or parsed.query or parsed.fragment:
            return None
        username = segments[0][1:]
        canonical = f"https://www.tiktok.com/@{username}"
    elif platform == "bluesky":
        if not host_matches(host, "bsky.app") or len(segments) != 2 or lower[0] != "profile":
            return None
        username = segments[1]
        canonical = f"https://bsky.app/profile/{username}"
    elif platform == "threads":
        if not (host_matches(host, "threads.com") or host_matches(host, "threads.net")) or len(segments) != 1 or not segments[0].startswith("@"):
            return None
        username = segments[0][1:]
        canonical = f"https://www.threads.com/@{username}"
    elif platform == "facebook":
        if not host_matches(host, "facebook.com"):
            return None
        if lower[0] == "profile.php" and query.get("id"):
            username = query["id"][0]
            platform_id = username
            canonical = f"https://www.facebook.com/profile.php?id={username}"
        elif len(segments) == 1 and lower[0] not in GENERIC_EXCLUDED and not lower[0].endswith(".php"):
            username = segments[0]
            canonical = f"https://www.facebook.com/{username}"
        else:
            return None
    elif platform == "github":
        if not host_matches(host, "github.com") or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED | {"features", "pricing", "orgs", "topics", "marketplace", "sponsors"}:
            return None
        username = segments[0]
        canonical = f"https://github.com/{username}"
    elif platform == "strava":
        if not host_matches(host, "strava.com") or len(segments) < 2 or lower[0] not in {"athletes", "pros"}:
            return None
        username = segments[1]
        platform_id = username if username.isdigit() else None
        canonical = f"https://www.strava.com/{segments[0]}/{username}"
    elif platform == "youtube":
        if not host_matches(host, "youtube.com"):
            return None
        if segments[0].startswith("@"):
            username = segments[0][1:]
            canonical = f"https://www.youtube.com/@{username}"
        elif lower[0] in {"channel", "user", "c"} and len(segments) >= 2:
            username = segments[1]
            platform_id = username if lower[0] == "channel" else None
            canonical = f"https://www.youtube.com/{segments[0]}/{username}"
        else:
            return None
    elif platform == "soundcloud":
        if not host_matches(host, "soundcloud.com") or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED:
            return None
        username = segments[0]
        canonical = f"https://soundcloud.com/{username}"
    elif platform == "pinterest":
        if not host_matches(host, "pinterest.com") or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED:
            return None
        username = segments[0]
        canonical = f"https://www.pinterest.com/{username}/"
    elif platform == "depop":
        if not host_matches(host, "depop.com") or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED:
            return None
        username = segments[0]
        canonical = f"https://www.depop.com/{username}/"
    elif platform == "poshmark":
        # Relationship rows and profile navigation both use the stable
        # ``/closet/<username>`` form.  Do not accept listing/product URLs.
        if not host_matches(host, "poshmark.com") or len(segments) != 2 or lower[0] != "closet" or lower[1] in GENERIC_EXCLUDED:
            return None
        username = segments[1]
        canonical = f"https://poshmark.com/closet/{username}"
    elif platform == "spotify":
        if not host_matches(host, "spotify.com") or len(segments) < 2 or lower[0] != "user":
            return None
        username = segments[1]
        platform_id = username
        canonical = f"https://open.spotify.com/user/{username}"
    elif platform == "mastodon":
        if not _same_site(host, source_host) or len(segments) != 1 or not segments[0].startswith("@"):
            return None
        username = segments[0][1:]
        canonical = f"https://{host}/@{username}"
    elif platform == "linkedin":
        if not host_matches(host, "linkedin.com") or len(segments) < 2 or lower[0] not in {"in", "company", "school"}:
            return None
        username = segments[1]
        canonical = f"https://www.linkedin.com/{segments[0]}/{username}/"
    elif platform == "reddit":
        if not host_matches(host, "reddit.com") or len(segments) < 2 or lower[0] not in {"user", "u"}:
            return None
        username = segments[1]
        canonical = f"https://www.reddit.com/user/{username}/"
    elif platform == "steam":
        if not host_matches(host, "steamcommunity.com") or len(segments) < 2 or lower[0] not in {"id", "profiles"}:
            return None
        username = segments[1]
        platform_id = username if lower[0] == "profiles" and username.isdigit() else None
        canonical = f"https://steamcommunity.com/{segments[0]}/{username}/"
    elif platform == "flickr":
        if not host_matches(host, "flickr.com") or len(segments) < 2 or lower[0] != "people":
            return None
        username = segments[1]
        canonical = f"https://www.flickr.com/people/{username}/"
    elif platform == "goodreads":
        if not host_matches(host, "goodreads.com") or len(segments) < 3 or lower[0:2] != ["user", "show"]:
            return None
        username = segments[2]
        platform_id = username.split("-")[0] if username.split("-")[0].isdigit() else None
        canonical = f"https://www.goodreads.com/user/show/{username}"
    elif platform == "quora":
        if not host_matches(host, "quora.com") or len(segments) < 2 or lower[0] != "profile":
            return None
        username = segments[1]
        canonical = f"https://www.quora.com/profile/{username}"
    elif platform == "hudl":
        # Hudl exposes navigation links such as /cookies beside athlete rows.
        # Only its stable athlete-profile route is a canonical contact.
        if (
            not host_matches(host, "hudl.com")
            or len(segments) != 3
            or lower[0] != "profile"
            or not segments[1].isdigit()
        ):
            return None
        platform_id = segments[1]
        username = segments[2]
        canonical = f"https://www.hudl.com/profile/{platform_id}/{username}"
    elif platform == "generic":
        if not _same_site(host, source_host) or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED:
            return None
        username = segments[0].lstrip("@")
        canonical = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/" + segments[0], "", "", ""))
    else:
        # Common one-segment profile form for the remaining registered platforms.
        if not _same_site(host, source_host) or len(segments) != 1 or lower[0] in GENERIC_EXCLUDED:
            return None
        username = segments[0].lstrip("@")
        canonical = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/" + segments[0], "", "", ""))

    if not username:
        return None
    return username, canonical, platform_id


def canonical_from_network(platform: str, username: str, source_url: str, platform_id: str | None = None) -> str | None:
    username = str(username or "").strip().lstrip("@")
    if not username:
        return None
    if platform == "instagram":
        return f"https://www.instagram.com/{username}/"
    if platform == "x":
        return f"https://x.com/{username}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{username}"
    if platform == "bluesky":
        return f"https://bsky.app/profile/{username}"
    if platform == "threads":
        return f"https://www.threads.com/@{username}"
    if platform == "github":
        return f"https://github.com/{username}"
    if platform == "mastodon":
        host = urllib.parse.urlparse(source_url).hostname or ""
        return f"https://{host}/@{username}"
    if platform == "facebook":
        if platform_id and str(platform_id).isdigit():
            return f"https://www.facebook.com/profile.php?id={platform_id}"
        return f"https://www.facebook.com/{username}"
    if platform == "poshmark":
        return f"https://poshmark.com/closet/{username}"
    return None


def network_keywords(platform: str, relation: str) -> tuple[str, ...]:
    return SPECS.get(platform, SPECS["generic"]).network_keywords.get(relation, tuple())

# CONTACT_ANALYZER_UNIVERSAL_CATALOG
try:
    from .platform_catalog import install_into_adapters as _install_universal_catalog
    _install_universal_catalog(globals())
except Exception:
    # Core adapters remain available even if an optional catalog extension fails.
    pass
