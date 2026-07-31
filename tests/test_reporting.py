import json
import tempfile
import unittest
from pathlib import Path

from contactanalyzer_app.db import Database
from contactanalyzer_app.exporter import export_subject
from contactanalyzer_app.reporting import write_run_report


class ReportingTests(unittest.TestCase):
    @staticmethod
    def _record(username: str) -> dict[str, str]:
        return {
            "platform": "instagram",
            "profile_url": f"https://www.instagram.com/{username}",
            "username": username,
        }

    @staticmethod
    def _threads_record(username: str) -> dict[str, str]:
        return {
            "platform": "threads",
            "profile_url": f"https://www.threads.com/@{username}",
            "username": username,
        }

    def test_master_exports_separate_unique_accounts_from_relationship_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            with Database(root / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Output Totals")
                profile, _ = db.add_profile(
                    subject["id"], "instagram", "https://www.instagram.com/source"
                )
                shared = self._record("shared")
                db.upsert_contacts(subject["id"], profile["id"], "followers", [shared, self._record("follower")])
                db.upsert_contacts(subject["id"], profile["id"], "following", [shared])

                output = export_subject(db, vault, subject["id"])

            master = json.loads((output / "master_contacts.json").read_text(encoding="utf-8"))
            platform = json.loads((output / "platforms" / "instagram.json").read_text(encoding="utf-8"))
            master_markdown = (output / "Master Contacts.md").read_text(encoding="utf-8")
            summary_markdown = (output / "Summary.md").read_text(encoding="utf-8")

            self.assertEqual(master["unique_contacts"], 2)
            self.assertEqual(master["relationship_edges"], 3)
            self.assertEqual(master["relationship_membership_overlap"], 1)
            self.assertEqual(platform["relationship_totals"]["followers"], 2)
            self.assertEqual(platform["relationship_totals"]["following"], 1)
            self.assertEqual(platform["relationship_edges"], 3)
            self.assertIn("2 unique platform accounts", master_markdown)
            self.assertIn("3 saved relationship edges", master_markdown)
            self.assertIn("1 additional relationship membership", master_markdown)
            self.assertIn("**3** saved relationship edges", summary_markdown)

    def test_associated_people_exports_deduplicate_names_across_source_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            with Database(root / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Website Output")
                first, _ = db.add_profile(subject["id"], "generic", "https://one.example/team")
                second, _ = db.add_profile(subject["id"], "generic", "https://two.example/about")
                base = {
                    "normalized_name": "example person",
                    "display_name": "Example Person",
                    "role": "Master Barber",
                    "organization": "Example",
                    "evidence_text": "Example Person works with Example Person.",
                    "extraction_source": "codex_rendered_direct",
                }
                db.upsert_associated_people(subject["id"], first["id"], [{**base, "source_url": first["url"]}])
                db.upsert_associated_people(subject["id"], second["id"], [{**base, "source_url": second["url"]}])

                output = export_subject(db, vault, subject["id"])

            master = json.loads((output / "master_contacts.json").read_text(encoding="utf-8"))
            associated = json.loads((output / "associated_people.json").read_text(encoding="utf-8"))
            markdown = (output / "Associated People.md").read_text(encoding="utf-8")
            self.assertEqual(master["unique_contacts"], 0)
            self.assertEqual(master["unique_associated_people"], 1)
            self.assertEqual(master["associated_people_mentions"], 2)
            self.assertEqual(associated["unique_associated_people"], 1)
            self.assertEqual(len(associated["people"][0]["sources"]), 2)
            self.assertIn("Example Person", markdown)
            self.assertIn("Unresolved", markdown)

    def test_final_exports_preserve_displayed_count_saved_count_gap_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            with Database(root / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Coverage Output")
                threads, _ = db.add_profile(
                    subject["id"], "threads", "https://www.threads.com/@source"
                )
                instagram, _ = db.add_profile(
                    subject["id"], "instagram", "https://www.instagram.com/private"
                )
                first = self._threads_record("one")
                second = self._threads_record("two")
                db.upsert_contacts(subject["id"], threads["id"], "followers", [first, second])
                db.upsert_contacts(subject["id"], threads["id"], "following", [first])
                run_id = db.start_run(subject["id"], "coverage-run", str(root / "run"))
                for relation, reported, collected, total, status, reason in (
                    (
                        "followers", 3, 2, 2, "incomplete",
                        "platform_relationship_payload_exhausted_before_displayed_count",
                    ),
                    (
                        "following", 1, 1, 1, "complete",
                        "accumulated_unique_urls_equal_reported_count",
                    ),
                ):
                    db.save_collection_result(
                        run_id=run_id,
                        profile_id=threads["id"],
                        relation=relation,
                        reported_count=reported,
                        collected_this_run=collected,
                        new_contacts_added=collected,
                        total_unique_saved=total,
                        status=status,
                        reason=reason,
                        diagnostics_path=None,
                        started_at="2026-01-01T00:00:00+00:00",
                        completed_at="2026-01-01T00:01:00+00:00",
                    )
                for relation, reported in (("followers", 1119), ("following", 919)):
                    db.save_collection_result(
                        run_id=run_id,
                        profile_id=instagram["id"],
                        relation=relation,
                        reported_count=reported,
                        collected_this_run=0,
                        new_contacts_added=0,
                        total_unique_saved=0,
                        status="private",
                        reason="private_profile_relationship_list_unavailable",
                        diagnostics_path=None,
                        started_at="2026-01-01T00:00:00+00:00",
                        completed_at="2026-01-01T00:01:00+00:00",
                    )
                db.finish_run(run_id, "partial")
                output = export_subject(db, vault, subject["id"])

            master = json.loads((output / "master_contacts.json").read_text(encoding="utf-8"))
            threads_json = json.loads(
                (output / "platforms" / "threads.json").read_text(encoding="utf-8")
            )
            followers_json = json.loads(
                (output / "lists" / "threads" / "followers.json").read_text(encoding="utf-8")
            )
            followers = next(
                row for row in master["relationship_coverage"]
                if row["platform"] == "threads" and row["relationship"] == "followers"
            )
            following = next(
                row for row in master["relationship_coverage"]
                if row["platform"] == "threads" and row["relationship"] == "following"
            )
            self.assertEqual(followers["reported_count"], 3)
            self.assertEqual(followers["saved_unique_urls"], 2)
            self.assertEqual(followers["remaining_gap"], 1)
            self.assertEqual(followers["status"], "incomplete")
            self.assertEqual(followers["status_label"], "Incomplete — Browser-limited")
            self.assertEqual(following["saved_unique_urls"], 1)
            self.assertEqual(following["status"], "verified")
            self.assertEqual(master["relationship_coverage_summary"]["collectible_displayed_records"], 4)
            self.assertEqual(master["relationship_coverage_summary"]["collectible_saved_records"], 3)
            self.assertEqual(master["relationship_coverage_summary"]["collectible_remaining_gap"], 1)
            self.assertEqual(master["relationship_coverage_summary"]["private_displayed_records"], 2038)
            self.assertEqual(threads_json["relationship_coverage_summary"]["collectible_remaining_gap"], 1)
            self.assertEqual(followers_json["unique_contacts"], 2)
            self.assertEqual(followers_json["relationship_coverage"][0]["remaining_gap"], 1)

            for relative in (
                "Summary.md",
                "Master Contacts.md",
                "platforms/threads.md",
                "lists/threads/followers.md",
            ):
                markdown = (output / relative).read_text(encoding="utf-8")
                self.assertIn("Relationship collection coverage", markdown)
                self.assertIn("Incomplete — Browser-limited", markdown)
                self.assertIn("| threads | followers | 3 | 2 | 2 | 1 |", markdown)

    def test_run_report_includes_website_evidence_totals_and_source_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            run_dir = root / "run"
            with Database(root / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Website Run")
                profile, _ = db.add_profile(subject["id"], "generic", "https://example.com/team")
                db.upsert_associated_people(subject["id"], profile["id"], [{
                    "normalized_name": "example person",
                    "display_name": "Example Person",
                    "source_url": profile["url"],
                    "evidence_text": "Example Person works with Example Person.",
                    "extraction_source": "codex_rendered_direct",
                }])
                run_id = db.start_run(subject["id"], "website-run", str(run_dir))
                db.save_website_result(
                    run_id=run_id,
                    profile_id=profile["id"],
                    subject_present=True,
                    people_detected=1,
                    new_associations=1,
                    new_contacts_added=0,
                    status="complete",
                    reason="people_evidence_validated",
                    analysis_mode="direct",
                    author_name=None,
                    author_entity_type=None,
                    diagnostics_path=str(run_dir / "diagnostics"),
                    started_at="2026-01-01T00:00:00+00:00",
                    completed_at="2026-01-01T00:01:00+00:00",
                )
                db.finish_run(run_id, "complete")
                report = write_run_report(db, vault, subject["id"], run_id)

            markdown = report.read_text(encoding="utf-8")
            payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertIn("Website and content evidence", markdown)
            self.assertIn("**1** unique associated people", markdown)
            self.assertIn("people evidence validated", markdown)
            self.assertEqual(payload["summary"]["associated_people_unique_saved"], 1)
            self.assertEqual(payload["summary"]["website_sources_complete"], 1)

    def test_run_report_shows_latest_pass_and_accumulated_gap_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            run_dir = root / "run"
            with Database(root / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Partial Output")
                profile, _ = db.add_profile(
                    subject["id"], "instagram", "https://www.instagram.com/source"
                )
                db.upsert_contacts(
                    subject["id"], profile["id"], "followers",
                    [self._record("one"), self._record("two")],
                )
                run_id = db.start_run(subject["id"], "test-run", str(run_dir))
                db.save_collection_result(
                    run_id=run_id,
                    profile_id=profile["id"],
                    relation="followers",
                    reported_count=3,
                    collected_this_run=1,
                    new_contacts_added=0,
                    total_unique_saved=2,
                    status="incomplete",
                    reason="platform_relationship_payload_exhausted_before_displayed_count",
                    diagnostics_path=None,
                    started_at="2026-01-01T00:00:00+00:00",
                    completed_at="2026-01-01T00:01:00+00:00",
                )
                db.finish_run(run_id, "partial")
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "run.json").write_text(json.dumps({
                    "summary": {"new_relationship_urls": 1},
                    "results": [{
                        "profile_id": int(profile["id"]),
                        "relation": "followers",
                    }],
                }), encoding="utf-8")
                relation_dir = run_dir / "instagram" / f"profile-{profile['id']}"
                relation_dir.mkdir(parents=True, exist_ok=True)
                (relation_dir / "followers.json").write_text(json.dumps({
                    "profile_id": int(profile["id"]),
                    "relation": "followers",
                    "new_relationship_urls": 1,
                    "collector_status": "incomplete",
                    "records": [self._record("one")],
                }), encoding="utf-8")
                report = write_run_report(db, vault, subject["id"], run_id)

            markdown = report.read_text(encoding="utf-8")
            payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertIn("Partial — browser-limited", markdown)
            self.assertIn("Accumulated exact-count coverage: **2/3**", markdown)
            self.assertIn("Latest browser pass: **1/3**", markdown)
            self.assertIn("Remaining exact-count gap: **1**", markdown)
            self.assertEqual(payload["summary"]["latest_pass_count_gap"], 2)
            self.assertEqual(payload["summary"]["accumulated_count_gap"], 1)
            self.assertEqual(payload["results"][0]["new_relationship_urls"], 1)
            self.assertEqual(payload["results"][0]["collector_status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
