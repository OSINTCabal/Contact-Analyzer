from __future__ import annotations

import html
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .util import utc_now, write_json
from .collection_status import (
    NON_COLLECTIBLE_REASONS,
    SUCCESS_STATUSES,
    build_run_summary,
    cumulative_relationship_status,
    reason_label,
    relationship_coverage_note,
    run_status_label,
    status_label,
)
from .website_people import merge_associated_people

RELATIONS = ("followers", "following", "friends")


def _subject_root(vault: Path, slug: str) -> Path:
    return vault / "Subjects" / slug


def _contact_item(db: Any, row: Any) -> dict[str, Any]:
    sources = [dict(source) for source in db.contact_sources(int(row["id"]))]
    relations = sorted({str(source["relation"]) for source in sources if source.get("relation")})
    return {
        "contact_id": int(row["id"]),
        "platform": str(row["platform"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "profile_url": str(row["canonical_url"]),
        "platform_user_id": row["platform_user_id"],
        "avatar_url": row["avatar_url"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "relations": relations,
        "sources": sources,
    }


def _md_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _svg_bar_chart(path: Path, title: str, rows: list[tuple[str, int]], *, width: int = 1600) -> None:
    rows = sorted(rows, key=lambda item: (-item[1], item[0].casefold()))
    row_h = 48
    top = 110
    bottom = 60
    height = max(320, top + len(rows) * row_h + bottom)
    label_w = 250
    value_w = 110
    chart_w = width - label_w - value_w - 90
    max_value = max((value for _, value in rows), default=1) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="24" fill="#111318"/>',
        f'<text x="48" y="58" fill="#f4f7fb" font-family="Inter,Arial,sans-serif" font-size="30" font-weight="700">{html.escape(title)}</text>',
        '<text x="48" y="88" fill="#8f9bad" font-family="Inter,Arial,sans-serif" font-size="15">Accumulated unique platform accounts · canonical URL deduplication</text>',
    ]
    for index, (label, value) in enumerate(rows):
        y = top + index * row_h
        bar_y = y + 8
        bar_h = 24
        bar_w = int(chart_w * value / max_value)
        parts.extend([
            f'<text x="48" y="{y + 27}" fill="#dfe6ef" font-family="JetBrains Mono,monospace" font-size="16">{html.escape(label)}</text>',
            f'<rect x="{label_w}" y="{bar_y}" width="{chart_w}" height="{bar_h}" rx="12" fill="#232833"/>',
            f'<rect x="{label_w}" y="{bar_y}" width="{max(3, bar_w)}" height="{bar_h}" rx="12" fill="#8b5cf6"/>',
            f'<text x="{label_w + chart_w + 24}" y="{y + 27}" fill="#ffffff" font-family="JetBrains Mono,monospace" font-size="17" font-weight="700">{value}</text>',
        ])
    parts.append('</svg>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _write_contact_markdown(
    path: Path,
    title: str,
    subject: Any,
    contacts: list[dict[str, Any]],
    *,
    platform: str | None = None,
    relation: str | None = None,
    relationship_edges: int | None = None,
    coverage: list[dict[str, Any]] | None = None,
) -> None:
    lines = [
        "---",
        f'subject: "{subject["name"]}"',
        f"subject_id: {subject['id']}",
        f"generated: {utc_now()}",
        "cssclasses:",
        "  - contact-analyzer-wide",
        "tags:",
        "  - contact-analyzer",
        "  - contact-list",
    ]
    if platform:
        lines.append(f"  - platform/{platform}")
    if relation:
        lines.append(f"  - relation/{relation}")
    lines.extend([
        "---",
        "",
        f"# {title}",
        "",
    ])
    if relationship_edges is None:
        lines.extend([
            f"> [!info] **{len(contacts)} unique platform accounts**",
            f"> Generated `{utc_now()}`. Identity is platform + canonical profile URL.",
            "",
        ])
    else:
        overlap = max(0, relationship_edges - len(contacts))
        membership_word = "membership" if overlap == 1 else "memberships"
        lines.extend([
            (
                f"> [!info] **{len(contacts)} unique platform accounts** · "
                f"**{relationship_edges} saved relationship edges**"
            ),
            (
                f"> **{overlap} additional relationship {membership_word}** belong to accounts already "
                "represented in the unique-account total. Accounts in both Followers and Following are listed once, "
                "with both edges retained."
            ),
            f"> Generated `{utc_now()}`. Identity is platform + canonical profile URL.",
            "",
        ])

    if coverage:
        lines.extend(_coverage_markdown_lines(coverage))
        lines.append("")

    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in contacts:
        by_platform[str(item["platform"])].append(item)

    for platform_name, items in sorted(by_platform.items()):
        if len(by_platform) > 1:
            lines.extend([f"## {platform_name.title()} · {len(items)}", ""])
        lines.extend([
            "| Username | Display name | Relationships | Profile | First seen | Last seen |",
            "|---|---|---|---|---|---|",
        ])
        for item in items:
            relations = ", ".join(item.get("relations") or [])
            url = _md_escape(item.get("profile_url"))
            lines.append(
                "| {username} | {display} | {relations} | [Open profile]({url}) | {first} | {last} |".format(
                    username=_md_escape(item.get("username")),
                    display=_md_escape(item.get("display_name")),
                    relations=_md_escape(relations),
                    url=url,
                    first=_md_escape(item.get("first_seen_at")),
                    last=_md_escape(item.get("last_seen_at")),
                )
            )
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _relationship_counts(db: Any, subject_id: int) -> dict[str, int]:
    counts = {relation: 0 for relation in RELATIONS}
    for row in db.conn.execute(
        """
        SELECT e.relation,COUNT(*) AS n
        FROM contact_edges e
        JOIN contacts c ON c.id=e.contact_id
        WHERE c.subject_id=?
        GROUP BY e.relation
        """,
        (subject_id,),
    ):
        counts[str(row["relation"])] = int(row["n"])
    return counts


def subject_relationship_coverage(db: Any, subject_id: int) -> list[dict[str, Any]]:
    """Build authoritative cumulative coverage for all known relationships.

    Counts saved in ``contact_edges`` are authoritative for accumulated data.
    Displayed counts and browser-access status come from the latest known result
    for each profile/relationship pair, even when a later run did not attempt
    that pair.
    """
    latest_rows = db.latest_relationship_results(subject_id)
    latest_map = {
        (int(row["profile_id"]), str(row["relation"])): row
        for row in latest_rows
    }
    total_map = {
        (int(row["profile_id"]), str(row["relation"])): int(row["total_unique_saved"] or 0)
        for row in db.all_profile_totals(subject_id)
        if row["relation"]
    }
    keys = sorted(
        set(latest_map) | set(total_map),
        key=lambda key: (
            str((latest_map.get(key) or {})["platform"] if latest_map.get(key) else ""),
            key[0],
            RELATIONS.index(key[1]) if key[1] in RELATIONS else 99,
        ),
    )
    coverage: list[dict[str, Any]] = []
    for profile_id, relation in keys:
        latest = latest_map.get((profile_id, relation))
        saved = int(total_map.get((profile_id, relation), 0))
        reported = (
            int(latest["reported_count"])
            if latest is not None and latest["reported_count"] is not None
            else None
        )
        latest_status = str(latest["status"] if latest is not None else "not_run")
        latest_reason = str(latest["reason"] if latest is not None else "not_run")
        status, reason = cumulative_relationship_status(
            latest_status,
            latest_reason,
            reported,
            saved,
        )
        remaining_gap = max(0, reported - saved) if reported is not None else None
        excess = max(0, saved - reported) if reported is not None else 0
        collected_latest = int(latest["collected_this_run"] or 0) if latest is not None else 0
        platform = str(latest["platform"] if latest is not None else "")
        source_url = str(latest["source_profile_url"] if latest is not None else "")
        if latest is None:
            profile = db.conn.execute(
                "SELECT platform,url FROM profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
            if profile:
                platform = str(profile["platform"])
                source_url = str(profile["url"])
        private = status == "private"
        unavailable = reason in NON_COLLECTIBLE_REASONS
        excluded = private or unavailable
        coverage.append({
            "profile_id": profile_id,
            "platform": platform,
            "source_profile_url": source_url,
            "relationship": relation,
            "reported_count": reported,
            "latest_pass_count": collected_latest,
            "saved_unique_urls": saved,
            "remaining_gap": remaining_gap,
            "excess_records": excess,
            "coverage_percent": (
                round(min(saved, reported) * 100 / reported, 1)
                if reported not in (None, 0)
                else (100.0 if reported == 0 and saved == 0 else None)
            ),
            "status": status,
            "status_label": status_label(status, reason),
            "reason": reason,
            "reason_label": reason_label(reason),
            "latest_attempt_status": latest_status,
            "latest_attempt_status_label": status_label(latest_status, latest_reason),
            "latest_attempt_reason": latest_reason,
            "collectible": not excluded,
            "excluded_from_collectible_coverage": excluded,
            "complete": status in SUCCESS_STATUSES,
            "coverage_note": relationship_coverage_note(
                platform,
                reported,
                collected_latest,
                saved,
                status,
                reason,
            ),
            "last_run_stamp": latest["run_stamp"] if latest is not None else None,
            "last_completed_at": latest["completed_at"] if latest is not None else None,
        })
    return coverage


def summarize_relationship_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    private = [row for row in rows if row["status"] == "private"]
    unavailable = [
        row for row in rows
        if row["reason"] in NON_COLLECTIBLE_REASONS
    ]
    collectible = [row for row in rows if not row["excluded_from_collectible_coverage"]]
    exact = [row for row in collectible if row["reported_count"] is not None]
    displayed = sum(int(row["reported_count"]) for row in exact)
    saved_exact = sum(
        min(int(row["reported_count"]), int(row["saved_unique_urls"]))
        for row in exact
    )
    return {
        "relationships": len(rows),
        "collectible_relationships": len(collectible),
        "verified_relationships": sum(1 for row in collectible if row["complete"]),
        "incomplete_relationships": sum(1 for row in collectible if not row["complete"]),
        "exact_count_relationships": len(exact),
        "unknown_count_relationships": len(collectible) - len(exact),
        "collectible_displayed_records": displayed,
        "collectible_saved_records": saved_exact,
        "collectible_remaining_gap": sum(int(row["remaining_gap"] or 0) for row in exact),
        "collectible_coverage_percent": round(saved_exact * 100 / displayed, 1) if displayed else None,
        "private_relationships": len(private),
        "private_displayed_records": sum(int(row["reported_count"] or 0) for row in private),
        "unavailable_relationships": len(unavailable),
    }


def _coverage_markdown_lines(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    lines = [
        "## Relationship collection coverage",
        "",
        "| Platform | Relationship | Displayed | Latest pass | Saved | Gap | Status |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        displayed = row["reported_count"] if row["reported_count"] is not None else "?"
        gap = (
            "Excluded — private"
            if row["status"] == "private"
            else "Excluded — unavailable"
            if row["excluded_from_collectible_coverage"]
            else (row["remaining_gap"] if row["remaining_gap"] is not None else "?")
        )
        lines.append(
            f"| {_md_escape(row['platform'])} | {_md_escape(row['relationship'])} | {displayed} | "
            f"{row['latest_pass_count']} | {row['saved_unique_urls']} | {gap} | "
            f"{_md_escape(row['status_label'])} |"
        )
    return lines


def write_subject_aux_exports(db: Any, vault: Path, subject_id: int) -> Path:
    subject = db.get_subject(subject_id)
    if not subject:
        raise ValueError(f"Subject not found: {subject_id}")

    root = _subject_root(vault, str(subject["slug"]))
    platforms_dir = root / "platforms"
    lists_dir = root / "lists"
    assets_dir = root / "assets"
    platforms_dir.mkdir(parents=True, exist_ok=True)
    lists_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    contacts = [_contact_item(db, row) for row in db.subject_contacts(subject_id)]
    associated_people = merge_associated_people(db.subject_associated_people(subject_id))
    contacts.sort(key=lambda item: (str(item["platform"]).casefold(), str(item.get("username") or item["profile_url"]).casefold()))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in contacts:
        grouped[str(item["platform"])].append(item)
    profile_platforms = sorted({str(row["platform"]) for row in db.profiles_for_subject(subject_id)} | set(grouped))

    relation_counts = _relationship_counts(db, subject_id)
    total_relationship_edges = sum(relation_counts.values())
    repeated_memberships = max(0, total_relationship_edges - len(contacts))
    repeated_membership_word = "membership" if repeated_memberships == 1 else "memberships"
    relationship_coverage = subject_relationship_coverage(db, subject_id)
    coverage_summary = summarize_relationship_coverage(relationship_coverage)
    _write_contact_markdown(
        root / "Master Contacts.md",
        f"Master Contacts · {subject['name']}",
        subject,
        contacts,
        relationship_edges=total_relationship_edges,
        coverage=relationship_coverage,
    )
    associated_lines = [
        "---",
        f'subject: "{subject["name"]}"',
        f"subject_id: {subject['id']}",
        f"generated: {utc_now()}",
        "tags:",
        "  - contact-analyzer",
        "  - associated-people",
        "---",
        "",
        f"# Associated People · {subject['name']}",
        "",
        (
            f"> [!info] **{len(associated_people)} unique named people** found in direct-page business/team evidence. "
            "Only rows with a verified canonical person profile URL are promoted into Master Contacts."
        ),
        "",
        "| Name | Role | Organization | Canonical profile | Evidence sources |",
        "|---|---|---|---|---:|",
    ]
    for person in associated_people:
        url = str(person.get("canonical_profile_url") or "")
        profile = f"[Open profile]({_md_escape(url)})" if url else "Unresolved — no visible person URL"
        associated_lines.append(
            f"| {_md_escape(person.get('display_name'))} | {_md_escape(', '.join(person.get('roles') or []))} | "
            f"{_md_escape(', '.join(person.get('organizations') or []))} | {profile} | {len(person.get('sources') or [])} |"
        )
    associated_lines.extend(["", "## Source evidence", ""])
    for person in associated_people:
        associated_lines.append(f"### {_md_escape(person.get('display_name'))}")
        associated_lines.append("")
        for source in person.get("sources") or []:
            associated_lines.append(
                f"- [{_md_escape(source.get('source_platform'))}]({_md_escape(source.get('source_url'))}) — "
                f"{_md_escape(source.get('evidence_text'))}"
            )
        associated_lines.append("")
    (root / "Associated People.md").write_text("\n".join(associated_lines) + "\n", encoding="utf-8")
    write_json(root / "associated_people.json", {
        "subject_id": int(subject["id"]),
        "subject_name": str(subject["name"]),
        "generated_at": utc_now(),
        "unique_associated_people": len(associated_people),
        "unresolved_associated_people": sum(1 for item in associated_people if not item.get("canonical_profile_url")),
        "people": associated_people,
    })

    relationship_platforms = sorted(
        set(grouped) | {str(row["platform"]) for row in relationship_coverage}
    )
    for platform in relationship_platforms:
        platform_contacts = grouped.get(platform, [])
        platform_coverage = [
            row for row in relationship_coverage if row["platform"] == platform
        ]
        _write_contact_markdown(
            platforms_dir / f"{platform}.md",
            f"{platform.title()} Contacts · {subject['name']}",
            subject,
            platform_contacts,
            platform=platform,
            coverage=platform_coverage,
        )
        for relation in RELATIONS:
            relation_contacts = [item for item in platform_contacts if relation in set(item.get("relations") or [])]
            relation_coverage = [
                row for row in platform_coverage if row["relationship"] == relation
            ]
            payload = {
                "subject_id": int(subject["id"]),
                "subject_name": str(subject["name"]),
                "subject_slug": str(subject["slug"]),
                "platform": platform,
                "relationship": relation,
                "generated_at": utc_now(),
                "unique_contacts": len(relation_contacts),
                "relationship_coverage": relation_coverage,
                "relationship_coverage_summary": summarize_relationship_coverage(relation_coverage),
                "contacts": relation_contacts,
            }
            write_json(lists_dir / platform / f"{relation}.json", payload)
            _write_contact_markdown(
                lists_dir / platform / f"{relation}.md",
                f"{platform.title()} {relation.title()} · {subject['name']}",
                subject,
                relation_contacts,
                platform=platform,
                relation=relation,
                coverage=relation_coverage,
            )

    platform_rows = [(platform, len(items)) for platform, items in grouped.items()]
    relation_rows = [(relation.title(), relation_counts[relation]) for relation in RELATIONS]
    _svg_bar_chart(assets_dir / "Platform Contacts.svg", f"Platform Contacts · {subject['name']}", platform_rows)
    _svg_bar_chart(assets_dir / "Relationship Coverage.svg", f"Relationship Coverage · {subject['name']}", relation_rows)

    latest_run = db.latest_run(subject_id)
    latest_results = [dict(row) for row in db.latest_results(subject_id)]
    latest_status = (
        run_status_label(str(latest_run["status"]), latest_results)
        if latest_run else "Never run"
    )
    summary = [
        "---",
        f'subject: "{subject["name"]}"',
        f"subject_id: {subject['id']}",
        f"generated: {utc_now()}",
        "cssclasses:",
        "  - contact-analyzer-wide",
        "tags:",
        "  - contact-analyzer",
        "  - social-graph",
        "---",
        "",
        f"# Contact Analyzer · {subject['name']}",
        "",
        "> [!summary] Collection status",
        f"> **{len(contacts)}** unique platform accounts after canonical URL deduplication  ",
        f"> **{len(associated_people)}** unique associated people from verified website/team evidence  ",
        f"> **{total_relationship_edges}** saved relationship edges  ",
        f"> **{repeated_memberships}** repeated {repeated_membership_word} are retained as edges but listed once in Master Contacts  ",
        f"> **{relation_counts['followers']}** follower edges · **{relation_counts['following']}** following edges · **{relation_counts['friends']}** friend/connection edges  ",
        (
            f"> Collectible exact-count coverage: **{coverage_summary['collectible_saved_records']}/"
            f"{coverage_summary['collectible_displayed_records']}** · "
            f"**{coverage_summary['collectible_remaining_gap']}** still browser-unexposed  "
        ),
        (
            f"> Private lists: **{coverage_summary['private_relationships']}** relationships · "
            f"**{coverage_summary['private_displayed_records']}** displayed accounts recorded but excluded from collectible coverage  "
        ),
        (
            f"> Unavailable source profiles: **{coverage_summary.get('unavailable_relationships', 0)}** relationships excluded from collectible coverage  "
        ),
        f"> Latest run: **{latest_status}** `{latest_run['run_stamp'] if latest_run else ''}`",
        "",
        *_coverage_markdown_lines(relationship_coverage),
        "",
        "## Platform overview",
        "",
        "![[assets/Platform Contacts.svg]]",
        "",
        "## Relationship overview",
        "",
        "![[assets/Relationship Coverage.svg]]",
        "",
        "## Platforms",
        "",
        "| Platform | Unique accounts | Associated people | Followers | Following | Friends/connections | Files |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for platform in profile_platforms:
        items = grouped.get(platform, [])
        associated_count = sum(
            1 for person in associated_people
            if any(source.get("source_platform") == platform for source in person.get("sources") or [])
        )
        counts = {}
        for relation in RELATIONS:
            row = db.conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM contact_edges e
                JOIN profiles p ON p.id=e.profile_id
                WHERE p.subject_id=? AND p.platform=? AND e.relation=?
                """,
                (subject_id, platform, relation),
            ).fetchone()
            counts[relation] = int(row["n"] if row else 0)
        summary.append(
            f"| {platform} | {len(items)} | {associated_count} | {counts['followers']} | {counts['following']} | {counts['friends']} | "
            f"[[platforms/{platform}.json|JSON]] |"
        )

    summary.extend([
        "",
        "## Working files",
        "",
        "- [[Master Contacts]] — complete clickable Markdown list",
        "- [[Associated People]] — name evidence from direct business/team pages; unresolved URLs stay explicit",
        "- [[associated_people.json]] — machine-readable website association evidence",
        "- [[master_contacts.json]] — authoritative machine-readable master",
        "- [[Run History]] — every collection attempt",
        "- [[Latest Run]] — latest run status and failures",
        "- `lists/<platform>/<relationship>.json` — accumulated platform-specific relationship lists",
        "",
        "## Deduplication contract",
        "",
        "A contact is unique by **subject + platform + canonical profile URL**. The same username on Facebook and Instagram is retained as two separate platform accounts. Repeated runs update the existing account and add only new relationships or new canonical profile URLs.",
    ])
    (root / "Summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

    css = """/* Enable in Obsidian: Settings → Appearance → CSS snippets → contact-analyzer */
.contact-analyzer-wide .markdown-preview-sizer,
.contact-analyzer-wide .markdown-source-view.mod-cm6 .cm-scroller {
  max-width: 1600px !important;
}
.contact-analyzer-wide table { width: 100%; }
.contact-analyzer-wide img[src$='.svg'] { width: 100%; min-width: 1100px; }
"""
    snippet = vault / ".obsidian" / "snippets" / "contact-analyzer.css"
    snippet.parent.mkdir(parents=True, exist_ok=True)
    snippet.write_text(css, encoding="utf-8")
    return root


def _run_rows(db: Any, run_id: int) -> tuple[Any, list[Any]]:
    run = db.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise ValueError(f"Run not found: {run_id}")
    rows = list(
        db.conn.execute(
            """
            SELECT cr.*,p.platform,p.url AS source_profile_url
            FROM collection_results cr
            JOIN profiles p ON p.id=cr.profile_id
            WHERE cr.run_id=?
            ORDER BY p.platform,p.url,cr.relation
            """,
            (run_id,),
        )
    )
    return run, rows


def write_run_report(db: Any, vault: Path, subject_id: int, run_id: int) -> Path:
    subject = db.get_subject(subject_id)
    if not subject:
        raise ValueError(f"Subject not found: {subject_id}")
    run, rows = _run_rows(db, run_id)
    root = _subject_root(vault, str(subject["slug"]))
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(str(run["run_dir"]))
    run_dir.mkdir(parents=True, exist_ok=True)
    completed_at = run["completed_at"] or utc_now()

    existing_run_json = {}
    run_json_path = run_dir / "run.json"
    if run_json_path.exists():
        try:
            existing_run_json = json.loads(run_json_path.read_text(encoding="utf-8"))
        except Exception:
            existing_run_json = {}
    discoveries = list(existing_run_json.get("discoveries") or [])
    website_rows = list(db.conn.execute(
        """
        SELECT wr.*,p.platform,p.url AS source_profile_url
        FROM website_results wr
        JOIN profiles p ON p.id=wr.profile_id
        WHERE wr.run_id=?
        ORDER BY p.platform,p.url
        """,
        (run_id,),
    ))
    website_payloads = [dict(row) for row in website_rows]
    associated_people = merge_associated_people(db.subject_associated_people(subject_id))

    existing_results = {
        (int(item.get("profile_id") or 0), str(item.get("relation") or "")): dict(item)
        for item in existing_run_json.get("results") or []
        if isinstance(item, dict)
    }
    # A final report can be regenerated after collection. Recover collector-only
    # fields from the saved relation result files because collection_results is
    # intentionally a lean database table and does not store fields such as
    # new_relationship_urls or collector_status.
    for relation_path in run_dir.glob("*/profile-*/*.json"):
        if relation_path.stem not in {"followers", "following", "friends"}:
            continue
        try:
            relation_payload = json.loads(relation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(relation_payload, dict):
            continue
        relation = str(relation_payload.get("relation") or relation_path.stem)
        key = (int(relation_payload.get("profile_id") or 0), relation)
        if not key[0]:
            continue
        result_extras = {
            name: value
            for name, value in relation_payload.items()
            if name != "records"
        }
        existing_results[key] = {**existing_results.get(key, {}), **result_extras}
    row_payloads = []
    for row in rows:
        authoritative = dict(row)
        key = (int(authoritative.get("profile_id") or 0), str(authoritative.get("relation") or ""))
        # Keep run-only fields such as new_relationship_urls while allowing the
        # persisted collection_results row to remain authoritative for counts,
        # status, timestamps, and diagnostics.
        row_payloads.append({**existing_results.get(key, {}), **authoritative})
    relationship_new_contacts = sum(int(row["new_contacts_added"] or 0) for row in rows)
    website_new_contacts = sum(int(row["new_contacts_added"] or 0) for row in website_rows)
    new_contacts = relationship_new_contacts + website_new_contacts
    unique_contacts = len(db.subject_contacts(subject_id))
    accumulated_relationship_records = sum(
        int(row["total_unique_saved"] or 0)
        for row in db.all_profile_totals(subject_id)
        if row["relation"]
    )
    previous_summary = dict(existing_run_json.get("summary") or {})
    metrics = build_run_summary(
        row_payloads,
        profiles_inspected=int(previous_summary.get("profiles_inspected") or len(discoveries)),
        unique_contacts_saved=unique_contacts,
        accumulated_relationship_records=accumulated_relationship_records,
        status=str(run["status"]),
        new_relationship_urls=int(previous_summary.get("new_relationship_urls") or 0),
        new_contacts_added=new_contacts,
    )
    metrics.update({
        "website_sources_attempted": len(website_rows),
        "website_sources_complete": sum(1 for row in website_rows if row["status"] == "complete"),
        "website_sources_skipped": sum(1 for row in website_rows if row["status"] == "skipped"),
        "associated_people_detected": sum(int(row["people_detected"] or 0) for row in website_rows),
        "associated_people_unique_saved": len(associated_people),
        "new_associations": sum(int(row["new_associations"] or 0) for row in website_rows),
    })
    retrieved = int(metrics["latest_pass_relationship_records"])
    displayed = int(metrics["displayed_relationship_records"])
    accumulated_exact = int(metrics["accumulated_exact_count_records"])
    accumulated_gap = int(metrics["accumulated_count_gap"])
    latest_gap = int(metrics["latest_pass_count_gap"])
    complete = int(metrics["complete_relations"])
    partial = int(metrics["partial_or_failed_relations"])
    private_relations = int(metrics.get("private_relationships") or 0)
    collectible_relations = int(
        metrics.get("collectible_relationships", metrics.get("relationships_attempted", 0))
    )
    new_edge_word = "edge" if int(metrics["new_relationship_urls"]) == 1 else "edges"
    new_account_word = "account" if new_contacts == 1 else "accounts"

    payload = {
        **existing_run_json,
        "run_id": int(run["id"]),
        "subject_id": int(subject["id"]),
        "subject_name": str(subject["name"]),
        "subject_slug": str(subject["slug"]),
        "run_stamp": str(run["run_stamp"]),
        "started_at": run["started_at"],
        "completed_at": completed_at,
        "status": run["status"],
        "summary": {
            **previous_summary,
            **metrics,
            "relations_attempted": len(rows),
            "retrieved_this_run": retrieved,
        },
        "discoveries": discoveries,
        "website_results": website_payloads,
        "results": row_payloads,
    }
    write_json(run_json_path, payload)

    lines = [
        "---",
        f'subject: "{subject["name"]}"',
        f"subject_id: {subject['id']}",
        f"run_id: {run['id']}",
        f"run_stamp: {run['run_stamp']}",
        f"status: {run['status']}",
        "cssclasses:",
        "  - contact-analyzer-wide",
        "tags:",
        "  - contact-analyzer",
        "  - run-summary",
        "---",
        "",
        f"# Run · {subject['name']} · {run['run_stamp']}",
        "",
        f"> [!{'success' if run['status'] == 'complete' else 'warning'}] **{metrics['status_label']}**",
        f"> Started `{run['started_at']}` · completed `{completed_at}`  ",
        (
            f"> {'Collectible' if private_relations else 'Accumulated'} exact-count coverage: "
            f"**{accumulated_exact}/{displayed}** relationship edges  "
        ),
        f"> Latest browser pass: **{retrieved}/{displayed}** · latest-pass gap **{latest_gap}**  ",
        f"> Remaining exact-count gap: **{accumulated_gap}** browser-unexposed relationship edges  ",
        (
            f"> Database totals: **{accumulated_relationship_records}** saved relationship edges · "
            f"**{unique_contacts}** unique platform accounts  "
        ),
        (
            f"> Deduplication: **{metrics['relationship_membership_overlap']}** additional memberships "
            "belong to accounts already represented in the unique-account total  "
        ),
        (
            f"> Added this run: **{metrics['new_relationship_urls']}** relationship {new_edge_word} · "
            f"**{new_contacts}** new unique {new_account_word}  "
        ),
        (
            f"> **{complete}/{collectible_relations}** collectible relationships verified · "
            f"**{partial}** partial/failed"
        ),
        *(
            [
                f"> Private lists excluded from coverage: **{private_relations}** relationships · "
                f"**{metrics.get('private_displayed_records', 0)}** displayed counts recorded"
            ]
            if private_relations
            else []
        ),
        (
            f"> Website evidence: **{len(associated_people)}** unique associated people · "
            f"**{metrics['associated_people_detected']}** source mentions this run · "
            f"**{metrics['new_associations']}** new source associations  "
        ),
        (
            f"> Website sources: **{metrics['website_sources_complete']}** complete · "
            f"**{metrics['website_sources_skipped']}** intentionally skipped"
        ),
        "",
        "## Browser discovery",
        "",
        "| Platform | Profile | Mode | Relationships found | Notes |",
        "|---|---|---|---|---|",
    ]
    for item in discoveries:
        lines.append(
            f"| {_md_escape(item.get('platform'))} | {_md_escape(item.get('source_url'))} | {_md_escape(item.get('graph_mode'))} | "
            f"{_md_escape(', '.join(item.get('available_relations') or []))} | {_md_escape(item.get('notes'))} |"
        )

    lines.extend([
        "",
        "## Website and content evidence",
        "",
        "| Platform | Source | Subject visible | People found | New associations | Author type | Status | Reason |",
        "|---|---|---|---:|---:|---|---|---|",
    ])
    for row in website_payloads:
        lines.append(
            f"| {_md_escape(row.get('platform'))} | {_md_escape(row.get('source_profile_url'))} | "
            f"{'yes' if row.get('subject_present') else 'no'} | {int(row.get('people_detected') or 0)} | "
            f"{int(row.get('new_associations') or 0)} | {_md_escape(row.get('author_entity_type') or '')} | "
            f"{_md_escape(row.get('status'))} | {_md_escape(str(row.get('reason') or '').replace('_', ' '))} |"
        )

    lines.extend([
        "",
        "## Extraction results",
        "",
        "| Platform | Relationship | Displayed | Latest pass | Saved total | Remaining gap | New accounts | Status | Source profile |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ])
    for row in rows:
        reported = row["reported_count"] if row["reported_count"] is not None else "?"
        remaining = (
            max(0, int(row["reported_count"]) - int(row["total_unique_saved"] or 0))
            if row["reported_count"] is not None else "?"
        )
        lines.append(
            f"| {_md_escape(row['platform'])} | {_md_escape(row['relation'])} | {reported} | {int(row['collected_this_run'] or 0)} | "
            f"{int(row['total_unique_saved'] or 0)} | {remaining} | {int(row['new_contacts_added'] or 0)} | "
            f"{_md_escape(status_label(row['status'], row['reason']))} | {_md_escape(row['source_profile_url'])} |"
        )

    follow_up = [row for row in rows if row["status"] not in SUCCESS_STATUSES]
    missing = [item for item in discoveries if not item.get("available_relations")]
    if follow_up or missing:
        lines.extend(["", "## Follow-up", ""])
        for item in missing:
            lines.append(f"- **{item.get('platform')}** needs an adapter or does not expose an enumerable list: {item.get('source_url')}")
        for row in follow_up:
            coverage = relationship_coverage_note(
                str(row["platform"]),
                row["reported_count"],
                int(row["collected_this_run"] or 0),
                int(row["total_unique_saved"] or 0),
                str(row["status"]),
                str(row["reason"]),
            )
            lines.append(
                f"- `{row['platform']}:{row['relation']}` — "
                f"**{status_label(row['status'], row['reason'])}** — "
                f"{reason_label(row['reason'])} — {row['source_profile_url']}"
            )
            if coverage:
                lines.append(f"  - Coverage: {coverage}")
        lines.extend(["", "> [!tip] Codex repair", f'> Run `contactanalyzer codex "{subject["name"]}"` to inspect the newest diagnostics and teach the adapter.'])

    summary_path = run_dir / "Run Summary.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copy2(summary_path, root / "Latest Run.md")
    write_run_history(db, vault, subject_id)
    return summary_path


def write_run_history(db: Any, vault: Path, subject_id: int) -> Path:
    subject = db.get_subject(subject_id)
    if not subject:
        raise ValueError(f"Subject not found: {subject_id}")
    root = _subject_root(vault, str(subject["slug"]))
    rows = list(db.conn.execute("SELECT * FROM runs WHERE subject_id=? ORDER BY id DESC", (subject_id,)))
    lines = [
        "---",
        f'subject: "{subject["name"]}"',
        "cssclasses:",
        "  - contact-analyzer-wide",
        "tags:",
        "  - contact-analyzer",
        "  - run-history",
        "---",
        "",
        f"# Run History · {subject['name']}",
        "",
        "| Run | Started | Completed | Status | Report |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        rel = f"runs/{row['run_stamp']}/Run Summary"
        lines.append(f"| {row['run_stamp']} | {row['started_at']} | {row['completed_at'] or ''} | {row['status']} | [[{rel}|Open]] |")
    path = root / "Run History.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
