from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .adapters import normalize_profile_link
from .browser import CDPBrowser
from .platform_catalog import platform_for_url
from .util import utc_now, write_json


APP_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_SCHEMA = Path(__file__).with_name("website_people.schema.json")

FACEBOOK_CONTENT_SEGMENTS = frozenset({"photos", "posts", "videos", "reel", "watch"})
BUSINESS_WORDS = frozenset({
    "barber", "barbers", "barbershop", "company", "corp", "corporation", "inc", "llc",
    "salon", "shop", "store", "studio", "official", "foundation", "association", "agency",
    "restaurant", "school", "church", "club", "team", "news", "media", "press", "hotel",
})
ABOUT_LABELS = (
    "about us", "about", "our team", "team", "meet the team", "meet us", "our story", "people",
)


PAGE_EVIDENCE_JS = r"""
(() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' &&
      s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
  };
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const text = (el) => clean(el?.innerText || el?.textContent || '');
  const anchors = [...document.querySelectorAll('a[href]')]
    .filter(visible)
    .map((a) => ({
      text: text(a).slice(0, 300),
      href: a.href,
      aria_label: a.getAttribute('aria-label') || '',
      context_text: text(a.closest('article,section,li,[role="listitem"],[role="article"],[data-testid],div') || a).slice(0, 1200)
    }));
  const headings = [...document.querySelectorAll('main h1,main h2,main h3,main h4,article h1,article h2,article h3,article h4,[role="main"] [role="heading"]')]
    .filter(visible)
    .map((el) => ({
      tag: el.tagName.toLowerCase(),
      text: text(el).slice(0, 500),
      context_text: text(el.closest('article,section,li,[role="listitem"],[data-testid],div') || el).slice(0, 1800)
    }));
  const jsonLdTypes = [];
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const walk = value => {
        if (!value || typeof value !== 'object') return;
        if (Array.isArray(value)) { value.forEach(walk); return; }
        const type = value['@type'];
        if (Array.isArray(type)) jsonLdTypes.push(...type.map(String));
        else if (type) jsonLdTypes.push(String(type));
        Object.values(value).forEach(walk);
      };
      walk(JSON.parse(script.textContent || 'null'));
    } catch (_) {}
  }
  return {
    current_url: location.href,
    title: document.title,
    body_text: text(document.body).slice(0, 100000),
    anchors: anchors.slice(0, 1000),
    headings: headings.slice(0, 500),
    page_signals: {
      og_type: document.querySelector('meta[property="og:type"]')?.content || '',
      published_time: document.querySelector('meta[property="article:published_time"],meta[name="date"],meta[name="pubdate"]')?.content || '',
      jsonld_types: [...new Set(jsonLdTypes)].slice(0, 100)
    }
  };
})()
"""


@dataclass
class WebsitePeopleOutcome:
    platform: str
    source_url: str
    inspected_url: str
    subject_present: bool
    status: str
    reason: str
    people: list[dict[str, Any]]
    analysis_mode: str
    author_name: str | None
    author_entity_type: str | None
    started_at: str
    completed_at: str
    diagnostics_path: str


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean_text(value).casefold())


def subject_display_name(subject_name: str) -> str:
    value = re.sub(r"^\s*\d+\s*[-_:]+\s*", "", str(subject_name or "")).strip()
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = re.sub(r"[_-]+", " ", value)
    return _clean_text(value)


def subject_is_visible(subject_name: str, body_text: str) -> bool:
    subject = _compact(subject_display_name(subject_name))
    return bool(subject and subject in _compact(body_text))


def normalized_person_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean_text(value).casefold()).strip()


def _validated_person_evidence(body_text: str, person_name: str, evidence_quote: str) -> str | None:
    """Return verbatim rendered evidence that contains the person's full name.

    Codex occasionally selects a valid sentence that refers to a nearby team-card
    heading by first name only. Accept that only when both excerpts are present in
    the rendered body, a name token appears in the quote, and the full-name heading
    is close enough to prove they belong to the same rendered section.
    """
    body = _clean_text(body_text)
    name = _clean_text(person_name)
    quote = _clean_text(evidence_quote)[:1000]
    if not body or not name or not quote:
        return None

    body_folded = body.casefold()
    name_folded = name.casefold()
    quote_folded = quote.casefold()
    quote_start = body_folded.find(quote_folded)
    if quote_start < 0:
        return None
    if name_folded in quote_folded:
        return body[quote_start:quote_start + len(quote)]

    name_tokens = [token for token in normalized_person_name(name).split() if len(token) >= 3]
    quote_tokens = set(normalized_person_name(quote).split())
    if not name_tokens or not any(token in quote_tokens for token in name_tokens):
        return None

    name_starts = [match.start() for match in re.finditer(re.escape(name_folded), body_folded)]
    if not name_starts:
        return None
    name_start = min(name_starts, key=lambda start: abs(start - quote_start))
    quote_end = quote_start + len(quote)
    name_end = name_start + len(name)
    if max(name_start, quote_start) - min(name_end, quote_end) > 600:
        return None

    excerpt_start = min(name_start, quote_start)
    excerpt_end = max(name_end, quote_end)
    if excerpt_end - excerpt_start > 1000:
        return None
    return body[excerpt_start:excerpt_end]


def is_facebook_content_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        return False
    path = parsed.path.casefold()
    segments = {item for item in path.split("/") if item}
    return bool(
        segments & FACEBOOK_CONTENT_SEGMENTS
        or path.endswith("/story.php")
        or path.endswith("/permalink.php")
        or parsed.path.casefold() in {"/story.php", "/permalink.php"}
    )


def is_instagram_content_url(url: str) -> bool:
    """Return true only for Instagram post/reel/media routes, never profiles."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host != "instagram.com" and not host.endswith(".instagram.com"):
        return False
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    return bool(segments and segments[0] in {"p", "reel", "reels", "tv"})


def is_article_page(evidence: dict[str, Any]) -> bool:
    """Recognize explicit editorial-article metadata without guessing from URLs."""
    signals = evidence.get("page_signals") if isinstance(evidence.get("page_signals"), dict) else {}
    og_type = _clean_text(signals.get("og_type")).casefold()
    published_time = _clean_text(signals.get("published_time"))
    schema_types = {
        _clean_text(value).casefold()
        for value in signals.get("jsonld_types") or []
        if _clean_text(value)
    }
    return bool(
        og_type in {"article", "newsarticle", "blogposting"}
        or published_time
        or schema_types & {"article", "newsarticle", "blogposting", "reportagenewsarticle"}
    )


def _looks_like_person_name(value: str) -> bool:
    clean = _clean_text(value)
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’.-]+", clean)
    if not 2 <= len(words) <= 5:
        return False
    lowered = {word.strip(".'’-").casefold() for word in words}
    return bool(lowered) and not bool(lowered & BUSINESS_WORDS)


def facebook_post_author(evidence: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    body = _compact(str(evidence.get("body_text") or "")[:2000])
    candidates: list[tuple[str, str]] = []
    for anchor in evidence.get("anchors") or []:
        href = str(anchor.get("href") or "")
        text = _clean_text(anchor.get("text") or anchor.get("aria_label"))
        normalized = normalize_profile_link("facebook", href, "https://www.facebook.com/source")
        if not normalized or not text or _compact(text) not in body:
            continue
        candidates.append((text, str(normalized[1])))
    if not candidates:
        return None, "unknown", None
    name, url = candidates[0]
    lowered = set(normalized_person_name(name).split())
    if lowered & BUSINESS_WORDS:
        return name, "organization", url
    if _looks_like_person_name(name):
        return name, "person", url
    return name, "unknown", url


def human_facebook_author_record(
    subject_name: str,
    source_url: str,
    evidence: dict[str, Any],
    author_name: str | None,
    author_url: str | None,
) -> dict[str, Any] | None:
    """Build one contact only for a visible, non-subject human post author."""
    name = _clean_text(author_name)
    if not name or _compact(name) == _compact(subject_display_name(subject_name)):
        return None
    body = _clean_text(evidence.get("body_text"))
    if not subject_is_visible(subject_name, body) or _compact(name) not in _compact(body):
        return None
    normalized = normalize_profile_link("facebook", str(author_url or ""), source_url)
    if not normalized:
        return None
    username, canonical_url, _ = normalized

    body_folded = body.casefold()
    name_start = body_folded.find(name.casefold())
    subject_text = subject_display_name(subject_name)
    subject_start = body_folded.find(subject_text.casefold())
    if name_start < 0 or subject_start < 0:
        return None
    excerpt_start = max(0, min(name_start, subject_start) - 120)
    excerpt_end = min(
        len(body),
        max(name_start + len(name), subject_start + len(subject_text)) + 360,
    )
    return {
        "normalized_name": normalized_person_name(name),
        "display_name": name,
        "role": None,
        "organization": None,
        "source_url": source_url,
        "evidence_text": body[excerpt_start:excerpt_end],
        "extraction_source": "visible_browser_facebook_post_author",
        "canonical_profile_url": canonical_url,
        "canonical_platform": "facebook",
        "username": username,
    }


def select_about_url(source_url: str, anchors: Iterable[dict[str, Any]]) -> str | None:
    source = urllib.parse.urlparse(source_url)
    source_host = (source.hostname or "").casefold()
    ranked: list[tuple[int, str]] = []
    for anchor in anchors:
        if not bool(anchor.get("href")):
            continue
        href = urllib.parse.urljoin(source_url, str(anchor.get("href")))
        parsed = urllib.parse.urlparse(href)
        host = (parsed.hostname or "").casefold()
        if host != source_host or parsed.scheme not in {"http", "https"}:
            continue
        label = _clean_text(anchor.get("text") or anchor.get("aria_label")).casefold()
        if not label or href.rstrip("/") == source_url.rstrip("/"):
            continue
        try:
            rank = ABOUT_LABELS.index(label)
        except ValueError:
            rank = next((index + len(ABOUT_LABELS) for index, term in enumerate(ABOUT_LABELS) if term in label), -1)
        if rank >= 0:
            ranked.append((rank, href.split("#", 1)[0]))
    return min(ranked)[1] if ranked else None


def _canonical_visible_profile(
    raw_url: str | None,
    declared_platform: str | None,
    source_url: str,
    person_name: str,
    anchors: Iterable[dict[str, Any]],
) -> tuple[str | None, str | None, str | None]:
    if not raw_url:
        return None, None, None
    raw = str(raw_url).strip()
    matched_context = ""
    for anchor in anchors:
        href = str(anchor.get("href") or "").strip()
        if href == raw or href.rstrip("/") == raw.rstrip("/"):
            matched_context = _clean_text(anchor.get("context_text") or anchor.get("text"))
            break
    if not matched_context or _compact(person_name) not in _compact(matched_context):
        return None, None, None
    platform = platform_for_url(raw)
    if declared_platform and platform != str(declared_platform):
        return None, None, None
    normalized = normalize_profile_link(platform, raw, source_url)
    if not normalized:
        return None, None, None
    return platform, str(normalized[1]), str(normalized[0])


def validate_codex_people(
    payload: dict[str, Any],
    *,
    subject_name: str,
    source_url: str,
    evidence: dict[str, Any],
    extraction_source: str,
) -> list[dict[str, Any]]:
    if not payload.get("subject_present") or not subject_is_visible(subject_name, str(evidence.get("body_text") or "")):
        return []
    body_compact = _compact(evidence.get("body_text"))
    subject_key = _compact(subject_display_name(subject_name))
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in payload.get("people") or []:
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("display_name"))
        name_key = normalized_person_name(name)
        if not name_key or name_key in seen or _compact(name) == subject_key:
            continue
        if not _looks_like_person_name(name) or _compact(name) not in body_compact:
            continue
        evidence_quote = _validated_person_evidence(
            str(evidence.get("body_text") or ""),
            name,
            str(raw.get("evidence_quote") or ""),
        )
        if not evidence_quote:
            continue
        canonical_platform, canonical_url, username = _canonical_visible_profile(
            raw.get("canonical_profile_url"),
            raw.get("canonical_platform"),
            source_url,
            name,
            evidence.get("anchors") or [],
        )
        seen.add(name_key)
        output.append({
            "normalized_name": name_key,
            "display_name": name,
            "role": _clean_text(raw.get("role")) or None,
            "organization": _clean_text(raw.get("organization")) or None,
            "source_url": source_url,
            "evidence_text": evidence_quote,
            "extraction_source": extraction_source,
            "canonical_profile_url": canonical_url,
            "canonical_platform": canonical_platform,
            "username": username,
        })
    return sorted(output, key=lambda item: item["normalized_name"])


def merge_associated_people(rows: Iterable[Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        key = str(item.get("normalized_name") or normalized_person_name(item.get("display_name") or ""))
        if not key:
            continue
        current = merged.setdefault(key, {
            "normalized_name": key,
            "display_name": item.get("display_name"),
            "canonical_profile_url": item.get("canonical_profile_url"),
            "canonical_platform": item.get("canonical_platform"),
            "contact_id": item.get("contact_id"),
            "roles": [],
            "organizations": [],
            "sources": [],
        })
        if not current.get("canonical_profile_url") and item.get("canonical_profile_url"):
            current["canonical_profile_url"] = item.get("canonical_profile_url")
            current["canonical_platform"] = item.get("canonical_platform")
            current["contact_id"] = item.get("contact_id")
        for field, target in (("role", "roles"), ("organization", "organizations")):
            value = _clean_text(item.get(field))
            if value and value not in current[target]:
                current[target].append(value)
        current["sources"].append({
            "profile_id": item.get("profile_id"),
            "source_platform": item.get("source_platform"),
            "source_url": item.get("source_url"),
            "evidence_text": item.get("evidence_text"),
            "extraction_source": item.get("extraction_source"),
            "first_seen_at": item.get("first_seen_at"),
            "last_seen_at": item.get("last_seen_at"),
            "times_seen": item.get("times_seen"),
        })
    return sorted(merged.values(), key=lambda item: str(item["display_name"] or item["normalized_name"]).casefold())


class WebsitePeopleCollector:
    def __init__(self, endpoint: str, settings: dict[str, Any] | None = None):
        self.endpoint = endpoint
        self.settings = settings or {}

    def _run_codex(self, prompt: str, output_path: Path, log_path: Path) -> dict[str, Any]:
        if not shutil.which("codex"):
            raise RuntimeError("codex_not_available")
        command = [
            "codex", "exec",
            "--sandbox", "read-only",
            "--ephemeral",
            "--output-schema", str(OUTPUT_SCHEMA),
            "--output-last-message", str(output_path),
            "--cd", str(APP_ROOT),
            "-",
        ]
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=float(self.settings.get("codex_timeout_seconds", 900)),
            check=False,
        )
        log_path.write_text(
            f"exit_code={completed.returncode}\n\nSTDOUT\n{completed.stdout}\n\nSTDERR\n{completed.stderr}\n",
            encoding="utf-8",
        )
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError(f"codex_exec_failed:{completed.returncode}")
        raw = output_path.read_text(encoding="utf-8").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("codex_output_not_an_object")
        return payload

    @staticmethod
    def _prompt(subject_name: str, source_url: str, evidence: dict[str, Any]) -> str:
        compact_evidence = {
            "source_url": source_url,
            "current_url": evidence.get("current_url"),
            "title": evidence.get("title"),
            "body_text": str(evidence.get("body_text") or "")[:70000],
            "headings": list(evidence.get("headings") or [])[:300],
            "visible_anchors": list(evidence.get("anchors") or [])[:500],
        }
        return f"""Analyze only the supplied rendered-page evidence. Do not browse, run commands, or infer URLs.

Subject: {subject_display_name(subject_name)}
Provided source URL: {source_url}

Rules:
1. Set subject_present true only if the named subject is visibly present in body_text.
2. Return only real human coworkers, co-owners, team members, staff, or close business collaborators explicitly connected to the subject in the same personnel card, team section, or sentence.
3. Exclude the subject, reporters/authors, commenters, customers, quoted people from unrelated sections, navigation/sidebar names, ads, companies, and organizations.
4. For an article, stay within the sentence or paragraph that connects the subject to coworkers/co-owners. Do not collect every person named in the article.
5. evidence_quote must be a short verbatim excerpt from body_text that includes the person's name and proves the association.
6. canonical_profile_url must be null unless an exact visible_anchors href is a person profile and that anchor's context_text includes this person's name. Never construct or guess a URL. Booking, post, media, search, tag, product, and company URLs are not person profiles.
7. Use null for unknown role, organization, canonical_profile_url, or canonical_platform.

Rendered evidence JSON:
{json.dumps(compact_evidence, ensure_ascii=False)}
"""

    def collect(
        self,
        *,
        subject_name: str,
        source_url: str,
        platform: str,
        diagnostics_dir: Path,
    ) -> WebsitePeopleOutcome:
        started = utc_now()
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        tab = CDPBrowser(self.endpoint).new_tab("about:blank")
        author_name: str | None = None
        author_type: str | None = None
        inspected_url = source_url
        analysis_mode = "direct"
        try:
            tab.navigate(source_url, float(self.settings.get("settle_seconds", 3.0)))
            evidence = tab.evaluate(PAGE_EVIDENCE_JS) or {}
            write_json(diagnostics_dir / "direct-page.json", evidence)
            tab.save_html(diagnostics_dir / "direct-page.html")
            tab.screenshot(diagnostics_dir / "direct-page.png")

            if is_facebook_content_url(source_url):
                author_name, author_type, author_url = facebook_post_author(evidence)
                write_json(diagnostics_dir / "facebook-author.json", {
                    "author_name": author_name,
                    "author_entity_type": author_type,
                    "author_profile_url": author_url,
                })
                if author_type != "person":
                    reason = "business_authored_facebook_post" if author_type == "organization" else "facebook_post_author_not_proven_human"
                    return WebsitePeopleOutcome(
                        platform, source_url, str(evidence.get("current_url") or source_url),
                        subject_is_visible(subject_name, str(evidence.get("body_text") or "")),
                        "skipped", reason, [], "facebook_author_gate", author_name, author_type,
                        started, utc_now(), str(diagnostics_dir),
                    )
                author_record = human_facebook_author_record(
                    subject_name,
                    source_url,
                    evidence,
                    author_name,
                    author_url,
                )
                if not author_record:
                    return WebsitePeopleOutcome(
                        platform, source_url, str(evidence.get("current_url") or source_url),
                        subject_is_visible(subject_name, str(evidence.get("body_text") or "")),
                        "skipped", "human_facebook_post_author_contact_not_validated", [],
                        "facebook_author_gate", author_name, author_type,
                        started, utc_now(), str(diagnostics_dir),
                    )
                write_json(diagnostics_dir / "validated-people.json", [author_record])
                return WebsitePeopleOutcome(
                    platform, source_url, str(evidence.get("current_url") or source_url), True,
                    "complete", "human_facebook_post_author_validated", [author_record],
                    "facebook_author_gate", author_name, author_type,
                    started, utc_now(), str(diagnostics_dir),
                )

            if platform == "generic" and is_article_page(evidence):
                return WebsitePeopleOutcome(
                    platform, source_url, str(evidence.get("current_url") or source_url),
                    subject_is_visible(subject_name, str(evidence.get("body_text") or "")),
                    "skipped", "editorial_article_not_people_source", [], "article_gate",
                    author_name, author_type, started, utc_now(), str(diagnostics_dir),
                )

            if not subject_is_visible(subject_name, str(evidence.get("body_text") or "")):
                fallback_url = select_about_url(source_url, evidence.get("anchors") or [])
                if not fallback_url:
                    return WebsitePeopleOutcome(
                        platform, source_url, str(evidence.get("current_url") or source_url), False,
                        "skipped", "subject_not_visible_and_no_about_page", [], "direct", author_name,
                        author_type, started, utc_now(), str(diagnostics_dir),
                    )
                tab.navigate(fallback_url, float(self.settings.get("settle_seconds", 3.0)))
                evidence = tab.evaluate(PAGE_EVIDENCE_JS) or {}
                inspected_url = fallback_url
                analysis_mode = "about_fallback"
                write_json(diagnostics_dir / "about-page.json", evidence)
                tab.save_html(diagnostics_dir / "about-page.html")
                tab.screenshot(diagnostics_dir / "about-page.png")
                if is_article_page(evidence):
                    return WebsitePeopleOutcome(
                        platform, source_url, str(evidence.get("current_url") or fallback_url),
                        subject_is_visible(subject_name, str(evidence.get("body_text") or "")),
                        "skipped", "editorial_article_not_people_source", [], "article_gate",
                        author_name, author_type, started, utc_now(), str(diagnostics_dir),
                    )
                if not subject_is_visible(subject_name, str(evidence.get("body_text") or "")):
                    return WebsitePeopleOutcome(
                        platform, source_url, str(evidence.get("current_url") or fallback_url), False,
                        "skipped", "subject_not_visible_on_direct_or_about_page", [], analysis_mode,
                        author_name, author_type, started, utc_now(), str(diagnostics_dir),
                    )

            codex_path = diagnostics_dir / "codex-analysis.json"
            codex_payload = self._run_codex(
                self._prompt(subject_name, inspected_url, evidence),
                codex_path,
                diagnostics_dir / "codex-exec.log",
            )
            people = validate_codex_people(
                codex_payload,
                subject_name=subject_name,
                source_url=inspected_url,
                evidence=evidence,
                extraction_source=f"codex_rendered_{analysis_mode}",
            )
            write_json(diagnostics_dir / "validated-people.json", people)
            return WebsitePeopleOutcome(
                platform, source_url, str(evidence.get("current_url") or inspected_url), True,
                "complete", "people_evidence_validated", people, analysis_mode, author_name,
                author_type, started, utc_now(), str(diagnostics_dir),
            )
        except Exception as exc:
            write_json(diagnostics_dir / "failure.json", {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "source_url": source_url,
                "inspected_url": inspected_url,
            })
            return WebsitePeopleOutcome(
                platform, source_url, inspected_url, False, "failed",
                f"website_people_analysis_failed:{type(exc).__name__}", [], analysis_mode,
                author_name, author_type, started, utc_now(), str(diagnostics_dir),
            )
        finally:
            tab.close()


def outcome_payload(outcome: WebsitePeopleOutcome) -> dict[str, Any]:
    return asdict(outcome)
