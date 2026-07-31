from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Database
from .collection_status import SUCCESS_STATUSES, reason_label, relationship_coverage_note, status_label
from .util import utc_now, write_json
from .reporting import (
    subject_relationship_coverage,
    summarize_relationship_coverage,
    write_subject_aux_exports,
)
from .website_people import merge_associated_people


def subject_dir(vault: Path, subject_slug: str) -> Path:
    return vault / "Subjects" / subject_slug


def export_subject(db: Database, vault: Path, subject_id: int) -> Path:
    subject = db.get_subject(subject_id)
    if not subject:
        raise ValueError(f"Subject not found: {subject_id}")
    root = subject_dir(vault, subject["slug"])
    (root / "platforms").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)

    profiles = db.profiles_for_subject(subject_id)
    contacts = db.subject_contacts(subject_id)
    associated_people = merge_associated_people(db.subject_associated_people(subject_id))
    latest_results = db.latest_results(subject_id)
    all_totals = db.all_profile_totals(subject_id)
    latest_run = db.latest_run(subject_id)
    relationship_coverage = subject_relationship_coverage(db, subject_id)
    relationship_coverage_summary = summarize_relationship_coverage(relationship_coverage)

    subject_payload = {
        "subject_id": subject["id"],
        "name": subject["name"],
        "slug": subject["slug"],
        "created_at": subject["created_at"],
        "updated_at": subject["updated_at"],
        "profiles": [
            {
                "profile_id": row["id"],
                "platform": row["platform"],
                "url": row["url"],
                "last_run_at": row["last_run_at"],
            }
            for row in profiles
        ],
    }
    write_json(root / "subject.json", subject_payload)

    master_contacts: list[dict[str, Any]] = []
    platform_groups: dict[str, list[dict[str, Any]]] = {}
    for row in contacts:
        sources = [dict(source) for source in db.contact_sources(row["id"])]
        item = {
            "contact_id": row["id"],
            "platform": row["platform"],
            "username": row["username"],
            "display_name": row["display_name"],
            "profile_url": row["canonical_url"],
            "platform_user_id": row["platform_user_id"],
            "avatar_url": row["avatar_url"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "sources": sources,
        }
        master_contacts.append(item)
        platform_groups.setdefault(row["platform"], []).append(item)

    relation_totals = {"followers": 0, "following": 0, "friends": 0}
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
        relation_totals[row["relation"]] = int(row["n"])
    relationship_edges = sum(relation_totals.values())

    master_payload = {
        "subject_id": subject["id"],
        "subject_name": subject["name"],
        "subject_slug": subject["slug"],
        "generated_at": utc_now(),
        "unique_contacts": len(master_contacts),
        "relationship_edges": relationship_edges,
        "relationship_totals": relation_totals,
        "relationship_coverage": relationship_coverage,
        "relationship_coverage_summary": relationship_coverage_summary,
        "relationship_membership_overlap": max(0, relationship_edges - len(master_contacts)),
        "unique_associated_people": len(associated_people),
        "associated_people_mentions": sum(len(item.get("sources") or []) for item in associated_people),
        "unresolved_associated_people": sum(1 for item in associated_people if not item.get("canonical_profile_url")),
        "associated_people": associated_people,
        "contacts": master_contacts,
    }
    write_json(root / "master_contacts.json", master_payload)

    all_platforms = sorted({row["platform"] for row in profiles} | set(platform_groups))
    for platform in all_platforms:
        items = platform_groups.get(platform, [])
        profile_rows = [row for row in profiles if row["platform"] == platform]
        platform_associated_people = []
        for person in associated_people:
            sources = [
                source for source in person.get("sources") or []
                if source.get("source_platform") == platform
            ]
            if sources:
                platform_associated_people.append({**person, "sources": sources})
        relationship_totals = {}
        for relation in ("followers", "following", "friends"):
            row = db.conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM contact_edges e
                JOIN profiles p ON p.id=e.profile_id
                WHERE p.subject_id=? AND p.platform=? AND e.relation=?
                """,
                (subject_id, platform, relation),
            ).fetchone()
            relationship_totals[relation] = int(row["n"] if row else 0)
        platform_payload = {
            "subject_id": subject["id"],
            "subject_name": subject["name"],
            "platform": platform,
            "generated_at": utc_now(),
            "profiles": [
                {"profile_id": row["id"], "url": row["url"], "last_run_at": row["last_run_at"]}
                for row in profile_rows
            ],
            "relationship_totals": relationship_totals,
            "relationship_coverage": [
                row for row in relationship_coverage if row["platform"] == platform
            ],
            "relationship_coverage_summary": summarize_relationship_coverage([
                row for row in relationship_coverage if row["platform"] == platform
            ]),
            "relationship_edges": sum(relationship_totals.values()),
            "relationship_membership_overlap": max(
                0, sum(relationship_totals.values()) - len(items)
            ),
            "unique_contacts": len(items),
            "unique_associated_people": len(platform_associated_people),
            "associated_people": platform_associated_people,
            "contacts": items,
        }
        write_json(root / "platforms" / f"{platform}.json", platform_payload)

    latest_map = {
        (row["profile_id"], row["relation"]): row
        for row in latest_results
    }
    total_map = {
        (row["profile_id"], row["relation"]): int(row["total_unique_saved"] or 0)
        for row in all_totals
        if row["relation"]
    }

    lines = [
        "---",
        f"subject: \"{subject['name']}\"",
        f"subject_id: {subject['id']}",
        f"generated: {utc_now()}",
        "tags:",
        "  - contact-analyzer",
        "  - social-graph",
        "---",
        "",
        f"# Contact Analyzer: {subject['name']}",
        "",
        f"- Unique platform accounts after canonical URL deduplication: **{len(master_contacts)}**",
        f"- Unique associated people from verified website/team evidence: **{len(associated_people)}**",
        f"- Saved relationship edges: **{relationship_edges}**",
        f"- Repeated memberships represented once in Master Contacts: **{max(0, relationship_edges - len(master_contacts))}**",
        f"- Follower edges saved: **{relation_totals['followers']}**",
        f"- Following edges saved: **{relation_totals['following']}**",
        f"- Friend/connection edges saved: **{relation_totals['friends']}**",
    ]
    if latest_run:
        lines.append(f"- Latest run: `{latest_run['run_stamp']}` — **{latest_run['status']}**")
    lines.extend([
        "",
        "## Profile collection status",
        "",
        "| Platform | Profile | Relationship | Reported total | Retrieved latest run | New latest run | Unique saved total | Status |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ])

    relation_order = {"followers": 0, "following": 1, "friends": 2}
    table_rows: list[tuple[str, str, str, Any, int, int, int, str, str]] = []
    for profile in profiles:
        relations = sorted(
            {key[1] for key in total_map if key[0] == profile["id"]}
            | {key[1] for key in latest_map if key[0] == profile["id"]},
            key=lambda x: relation_order.get(x, 99),
        )
        for relation in relations:
            latest = latest_map.get((profile["id"], relation))
            total = total_map.get((profile["id"], relation), 0)
            reported = latest["reported_count"] if latest else None
            retrieved = int(latest["collected_this_run"] if latest else 0)
            new = int(latest["new_contacts_added"] if latest else 0)
            status = str(latest["status"] if latest else "not-run")
            if reported is not None and total == int(reported) and status in {"complete", "complete_accessible_list", "incomplete"}:
                status = "verified"
            elif reported is not None and total > int(reported):
                status = "review"
            reason = str(latest["reason"] if latest else "not_run")
            table_rows.append((profile["platform"], profile["url"], relation, reported, retrieved, new, total, status, reason))

    for platform, url, relation, reported, retrieved, new, total, status, reason in table_rows:
        reported_text = str(reported) if reported is not None else "?"
        lines.append(
            f"| {platform} | {url} | {relation} | {reported_text} | {retrieved} | {new} | {total} | {status_label(status, reason)} |"
        )

    lines.extend([
        "",
        "## Platform relationship totals",
        "",
        "| Platform | Relationship | Reported latest | Retrieved latest | New latest | Unique saved total |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for platform in all_platforms:
        for relation in ("followers", "following", "friends"):
            matching_latest = [row for row in latest_results if row["platform"] == platform and row["relation"] == relation]
            matching_profiles = [row for row in profiles if row["platform"] == platform]
            if not matching_latest and not any((row["id"], relation) in total_map for row in matching_profiles):
                continue
            reported_values = [row["reported_count"] for row in matching_latest if row["reported_count"] is not None]
            reported = sum(int(value) for value in reported_values) if len(reported_values) == len(matching_latest) and matching_latest else "?"
            retrieved = sum(int(row["collected_this_run"]) for row in matching_latest)
            new = sum(int(row["new_contacts_added"]) for row in matching_latest)
            saved_row = db.conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM contact_edges e
                JOIN profiles p ON p.id=e.profile_id
                WHERE p.subject_id=? AND p.platform=? AND e.relation=?
                """,
                (subject_id, platform, relation),
            ).fetchone()
            saved = int(saved_row["n"] if saved_row else 0)
            lines.append(f"| {platform} | {relation} | {reported} | {retrieved} | {new} | {saved} |")

    lines.extend(["", "## Platform totals", "", "| Platform | Unique contacts |", "|---|---:|"])
    for platform in all_platforms:
        lines.append(f"| {platform} | {len(platform_groups.get(platform, []))} |")

    incomplete = [row for row in latest_results if row["status"] not in SUCCESS_STATUSES]
    if incomplete:
        lines.extend(["", "## Incomplete or review-required collections", ""])
        for row in incomplete:
            coverage = relationship_coverage_note(
                str(row["platform"]),
                row["reported_count"],
                int(row["collected_this_run"] or 0),
                int(row["total_unique_saved"] or 0),
                str(row["status"]),
                str(row["reason"]),
            )
            lines.append(
                f"- `{row['platform']}:{row['relation']}` — {status_label(row['status'], row['reason'])} — "
                f"{reason_label(row['reason'])} — {row['source_profile_url']}"
            )
            if coverage:
                lines.append(f"  - Coverage: {coverage}")

    lines.extend([
        "",
        "## Associated people",
        "",
        "Name-only evidence remains unresolved until a real canonical person profile URL is visible.",
        "",
        "| Name | Role | Organization | Canonical profile | Source mentions |",
        "|---|---|---|---|---:|",
    ])
    for person in associated_people:
        canonical = person.get("canonical_profile_url")
        canonical_text = f"[Open profile]({canonical})" if canonical else "Unresolved"
        lines.append(
            f"| {person.get('display_name') or ''} | {', '.join(person.get('roles') or [])} | "
            f"{', '.join(person.get('organizations') or [])} | {canonical_text} | {len(person.get('sources') or [])} |"
        )

    lines.extend([
        "",
        "## Files",
        "",
        "- [[master_contacts.json]]",
        "- [[subject.json]]",
        "- [[associated_people.json]]",
        "- [[Associated People]]",
    ])
    for platform in all_platforms:
        lines.append(f"- [[platforms/{platform}.json]]")

    (root / "Summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_subject_aux_exports(db, vault, subject_id)
    return root
