from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adapters import relation_url
from .browser import CDPBrowser
from .platform_catalog import (
    NON_ENUMERABLE_MODES,
    PROTECTED_CORE_PLATFORMS,
    RELATIONS,
    definition_for_name,
    graph_mode,
    load_learned,
    platform_for_url,
    save_learned,
)
from .util import parse_count, utc_now, write_json


CONTROL_SCAN_JS = r"""
(() => {
  const relationTerms = __RELATION_TERMS__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity || 1) > 0;
  };
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const cssPath = el => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id && !/\d{6,}/.test(el.id)) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    for (let depth = 0; node && node !== document.body && depth < 7; depth++, node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      const testid = node.getAttribute('data-testid');
      const role = node.getAttribute('role');
      const aria = node.getAttribute('aria-label');
      if (testid) part += `[data-testid="${CSS.escape(testid)}"]`;
      else if (role) part += `[role="${CSS.escape(role)}"]`;
      else if (aria && aria.length < 80) part += `[aria-label="${CSS.escape(aria)}"]`;
      else {
        const siblings = node.parentElement ? [...node.parentElement.children].filter(x => x.tagName === node.tagName) : [];
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
    }
    return parts.join(' > ');
  };
  const controls = [];
  const elements = [...document.querySelectorAll('a[href],button,[role="link"],[role="button"],summary')];
  for (const el of elements) {
    if (!visible(el)) continue;
    const text = clean([
      el.innerText,
      el.getAttribute('aria-label'),
      el.getAttribute('title')
    ].filter(Boolean).join(' '));
    if (!text || text.length > 140) continue;
    const lower = text.toLowerCase();
    const href = el.href || el.getAttribute('href') || '';
    for (const [relation, terms] of Object.entries(relationTerms)) {
      const term = terms.find(value => new RegExp(`(^|[^a-z])${value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^a-z]|$)`, 'i').test(lower));
      if (!term) continue;
      controls.push({
        relation,
        matchedTerm: term,
        text,
        href,
        selector: cssPath(el),
        tag: el.tagName,
        role: el.getAttribute('role') || '',
        aria: el.getAttribute('aria-label') || ''
      });
    }
  }
  return {
    url: location.href,
    title: document.title,
    controls,
    bodyText: clean(document.body?.innerText || '').slice(0, 2500)
  };
})()
"""

QUORA_PROFILE_RELATION_STATE_JS = r"""
(() => {
  const source = new URL(__SOURCE__);
  const current = new URL(location.href);
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const sourcePath = source.pathname.replace(/\/+$/, '');
  const currentPath = current.pathname.replace(/\/+$/, '');
  if (!/^\/profile\/[^/]+$/i.test(sourcePath) || currentPath !== sourcePath) {
    return {ok:false, reason:'not_source_quora_profile', url:location.href, title:document.title};
  }
  const tabs = [...document.querySelectorAll('[role="tab"]')]
    .filter(visible)
    .map(el => ({el, text:clean(el.innerText || el.textContent), rect:el.getBoundingClientRect()}))
    .filter(item => item.rect.y > 100);
  const follower = tabs.find(item => /^\d[\d,]*\s+followers?$/i.test(item.text));
  if (!follower) {
    return {ok:false, reason:'exact_source_follower_tab_not_found', url:location.href, title:document.title};
  }
  const match = follower.text.match(/^(\d[\d,]*)\s+followers?$/i);
  const raw = match ? match[1] : null;
  const count = raw === null ? null : Number(raw.replace(/,/g, ''));
  return {
    ok:Number.isFinite(count),
    relation:'followers',
    count:Number.isFinite(count) ? count : null,
    raw,
    text:follower.text,
    sourcePath,
    route:source.origin + sourcePath + '/followers',
    url:location.href,
    title:document.title
  };
})()
"""

VERIFY_LIST_JS = r"""
(() => {
  const selectors = __SELECTORS__;
  const platform = __PLATFORM__;
  const relation = __RELATION__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const hrefs = [];
  const seen = new Set();
  let relationshipFloor = null;
  if (platform === 'facebook') {
    const activeTab = [...document.querySelectorAll('a[role="tab"][aria-selected="true"]')]
      .find(el => (el.innerText || '').trim().toLowerCase() === relation);
    if (activeTab) relationshipFloor = activeTab.getBoundingClientRect().bottom;
  }
  for (const selector of selectors) {
    let nodes = [];
    try { nodes = [...document.querySelectorAll(selector)]; } catch (_) { continue; }
    for (const node of nodes) {
      const anchor = node.matches?.('a[href]') ? node : node.closest?.('a[href]') || node.querySelector?.('a[href]');
      const href = anchor?.href || '';
      if (!href || !visible(anchor) || seen.has(href)) continue;
      if (platform === 'facebook') {
        const rect = anchor.getBoundingClientRect();
        if (relationshipFloor === null || rect.bottom <= relationshipFloor + 2 || anchor.getAttribute('role') === 'tab') continue;
      }
      seen.add(href);
      hrefs.push(href);
    }
  }
  const body = (document.body?.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const emptyTerms = ['no followers', 'no following', 'no friends', 'no connections', 'nothing to show', 'no users found'];
  return {
    url: location.href,
    title: document.title,
    dialogVisible: [...document.querySelectorAll('[role="dialog"]')].some(visible),
    hrefs,
    emptyText: emptyTerms.find(term => body.includes(term)) || null,
    bodyText: body.slice(0, 1600)
  };
})()
"""

CLICK_SELECTOR_JS = r"""
(() => {
  const selector = __SELECTOR__;
  let el = null;
  try { el = document.querySelector(selector); } catch (_) { return {clicked:false, reason:'invalid_selector'}; }
  if (!el) return {clicked:false, reason:'not_found'};
  el.scrollIntoView({block:'center', inline:'center'});
  el.click();
  return {clicked:true};
})()
"""

RELATION_TERMS = {
    "followers": ["followers", "follower", "subscribers", "subscriber", "fans", "fan", "watchers", "watcher"],
    "following": ["following", "follows", "subscriptions", "subscribed", "watching"],
    "friends": ["friends", "friend", "connections", "connection", "contacts", "contact"],
}

ROUTE_TERMS = {
    "followers": ("followers", "subscribers", "fans", "watchers"),
    "following": ("following", "subscriptions", "watching"),
    "friends": ("friends", "connections", "contacts"),
}


@dataclass
class DiscoveryResult:
    platform: str
    source_url: str
    host: str
    title: str
    graph_mode: str
    available_relations: list[str]
    controls: list[dict[str, Any]]
    notes: str
    discovered_at: str


def _count_from_control(control: dict[str, Any], relation: str) -> int | None:
    text = str(control.get("text") or "")
    terms = "|".join(re.escape(item) for item in RELATION_TERMS[relation])
    for pattern in (
        re.compile(rf"(\d[\d,.\s]*[kmb]?)\s*(?:{terms})", re.I),
        re.compile(rf"(?:{terms})\s*(\d[\d,.\s]*[kmb]?)", re.I),
    ):
        match = pattern.search(text)
        if match:
            return parse_count(match.group(1))
    return None


def _same_site(source_url: str, target_url: str) -> bool:
    source = (urllib.parse.urlparse(source_url).hostname or "").casefold()
    target = (urllib.parse.urlparse(target_url).hostname or "").casefold()
    return bool(source and target and (source == target or source.endswith("." + target) or target.endswith("." + source)))


def _is_source_relationship_route(source_url: str, target_url: str, platform: str, relation: str) -> bool:
    if not _same_site(source_url, target_url):
        return False
    source = urllib.parse.urlparse(source_url)
    target = urllib.parse.urlparse(target_url)
    source_path = source.path.rstrip("/")
    target_path = target.path.rstrip("/")
    if platform == "facebook":
        if source_path.casefold().endswith("/profile.php"):
            source_query = urllib.parse.parse_qs(source.query)
            target_query = urllib.parse.parse_qs(target.query)
            source_ids = source_query.get("id") or []
            target_ids = target_query.get("id") or []
            allowed_sections = {relation}
            if relation == "friends":
                allowed_sections.add("friends_all")
            return bool(
                target_path.casefold().endswith("/profile.php")
                and source_ids
                and target_ids == source_ids
                and str((target_query.get("sk") or [""])[0]).casefold() in allowed_sections
            )
        return target_path in {
            f"{source_path}/{relation}",
            f"{source_path}/friends_all" if relation == "friends" else "",
        }
    source_parts = [part.casefold() for part in source_path.split("/") if part]
    target_parts = [part.casefold() for part in target_path.split("/") if part]
    route_match = any(term in urllib.parse.unquote(target_url).casefold() for term in ROUTE_TERMS[relation])
    return bool(route_match and (not source_parts or any(part in target_parts for part in source_parts)))


def _url_template(source_url: str, target_url: str) -> str | None:
    if not target_url or target_url in {"#", source_url + "#"}:
        return None
    source = urllib.parse.urlparse(source_url)
    target = urllib.parse.urlparse(target_url)
    if not target.hostname or not source.hostname or not _same_site(source_url, target_url):
        return None
    source_parts = [x for x in source.path.split("/") if x]
    target_path = target.path
    if source_parts:
        source_username = source_parts[-1].lstrip("@")
        target_path = re.sub(re.escape(source_username), "{username}", target_path, flags=re.I)
        if source_parts[0].startswith("@"):
            target_path = re.sub(re.escape(source_parts[0]), "@{username}", target_path, flags=re.I)
    query = ("?" + target.query) if target.query else ""
    return target_path + query


def _control_is_exact(control: dict[str, Any], relation: str) -> bool:
    text = str(control.get("text") or "").strip().casefold()
    href = str(control.get("href") or "").strip().casefold()
    count = control.get("count")
    route_match = any(term in urllib.parse.unquote(href) for term in ROUTE_TERMS[relation])
    compact_text = len(text) <= 80 and any(
        re.search(rf"(^|\s){re.escape(term)}(\s|$)", text, re.I)
        for term in RELATION_TERMS[relation]
    )
    # Buttons without routes are accepted only when they expose an exact count, as on
    # Instagram/TikTok dialogs. This rejects sidebar prose and recommendation cards.
    return bool(route_match or (compact_text and count is not None))


def _facebook_source_relations(
    source_url: str,
    controls: list[dict[str, Any]],
    allowed_relations: tuple[str, ...],
) -> list[str]:
    """Return only visible controls tied to the rendered source profile.

    Facebook can render a valid relationship route while withholding every row
    from the authenticated browser.  Discovery must still let the collector
    attempt that route so the run records an explicit incomplete/failed result.
    The source-route check rejects Facebook's global ``/friends/`` navigation.
    """
    available: list[str] = []
    for relation in allowed_relations:
        matches = [
            control
            for control in controls
            if control.get("relation") == relation
            and control.get("exact_candidate") is True
            and _is_source_relationship_route(
                source_url,
                str(control.get("href") or ""),
                "facebook",
                relation,
            )
        ]
        if not matches:
            continue
        available.append(relation)
        for control in matches:
            control["verified"] = True
            control["verification_reason"] = "source_scoped_profile_relationship_control"
    return available


def _verify_candidate(tab: Any, source_url: str, platform: str, relation: str, control: dict[str, Any], selectors: tuple[str, ...], settle_seconds: float) -> tuple[bool, dict[str, Any], list[str]]:
    href = str(control.get("href") or "").strip()
    selector = str(control.get("selector") or "").strip()
    opened = False
    try:
        # LinkedIn profile pages commonly contain recommendation and experience
        # cards with large company follower counts. Those destination profiles
        # are not the subject's relationship directories, even if LinkedIn's
        # persistent messaging overlay happens to make a dialog visible.
        if (
            platform == "linkedin"
            and href
            and href not in {"#", source_url + "#"}
            and not _is_source_relationship_route(source_url, href, platform, relation)
        ):
            return False, {
                "verification_reason": "no_profile_rows_in_relationship_context",
                "opened_url": href,
                "dialog_visible": False,
                "valid_profile_rows": 0,
                "empty_text": None,
            }, []
        if href and href not in {"#", source_url + "#"} and _same_site(source_url, href):
            tab.navigate(href, settle_seconds)
            opened = True
        elif selector:
            action = tab.evaluate(CLICK_SELECTOR_JS.replace("__SELECTOR__", json.dumps(selector))) or {}
            opened = bool(action.get("clicked"))
            if opened:
                time.sleep(settle_seconds)
        if not opened:
            return False, {"verification_reason": "control_not_openable"}, []

        state = tab.evaluate(
            VERIFY_LIST_JS
            .replace("__SELECTORS__", json.dumps(list(selectors)))
            .replace("__PLATFORM__", json.dumps(platform))
            .replace("__RELATION__", json.dumps(relation))
        ) or {}
        from .adapters import normalize_profile_link

        valid_urls: list[str] = []
        seen: set[str] = set()
        for candidate in state.get("hrefs") or []:
            normalized = normalize_profile_link(platform, str(candidate), source_url)
            if not normalized:
                continue
            canonical = str(normalized[1]).rstrip("/")
            key = canonical.casefold()
            if key in seen:
                continue
            seen.add(key)
            valid_urls.append(canonical)

        target_url = str(state.get("url") or "")
        relation_route = _is_source_relationship_route(source_url, target_url, platform, relation)
        # Navigating to any other same-site profile is not relationship evidence.
        # Recommendation cards often contain follower counts and can otherwise make
        # an unrelated company/person page look like the source subject's list.
        list_context = bool(state.get("dialogVisible") or relation_route)
        reported = control.get("count")
        verified = bool(list_context and (valid_urls or state.get("emptyText") or reported == 0))
        reason = (
            "verified_profile_rows"
            if verified and valid_urls
            else "verified_empty_list"
            if verified
            else "no_profile_rows_in_relationship_context"
        )
        evidence = {
            "verified": verified,
            "verification_reason": reason,
            "opened_url": target_url,
            "dialog_visible": bool(state.get("dialogVisible")),
            "valid_profile_rows": len(valid_urls),
            "empty_text": state.get("emptyText"),
        }
        return verified, evidence, valid_urls
    finally:
        try:
            if tab.current_url().rstrip("/") != source_url.rstrip("/"):
                tab.navigate(source_url, settle_seconds)
            else:
                # Close modal overlays and restore a clean profile page.
                tab.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true}))")
                time.sleep(0.25)
        except Exception:
            pass


def _write_result(output_path: Path | None, result: DiscoveryResult) -> DiscoveryResult:
    if output_path:
        write_json(output_path, asdict(result))
    return result


def discover_profile(endpoint: str, source_url: str, platform: str | None = None, *, settle_seconds: float = 3.0, output_path: Path | None = None) -> DiscoveryResult:
    detected = platform or platform_for_url(source_url)
    definition = definition_for_name(detected)
    parsed = urllib.parse.urlparse(source_url)
    host = (parsed.hostname or "").lower()
    mode = graph_mode(detected)

    # These platforms have dedicated, source-scoped modal behavior. Never let
    # generic discovery click arbitrary controls, visit row links, or learn
    # broad routes from recommendation content.
    if detected in {"tiktok", "threads", "pinterest", "depop"}:
        adapter_kind = "modal" if detected in {"tiktok", "threads"} else "relationship"
        return _write_result(output_path, DiscoveryResult(
            platform=detected, source_url=source_url, host=host, title="",
            graph_mode="conditional", available_relations=["followers", "following"],
            controls=[], notes=f"Dedicated {detected.title()} {adapter_kind} adapter; generic discovery disabled.", discovered_at=utc_now(),
        ))

    if mode in NON_ENUMERABLE_MODES:
        return _write_result(output_path, DiscoveryResult(
            platform=detected,
            source_url=source_url,
            host=host,
            title="",
            graph_mode=mode,
            available_relations=[],
            controls=[],
            notes=definition.notes or "This profile type has no enumerable browser relationship list and was skipped.",
            discovered_at=utc_now(),
        ))

    browser = CDPBrowser(endpoint)
    tab = browser.new_tab("about:blank")
    try:
        tab.navigate(source_url, settle_seconds)
        expression = CONTROL_SCAN_JS.replace("__RELATION_TERMS__", json.dumps(RELATION_TERMS))
        raw = tab.evaluate(expression) or {}
        controls = list(raw.get("controls") or [])
        allowed = set(definition.relations if detected != "generic" else RELATIONS)
        controls = [item for item in controls if item.get("relation") in allowed]
        for control in controls:
            relation = str(control.get("relation") or "")
            control["count"] = _count_from_control(control, relation)
            control["exact_candidate"] = _control_is_exact(control, relation)

        if detected == "quora":
            state = tab.evaluate(
                QUORA_PROFILE_RELATION_STATE_JS.replace("__SOURCE__", json.dumps(source_url))
            ) or {}
            dedicated_controls: list[dict[str, Any]] = []
            available: list[str] = []
            if state.get("ok"):
                available.append("followers")
                dedicated_controls.append({
                    "relation": "followers",
                    "matchedTerm": "follower",
                    "text": state.get("text"),
                    "href": state.get("route"),
                    "selector": "[role='tab']",
                    "tag": "DIV",
                    "role": "tab",
                    "aria": "",
                    "count": state.get("count"),
                    "exact_candidate": True,
                    "verified": True,
                    "verification_reason": "source_scoped_quora_follower_tab",
                })
            notes = (
                "Dedicated Quora source-profile follower tab adapter. "
                "Quora's Following view exposes Spaces, Topics, and Questions rather than "
                "an enumerable people directory, so it is not collected."
            )
            if not available:
                notes += " No exact source-profile follower tab was found."
            return _write_result(output_path, DiscoveryResult(
                platform=detected,
                source_url=source_url,
                host=host,
                title=str(state.get("title") or raw.get("title") or tab.title()),
                graph_mode=mode,
                available_relations=available,
                controls=dedicated_controls,
                notes=notes,
                discovered_at=utc_now(),
            ))

        if detected == "facebook":
            # Use Facebook's live canonical URL because it may normalize the
            # supplied vanity name (for example Unicode dotted-I to ASCII i).
            rendered_source_url = str(raw.get("url") or source_url).split("#", 1)[0]
            available = _facebook_source_relations(
                rendered_source_url,
                controls,
                definition.relations,
            )
            if "friends" in available and "following" not in available:
                friends_url = relation_url("facebook", rendered_source_url, "friends")
                if friends_url:
                    tab.navigate(friends_url, max(settle_seconds, 12.0))
                    directory_raw = tab.evaluate(expression) or {}
                    known_controls = {
                        (
                            str(item.get("relation") or ""),
                            str(item.get("href") or "").rstrip("/").casefold(),
                            str(item.get("text") or "").casefold(),
                        )
                        for item in controls
                    }
                    for control in directory_raw.get("controls") or []:
                        relation = str(control.get("relation") or "")
                        if relation not in allowed:
                            continue
                        control["count"] = _count_from_control(control, relation)
                        control["exact_candidate"] = _control_is_exact(control, relation)
                        key = (
                            relation,
                            str(control.get("href") or "").rstrip("/").casefold(),
                            str(control.get("text") or "").casefold(),
                        )
                        if key in known_controls:
                            continue
                        known_controls.add(key)
                        control["discovery_context"] = "facebook_friend_directory_tabs"
                        controls.append(control)
                    available = _facebook_source_relations(
                        rendered_source_url,
                        controls,
                        definition.relations,
                    )
            notes = definition.notes
            if available:
                notes = (
                    f"{notes} Exact source-profile controls found: "
                    f"{', '.join(available)}."
                ).strip()
            return _write_result(output_path, DiscoveryResult(
                platform=detected,
                source_url=source_url,
                host=host,
                title=str(raw.get("title") or tab.title()),
                graph_mode=mode,
                available_relations=available,
                controls=controls,
                notes=notes,
                discovered_at=utc_now(),
            ))

        # Proven core adapters remain authoritative; discovery here is diagnostic only.
        if mode == "enumerable" or detected in PROTECTED_CORE_PLATFORMS:
            return _write_result(output_path, DiscoveryResult(
                platform=detected,
                source_url=source_url,
                host=host,
                title=str(raw.get("title") or tab.title()),
                graph_mode=mode,
                available_relations=list(definition.relations),
                controls=controls,
                notes=definition.notes,
                discovered_at=utc_now(),
            ))

        verified_relations: list[str] = []
        learned = load_learned()
        host_entry = learned.setdefault(host, {
            "platform": detected,
            "source_urls": [],
            "relations": {},
            "profile_patterns": [],
            "updated_at": utc_now(),
        })
        if source_url not in host_entry["source_urls"]:
            host_entry["source_urls"].append(source_url)

        for relation in definition.relations if detected != "generic" else RELATIONS:
            candidates = [item for item in controls if item.get("relation") == relation and item.get("exact_candidate")]
            candidates.sort(key=lambda item: (
                0 if _is_source_relationship_route(source_url, str(item.get("href") or ""), detected, relation) else 1,
                0 if item.get("count") is not None else 1,
                0 if item.get("href") else 1,
                len(str(item.get("text") or "")),
            ))
            verified_control = None
            verified_urls: list[str] = []
            for candidate in candidates[:4]:
                verified, evidence, valid_urls = _verify_candidate(
                    tab,
                    source_url,
                    detected,
                    relation,
                    candidate,
                    definition.row_selectors,
                    max(settle_seconds, 12.0) if detected == "facebook" else settle_seconds,
                )
                candidate.update(evidence)
                if verified:
                    verified_control = candidate
                    verified_urls = valid_urls
                    break

            if not verified_control:
                continue

            verified_relations.append(relation)
            href = str(verified_control.get("href") or "")
            target = urllib.parse.urlparse(href) if href else None
            fragments = []
            if target and target.path:
                fragments.append(target.path + (("?" + target.query) if target.query else ""))
            host_entry["relations"][relation] = {
                "verified": True,
                "control_selector": verified_control.get("selector"),
                "control_text": verified_control.get("text"),
                "reported_count": verified_control.get("count"),
                "url_template": _url_template(source_url, href),
                "href_fragments": fragments,
                "sample_profile_urls": verified_urls[:10],
                "updated_at": utc_now(),
            }

        host_entry["updated_at"] = utc_now()
        # Remove unverified stale relations from previous 1.2.0 discovery runs.
        host_entry["relations"] = {
            relation: data for relation, data in host_entry.get("relations", {}).items()
            if isinstance(data, dict) and data.get("verified") is True and relation in verified_relations
        }
        if verified_relations:
            save_learned(learned)
        elif host in learned:
            learned.pop(host, None)
            save_learned(learned)

        notes = definition.notes
        if not verified_relations:
            notes = notes or "No verified enumerable relationship list was found. The profile was recorded, but no contacts were collected."

        return _write_result(output_path, DiscoveryResult(
            platform=detected,
            source_url=source_url,
            host=host,
            title=str(raw.get("title") or tab.title()),
            graph_mode=mode,
            available_relations=verified_relations,
            controls=controls,
            notes=notes,
            discovered_at=utc_now(),
        ))
    finally:
        tab.close(close_target=True)
