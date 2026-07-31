import tempfile
import unittest
import sqlite3
from pathlib import Path

from contactanalyzer_app.db import Database
from contactanalyzer_app.collection_status import (
    build_run_summary,
    cumulative_relationship_status,
    reason_label,
    relationship_coverage_note,
    run_status_label,
    status_label,
)


class DatabaseTests(unittest.TestCase):
    def test_subject_menu_order_preserves_creation_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                first = db.create_or_get_subject("900-Zulu")
                second = db.create_or_get_subject("100-Alpha")
                third = db.create_or_get_subject("500-Middle")
                rows = db.list_subjects()

        self.assertEqual(
            [row["id"] for row in rows],
            [first["id"], second["id"], third["id"]],
        )

    def test_threads_upsert_repairs_legacy_follow_button_display_names(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Threads Subject")
                profile, _ = db.add_profile(
                    subject["id"], "threads", "https://www.threads.com/@source"
                )
                db.upsert_contacts(
                    subject["id"],
                    profile["id"],
                    "followers",
                    [{
                        "platform": "threads",
                        "profile_url": "https://www.threads.com/@legacy",
                        "username": "legacy",
                        "display_name": "Legacy Person",
                    }],
                )
                db.conn.execute(
                    "UPDATE contacts SET display_name='legacy Legacy Person Follow' WHERE subject_id=?",
                    (subject["id"],),
                )
                db.conn.commit()
                db.upsert_contacts(
                    subject["id"],
                    profile["id"],
                    "followers",
                    [{
                        "platform": "threads",
                        "profile_url": "https://www.threads.com/@other",
                        "username": "other",
                        "display_name": "other Other Person Follow",
                    }],
                )
                names = {
                    row["username"]: row["display_name"]
                    for row in db.subject_contacts(subject["id"], "threads")
                }
                self.assertEqual(names, {"legacy": "Legacy Person", "other": "Other Person"})

    def test_private_profile_status_is_explicit(self):
        reason = "private_profile_relationship_list_unavailable"
        self.assertEqual(status_label("private", reason), "Private — List unavailable")
        self.assertIn("relationship list is private or hidden", reason_label(reason).casefold())

    def test_mixed_private_and_browser_limited_run_status_is_explicit(self):
        rows = [
            {"status": "private", "reason": "private_profile_relationship_list_unavailable"},
            {"status": "incomplete", "reason": "platform_relationship_payload_exhausted_before_displayed_count"},
        ]
        self.assertEqual(
            run_status_label("partial", rows),
            "Partial — private/browser-limited lists",
        )

    def test_private_counts_are_excluded_from_collectible_run_coverage(self):
        summary = build_run_summary(
            [
                {
                    "status": "private",
                    "reason": "private_profile_relationship_list_unavailable",
                    "reported_count": 1119,
                    "collected_this_run": 0,
                    "total_unique_saved": 0,
                },
                {
                    "status": "incomplete",
                    "reason": "platform_relationship_payload_exhausted_before_displayed_count",
                    "reported_count": 143,
                    "collected_this_run": 111,
                    "total_unique_saved": 111,
                },
            ],
            profiles_inspected=2,
            unique_contacts_saved=111,
            accumulated_relationship_records=111,
            status="partial",
        )

        self.assertEqual(summary["displayed_relationship_records"], 143)
        self.assertEqual(summary["accumulated_exact_count_records"], 111)
        self.assertEqual(summary["accumulated_count_gap"], 32)
        self.assertEqual(summary["collectible_relationships"], 1)
        self.assertEqual(summary["private_relationships"], 1)
        self.assertEqual(summary["private_displayed_records"], 1119)

    def test_unavailable_source_profile_is_excluded_from_collectible_coverage(self):
        rows = [
            {
                "status": "blocked",
                "reason": "source_profile_unavailable",
                "reported_count": None,
                "collected_this_run": 0,
                "total_unique_saved": 0,
            },
            {
                "status": "complete",
                "reason": "accumulated_unique_urls_equal_reported_count",
                "reported_count": 9,
                "collected_this_run": 9,
                "total_unique_saved": 9,
            },
        ]
        summary = build_run_summary(
            rows,
            profiles_inspected=2,
            unique_contacts_saved=9,
            accumulated_relationship_records=9,
            status="partial",
        )
        self.assertEqual(status_label("blocked", "source_profile_unavailable"), "Unavailable — Profile not found")
        self.assertEqual(summary["collectible_relationships"], 1)
        self.assertEqual(summary["unavailable_relationships"], 1)
        self.assertEqual(summary["displayed_relationship_records"], 9)

    def test_website_associations_deduplicate_without_inventing_graph_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Website Subject")
                profile, _ = db.add_profile(subject["id"], "generic", "https://example.com/team")
                record = {
                    "normalized_name": "example person",
                    "display_name": "Example Person",
                    "role": "Master Barber",
                    "organization": "Example",
                    "source_url": "https://example.com/team",
                    "evidence_text": "Example Person works with Example Person.",
                    "extraction_source": "codex_rendered_direct",
                    "canonical_profile_url": None,
                    "canonical_platform": None,
                }

                self.assertEqual(db.upsert_associated_people(subject["id"], profile["id"], [record]), (1, 1, 0))
                self.assertEqual(db.upsert_associated_people(subject["id"], profile["id"], [record]), (1, 0, 0))
                self.assertEqual(len(db.subject_associated_people(subject["id"])), 1)
                self.assertEqual(len(db.subject_contacts(subject["id"])), 0)
                self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM contact_edges").fetchone()[0], 0)

    def test_visible_canonical_association_promotes_contact_without_relationship_edge(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Website Subject")
                profile, _ = db.add_profile(subject["id"], "generic", "https://example.com/team")
                record = {
                    "normalized_name": "example person",
                    "display_name": "Example Person",
                    "source_url": "https://example.com/team",
                    "evidence_text": "Example Person is on the team.",
                    "extraction_source": "codex_rendered_direct",
                    "canonical_profile_url": "https://www.instagram.com/exampleperson/",
                    "canonical_platform": "instagram",
                    "username": "exampleperson",
                }

                self.assertEqual(db.upsert_associated_people(subject["id"], profile["id"], [record]), (1, 1, 1))
                contacts = db.subject_contacts(subject["id"])
                self.assertEqual(len(contacts), 1)
                self.assertEqual(contacts[0]["canonical_url"], "https://www.instagram.com/exampleperson")
                self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM contact_edges").fetchone()[0], 0)
                sources = [dict(row) for row in db.contact_sources(contacts[0]["id"])]
                self.assertEqual(sources[0]["source_type"], "website_association")
                self.assertIsNone(sources[0]["relation"])

    def test_human_facebook_post_author_deduplicates_existing_profile_contact(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Facebook Subject")
                profile, _ = db.add_profile(
                    subject["id"], "facebook", "https://www.facebook.com/subject"
                )
                post, _ = db.add_profile(
                    subject["id"],
                    "facebook",
                    "https://www.facebook.com/person/posts/123/",
                )
                db.upsert_contacts(
                    subject["id"],
                    profile["id"],
                    "friends",
                    [{
                        "platform": "facebook",
                        "profile_url": "https://www.facebook.com/example.author",
                        "username": "example.author",
                        "display_name": "Example Author",
                    }],
                )
                result = db.upsert_associated_people(
                    subject["id"],
                    post["id"],
                    [{
                        "normalized_name": "example author",
                        "display_name": "Example Author",
                        "source_url": "https://www.facebook.com/person/posts/123/",
                        "evidence_text": "Example Author posted about the subject.",
                        "extraction_source": "visible_browser_facebook_post_author",
                        "canonical_profile_url": "https://www.facebook.com/example.author",
                        "canonical_platform": "facebook",
                        "username": "example.author",
                    }],
                )
                self.assertEqual(result, (1, 1, 0))
                self.assertEqual(len(db.subject_contacts(subject["id"], "facebook")), 1)
                self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM contact_edges").fetchone()[0], 1)
                association = db.subject_associated_people(subject["id"])[0]
                self.assertIsNotNone(association["contact_id"])

    def test_deleted_subject_folders_are_pruned_after_database_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subjects_root = root / "vault" / "Subjects"
            subjects_root.mkdir(parents=True)
            backup_dir = root / "vault" / ".contactanalyzer" / "backups"
            database_path = root / "test.sqlite3"
            with Database(database_path) as db:
                keep = db.create_or_get_subject("Keep Subject")
                deleted = db.create_or_get_subject("Deleted Subject")
                (subjects_root / str(keep["slug"])).mkdir()
                db.add_profile(deleted["id"], "x", "https://x.com/deleted")

                result = db.prune_subjects_missing_folders(subjects_root, backup_dir)

                self.assertEqual([row["name"] for row in result["removed"]], ["Deleted Subject"])
                self.assertIsNone(db.get_subject("Deleted Subject"))
                self.assertIsNotNone(db.get_subject("Keep Subject"))
                self.assertEqual(
                    db.conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0],
                    0,
                )

            backup_path = Path(result["backup_path"])
            self.assertTrue(backup_path.is_file())
            backup = sqlite3.connect(backup_path)
            try:
                self.assertEqual(backup.execute("SELECT COUNT(*) FROM subjects").fetchone()[0], 2)
                self.assertEqual(backup.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 1)
            finally:
                backup.close()

    def test_dedup_across_reruns(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Test Subject")
                profile, _ = db.add_profile(subject["id"], "x", "https://x.com/source")
                record = {
                    "platform": "x",
                    "profile_url": "https://x.com/person",
                    "username": "person",
                    "display_name": "Person",
                    "platform_user_id": None,
                    "avatar_url": None,
                }
                first = db.upsert_contacts(subject["id"], profile["id"], "followers", [record])
                second = db.upsert_contacts(subject["id"], profile["id"], "followers", [record])
                self.assertEqual(first, (1, 1))
                self.assertEqual(second, (1, 0))
                self.assertEqual(db.total_for_profile_relation(profile["id"], "followers"), 1)

    def test_platform_contact_is_unique_across_relations(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Test Subject")
                profile, _ = db.add_profile(subject["id"], "x", "https://x.com/source")
                record = {
                    "platform": "x",
                    "profile_url": "https://x.com/person/",
                    "username": "person",
                    "display_name": "Person",
                    "platform_user_id": None,
                    "avatar_url": None,
                }
                db.upsert_contacts(subject["id"], profile["id"], "followers", [record])
                db.upsert_contacts(subject["id"], profile["id"], "following", [record])
                self.assertEqual(len(db.subject_contacts(subject["id"], "x")), 1)
                self.assertEqual(db.total_for_profile_relation(profile["id"], "followers"), 1)
                self.assertEqual(db.total_for_profile_relation(profile["id"], "following"), 1)

    def test_upsert_rejects_any_saved_subject_profile_as_a_contact(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Test Subject")
                source, _ = db.add_profile(
                    subject["id"], "facebook", "https://www.facebook.com/source"
                )
                other_source, _ = db.add_profile(
                    subject["id"], "facebook", "https://www.facebook.com/OtherSource/"
                )

                result = db.upsert_contacts(
                    subject["id"],
                    source["id"],
                    "friends",
                    [
                        {
                            "platform": "facebook",
                            "profile_url": "https://www.facebook.com/othersource",
                            "username": "othersource",
                        },
                        {
                            "platform": "facebook",
                            "profile_url": "https://www.facebook.com/external",
                            "username": "external",
                        },
                    ],
                )

                self.assertEqual(result, (1, 1))
                self.assertEqual(
                    [row["canonical_url"] for row in db.subject_contacts(subject["id"])],
                    ["https://www.facebook.com/external"],
                )
                self.assertEqual(
                    db.total_for_profile_relation(other_source["id"], "friends"),
                    0,
                )

    def test_partial_reruns_add_only_unseen_relationship_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Cumulative Subject")
                profile, _ = db.add_profile(subject["id"], "x", "https://x.com/source")

                first_records = [
                    {"platform": "x", "profile_url": "https://x.com/one", "username": "one"},
                    {"platform": "x", "profile_url": "https://x.com/two", "username": "two"},
                ]
                second_records = [
                    {"platform": "x", "profile_url": "https://x.com/two", "username": "two"},
                    {"platform": "x", "profile_url": "https://x.com/three", "username": "three"},
                ]

                self.assertEqual(db.upsert_contacts(subject["id"], profile["id"], "followers", first_records), (2, 2))
                self.assertEqual(db.upsert_contacts(subject["id"], profile["id"], "followers", second_records), (2, 1))
                self.assertEqual(db.total_for_profile_relation(profile["id"], "followers"), 3)
                self.assertEqual(len(db.subject_contacts(subject["id"], "x")), 3)

    def test_accumulated_exact_count_is_verified(self):
        self.assertEqual(
            cumulative_relationship_status("incomplete", "early_end", 3, 3),
            ("verified", "accumulated_unique_urls_equal_reported_count"),
        )

    def test_latest_relationship_results_retain_pairs_not_attempted_in_newer_run(self):
        with tempfile.TemporaryDirectory() as directory:
            with Database(Path(directory) / "test.sqlite3") as db:
                subject = db.create_or_get_subject("Cumulative Results")
                profile, _ = db.add_profile(
                    subject["id"], "threads", "https://www.threads.com/@source"
                )
                first_run = db.start_run(subject["id"], "first", str(Path(directory) / "first"))
                db.save_collection_result(
                    run_id=first_run,
                    profile_id=profile["id"],
                    relation="followers",
                    reported_count=143,
                    collected_this_run=141,
                    new_contacts_added=141,
                    total_unique_saved=141,
                    status="incomplete",
                    reason="platform_relationship_payload_exhausted_before_displayed_count",
                    diagnostics_path=None,
                    started_at="2026-01-01T00:00:00+00:00",
                    completed_at="2026-01-01T00:01:00+00:00",
                )
                db.finish_run(first_run, "partial")
                second_run = db.start_run(subject["id"], "second", str(Path(directory) / "second"))
                db.save_collection_result(
                    run_id=second_run,
                    profile_id=profile["id"],
                    relation="following",
                    reported_count=36,
                    collected_this_run=36,
                    new_contacts_added=36,
                    total_unique_saved=36,
                    status="verified",
                    reason="accumulated_unique_urls_equal_reported_count",
                    diagnostics_path=None,
                    started_at="2026-01-02T00:00:00+00:00",
                    completed_at="2026-01-02T00:01:00+00:00",
                )
                db.finish_run(second_run, "partial")

                rows = db.latest_relationship_results(subject["id"])

            self.assertEqual({row["relation"] for row in rows}, {"followers", "following"})
            followers = next(row for row in rows if row["relation"] == "followers")
            self.assertEqual(followers["reported_count"], 143)
            self.assertEqual(followers["run_stamp"], "first")

    def test_accumulated_short_and_excess_counts_are_not_verified(self):
        self.assertEqual(
            cumulative_relationship_status("incomplete", "early_end", 3, 2)[0],
            "incomplete",
        )
        self.assertEqual(
            cumulative_relationship_status("complete", "count_reached", 3, 4)[0],
            "review",
        )

    def test_x_browser_limit_reason_survives_cumulative_status(self):
        status, reason = cumulative_relationship_status(
            "incomplete",
            "browser_relationship_cursor_not_requested",
            1501,
            57,
        )
        self.assertEqual(status, "incomplete")
        self.assertEqual(reason, "browser_relationship_cursor_not_requested")
        self.assertEqual(status_label(status, reason), "Incomplete — Browser-limited")
        self.assertIn("All rows exposed this run were saved", reason_label(reason))

    def test_x_browser_limit_has_explicit_coverage_note(self):
        note = relationship_coverage_note(
            "x",
            1501,
            57,
            57,
            "incomplete",
            "browser_relationship_cursor_not_requested",
        )
        self.assertIn("57/1501", note)
        self.assertIn("1444 displayed accounts were not exposed", note)

    def test_partial_browser_control_error_is_reported_as_browser_limited(self):
        reason = "browser_control_error_after_partial_collection"
        status, persisted_reason = cumulative_relationship_status(
            "incomplete", reason, 64, 50
        )
        self.assertEqual((status, persisted_reason), ("incomplete", reason))
        self.assertEqual(status_label(status, reason), "Incomplete — Browser-limited")
        self.assertIn("trusted rows", reason_label(reason).casefold())

    def test_instagram_browser_limit_has_explicit_numeric_coverage(self):
        note = relationship_coverage_note(
            "instagram",
            87,
            85,
            85,
            "incomplete",
            "platform_relationship_payload_exhausted_before_displayed_count",
        )
        self.assertIn("85/87", note)
        self.assertIn("2 displayed accounts were not exposed", note)

        singular = relationship_coverage_note(
            "instagram",
            133,
            132,
            132,
            "incomplete",
            "platform_relationship_payload_exhausted_before_displayed_count",
        )
        self.assertIn("1 displayed account was not exposed", singular)

    def test_untrusted_pass_cannot_promote_accumulated_records(self):
        self.assertEqual(
            cumulative_relationship_status("failed", "navigation_guard", 3, 3),
            ("failed", "navigation_guard"),
        )

    def test_run_summary_separates_latest_pass_edges_and_unique_accounts(self):
        results = [
            {
                "reported_count": 221,
                "collected_this_run": 218,
                "total_unique_saved": 218,
                "new_relationship_urls": 1,
                "new_contacts_added": 0,
                "status": "incomplete",
                "reason": "platform_relationship_payload_exhausted_before_displayed_count",
            },
            {
                "reported_count": 13,
                "collected_this_run": 13,
                "total_unique_saved": 13,
                "new_relationship_urls": 0,
                "new_contacts_added": 0,
                "status": "verified",
                "reason": "accumulated_unique_urls_equal_reported_count",
            },
        ]
        summary = build_run_summary(
            results,
            profiles_inspected=3,
            unique_contacts_saved=155,
            accumulated_relationship_records=231,
            new_relationship_urls=1,
            status="partial",
        )

        self.assertEqual(summary["displayed_relationship_records"], 234)
        self.assertEqual(summary["latest_pass_relationship_records"], 231)
        self.assertEqual(summary["accumulated_exact_count_records"], 231)
        self.assertEqual(summary["accumulated_count_gap"], 3)
        self.assertEqual(summary["unique_contacts_saved"], 155)
        self.assertEqual(summary["relationship_membership_overlap"], 76)
        self.assertEqual(summary["status_label"], "Partial — browser-limited")

    def test_run_summary_uses_accumulated_coverage_across_partial_reruns(self):
        summary = build_run_summary(
            [{
                "reported_count": 3,
                "collected_this_run": 2,
                "total_unique_saved": 3,
                "status": "verified",
                "reason": "accumulated_unique_urls_equal_reported_count",
            }],
            profiles_inspected=1,
            unique_contacts_saved=3,
            accumulated_relationship_records=3,
            status="complete",
        )

        self.assertEqual(summary["latest_pass_count_gap"], 1)
        self.assertEqual(summary["accumulated_count_gap"], 0)
        self.assertEqual(summary["status_label"], "Complete — all exact counts verified")


if __name__ == "__main__":
    unittest.main()
