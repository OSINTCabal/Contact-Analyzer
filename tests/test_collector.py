import tempfile
import unittest
import inspect
from pathlib import Path
from unittest.mock import patch

from contactanalyzer_app.collector import (
    BrowserCollector,
    CollectionOutcome,
    FACEBOOK_FRIEND_FILTER_TABS_JS,
    FACEBOOK_RELATION_UNAVAILABLE_STATE_JS,
    GITHUB_EMPTY_STATE_JS,
    INSTAGRAM_PRIVATE_STATE_JS,
    LIST_STATE_JS,
    PINTEREST_ZERO_COUNT_JS,
    QUORA_FOLLOWER_COUNT_JS,
    THREADS_LIST_STATE_JS,
    THREADS_MODAL_JS,
    THREADS_PROFILE_CONTROL_JS,
    THREADS_PROFILE_STATE_JS,
    THREADS_SCROLL_JS,
    TIKTOK_LIST_STATE_JS,
    TIKTOK_MODAL_JS,
    TIKTOK_PROFILE_PRIVATE_STATE_JS,
    TIKTOK_PROFILE_UNAVAILABLE_STATE_JS,
    X_SCROLL_TARGET_JS,
    X_PROFILE_UNAVAILABLE_STATE_JS,
    relationship_collection_policy,
    relationship_payload_pages,
    tiktok_parse_exact_tab_count,
    tiktok_row_candidate,
    trusted_exhausted_instagram_count_lag,
)


class FakeTab:
    def __init__(self, result):
        self.result = result

    def evaluate(self, expression):
        return self.result


class FakePaginationTab:
    def __init__(self, action):
        self.action = action
        self.url = "https://github.com/source?tab=followers"
        self.cleared = False
        self.navigated = []

    def evaluate(self, expression):
        return self.action

    def clear_network_capture(self):
        self.cleared = True

    def navigate(self, url, settle_seconds):
        self.url = url
        self.navigated.append((url, settle_seconds))

    def current_url(self):
        return self.url


class FakeCDPClickTab:
    def __init__(self):
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params or {}))


class PartialCollectionTab:
    def __init__(self):
        self.url = "about:blank"

    def navigate(self, url, _settle_seconds):
        self.url = url

    def evaluate(self, expression):
        if "source_profile_unavailable" in expression:
            return {"unavailable": False}
        if "const selectors" in expression:
            return {
                "records": [{
                    "href": "https://x.com/collected_person",
                    "anchorText": "Collected Person",
                    "itemText": "Collected Person @collected_person",
                }],
                "scrollTop": 0,
                "scrollHeight": 1800,
                "clientHeight": 900,
                "atEnd": False,
                "spinner": False,
            }
        if "timeline: followers" in expression.casefold():
            return {"x": 400, "y": 700}
        return None

    def clear_network_capture(self):
        pass

    def drain_json_responses(self, _keywords):
        return []

    def current_url(self):
        return self.url

    def title(self):
        return "People following source / X"

    def screenshot(self, _path):
        pass

    def save_html(self, _path):
        pass

    def close(self, close_target=True):
        pass


class PartialCollectionBrowser:
    def __init__(self, tab):
        self.tab = tab

    def new_tab(self, _url):
        return self.tab


class CollectorTests(unittest.TestCase):
    def test_instagram_one_row_stale_count_requires_dual_source_exhaustion(self):
        records = [
            {"extraction_source": "browser_network_response+visible_browser_dom"},
            {"extraction_source": "visible_browser_dom+browser_network_response"},
        ]
        self.assertTrue(trusted_exhausted_instagram_count_lag(
            "instagram", 1, records, True, False
        ))
        self.assertFalse(trusted_exhausted_instagram_count_lag(
            "instagram", 1, records, False, None
        ))
        self.assertFalse(trusted_exhausted_instagram_count_lag(
            "instagram", 1, [{"extraction_source": "visible_browser_dom"}] * 2, True, False
        ))
        self.assertFalse(trusted_exhausted_instagram_count_lag(
            "instagram", 0, records, True, False
        ))

    def test_pinterest_plain_text_zero_following_is_exact(self):
        class PinterestZeroTab:
            def __init__(self):
                self.calls = 0

            def evaluate(self, expression):
                self.calls += 1
                if "pinterest_visible_zero_count" in expression:
                    return {
                        "ok": True,
                        "count": 0,
                        "raw": "0",
                        "text": "0 following",
                    }
                return []

        collector = BrowserCollector.__new__(BrowserCollector)
        count, raw, candidates = collector._count(
            PinterestZeroTab(),
            "pinterest",
            "https://www.pinterest.com/source_account/",
            "following",
        )
        self.assertEqual((count, raw), (0, "0"))
        self.assertEqual(candidates[-1]["source"], "pinterest_visible_zero_count")
        self.assertIn("document.querySelectorAll('div, span')", PINTEREST_ZERO_COUNT_JS)

    def test_pinterest_missing_header_gets_bounded_hydration_retry(self):
        class DelayedPinterestTab:
            def __init__(self):
                self.scans = 0
                self.navigations = []

            def evaluate(self, expression):
                self.scans += 1
                if self.scans < 3:
                    return []
                return [{"text": "0 following", "href": "", "score": 40}]

            def navigate(self, url, settle_seconds):
                self.navigations.append((url, settle_seconds))

        tab = DelayedPinterestTab()
        collector = BrowserCollector.__new__(BrowserCollector)
        with patch("contactanalyzer_app.collector.time.sleep"):
            count, raw, candidates, retries = collector._count_with_hydration_retry(
                tab,
                "pinterest",
                "https://www.pinterest.com/source_account/",
                "following",
                3.0,
            )
        self.assertEqual((count, raw.strip(), retries), (0, "0", 1))
        self.assertEqual(tab.navigations, [])
        self.assertEqual(candidates[-1]["hydration_retry"], 1)

    def test_quora_count_uses_exact_source_follower_tab_only(self):
        collector = BrowserCollector.__new__(BrowserCollector)
        count, raw, candidates = collector._count(
            FakeTab({"ok": True, "count": 1, "raw": "1", "text": "1 Follower"}),
            "quora",
            "https://www.quora.com/profile/Example-Writer",
            "followers",
        )
        self.assertEqual((count, raw), (1, "1"))
        self.assertEqual(candidates[0]["source"], "source_scoped_quora_follower_tab")
        self.assertIn("[role=\"tab\"]", QUORA_FOLLOWER_COUNT_JS)

    def test_quora_list_rows_require_source_followers_route_and_left_panel(self):
        self.assertIn("/\\/profile\\/[^/]+\\/followers", LIST_STATE_JS)
        self.assertIn("relationshipRight", LIST_STATE_JS)
        self.assertIn("platform === 'quora'", LIST_STATE_JS)

    def test_github_explicit_empty_relationship_text_is_exact_zero(self):
        self.assertIn("doesn’t have any followers yet", GITHUB_EMPTY_STATE_JS)
        self.assertIn("isn’t following anybody", GITHUB_EMPTY_STATE_JS)
        self.assertIn("explicit_empty_state", inspect.getsource(BrowserCollector.collect))

    def test_threads_dom_record_excludes_follow_button_from_display_name(self):
        records = BrowserCollector._dom_records(
            "threads",
            "https://www.threads.com/@source",
            "followers",
            {
                "records": [{
                    "href": "https://www.threads.com/@alex_example",
                    "anchorText": "alex_example",
                    "itemText": "alex_example Alex Example Follow",
                    "imageSrc": "",
                    "imageAlt": "",
                }],
            },
        )
        self.assertEqual(records[0]["display_name"], "Alex Example")

    def test_facebook_uses_one_thorough_accessible_list_pass(self):
        policy = relationship_collection_policy("facebook", {
            "completion_retry_limit": 4,
            "facebook_content_stall_round_limit": 9,
        })
        self.assertEqual(policy, {
            "completion_retry_limit": 0,
            "content_stall_round_limit": 9,
        })
        self.assertEqual(
            relationship_collection_policy("instagram", {"completion_retry_limit": 2}),
            {"completion_retry_limit": 0, "content_stall_round_limit": 20},
        )
        self.assertEqual(
            relationship_collection_policy(
                "instagram", {"instagram_content_stall_round_limit": 7}
            ),
            {"completion_retry_limit": 0, "content_stall_round_limit": 7},
        )
        list_state = __import__(
            'contactanalyzer_app.collector', fromlist=['LIST_STATE_JS']
        ).LIST_STATE_JS
        self.assertIn(".some(visible)", list_state)
        self.assertIn('[data-visualcompletion="loading-state"]', list_state)
        self.assertIn("relation === 'friends' && text === 'all friends'", list_state)
        self.assertIn("section.startsWith('friends_')", list_state)
        self.assertIn('a[role="tab"][href]', FACEBOOK_FRIEND_FILTER_TABS_JS)
        self.assertIn(
            "facebook_loading_stall_round_limit",
            inspect.getsource(BrowserCollector.collect),
        )
        self.assertIn(
            "facebook_loading_content_stall_round_limit",
            inspect.getsource(BrowserCollector.collect),
        )
        self.assertIn(
            "facebook_loading_without_new_rows",
            inspect.getsource(BrowserCollector._collect_facebook_friend_filters),
        )
        self.assertIn(
            'platform not in {"facebook", "x"}',
            inspect.getsource(BrowserCollector.collect),
        )
        self.assertIn(
            "Supplemental filters improve coverage",
            inspect.getsource(BrowserCollector.collect),
        )

    def test_facebook_friend_filters_union_by_canonical_url_and_skip_following(self):
        source = "https://www.facebook.com/profile.php?id=234567890123456"

        class FilterTab:
            def __init__(self):
                self.url = source + "&sk=friends_all"
                self.navigated = []
                self.tab_scans = 0

            def navigate(self, url, _settle_seconds, **_kwargs):
                self.url = url
                self.navigated.append(url)

            def evaluate(self, expression, **_kwargs):
                if 'a[role="tab"][href]' in expression:
                    self.tab_scans += 1
                    if self.tab_scans == 1:
                        return []
                    return [
                        {"text": "Current city", "href": source + "&sk=friends_current_city"},
                        {"text": "Hometown", "href": source + "&sk=friends_hometown"},
                        {"text": "Following", "href": source + "&sk=following"},
                    ]
                if expression == "delete window.__contactAnalyzerRoot":
                    return None
                if "const selectors" in expression:
                    first = {
                        "href": "https://www.facebook.com/same.person",
                        "anchorText": "Same Person",
                        "itemText": "Same Person",
                    }
                    rows = [first]
                    if "friends_hometown" in self.url:
                        rows.append({
                            "href": "https://www.facebook.com/second.person",
                            "anchorText": "Second Person",
                            "itemText": "Second Person",
                        })
                    return {
                        "records": rows,
                        "scrollTop": 100,
                        "scrollHeight": 100,
                        "clientHeight": 100,
                        "atEnd": True,
                        "spinner": False,
                    }
                return None

        collector = BrowserCollector.__new__(BrowserCollector)
        collector.settings = {
            "scroll_delay_seconds": 0,
            "stall_round_limit": 1,
            "end_stall_round_limit": 1,
            "facebook_content_stall_round_limit": 1,
            "facebook_loading_stall_round_limit": 1,
            "facebook_friend_filter_max_rounds": 2,
        }
        records = {}
        tab = FilterTab()
        diagnostics = collector._collect_facebook_friend_filters(
            tab,
            source,
            records,
            '["main a[href]"]',
            10,
            0,
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(diagnostics["added_unique"], 2)
        self.assertTrue(diagnostics["reopened_all_friends"])
        self.assertEqual([attempt["added_unique"] for attempt in diagnostics["attempts"]], [1, 1])
        self.assertFalse(any("following" in url and "friends_" not in url for url in tab.navigated))
        self.assertEqual(
            records["https://www.facebook.com/same.person"]["extraction_source"],
            "visible_browser_facebook_friend_filter",
        )

    def test_x_uses_one_thorough_accessible_list_pass(self):
        policy = relationship_collection_policy("x", {
            "completion_retry_limit": 4,
            "x_content_stall_round_limit": 17,
        })
        self.assertEqual(policy, {
            "completion_retry_limit": 0,
            "content_stall_round_limit": 17,
        })

    def test_poshmark_uses_one_thorough_accessible_list_pass(self):
        self.assertEqual(
            relationship_collection_policy("poshmark", {"completion_retry_limit": 4}),
            {"completion_retry_limit": 0, "content_stall_round_limit": None},
        )

    def test_facebook_hidden_nonzero_relationship_list_is_private(self):
        self.assertIn("no friends to show", FACEBOOK_RELATION_UNAVAILABLE_STATE_JS.casefold())
        source = inspect.getsource(BrowserCollector.collect)
        self.assertIn("private_relationship_list", source)
        self.assertIn('if platform == "facebook":', source)
        self.assertNotIn(
            'if platform == "facebook" and reported_count is not None and reported_count > 0:',
            source,
        )

    def test_poshmark_network_relationship_rows_and_cursor_are_scoped(self):
        source = "https://poshmark.com/closet/source_account"
        payload = {
            "data": [{
                "id": "54ab546c3a3efc1193349fd3",
                "username": "example_follower",
                "full_name": "Example Seller",
                "picture_url": "https://example.test/avatar.jpg",
            }],
            "more": {"next_max_id": 96},
        }
        url = "https://poshmark.com/vm-rest/users/source_account/followers?max_id=48&count=48"
        records = BrowserCollector._network_candidates("poshmark", payload, source, "followers")
        self.assertEqual([record["profile_url"] for record in records], [
            "https://poshmark.com/closet/example_follower"
        ])
        self.assertEqual(relationship_payload_pages("poshmark", url, payload, "followers"), [{
            "records": 1,
            "has_more": True,
            "next_max_id": 96,
            "page_size": 1,
            "status": None,
        }])
        self.assertEqual(relationship_payload_pages("poshmark", url, payload, "following"), [])

    def test_abbreviated_counts_are_not_exact_completion_targets(self):
        class FakeTab:
            def evaluate(self, _expression):
                return [{"text": "1.4K followers", "score": 250}]

        collector = BrowserCollector("http://127.0.0.1:9222", {})
        count, raw, _ = collector._count(
            FakeTab(), "facebook", "https://www.facebook.com/source", "followers"
        )
        self.assertIsNone(count)
        self.assertEqual(raw, "1.4K")

    def test_facebook_friend_count_uses_exact_profile_card_context(self):
        class FacebookCountTab:
            def evaluate(self, expression):
                if "facebook_friends_card" in expression:
                    return None
                if "const source = new URL" in expression:
                    return {"raw": "905", "text": "Friends See all friends 905 friends"}
                return [{"href": "https://www.facebook.com/source/friends", "text": "Friends", "score": 240}]

        collector = BrowserCollector("http://127.0.0.1:9222", {})
        count, raw, candidates = collector._count(
            FacebookCountTab(), "facebook", "https://www.facebook.com/source", "friends"
        )
        self.assertEqual((count, raw), (905, "905"))
        self.assertEqual(candidates[-1]["source"], "facebook_friends_card")

    def test_count_ignores_parent_metrics(self):
        collector = BrowserCollector.__new__(BrowserCollector)
        count, raw, _ = collector._count(
            FakeTab([{"href": "https://x.com/source_account/followers", "text": "Followers", "parentText": "999 followers", "score": 240}]),
            "x",
            "https://x.com/source_account",
            "followers",
        )
        self.assertIsNone(count)
        self.assertIsNone(raw)

    def test_merge_uses_canonical_url_not_platform_id(self):
        records = {}
        base = {
            "platform": "x",
            "source_profile_url": "https://x.com/source_account",
            "username": "person",
            "profile_url": "https://x.com/person",
        }
        self.assertTrue(BrowserCollector._merge_record(records, {**base, "platform_user_id": None, "extraction_source": "visible_browser_dom"}))
        self.assertFalse(BrowserCollector._merge_record(records, {**base, "platform_user_id": "123", "extraction_source": "browser_network_response"}))
        self.assertEqual(len(records), 1)
        record = next(iter(records.values()))
        self.assertEqual(record["platform_user_id"], "123")
        self.assertEqual(record["extraction_source"], "browser_network_response+visible_browser_dom")
        self.assertFalse(BrowserCollector._merge_record(records, {**base, "platform_user_id": "123", "extraction_source": "visible_browser_dom"}))
        self.assertEqual(record["extraction_source"], "browser_network_response+visible_browser_dom")

    def test_merge_rejects_subject_and_non_profile_urls(self):
        records = {}
        self.assertFalse(BrowserCollector._merge_record(records, {
            "platform": "x",
            "source_profile_url": "https://x.com/source_account",
            "profile_url": "https://x.com/source_account",
        }))
        self.assertFalse(BrowserCollector._merge_record(records, {
            "platform": "bluesky",
            "source_profile_url": "https://bsky.app/profile/source.bsky.social",
            "profile_url": "https://bsky.app/profile/person.bsky.social/post/123",
        }))
        self.assertEqual(records, {})

    def test_network_parser_reads_only_relationship_collections(self):
        x_payload = {
            "unrelated": {"legacy": {"screen_name": "suggested"}, "rest_id": "1"},
            "entry": {"user_results": {"result": {"legacy": {"screen_name": "follower", "name": "Follower"}, "rest_id": "2"}}},
        }
        x_records = BrowserCollector._network_candidates("x", x_payload, "https://x.com/source_account", "followers")
        self.assertEqual([record["username"] for record in x_records], ["follower"])

        current_x_payload = {
            "entry": {
                "user_results": {
                    "result": {
                        "rest_id": "3",
                        "core": {"screen_name": "current_follower", "name": "Current Follower"},
                        "avatar": {"image_url": "https://pbs.twimg.com/profile_images/current.jpg"},
                        "legacy": {"description": "Current X response shape"},
                    }
                }
            }
        }
        current_x_records = BrowserCollector._network_candidates(
            "x", current_x_payload, "https://x.com/source_account", "followers"
        )
        self.assertEqual([record["username"] for record in current_x_records], ["current_follower"])
        self.assertEqual(current_x_records[0]["display_name"], "Current Follower")
        self.assertEqual(current_x_records[0]["avatar_url"], "https://pbs.twimg.com/profile_images/current.jpg")

        bluesky_payload = {
            "followers": [{"handle": "follower.bsky.social", "did": "did:plc:1"}],
            "suggestions": [{"handle": "suggested.bsky.social", "did": "did:plc:2"}],
        }
        bluesky_records = BrowserCollector._network_candidates(
            "bluesky", bluesky_payload, "https://bsky.app/profile/source.bsky.social", "followers"
        )
        self.assertEqual([record["username"] for record in bluesky_records], ["follower.bsky.social"])

    def test_instagram_relationship_payload_end_is_exactly_scoped(self):
        payload = {
            "users": [{"username": "one"}, {"username": "two"}],
            "has_more": False,
            "page_size": 2,
            "status": "ok",
        }
        pages = relationship_payload_pages(
            "instagram",
            "https://www.instagram.com/api/v1/friendships/123/followers/?count=12",
            payload,
            "followers",
        )
        self.assertEqual(pages, [{
            "records": 2,
            "has_more": False,
            "next_max_id": None,
            "page_size": 2,
            "status": "ok",
        }])
        self.assertEqual(
            relationship_payload_pages(
                "instagram",
                "https://www.instagram.com/api/v1/friendships/show_many/",
                {"friendship_statuses": {"1": {"following": True}}},
                "followers",
            ),
            [],
        )
        self.assertEqual(
            relationship_payload_pages(
                "instagram",
                "https://www.instagram.com/api/v1/friendships/123/following/?count=12",
                payload,
                "followers",
            ),
            [],
        )

    def test_x_relationship_payload_cursor_is_exactly_scoped(self):
        payload = {
            "data": {
                "timeline": {
                    "entries": [
                        {"entryId": "user-1", "content": {"itemContent": {"user_results": {"result": {"rest_id": "1"}}}}},
                        {"entryId": "user-2", "content": {"itemContent": {"user_results": {"result": {"rest_id": "2"}}}}},
                        {"entryId": "cursor-bottom", "content": {"cursorType": "Bottom", "value": "next-page"}},
                    ]
                }
            }
        }
        pages = relationship_payload_pages(
            "x",
            "https://x.com/i/api/graphql/hash/Followers?variables=encoded",
            payload,
            "followers",
        )
        self.assertEqual(pages, [{
            "records": 2,
            "has_more": True,
            "next_cursor": "next-page",
            "page_size": None,
            "status": None,
        }])
        self.assertEqual(
            relationship_payload_pages(
                "x",
                "https://x.com/i/api/graphql/hash/Following?variables=encoded",
                payload,
                "followers",
            ),
            [],
        )
        self.assertEqual(
            relationship_payload_pages(
                "x",
                "https://x.com/i/api/graphql/hash/FollowersYouKnow?variables=encoded",
                payload,
                "followers",
            ),
            [],
        )

    def test_x_uses_bounded_trusted_wheel_sequence(self):
        tab = FakeCDPClickTab()
        delta = BrowserCollector._x_trusted_scroll(tab, 420.5, 680.5, 1000)
        self.assertEqual(delta, 720)
        self.assertEqual([call[1]["type"] for call in tab.calls], ["mouseMoved", "mouseWheel"])
        self.assertEqual(tab.calls[1][1]["deltaY"], 720)
        self.assertEqual(tab.calls[1][1]["deltaX"], 0)
        self.assertIn("timeline: followers", X_SCROLL_TARGET_JS.casefold())
        self.assertIn("timeline: following", X_SCROLL_TARGET_JS.casefold())

    def test_browser_control_error_preserves_validated_partial_rows(self):
        source = "https://x.com/source"
        collector = BrowserCollector.__new__(BrowserCollector)
        collector.browser = PartialCollectionBrowser(PartialCollectionTab())
        collector.settings = {
            "settle_seconds": 0,
            "scroll_delay_seconds": 0,
            "network_capture": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collector, "_count", return_value=(2, "2", [])), patch.object(
                collector,
                "_x_trusted_scroll",
                side_effect=TimeoutError("CDP mouse event timed out"),
            ):
                outcome = collector.collect(
                    platform="x",
                    source_url=source,
                    relation="followers",
                    diagnostics_dir=root / "diagnostics",
                    checkpoint_path=root / "checkpoint.json",
                )

        self.assertEqual(outcome.status, "incomplete")
        self.assertEqual(outcome.reason, "browser_control_error_after_partial_collection")
        self.assertEqual(outcome.collected_this_run, 1)
        self.assertEqual(outcome.records[0]["profile_url"], "https://x.com/collected_person")
        self.assertIn("timed out", outcome.diagnostics["collection_error"])

    def test_pagination_navigates_next_link_once(self):
        next_url = "https://github.com/source?tab=followers&page=2"
        tab = FakePaginationTab({"navigate": True, "href": next_url})
        visited = {tab.current_url()}
        self.assertTrue(BrowserCollector._advance_page(tab, "next expression", visited, 0.0))
        self.assertTrue(tab.cleared)
        self.assertEqual(tab.navigated, [(next_url, 0.0)])
        self.assertIn(next_url, visited)
        self.assertFalse(BrowserCollector._advance_page(tab, "next expression", visited, 0.0))

    def test_tiktok_exact_tab_counts_and_suggested_rejection(self):
        self.assertEqual(tiktok_parse_exact_tab_count("Following 8", "Following"), 8)
        self.assertEqual(tiktok_parse_exact_tab_count("Followers 2", "Followers"), 2)
        self.assertIsNone(tiktok_parse_exact_tab_count("Suggested", "Following"))
        self.assertIsNone(tiktok_parse_exact_tab_count("Follow", "Following"))

    def test_tiktok_friends_is_rejected(self):
        collector = BrowserCollector.__new__(BrowserCollector)
        collector.browser = None
        collector.settings = {}
        outcome = collector._collect_tiktok("https://www.tiktok.com/@source_account", "friends", __import__('pathlib').Path('/tmp'), __import__('pathlib').Path('/tmp/tiktok.checkpoint.json'))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "unsupported_tiktok_relation")

    def test_tiktok_rows_are_extracted_not_clicked_and_scoped(self):
        source = "https://www.tiktok.com/@source_account"
        self.assertEqual(tiktok_row_candidate("row_user", "", source, active_relation="followers"), "https://www.tiktok.com/@row_user")
        self.assertIsNone(tiktok_row_candidate("suggested_user", "", source, active_relation="followers"))
        self.assertIsNone(tiktok_row_candidate("row_user", "", source, active_relation="suggested"))
        self.assertIn("suggested", TIKTOK_MODAL_JS.lower())
        self.assertIn("modal", TIKTOK_LIST_STATE_JS)

    def test_tiktok_completion_and_navigation_guards_are_explicit(self):
        collector = BrowserCollector.__new__(BrowserCollector)
        self.assertEqual(collector._collect_tiktok.__name__, "_collect_tiktok")
        self.assertIn("navigation_guard_triggered", collector._collect_tiktok.__code__.co_consts)
        self.assertIn("collected_unique_equals_reported_count", collector._collect_tiktok.__code__.co_consts)
        self.assertIn("modal_reached_stable_end", collector._collect_tiktok.__code__.co_consts)
        self.assertIn("zero_count_profile_retries", collector._collect_tiktok.__code__.co_consts)
        self.assertIn("tiktok_profile_modal_count_mismatch", collector._collect_tiktok.__code__.co_consts)

    def test_tiktok_private_relationship_list_is_explicit_and_bounded(self):
        self.assertIn("privateList", TIKTOK_MODAL_JS)
        self.assertIn("list is private", TIKTOK_MODAL_JS.casefold())
        self.assertIn("this account is private", TIKTOK_PROFILE_PRIVATE_STATE_JS.casefold())
        constants = BrowserCollector._collect_tiktok.__code__.co_consts
        self.assertIn("private_profile_relationship_list_unavailable", constants)
        self.assertIn("private", constants)
        self.assertIn("exact_zero_count", constants)
        self.assertIn(3, constants)

    def test_tiktok_virtualized_scan_accumulates_before_scroll(self):
        scan_pos = TIKTOK_LIST_STATE_JS.find("for (const node")
        scroll_pos = TIKTOK_LIST_STATE_JS.find("scrollTop")
        self.assertGreater(scan_pos, -1)
        self.assertGreater(scroll_pos, -1)
        self.assertIn("Math.floor(root.clientHeight * 0.8)", __import__('contactanalyzer_app.collector', fromlist=['TIKTOK_SCROLL_JS']).TIKTOK_SCROLL_JS)

    def test_tiktok_trusted_click_sequence_and_bounded_fallbacks(self):
        tab = FakeCDPClickTab()
        BrowserCollector._tiktok_trusted_click(tab, 10.5, 20.5)
        self.assertEqual([call[0] for call in tab.calls], [
            "Input.dispatchMouseEvent", "Input.dispatchMouseEvent", "Input.dispatchMouseEvent"
        ])
        self.assertEqual([call[1]["type"] for call in tab.calls], ["mouseMoved", "mousePressed", "mouseReleased"])
        self.assertEqual(tab.calls[1][1]["button"], "left")
        self.assertEqual(tab.calls[1][1]["clickCount"], 1)
        self.assertIn("pointer_center", __import__('contactanalyzer_app.collector', fromlist=['BrowserCollector']).BrowserCollector._collect_tiktok.__code__.co_consts)
        self.assertIn("Enter", BrowserCollector._tiktok_key_fallback.__code__.co_consts)
        self.assertIn("Space", BrowserCollector._collect_tiktok.__code__.co_consts)

    def test_pinterest_and_depop_use_trusted_exact_relation_clicks(self):
        class RelationTab(FakeCDPClickTab):
            def evaluate(self, expression):
                self.expression = expression
                return {
                    "found": True,
                    "text": "13 followers",
                    "x": 100.5,
                    "y": 200.5,
                    "rect": {"x": 80, "y": 190, "width": 41, "height": 21},
                    "candidates": [],
                }

        collector = BrowserCollector.__new__(BrowserCollector)
        for platform in ("pinterest", "depop"):
            tab = RelationTab()
            action = collector._click_relation(
                tab,
                platform,
                f"https://www.{platform}.com/source/",
                "followers",
            )
            self.assertTrue(action["clicked"])
            self.assertEqual(action["method"], "trusted_pointer")
            self.assertIn("followers", tab.expression)
            self.assertEqual(
                [params["type"] for method, params in tab.calls if method == "Input.dispatchMouseEvent"],
                ["mouseMoved", "mousePressed", "mouseReleased"],
            )

    def test_tiktok_modal_requires_subject_and_all_three_tabs(self):
        self.assertIn("text.includes(subject)", TIKTOK_MODAL_JS)
        self.assertIn("text.includes('suggested')", TIKTOK_MODAL_JS)
        self.assertIn("following", TIKTOK_MODAL_JS)
        self.assertIn("followers", TIKTOK_MODAL_JS)

    def test_tiktok_count_statuses(self):
        self.assertEqual(BrowserCollector._tiktok_status(8, 8, "count"), "complete")
        self.assertEqual(BrowserCollector._tiktok_status(8, 7, "early_end"), "incomplete")
        self.assertEqual(BrowserCollector._tiktok_status(8, 9, "overflow"), "review")

    def test_threads_payloads_are_scoped_to_requested_relationship(self):
        payload = {
            "data": {
                "user": {
                    "followers": {
                        "edges": [
                            {"node": {"username": "one", "id": "1"}},
                            {"node": {"username": "two", "id": "2"}},
                        ],
                        "page_info": {"end_cursor": "20", "has_next_page": True},
                    },
                    "suggested_users": [{"username": "suggested"}],
                }
            }
        }
        self.assertEqual(
            relationship_payload_pages(
                "threads", "https://www.threads.com/graphql/query", payload, "followers"
            ),
            [{"records": 2, "has_more": True, "next_cursor": "20", "page_size": 2, "status": None}],
        )
        records = BrowserCollector._network_candidates(
            "threads", payload, "https://www.threads.com/@source", "followers"
        )
        self.assertEqual([record["username"] for record in records], ["one", "two"])
        self.assertNotIn("suggested", [record["username"] for record in records])
        self.assertEqual(
            relationship_payload_pages(
                "threads", "https://www.threads.com/graphql/query", payload, "following"
            ),
            [],
        )

    def test_threads_uses_verified_modal_tabs_and_modal_scroll_only(self):
        self.assertIn("exact_threads_followers_control_not_found", THREADS_PROFILE_CONTROL_JS)
        self.assertIn("[role=\"dialog\"]", THREADS_MODAL_JS)
        self.assertIn("aria-selected", THREADS_MODAL_JS)
        self.assertIn("threads_requested_tab_not_active", THREADS_LIST_STATE_JS)
        self.assertIn("roots[0]||dialog", THREADS_LIST_STATE_JS)
        self.assertIn("root.scrollTop", THREADS_SCROLL_JS)
        self.assertIn("[0]||dialog", THREADS_SCROLL_JS)
        self.assertNotIn("window.scroll", THREADS_SCROLL_JS)
        self.assertIn("scrollX", THREADS_LIST_STATE_JS)
        pass_source = inspect.getsource(BrowserCollector._collect_threads_pass)
        self.assertIn("browser_relationship_cursor_not_requested", pass_source)
        self.assertIn("scroll_event_errors", pass_source)
        self.assertIn("modal_scoped_dom_scroll", pass_source)
        self.assertIn("follow_buttons_clicked", pass_source)
        self.assertIn("navigation_guard_triggered", pass_source)

    def test_threads_private_zero_profile_state_is_explicit(self):
        self.assertIn("this profile is private", THREADS_PROFILE_STATE_JS.casefold())
        self.assertIn("followersRaw", THREADS_PROFILE_STATE_JS)
        self.assertIn("bodyMatch", THREADS_PROFILE_STATE_JS)
        constants = BrowserCollector._collect_threads_pass.__code__.co_consts
        self.assertIn("private_profile_relationship_list_unavailable", constants)
        self.assertIn("private", constants)

    def test_threads_private_pass_is_not_retried_as_a_failed_empty_list(self):
        source = "https://www.threads.com/@source"
        private = CollectionOutcome(
            "threads", source, "following", None, None, 0, "private",
            "private_profile_relationship_list_unavailable",
            "start", "end", [], {},
        )
        collector = BrowserCollector("http://127.0.0.1:9222", {
            "threads_completion_pass_limit": 6,
            "threads_no_new_pass_limit": 2,
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collector, "_collect_threads_pass", return_value=private) as mocked:
                result = collector._collect_threads(
                    source, "following", root / "diagnostics", root / "checkpoint.json"
                )
        self.assertEqual(result.status, "private")
        self.assertEqual(mocked.call_count, 1)

    def test_x_unavailable_source_profile_is_detected_before_list_collection(self):
        self.assertIn("this account", X_PROFILE_UNAVAILABLE_STATE_JS.casefold())
        self.assertIn("doesn", X_PROFILE_UNAVAILABLE_STATE_JS.casefold())
        self.assertIn("source_profile_unavailable", BrowserCollector.collect.__code__.co_consts)

    def test_tiktok_unavailable_source_profile_is_detected_before_modal_collection(self):
        self.assertIn("find this account", TIKTOK_PROFILE_UNAVAILABLE_STATE_JS.casefold())
        self.assertIn("source_profile_unavailable", BrowserCollector._collect_tiktok.__code__.co_consts)

    def test_threads_bounded_passes_accumulate_until_exact_count(self):
        source = "https://www.threads.com/@source"

        def outcome(usernames):
            records = [{
                "platform": "threads",
                "relationship": "followers",
                "source_profile_url": source,
                "username": username,
                "profile_url": f"https://www.threads.com/@{username}",
                "extraction_source": "visible_browser_dom",
            } for username in usernames]
            return CollectionOutcome(
                "threads", source, "followers", 3, "3", len(records),
                "incomplete", "platform_relationship_payload_exhausted_before_displayed_count",
                "start", "end", records, {},
            )

        collector = BrowserCollector("http://127.0.0.1:9222", {
            "threads_completion_pass_limit": 6,
            "threads_no_new_pass_limit": 2,
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                collector,
                "_collect_threads_pass",
                side_effect=[outcome(["one", "two"]), outcome(["two", "three"])],
            ) as mocked:
                result = collector._collect_threads(
                    source, "followers", root / "diagnostics", root / "checkpoint.json"
                )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.collected_this_run, 3)
        self.assertEqual([record["username"] for record in result.records], ["one", "three", "two"])
        self.assertEqual(mocked.call_count, 2)

    def test_threads_bounded_passes_stop_after_repeated_no_growth(self):
        source = "https://www.threads.com/@source"
        record = {
            "platform": "threads",
            "relationship": "followers",
            "source_profile_url": source,
            "username": "one",
            "profile_url": "https://www.threads.com/@one",
            "extraction_source": "visible_browser_dom",
        }
        partial = CollectionOutcome(
            "threads", source, "followers", 3, "3", 1, "incomplete",
            "platform_relationship_payload_exhausted_before_displayed_count",
            "start", "end", [record], {},
        )
        collector = BrowserCollector("http://127.0.0.1:9222", {
            "threads_completion_pass_limit": 9,
            "threads_no_new_pass_limit": 2,
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                collector, "_collect_threads_pass", side_effect=[partial, partial, partial]
            ) as mocked:
                result = collector._collect_threads(
                    source, "followers", root / "diagnostics", root / "checkpoint.json"
                )

        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.collected_this_run, 1)
        self.assertEqual(result.diagnostics["list_passes"], 3)
        self.assertEqual(mocked.call_count, 3)

    def test_instagram_private_profile_is_detected_before_collection(self):
        self.assertIn("this profile is private", INSTAGRAM_PRIVATE_STATE_JS.casefold())
        self.assertIn("private_profile_relationship_list_unavailable", BrowserCollector.collect.__code__.co_consts)


if __name__ == "__main__":
    unittest.main()
