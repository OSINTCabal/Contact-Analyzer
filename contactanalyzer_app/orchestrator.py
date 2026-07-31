from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from .collector import BrowserCollector
from .collection_status import (
    COMMITTABLE_STATUSES,
    SUCCESS_STATUSES,
    build_run_summary,
    cumulative_relationship_status,
)
from .discovery import discover_profile
from .exporter import export_subject, subject_dir
from .platform_catalog import NON_ENUMERABLE_MODES, default_relations, graph_mode, platform_for_url
from .terminal_ui import get_ui
from .util import utc_now, write_json
from .website_people import (
    WebsitePeopleCollector,
    is_facebook_content_url,
    is_instagram_content_url,
    merge_associated_people,
    outcome_payload,
)


def _ensure_browser(config: dict[str, Any], launch: bool = True) -> dict[str, Any]:
    endpoint = str(config["cdp_endpoint"]).rstrip("/")
    try:
        response = requests.get(f"{endpoint}/json/version", timeout=3)
        response.raise_for_status()
        return response.json()
    except Exception:
        if not launch:
            raise RuntimeError(f"Chromium DevTools is not available at {endpoint}")
    launcher = str(config.get("browser_launcher") or "contactanalyzer-browser")
    if not shutil.which(launcher):
        raise RuntimeError(f"Browser is not running and launcher was not found: {launcher}")
    subprocess.Popen([launcher], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            response = requests.get(f"{endpoint}/json/version", timeout=2)
            response.raise_for_status()
            return response.json()
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Started {launcher}, but Chromium DevTools did not appear at {endpoint}")


def _run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in records or []:
        if not isinstance(raw, dict):
            continue
        platform = str(raw.get("platform") or "").strip()
        url = str(raw.get("profile_url") or "").strip().rstrip("/")
        if not platform or not url:
            continue
        key = (platform.casefold(), url.casefold())
        existing = merged.get(key)
        if existing is None:
            item = dict(raw)
            item["profile_url"] = url
            merged[key] = item
            continue
        for field in ("username", "display_name", "avatar_url", "platform_user_id", "source_profile_url", "relationship"):
            if not existing.get(field) and raw.get(field):
                existing[field] = raw[field]
        sources = set()
        for value in (existing.get("extraction_source"), raw.get("extraction_source")):
            for source in str(value or "").split("+"):
                if source.strip():
                    sources.add(source.strip())
        if sources:
            existing["extraction_source"] = "+".join(sorted(sources))
    return sorted(merged.values(), key=lambda row: (str(row.get("platform") or "").casefold(), str(row.get("profile_url") or "").casefold()))


def _write_run_report_if_available(db: Any, vault: Path, subject_id: int, run_id: int) -> None:
    try:
        from .reporting import write_run_report
        write_run_report(db, vault, subject_id, run_id)
    except Exception as exc:
        # Reporting should never destroy a successful collection.
        get_ui().warning(f"Run report generation failed: {type(exc).__name__}: {exc}")


def enhanced_run_subject(
    db: Any,
    config: dict[str, Any],
    vault: Path,
    subject: Any,
    profile_ids: set[int] | None = None,
) -> int:
    ui = get_ui()
    profiles = db.profiles_for_subject(subject["id"])
    if profile_ids:
        profiles = [row for row in profiles if int(row["id"]) in profile_ids]
    if not profiles:
        ui.warning("No profiles selected.")
        return 2

    # Re-detect platform names so older generic rows are upgraded automatically.
    normalized_profiles = []
    for profile in profiles:
        detected = platform_for_url(str(profile["url"]))
        platform = detected if detected != "generic" else str(profile["platform"] or "generic")
        if platform != profile["platform"]:
            db.conn.execute(
                "UPDATE profiles SET platform=?, updated_at=? WHERE id=?",
                (platform, utc_now(), profile["id"]),
            )
            db.conn.commit()
            profile = db.conn.execute("SELECT * FROM profiles WHERE id=?", (profile["id"],)).fetchone()
        normalized_profiles.append(profile)
    saved_profiles = normalized_profiles

    plan = []
    for profile in saved_profiles:
        platform = str(profile["platform"] or platform_for_url(profile["url"]))
        profile_url = str(profile["url"])
        mode = (
            "content-review" if is_facebook_content_url(profile_url)
            else "codex" if is_instagram_content_url(profile_url)
            else graph_mode(platform)
        )
        plan.append({
            "platform": platform,
            "url": str(profile["url"]),
            "relations": (
                [] if graph_mode(platform) in NON_ENUMERABLE_MODES
                else list(default_relations(platform)) if graph_mode(platform) == "enumerable"
                else []
            ),
            "mode": mode,
        })

    skipped_profiles = [
        profile for profile in saved_profiles
        if graph_mode(str(profile["platform"] or platform_for_url(profile["url"]))) in NON_ENUMERABLE_MODES
    ]
    profiles = [profile for profile in saved_profiles if profile not in skipped_profiles]

    ui.banner(str(subject["name"]), len(saved_profiles))
    ui.plan(plan)
    if skipped_profiles:
        skipped_names = ", ".join(
            f"{profile['platform']} ({profile['url']})" for profile in skipped_profiles
        )
        ui.note(f"Skipped without opening a browser tab — no enumerable relationship graph: {skipped_names}")
    if not profiles:
        ui.warning("No selected profile exposes an enumerable relationship graph.")
        export_subject(db, vault, subject["id"])
        return 0

    browser_info = _ensure_browser(config, launch=True)
    ui.note(f"Visible browser: {browser_info.get('Browser', 'Chromium')} · {config['cdp_endpoint']}")
    ui.note("Identity key: subject + platform + canonical profile URL")

    stamp = _run_stamp()
    root = subject_dir(vault, subject["slug"])
    run_dir = root / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = db.start_run(subject["id"], stamp, str(run_dir))
    collector_settings = dict(config.get("settings") or {})
    # Automated runs never enter an interactive rescue loop. A missing list is
    # recorded as unavailable and the next profile continues cleanly.
    collector_settings["manual_rescue"] = False
    collector = BrowserCollector(str(config["cdp_endpoint"]), collector_settings)
    website_collector = WebsitePeopleCollector(str(config["cdp_endpoint"]), collector_settings)
    overall_status = "complete"
    run_results: list[dict[str, Any]] = []
    website_results: list[dict[str, Any]] = []
    discoveries: list[dict[str, Any]] = []

    try:
        for profile_index, profile in enumerate(profiles, 1):
            source_url = str(profile["url"])
            platform = str(profile["platform"] or platform_for_url(source_url))
            profile_dir = run_dir / platform / f"profile-{profile['id']}"
            diagnostics_dir = profile_dir / "diagnostics"
            profile_dir.mkdir(parents=True, exist_ok=True)

            if (
                platform == "generic"
                or is_facebook_content_url(source_url)
                or is_instagram_content_url(source_url)
            ):
                ui.step(1, 5, f"Source {profile_index}/{len(profiles)}: open exact supplied URL", f"{platform} · {source_url}")
                ui.step(2, 5, "Verify the subject is visibly named", str(subject["name"]))
                if is_facebook_content_url(source_url):
                    ui.step(3, 5, "Verify the Facebook post author", "people analysis runs only for a proven human author")
                else:
                    ui.step(3, 5, "Run bounded Codex people analysis", "direct page first · About/Team fallback only when needed")
                outcome = website_collector.collect(
                    subject_name=str(subject["name"]),
                    source_url=source_url,
                    platform=platform,
                    diagnostics_dir=diagnostics_dir,
                )
                ui.step(4, 5, "Validate people evidence", "real rendered names; canonical URLs only when visibly linked")
                committed, new_associations, new_contacts = db.upsert_associated_people(
                    subject["id"], profile["id"], outcome.people
                ) if outcome.status == "complete" else (0, 0, 0)
                db.save_website_result(
                    run_id=run_id,
                    profile_id=profile["id"],
                    subject_present=outcome.subject_present,
                    people_detected=len(outcome.people),
                    new_associations=new_associations,
                    new_contacts_added=new_contacts,
                    status=outcome.status,
                    reason=outcome.reason,
                    analysis_mode=outcome.analysis_mode,
                    author_name=outcome.author_name,
                    author_entity_type=outcome.author_entity_type,
                    diagnostics_path=outcome.diagnostics_path,
                    started_at=outcome.started_at,
                    completed_at=outcome.completed_at,
                )
                payload = {
                    **outcome_payload(outcome),
                    "profile_id": int(profile["id"]),
                    "committed_associations": committed,
                    "new_associations": new_associations,
                    "new_contacts_added": new_contacts,
                }
                write_json(profile_dir / "associated_people.json", payload)
                website_results.append(payload)
                ui.website_result(outcome, committed, new_associations, new_contacts)
                if outcome.status == "failed":
                    overall_status = "partial"
                ui.step(5, 5, "Refresh Obsidian exports", "associated people, source evidence, master JSON/Markdown")
                export_subject(db, vault, subject["id"])
                continue

            ui.step(1, 7, f"Profile {profile_index}/{len(profiles)}: identify platform", f"{platform} · {source_url}")
            ui.step(2, 7, "Open the authenticated profile", "using the visible Chromium session")
            ui.step(3, 7, "Discover relationship controls", "friends / followers / following")
            discovery_path = profile_dir / "discovery.json"
            discovery = discover_profile(
                str(config["cdp_endpoint"]),
                source_url,
                platform,
                settle_seconds=float((config.get("settings") or {}).get("settle_seconds", 3.0)),
                output_path=discovery_path,
            )
            discovery_payload = dataclasses.asdict(discovery)
            discovery_payload["profile_id"] = int(profile["id"])
            discoveries.append(discovery_payload)

            relations = list(discovery.available_relations)
            if not relations:
                # Private/profile-only sites and conditional sites without a verified
                # relationship directory are intentionally skipped. Do not open random
                # profile links or invoke manual rescue.
                if discovery.graph_mode not in {"private", "none"}:
                    overall_status = "partial"
                ui.warning(discovery.notes or "No verified enumerable relationship list was found.")
                ui.note(f"Discovery evidence saved: {discovery_path}")
                continue

            ui.note(f"Relationship plan: {', '.join(relations)}")
            if discovery.notes:
                ui.note(discovery.notes)

            for relation_index, relation in enumerate(relations, 1):
                ui.step(4, 7, f"Read exact rendered count", f"{platform}:{relation} ({relation_index}/{len(relations)})")
                ui.step(5, 7, "Extract the full visible list", "DOM rows + browser XHR/fetch responses + pagination")
                checkpoint = profile_dir / f"{relation}.checkpoint.json"
                with ui.capture_collector(platform, relation, source_url):
                    outcome = collector.collect(
                        platform=platform,
                        source_url=source_url,
                        relation=relation,
                        diagnostics_dir=diagnostics_dir,
                        checkpoint_path=checkpoint,
                    )

                ui.step(6, 7, "Canonicalize and deduplicate", "one account per platform profile URL")
                records = _dedupe_records(list(outcome.records))
                outcome.records = records
                outcome.collected_this_run = len(records)

                collector_status = outcome.status
                collector_reason = outcome.reason
                previous_total = db.total_for_profile_relation(profile["id"], relation)
                commit_records = collector_status in COMMITTABLE_STATUSES
                if commit_records:
                    committed, new_contacts = db.upsert_contacts(
                        subject["id"], profile["id"], relation, records
                    )
                else:
                    committed, new_contacts = 0, 0
                total_saved = db.total_for_profile_relation(profile["id"], relation)
                new_relationship_urls = max(0, total_saved - previous_total)
                outcome.status, outcome.reason = cumulative_relationship_status(
                    collector_status,
                    collector_reason,
                    outcome.reported_count,
                    total_saved,
                )
                diagnostics_path = str(diagnostics_dir) if diagnostics_dir.exists() else None
                db.save_collection_result(
                    run_id=run_id,
                    profile_id=profile["id"],
                    relation=relation,
                    reported_count=outcome.reported_count,
                    collected_this_run=len(records),
                    new_contacts_added=new_contacts,
                    total_unique_saved=total_saved,
                    status=outcome.status,
                    reason=outcome.reason,
                    diagnostics_path=diagnostics_path,
                    started_at=outcome.started_at,
                    completed_at=outcome.completed_at,
                )
                result_payload = {
                    **{key: value for key, value in vars(outcome).items() if key != "records"},
                    "profile_id": int(profile["id"]),
                    "collector_status": collector_status,
                    "collector_reason": collector_reason,
                    "collected_this_run": len(records),
                    "committed_this_run": committed,
                    "new_contacts_added": new_contacts,
                    "new_relationship_urls": new_relationship_urls,
                    "total_unique_saved": total_saved,
                    "records": records,
                }
                write_json(profile_dir / f"{relation}.json", result_payload)
                run_results.append(result_payload)
                ui.result(outcome, committed, new_contacts, total_saved, new_relationship_urls)
                if outcome.status not in SUCCESS_STATUSES:
                    overall_status = "partial"

                ui.step(7, 7, "Refresh Obsidian exports", "platform lists, master JSON/Markdown, dashboard, run note")
                export_subject(db, vault, subject["id"])

        accumulated_relationship_records = sum(
            int(row["total_unique_saved"] or 0)
            for row in db.all_profile_totals(subject["id"])
            if row["relation"]
        )
        run_summary = build_run_summary(
            run_results,
            profiles_inspected=len(profiles),
            unique_contacts_saved=len(db.subject_contacts(subject["id"])),
            accumulated_relationship_records=accumulated_relationship_records,
            new_relationship_urls=sum(
                int(item.get("new_relationship_urls") or 0) for item in run_results
            ),
            status=overall_status,
        )
        run_summary["website_sources_attempted"] = len(website_results)
        run_summary["website_sources_complete"] = sum(1 for item in website_results if item.get("status") == "complete")
        run_summary["website_sources_skipped"] = sum(1 for item in website_results if item.get("status") == "skipped")
        run_summary["associated_people_detected"] = sum(len(item.get("people") or []) for item in website_results)
        run_summary["associated_people_unique_saved"] = len(
            merge_associated_people(db.subject_associated_people(subject["id"]))
        )
        run_summary["new_associations"] = sum(int(item.get("new_associations") or 0) for item in website_results)
        run_summary["new_contacts_added"] = int(run_summary.get("new_contacts_added") or 0) + sum(
            int(item.get("new_contacts_added") or 0) for item in website_results
        )
        write_json(run_dir / "run.json", {
            "run_id": run_id,
            "subject_id": subject["id"],
            "subject_name": subject["name"],
            "run_stamp": stamp,
            "completed_at": utc_now(),
            "status": overall_status,
            "summary": run_summary,
            "discoveries": discoveries,
            "website_results": [
                {key: value for key, value in item.items() if key != "people"}
                for item in website_results
            ],
            "results": [
                {key: value for key, value in item.items() if key != "records"}
                for item in run_results
            ],
        })
        db.finish_run(run_id, overall_status)
        _write_run_report_if_available(db, vault, subject["id"], run_id)
        output = export_subject(db, vault, subject["id"])
        ui.run_summary(run_summary)
        ui.finish(str(output))
        ui.note(f"Master JSON: {output / 'master_contacts.json'}")
        ui.note(f"Master Markdown: {output / 'Master Contacts.md'}")
        ui.note(f"Run summary: {run_dir / 'Run Summary.md'}")
        return 0
    except KeyboardInterrupt:
        db.finish_run(run_id, "interrupted")
        write_json(run_dir / "run.json", {
            "run_id": run_id,
            "subject_id": subject["id"],
            "subject_name": subject["name"],
            "run_stamp": stamp,
            "completed_at": utc_now(),
            "status": "interrupted",
            "discoveries": discoveries,
            "website_results": [{key: value for key, value in item.items() if key != "people"} for item in website_results],
            "results": [{key: value for key, value in item.items() if key != "records"} for item in run_results],
        })
        _write_run_report_if_available(db, vault, subject["id"], run_id)
        export_subject(db, vault, subject["id"])
        ui.warning("Interrupted. Checkpoints and all committed contacts were preserved.")
        return 130
    except Exception:
        db.finish_run(run_id, "failed")
        write_json(run_dir / "run.json", {
            "run_id": run_id,
            "subject_id": subject["id"],
            "subject_name": subject["name"],
            "run_stamp": stamp,
            "completed_at": utc_now(),
            "status": "failed",
            "discoveries": discoveries,
            "website_results": [{key: value for key, value in item.items() if key != "people"} for item in website_results],
            "results": [{key: value for key, value in item.items() if key != "records"} for item in run_results],
        })
        _write_run_report_if_available(db, vault, subject["id"], run_id)
        export_subject(db, vault, subject["id"])
        raise
