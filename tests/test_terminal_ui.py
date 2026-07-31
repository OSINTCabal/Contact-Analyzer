import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from rich.console import Console

from contactanalyzer_app.terminal_ui import (
    CollectorOutputBridge,
    PlainUI,
    RICH_AVAILABLE,
    RichUI,
)


class _FakeProgress:
    def __init__(self, console):
        self.console = console
        self.updates = []

    def update(self, *args, **kwargs):
        self.updates.append((args, kwargs))


@unittest.skipUnless(RICH_AVAILABLE, "Rich is not installed")
class TerminalUITests(unittest.TestCase):
    def test_plain_collection_plan_labels_non_enumerable_profiles_as_skipped(self):
        output = io.StringIO()
        with redirect_stdout(output):
            PlainUI().plan([{
                "platform": "google_maps",
                "relations": [],
                "url": "https://www.google.com/maps/contrib/123456789012345678901/reviews/",
                "mode": "none",
            }])

        rendered = output.getvalue()
        self.assertIn("skip — no enumerable public graph", rendered)
        self.assertNotIn("discovery required", rendered)

    def test_collection_plan_stays_compact_at_narrow_terminal_width(self):
        output = io.StringIO()
        ui = RichUI()
        ui.console = Console(file=output, width=80, color_system=None)
        ui.plan([
            {
                "platform": "instagram",
                "relations": ["followers", "following"],
                "url": "https://www.instagram.com/example_person/",
                "mode": "enumerable",
            },
            {
                "platform": "linkedin",
                "relations": [],
                "url": "https://www.linkedin.com/in/example-person-00000000/",
                "mode": "conditional",
            },
            {
                "platform": "youtube",
                "relations": [],
                "url": "https://www.youtube.com/channel/UC0000000000000000000000",
                "mode": "conditional",
            },
            {
                "platform": "generic",
                "relations": [],
                "url": "https://example.com/team",
                "mode": "codex",
            },
        ])

        rendered = output.getvalue()
        self.assertLessEqual(len(rendered.splitlines()), 14)
        self.assertIn("instagram · followers, following", rendered)
        self.assertIn("instagram.com/example_person", rendered)
        self.assertIn("linkedin · verify exact visible controls first", rendered)
        self.assertIn("generic · Codex direct-page people analysis", rendered)
        self.assertNotIn("│ h │", rendered)

    def test_run_summary_metric_labels_do_not_wrap_at_narrow_width(self):
        output = io.StringIO()
        ui = RichUI()
        ui.console = Console(file=output, width=80, color_system=None)
        ui.run_summary({
            "collected_relationship_records": 231,
            "displayed_relationship_records": 234,
            "unexposed_count_gap": 3,
            "unique_contacts_saved": 155,
            "accumulated_relationship_records": 231,
            "accumulated_exact_count_records": 231,
            "accumulated_count_gap": 3,
            "latest_pass_relationship_records": 231,
            "latest_pass_count_gap": 3,
            "relationship_membership_overlap": 76,
            "new_relationship_urls": 231,
            "new_contacts_added": 155,
            "complete_relations": 4,
            "relationships_attempted": 6,
            "known_count_relationships": 6,
            "unknown_count_relationships": 0,
            "status": "partial",
            "status_label": "Partial — browser-limited",
        })

        rendered = output.getvalue()
        self.assertLessEqual(len(rendered.splitlines()), 15)
        self.assertIn("Exact-count coverage", rendered)
        self.assertIn("Overlap memberships", rendered)
        self.assertIn("New accounts this run", rendered)

    def test_unmatched_tiktok_output_does_not_recurse_into_capture_bridge(self):
        original_stdout = io.StringIO()
        with redirect_stdout(original_stdout):
            ui = RichUI()
            progress = _FakeProgress(ui.console)
            bridge = CollectorOutputBridge(ui.console, progress, 1)
            with redirect_stdout(bridge):
                print("[tiktok:followers] opening source profile https://www.tiktok.com/@subject")
                bridge.flush()

        self.assertEqual(original_stdout.getvalue(), "")
        self.assertIn("Opening source profile", progress.updates[-1][1]["description"])

    def test_tiktok_progress_updates_relation_and_overall_counts(self):
        original_stdout = io.StringIO()
        with redirect_stdout(original_stdout):
            ui = RichUI()
            progress = _FakeProgress(ui.console)
            bridge = CollectorOutputBridge(ui.console, progress, 1, overall_base=11)
            bridge.write("[tiktok:following] modal verified; active relation=following; expected=8\n")
            bridge.write(
                "[tiktok:following] round=1 visible=9 new=8 accumulated=8 "
                "first=one last=nine scroll=0/720\n"
            )

        self.assertEqual(progress.updates[0][1]["total"], 8)
        self.assertEqual(progress.updates[1][1]["completed"], 8)
        self.assertEqual(progress.updates[1][1]["overall"], 19)
        self.assertIn("one → nine", progress.updates[1][1]["description"])
        self.assertIn("scroll 0%", progress.updates[1][1]["description"])

    def test_threads_progress_updates_live_counts_without_printing_raw_lines(self):
        original_stdout = io.StringIO()
        with redirect_stdout(original_stdout):
            ui = RichUI()
            progress = _FakeProgress(ui.console)
            bridge = CollectorOutputBridge(ui.console, progress, 1, overall_base=33)
            bridge.write("[threads:followers] opening source profile https://www.threads.com/@source\n")
            bridge.write("[threads:followers] modal verified; active relation=followers; expected=143\n")
            bridge.write(
                "[threads:followers] round=4 visible=44 new=8 accumulated=44/143 "
                "first=one last=fortyfour scroll=1915/3412 viewport=897\n"
            )
            bridge.write(
                "[threads:followers] aggregate pass=4 new=2 accumulated=141/143\n"
            )
            bridge.write(
                "[threads:followers] exact-count retry 5/5: 141/143\n"
            )
            bridge.write(
                "[threads:followers] round=14 visible=122 new=1 accumulated=122/143 "
                "first=one last=onetwentytwo scroll=7000/8000 viewport=897\n"
            )

        self.assertEqual(original_stdout.getvalue(), "")
        self.assertEqual(progress.updates[-1][1]["completed"], 141)
        self.assertEqual(progress.updates[-1][1]["overall"], 174)
        self.assertEqual(progress.updates[1][1]["total"], 143)
        self.assertIn("122 visible", progress.updates[-1][1]["description"])
        self.assertIn("cumulative ≥141", progress.updates[-1][1]["description"])

    def test_private_relation_does_not_pollute_collectible_running_total(self):
        output = io.StringIO()
        ui = PlainUI()
        private = SimpleNamespace(
            platform="instagram",
            relation="followers",
            collected_this_run=0,
            reported_count=1119,
            status="private",
            reason="private_profile_relationship_list_unavailable",
        )
        threads = SimpleNamespace(
            platform="threads",
            relation="followers",
            collected_this_run=111,
            reported_count=143,
            status="incomplete",
            reason="platform_relationship_payload_exhausted_before_displayed_count",
        )
        with redirect_stdout(output):
            ui.result(private, 0, 0, 0, 0)
            ui.result(threads, 111, 111, 111, 111)

        rendered = output.getvalue()
        self.assertIn("excluded — private list unavailable", rendered)
        self.assertIn("Run browser-pass count so far: 111/143", rendered)
        self.assertNotIn("111/1262", rendered)

    def test_generic_progress_includes_retry_pass_and_overall_count(self):
        original_stdout = io.StringIO()
        with redirect_stdout(original_stdout):
            ui = RichUI()
            progress = _FakeProgress(ui.console)
            bridge = CollectorOutputBridge(ui.console, progress, 1, overall_base=20)
            bridge.write(
                "[instagram:followers] 85/87 round=24 added=0 stalls=1/7 pages=1 pass=2 "
                "visible=12 first=alpha last=omega scroll=640/1440 viewport=720\n"
            )

        update = progress.updates[-1][1]
        self.assertEqual(update["completed"], 85)
        self.assertEqual(update["total"], 87)
        self.assertEqual(update["overall"], 105)
        self.assertIn("pass 2", update["description"])
        self.assertIn("stalls 1/7", update["description"])
        self.assertIn("12 visible", update["description"])
        self.assertIn("alpha → omega", update["description"])
        self.assertIn("scroll 89%", update["description"])

    def test_progress_bridge_suppresses_identical_updates(self):
        original_stdout = io.StringIO()
        with redirect_stdout(original_stdout):
            ui = RichUI()
            progress = _FakeProgress(ui.console)
            bridge = CollectorOutputBridge(ui.console, progress, 1)
            line = "[x:followers] 2/3 round=2 added=0 stalls=1/1 pages=1 pass=1\n"
            bridge.write(line)
            bridge.write(line)

        self.assertEqual(len(progress.updates), 1)

    def test_plain_terminal_prints_cumulative_and_final_totals(self):
        output = io.StringIO()
        ui = PlainUI()
        outcome = SimpleNamespace(
            platform="x",
            relation="followers",
            collected_this_run=1,
            reported_count=1,
            status="verified",
            reason="accumulated_unique_urls_equal_reported_count",
        )
        with redirect_stdout(output):
            ui.banner("Subject", 1)
            ui.result(outcome, 1, 1, 1, 1)
            ui.run_summary({
                "collected_relationship_records": 1,
                "displayed_relationship_records": 1,
                "unexposed_count_gap": 0,
                "unique_contacts_saved": 1,
                "accumulated_relationship_records": 1,
                "accumulated_exact_count_records": 1,
                "accumulated_count_gap": 0,
                "latest_pass_relationship_records": 1,
                "latest_pass_count_gap": 0,
                "relationship_membership_overlap": 0,
                "new_relationship_urls": 1,
                "new_contacts_added": 1,
                "complete_relations": 1,
                "relationships_attempted": 1,
                "status": "complete",
                "status_label": "Complete — all exact counts verified",
            })

        rendered = output.getvalue()
        self.assertIn("Run browser-pass count so far: 1/1", rendered)
        self.assertIn("x:followers — Verified", rendered)
        self.assertIn("newly saved edges: 1", rendered)
        self.assertIn("Run totals — Complete — all exact counts verified", rendered)
        self.assertIn("unique platform accounts: 1", rendered)
        self.assertIn("Accumulated exact-count coverage: 1/1 (100.0%)", rendered)

    def test_plain_terminal_marks_x_browser_limited_collection(self):
        output = io.StringIO()
        ui = PlainUI()
        outcome = SimpleNamespace(
            platform="x",
            relation="followers",
            collected_this_run=57,
            reported_count=1501,
            status="incomplete",
            reason="browser_relationship_cursor_not_requested",
        )
        with redirect_stdout(output):
            ui.result(outcome, 57, 0, 57, 0)

        rendered = output.getvalue()
        self.assertIn("x:followers — Incomplete — Browser-limited", rendered)
        self.assertIn("57/1501 are saved cumulatively", rendered)
        self.assertIn("1444 displayed accounts were not exposed", rendered)


if __name__ == "__main__":
    unittest.main()
