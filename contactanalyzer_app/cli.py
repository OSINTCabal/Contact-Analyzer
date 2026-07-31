from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from . import __version__
from .adapters import platform_for, relations_for
from .codex_assistant import run_codex
from .orchestrator import enhanced_run_subject
from .cli_commands import discover_command, print_platform_coverage
from .collector import BrowserCollector
from .config import ensure_vault, load_config, save_config, vault_path
from .db import Database
from .exporter import export_subject, subject_dir
from .reporting import write_run_report
from .util import extract_name, extract_urls, run_stamp, utc_now, write_json

APP_ROOT = Path(__file__).resolve().parent.parent


def _dedupe_records_for_save(records):
    """Return one record per platform and canonical profile URL."""
    merged = {}

    for raw in records or []:
        if not isinstance(raw, dict):
            continue

        record = dict(raw)
        platform = str(record.get("platform") or "").strip()
        profile_url = str(record.get("profile_url") or "").strip()

        if not platform or not profile_url:
            continue

        key = (
            platform.casefold(),
            profile_url.rstrip("/").casefold(),
        )

        existing = merged.get(key)

        if existing is None:
            merged[key] = record
            continue

        for field in (
            "username",
            "display_name",
            "avatar_url",
            "platform_user_id",
            "source_profile_url",
            "relationship",
        ):
            if not existing.get(field) and record.get(field):
                existing[field] = record[field]

        sources = set()

        for value in (
            existing.get("extraction_source"),
            record.get("extraction_source"),
        ):
            if not value:
                continue

            for source in str(value).split("+"):
                source = source.strip()
                if source:
                    sources.add(source)

        if sources:
            existing["extraction_source"] = "+".join(sorted(sources))

    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("platform") or "").casefold(),
            str(item.get("profile_url") or "").casefold(),
        ),
    )



def db_path(vault: Path) -> Path:
    return vault / ".contactanalyzer" / "contactanalyzer.sqlite3"


def read_paste(prompt: str, *, allow_name: bool = True) -> tuple[str | None, list[str]]:
    print(prompt)
    print("Finish by typing DONE on its own line.")
    print()
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "DONE":
            break
        lines.append(line)
    text = "\n".join(lines)
    return (extract_name(text) if allow_name else None, extract_urls(text))


def ensure_browser(config: dict[str, Any], launch: bool = True) -> dict[str, Any]:
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
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            response = requests.get(f"{endpoint}/json/version", timeout=2)
            response.raise_for_status()
            return response.json()
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Started {launcher}, but Chromium DevTools did not appear at {endpoint}")


def reconcile_deleted_subjects(db: Database, vault: Path) -> dict[str, Any]:
    result = db.prune_subjects_missing_folders(
        vault / "Subjects",
        vault / ".contactanalyzer" / "backups",
    )
    removed = list(result.get("removed") or [])
    if removed:
        names = ", ".join(str(row["name"]) for row in removed)
        print(
            f"Reconciled {len(removed)} deleted subject folder(s) from SQLite: {names}\n"
            f"Recovery backup: {result['backup_path']}"
        )
    return result


def choose_subject(db: Database, vault: Path, allow_new: bool = True) -> Any:
    reconcile_deleted_subjects(db, vault)
    subjects = db.list_subjects()
    print("\nPrevious subjects")
    if not subjects:
        print("  No subjects saved yet.")
    else:
        for index, row in enumerate(subjects, 1):
            last = row["last_run_at"] or "never"
            print(f"  {index}. {row['name']} — {row['profile_count']} profiles — {row['contact_count']} contacts — last run {last}")
    if allow_new:
        print("  N. New subject")
    print("  Q. Quit")
    while True:
        choice = input("Select: ").strip()
        if choice.casefold() == "q":
            return None
        if allow_new and choice.casefold() == "n":
            return "new"
        if choice.isdigit() and 1 <= int(choice) <= len(subjects):
            return subjects[int(choice) - 1]
        subject = db.get_subject(choice)
        if subject:
            return subject
        print("Invalid selection.")


def add_urls(db: Database, subject_id: int, urls: list[str]) -> tuple[int, list[Any]]:
    added = 0
    rows = []
    for url in urls:
        platform = platform_for(url)
        row, created = db.add_profile(subject_id, platform, url)
        rows.append(row)
        if created:
            added += 1
    return added, rows


def create_subject_from_paste(db: Database, explicit_name: str | None = None) -> Any:
    name, urls = read_paste(
        "Paste a subject line and profile URLs. Example: here's my next subject First Last",
        allow_name=True,
    )
    name = explicit_name or name
    if not name:
        name = input("Subject name: ").strip()
    if not name:
        raise ValueError("Subject name is required")
    if not urls:
        raise ValueError("No profile URLs were found")
    subject = db.create_or_get_subject(name)
    added, _ = add_urls(db, subject["id"], urls)
    print(f"Saved {subject['name']} with {added} new profile URL(s).")
    return subject


def add_to_subject_interactive(db: Database, subject: Any) -> list[Any]:
    _, urls = read_paste(
        f"Paste additional profile URLs for {subject['name']}",
        allow_name=False,
    )
    if not urls:
        print("No URLs found.")
        return []
    added, rows = add_urls(db, subject["id"], urls)
    print(f"Added {added} new URL(s); {len(rows) - added} already existed.")
    return rows


def run_subject(db: Database, config: dict[str, Any], vault: Path, subject: Any, profile_ids: set[int] | None = None) -> int:
    browser_info = ensure_browser(config, launch=True)
    print(f"Browser: {browser_info.get('Browser', 'Chromium')} at {config['cdp_endpoint']}")
    profiles = db.profiles_for_subject(subject["id"])
    if profile_ids:
        profiles = [row for row in profiles if int(row["id"]) in profile_ids]
    if not profiles:
        print("No profiles selected.")
        return 2

    stamp = run_stamp()
    root = subject_dir(vault, subject["slug"])
    run_dir = root / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = db.start_run(subject["id"], stamp, str(run_dir))
    collector = BrowserCollector(str(config["cdp_endpoint"]), dict(config.get("settings") or {}))
    overall_status = "complete"
    run_results: list[dict[str, Any]] = []

    try:
        print(f"\n=== {subject['name']} ===")
        for profile in profiles:
            platform = profile["platform"] or platform_for(profile["url"])
            relations = relations_for(platform)
            profile_dir = run_dir / platform / f"profile-{profile['id']}"
            diagnostics_dir = profile_dir / "diagnostics"
            print(f"\nProfile: {profile['url']}")
            for relation in relations:
                checkpoint = profile_dir / f"{relation}.checkpoint.json"
                outcome = collector.collect(
                    platform=platform,
                    source_url=profile["url"],
                    relation=relation,
                    diagnostics_dir=diagnostics_dir,
                    checkpoint_path=checkpoint,
                )
                # Review/failed/blocked collections are preserved in the run folder for
                # diagnostics, but are not allowed to contaminate the persistent subject
                # database or Obsidian master files. Incomplete runs may still contribute
                # valid partial contacts and are deduplicated by the database.
                deduped_records = _dedupe_records_for_save(outcome.records)
                commit_records = outcome.status in {"complete", "complete_accessible_list", "incomplete"}
                if commit_records:
                    committed, new_contacts = db.upsert_contacts(
                        subject["id"], profile["id"], relation, deduped_records
                    )
                else:
                    committed, new_contacts = 0, 0
                total_saved = db.total_for_profile_relation(profile["id"], relation)
                diagnostics_path = None
                if diagnostics_dir.exists():
                    diagnostics_path = str(diagnostics_dir)
                db.save_collection_result(
                    run_id=run_id,
                    profile_id=profile["id"],
                    relation=relation,
                    reported_count=outcome.reported_count,
                    collected_this_run=len(deduped_records),
                    new_contacts_added=new_contacts,
                    total_unique_saved=total_saved,
                    status=outcome.status,
                    reason=outcome.reason,
                    diagnostics_path=diagnostics_path,
                    started_at=outcome.started_at,
                    completed_at=outcome.completed_at,
                )
                result_payload = {
                    **{k: v for k, v in vars(outcome).items() if k != "records"},
                    "profile_id": profile["id"],
                    "collected_this_run": len(deduped_records),
                    "committed_this_run": committed,
                    "new_contacts_added": new_contacts,
                    "total_unique_saved": total_saved,
                    "records": deduped_records,
                }
                write_json(profile_dir / f"{relation}.json", result_payload)
                run_results.append(result_payload)
                print(
                    f"  found={len(deduped_records)} committed={committed} "
                    f"saved={total_saved} new={new_contacts} status={outcome.status}"
                )
                if outcome.status not in {"complete", "complete_accessible_list"}:
                    overall_status = "partial"
                export_subject(db, vault, subject["id"])

        write_json(run_dir / "run.json", {
            "run_id": run_id,
            "subject_id": subject["id"],
            "subject_name": subject["name"],
            "run_stamp": stamp,
            "completed_at": utc_now(),
            "status": overall_status,
            "results": [
                {k: v for k, v in item.items() if k != "records"}
                for item in run_results
            ],
        })
        db.finish_run(run_id, overall_status)
        write_run_report(db, vault, subject["id"], run_id)
        output = export_subject(db, vault, subject["id"])
        print(f"\nSaved Obsidian subject folder: {output}")
        print(f"Summary: {output / 'Summary.md'}")
        print(f"Master JSON: {output / 'master_contacts.json'}")
        return 0
    except KeyboardInterrupt:
        db.finish_run(run_id, "interrupted")
        write_run_report(db, vault, subject["id"], run_id)
        export_subject(db, vault, subject["id"])
        print("\nInterrupted. Checkpoints and saved contacts were preserved.")
        return 130
    except Exception:
        db.finish_run(run_id, "failed")
        write_run_report(db, vault, subject["id"], run_id)
        export_subject(db, vault, subject["id"])
        raise


def migrate_old(db: Database, old_root: Path, vault: Path) -> None:
    subjects_file = old_root / "subjects.json"
    if not subjects_file.exists():
        raise FileNotFoundError(subjects_file)
    data = json.loads(subjects_file.read_text(encoding="utf-8"))
    for old_subject in data.get("subjects", []):
        old_subject_id = str(old_subject.get("subject_id") or "")
        subject = db.create_or_get_subject(old_subject.get("name") or old_subject_id or "Imported subject")
        profile_lookup: dict[str, Any] = {}
        for profile in old_subject.get("profiles", []):
            if isinstance(profile, str):
                profile = {"url": profile}
            url = profile["url"]
            row, _ = db.add_profile(subject["id"], profile.get("platform") or platform_for(url), url)
            profile_lookup[url.rstrip("/").casefold()] = row

        # Import only relation files that the old collector marked complete. This avoids
        # carrying the known X/Bluesky/GitHub false positives from incomplete/review runs.
        old_output = old_root / "output" / old_subject_id
        imported_relations = 0
        if old_output.exists():
            for result_file in old_output.rglob("*.json"):
                if result_file.name.endswith(".checkpoint.json") or result_file.name in {"master_contacts.json", "subject.json"}:
                    continue
                if result_file.stem not in {"followers", "following", "friends"}:
                    continue
                try:
                    payload = json.loads(result_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if payload.get("status") not in {"verified", "complete", "complete_accessible_list"}:
                    continue
                source_url = str(payload.get("source_profile_url") or "")
                profile = profile_lookup.get(source_url.rstrip("/").casefold())
                relation = payload.get("relation") or payload.get("relationship") or result_file.stem
                if not profile or relation not in {"followers", "following", "friends"}:
                    continue
                records = [
                    record for record in payload.get("records", [])
                    if record.get("profile_url") and record.get("platform")
                ]
                db.upsert_contacts(subject["id"], profile["id"], relation, records)
                imported_relations += 1
        export_subject(db, vault, subject["id"])
        print(f"Migrated: {subject['name']} — profiles={len(profile_lookup)} complete_relation_files={imported_relations}")


# CONTACT_ANALYZER_ENHANCED_ORCHESTRATOR
run_subject = enhanced_run_subject


def run_subject_command(
    db: Database,
    config: dict[str, Any],
    vault: Path,
    subject: Any,
    *,
    with_codex: bool = False,
    codex_non_interactive: bool = False,
) -> int:
    """Run collection and optionally hand the completed run to Codex for review."""
    result = run_subject(db, config, vault, subject)
    if result != 0 or not with_codex:
        return result
    return run_codex(
        db,
        vault,
        subject["id"],
        APP_ROOT,
        non_interactive=codex_non_interactive,
        browser_endpoint=str(config["cdp_endpoint"]),
    )

def interactive(db: Database, config: dict[str, Any], vault: Path) -> int:
    while True:
        selection = choose_subject(db, vault, allow_new=True)
        if selection is None:
            return 0
        if selection == "new":
            subject = create_subject_from_paste(db)
            # Materialize the subject folder immediately. Startup reconciliation
            # treats a deleted folder as an intentional subject deletion, so a
            # newly created no-run subject must have its export before returning
            # to the subject menu.
            export_subject(db, vault, subject["id"])
            run_now = input("Run collection now? [Y/n]: ").strip().casefold()
            if run_now not in {"n", "no"}:
                run_subject(db, config, vault, subject)
            continue

        subject = selection
        while True:
            print(f"\n{subject['name']}")
            print("  1. Run/resume all saved profile URLs — existing contacts are deduplicated")
            print("  2. Run/resume all saved profile URLs, then start Codex review")
            print("  3. Add profile URLs and run the newly pasted URLs")
            print("  4. Add profile URLs without running")
            print("  5. Export/refresh Obsidian files")
            print("  6. Open Summary.md")
            print("  7. Start Codex assistant for the latest run")
            print("  8. Back")
            choice = input("Select: ").strip()
            if choice == "1":
                run_subject(db, config, vault, subject)
            elif choice == "2":
                run_subject_command(db, config, vault, subject, with_codex=True)
            elif choice == "3":
                rows = add_to_subject_interactive(db, subject)
                if rows:
                    run_subject(db, config, vault, subject, {int(row["id"]) for row in rows})
            elif choice == "4":
                add_to_subject_interactive(db, subject)
                export_subject(db, vault, subject["id"])
            elif choice == "5":
                print(export_subject(db, vault, subject["id"]))
            elif choice == "6":
                path = subject_dir(vault, subject["slug"]) / "Summary.md"
                print(path)
                subprocess.call([os.environ.get("PAGER", "less"), str(path)])
            elif choice == "7":
                run_codex(
                    db,
                    vault,
                    subject["id"],
                    APP_ROOT,
                    non_interactive=False,
                    browser_endpoint=str(config["cdp_endpoint"]),
                )
            elif choice == "8":
                break
            else:
                print("Invalid selection.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contactanalyzer")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("subjects", help="List saved subjects")
    sub.add_parser("platforms", help="Show platform coverage and relationship modes")
    discover = sub.add_parser("discover", help="Open one profile and discover friends/followers/following controls")
    discover.add_argument("url")
    new = sub.add_parser("new", help="Create a subject from pasted text and URLs")
    new.add_argument("--name")
    new.add_argument("--no-run", action="store_true")

    add = sub.add_parser("add", help="Add pasted URLs to an existing subject")
    add.add_argument("subject")
    add.add_argument("--run", action="store_true")

    run = sub.add_parser("run", help="Run all saved profile URLs for a subject")
    run.add_argument("subject")
    run_assistant = run.add_mutually_exclusive_group()
    run_assistant.add_argument(
        "--with-codex",
        action="store_true",
        help="Launch an interactive Codex review after collection finishes",
    )
    run_assistant.add_argument(
        "--with-codex-exec",
        action="store_true",
        help="Run a non-interactive Codex diagnostics review after collection finishes",
    )

    export = sub.add_parser("export", help="Refresh Obsidian JSON and Markdown exports")
    export.add_argument("subject")

    sub.add_parser("doctor", help="Check browser, vault, database, and Codex")

    audit = sub.add_parser("audit", help="Verify a subject database and exported JSON for duplicates")
    audit.add_argument("subject")

    purge = sub.add_parser(
        "purge-review",
        help="Remove contacts first introduced by review/failed/blocked results in the latest run",
    )
    purge.add_argument("subject")

    codex = sub.add_parser("codex", help="Launch Codex with the latest failed-run diagnostics")
    codex.add_argument("subject")
    codex.add_argument("--exec", action="store_true", dest="non_interactive")

    migrate = sub.add_parser("migrate", help="Import the previous SocialGraph installation")
    migrate.add_argument("old_root", type=Path)

    configure = sub.add_parser("config", help="Set the vault path or CDP endpoint")
    configure.add_argument("--vault")
    configure.add_argument("--cdp")
    configure.add_argument("--browser-launcher")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()
    if args.command == "config":
        if args.vault:
            config["vault_path"] = args.vault
        if args.cdp:
            config["cdp_endpoint"] = args.cdp
        if args.browser_launcher:
            config["browser_launcher"] = args.browser_launcher
        save_config(config)
        print(json.dumps(config, indent=2))
        return 0

    vault = ensure_vault(config)
    with Database(db_path(vault)) as db:
        reconcile_deleted_subjects(db, vault)
        if args.command is None:
            return interactive(db, config, vault)
        if args.command == "platforms":
            return print_platform_coverage()
        if args.command == "discover":
            return discover_command(config, args.url)
        if args.command == "subjects":
            subjects = db.list_subjects()
            if not subjects:
                print("No subjects saved.")
            for row in subjects:
                print(f"{row['name']}\t{row['slug']}\tprofiles={row['profile_count']}\tcontacts={row['contact_count']}\tlast={row['last_run_at'] or 'never'}")
            return 0
        if args.command == "new":
            subject = create_subject_from_paste(db, args.name)
            export_subject(db, vault, subject["id"])
            return 0 if args.no_run else run_subject(db, config, vault, subject)
        if args.command == "add":
            subject = db.get_subject(args.subject)
            if not subject:
                raise ValueError(f"Subject not found: {args.subject}")
            rows = add_to_subject_interactive(db, subject)
            export_subject(db, vault, subject["id"])
            return run_subject(db, config, vault, subject, {int(row["id"]) for row in rows}) if args.run and rows else 0
        if args.command == "run":
            subject = db.get_subject(args.subject)
            if not subject:
                raise ValueError(f"Subject not found: {args.subject}")
            return run_subject_command(
                db,
                config,
                vault,
                subject,
                with_codex=bool(args.with_codex or args.with_codex_exec),
                codex_non_interactive=bool(args.with_codex_exec),
            )
        if args.command == "export":
            subject = db.get_subject(args.subject)
            if not subject:
                raise ValueError(f"Subject not found: {args.subject}")
            print(export_subject(db, vault, subject["id"]))
            return 0
        if args.command == "doctor":
            print(f"Vault: {vault}")
            print(f"Database: {db.path}")
            try:
                info = ensure_browser(config, launch=False)
                print(f"Browser: OK — {info.get('Browser')} — {config['cdp_endpoint']}")
            except Exception as exc:
                print(f"Browser: FAIL — {exc}")
            print(f"Codex: {'OK — ' + shutil.which('codex') if shutil.which('codex') else 'not found'}")
            print(f"Subjects: {len(db.list_subjects())}")
            return 0
        if args.command == "audit":
            subject = db.get_subject(args.subject)
            if not subject:
                raise ValueError(f"Subject not found: {args.subject}")
            output = export_subject(db, vault, subject["id"])
            report = db.audit_subject(subject["id"])
            json_duplicates: dict[str, list[str]] = {}
            for path in [output / "master_contacts.json", *(output / "platforms").glob("*.json")]:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                seen: set[tuple[str, str]] = set()
                duplicates: list[str] = []
                for record in payload.get("contacts", []):
                    key = (
                        str(record.get("platform") or payload.get("platform") or "").casefold(),
                        str(record.get("profile_url") or "").rstrip("/").casefold(),
                    )
                    if key in seen:
                        duplicates.append(str(record.get("profile_url") or ""))
                    else:
                        seen.add(key)
                if duplicates:
                    json_duplicates[str(path)] = duplicates
            report["json_duplicate_urls"] = json_duplicates
            ok = not report["duplicate_contact_keys"] and not report["duplicate_edges"] and not json_duplicates
            print(json.dumps(report, indent=2))
            print("AUDIT: PASS — no duplicate contacts or edges" if ok else "AUDIT: FAIL — duplicates found")
            return 0 if ok else 1
        if args.command == "purge-review":
            subject = db.get_subject(args.subject)
            if not subject:
                raise ValueError(f"Subject not found: {args.subject}")
            result = db.purge_latest_untrusted_results(subject["id"])
            output = export_subject(db, vault, subject["id"])
            print(
                f"Purged latest untrusted results: results={result['results']} "
                f"edges={result['edges']} orphan_contacts={result['contacts']}"
            )
            print(f"Refreshed: {output}")
            return 0
        if args.command == "codex":
            subject = db.get_subject(args.subject)
            if not subject:
                raise ValueError(f"Subject not found: {args.subject}")
            return run_codex(
                db,
                vault,
                subject["id"],
                APP_ROOT,
                args.non_interactive,
                browser_endpoint=str(config["cdp_endpoint"]),
            )
        if args.command == "migrate":
            migrate_old(db, args.old_root.expanduser().resolve(), vault)
            return 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
