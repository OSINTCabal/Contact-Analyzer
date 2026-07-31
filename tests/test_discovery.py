import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from contactanalyzer_app.discovery import (
    _facebook_source_relations,
    _is_source_relationship_route,
    _verify_candidate,
    discover_profile,
)


class _FakeDiscoveryTab:
    def __init__(self, source_url, state):
        self.url = source_url
        self.state = state
        self.navigations = []

    def navigate(self, url, settle_seconds):
        self.url = url
        self.navigations.append(url)

    def evaluate(self, expression):
        return {**self.state, "url": self.url}

    def current_url(self):
        return self.url


class DiscoveryTests(unittest.TestCase):
    def test_quora_uses_only_the_source_profile_follower_tab(self):
        source = "https://www.quora.com/profile/Example-Writer"

        class Tab:
            def navigate(self, _url, _settle_seconds):
                return None

            def evaluate(self, expression):
                if "exact_source_follower_tab_not_found" in expression:
                    return {
                        "ok": True,
                        "relation": "followers",
                        "count": 1,
                        "raw": "1",
                        "text": "1 Follower",
                        "route": source + "/followers",
                        "title": "Example Writer - Quora",
                    }
                return {
                    "url": source,
                    "title": "Example Writer - Quora",
                    "controls": [{
                        "relation": "following",
                        "matchedTerm": "following",
                        "text": "Following",
                        "href": "https://www.quora.com/following",
                    }],
                }

            def title(self):
                return "Example Writer - Quora"

            def close(self, close_target=True):
                return None

        class Browser:
            def new_tab(self, _url):
                return Tab()

        with patch("contactanalyzer_app.discovery.CDPBrowser", return_value=Browser()):
            result = discover_profile(
                "http://127.0.0.1:9222",
                source,
                "quora",
                settle_seconds=0,
            )

        self.assertEqual(result.available_relations, ["followers"])
        self.assertEqual(result.controls[0]["count"], 1)
        self.assertTrue(result.controls[0]["verified"])
        self.assertIn("Spaces, Topics, and Questions", result.notes)

    def test_non_graph_profile_returns_without_opening_browser(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "discovery.json"
            with patch("contactanalyzer_app.discovery.CDPBrowser", side_effect=AssertionError("browser opened")):
                result = discover_profile(
                    "http://127.0.0.1:9222",
                    "https://www.ebay.com/usr/example",
                    "ebay",
                    output_path=output,
                )
        self.assertEqual(result.graph_mode, "none")
        self.assertEqual(result.available_relations, [])

    def test_ignored_messaging_and_non_graph_sites_never_open_browser(self):
        cases = (
            ("https://t.me/examplecreator", "telegram"),
            ("https://kik.me/examplecreator", "kik"),
            ("https://www.tumblr.com/examplecreator-blog", "tumblr"),
            ("https://www.reddit.com/user/ExampleCreator/", "reddit"),
            ("https://open.spotify.com/user/examplecreator", "spotify"),
            ("https://soundbetter.com/profiles/123456-examplecreator", "soundbetter"),
            ("https://www.gofundme.com/f/example-fundraiser", "gofundme"),
        )
        with patch("contactanalyzer_app.discovery.CDPBrowser", side_effect=AssertionError("browser opened")):
            for url, platform in cases:
                with self.subTest(platform=platform):
                    result = discover_profile("http://127.0.0.1:9222", url, platform)
                    self.assertEqual(result.available_relations, [])

    def test_pinterest_and_depop_disable_generic_discovery(self):
        with patch("contactanalyzer_app.discovery.CDPBrowser", side_effect=AssertionError("generic browser discovery opened")):
            for url, platform in (
                ("https://www.pinterest.com/examplecreator/", "pinterest"),
                ("https://www.depop.com/examplecreator/", "depop"),
            ):
                with self.subTest(platform=platform):
                    result = discover_profile("http://127.0.0.1:9222", url, platform)
                    self.assertEqual(result.available_relations, ["followers", "following"])
                    self.assertIn("generic discovery disabled", result.notes)

    def test_threads_uses_only_its_dedicated_modal_adapter(self):
        with patch("contactanalyzer_app.discovery.CDPBrowser", side_effect=AssertionError("generic browser discovery opened")):
            result = discover_profile(
                "http://127.0.0.1:9222",
                "https://www.threads.com/@source",
                "threads",
            )
        self.assertEqual(result.available_relations, ["followers", "following"])
        self.assertIn("Dedicated Threads modal adapter", result.notes)

    def test_facebook_global_friends_page_is_not_a_subject_relationship_route(self):
        source = "https://www.facebook.com/example.person.589"
        self.assertFalse(
            _is_source_relationship_route(source, "https://www.facebook.com/friends/", "facebook", "friends")
        )
        self.assertTrue(
            _is_source_relationship_route(source, f"{source}/friends", "facebook", "friends")
        )

    def test_facebook_numeric_profile_accepts_only_same_id_relationship_routes(self):
        source = "https://www.facebook.com/profile.php?id=123456789012345"
        self.assertTrue(
            _is_source_relationship_route(
                source,
                source + "&sk=friends_all",
                "facebook",
                "friends",
            )
        )
        self.assertTrue(
            _is_source_relationship_route(
                source,
                source + "&sk=friends",
                "facebook",
                "friends",
            )
        )
        self.assertFalse(
            _is_source_relationship_route(
                source,
                "https://www.facebook.com/profile.php?id=999&sk=friends_all",
                "facebook",
                "friends",
            )
        )

    def test_facebook_selects_only_source_profile_relationship_controls(self):
        source = "https://www.facebook.com/ExampleCreator"
        controls = [
            {
                "relation": "friends",
                "href": "https://www.facebook.com/friends/",
                "exact_candidate": True,
            },
            {
                "relation": "friends",
                "href": f"{source}/friends",
                "exact_candidate": True,
            },
            {
                "relation": "followers",
                "href": f"{source}/followers/",
                "exact_candidate": True,
            },
            {
                "relation": "following",
                "href": f"{source}/following/",
                "exact_candidate": True,
            },
            {
                "relation": "followers",
                "href": "https://www.facebook.com/unrelated/followers/",
                "exact_candidate": True,
            },
        ]

        available = _facebook_source_relations(
            source,
            controls,
            ("friends", "followers", "following"),
        )

        self.assertEqual(available, ["friends", "followers", "following"])
        self.assertIsNone(controls[0].get("verified"))
        self.assertTrue(controls[1]["verified"])
        self.assertTrue(controls[2]["verified"])
        self.assertTrue(controls[3]["verified"])
        self.assertIsNone(controls[4].get("verified"))

    def test_facebook_source_control_allows_explicit_empty_collection_attempt(self):
        controls = [{
            "relation": "following",
            "href": "https://www.facebook.com/ExampleCreator/following/",
            "exact_candidate": True,
            "count": 493,
        }]
        available = _facebook_source_relations(
            "https://www.facebook.com/ExampleCreator",
            controls,
            ("friends", "followers", "following"),
        )
        self.assertEqual(available, ["following"])
        self.assertEqual(
            controls[0]["verification_reason"],
            "source_scoped_profile_relationship_control",
        )

    def test_facebook_discovers_following_from_friend_directory_tabs(self):
        source = "https://www.facebook.com/profile.php?id=234567890123456"

        class Tab:
            def __init__(self):
                self.url = source
                self.navigations = []

            def navigate(self, url, _settle_seconds):
                self.url = url
                self.navigations.append(url)

            def evaluate(self, _expression):
                if "friends_all" in self.url:
                    controls = [{
                        "relation": "following",
                        "matchedTerm": "following",
                        "text": "Following",
                        "href": source + "&sk=following",
                    }]
                else:
                    controls = [{
                        "relation": "friends",
                        "matchedTerm": "friends",
                        "text": "148 friends",
                        "href": source + "&sk=friends_all",
                    }]
                return {
                    "url": self.url,
                    "title": "Example Subject | Facebook",
                    "controls": controls,
                }

            def title(self):
                return "Example Subject | Facebook"

            def close(self, close_target=True):
                return None

        tab = Tab()

        class Browser:
            def new_tab(self, _url):
                return tab

        with patch("contactanalyzer_app.discovery.CDPBrowser", return_value=Browser()):
            result = discover_profile(
                "http://127.0.0.1:9222",
                source,
                "facebook",
                settle_seconds=0,
            )

        self.assertEqual(result.available_relations, ["friends", "following"])
        following = [control for control in result.controls if control["relation"] == "following"]
        self.assertEqual(len(following), 1)
        self.assertEqual(following[0]["discovery_context"], "facebook_friend_directory_tabs")
        self.assertTrue(following[0]["verified"])
        self.assertIn(source + "&sk=friends_all", tab.navigations)

    def test_unrelated_linkedin_profile_is_not_a_relationship_list(self):
        source = "https://www.linkedin.com/in/example-person-00000000/"
        target = "https://www.linkedin.com/company/example-research-center/"
        tab = _FakeDiscoveryTab(source, {
            "dialogVisible": True,
            "hrefs": ["https://www.linkedin.com/in/other-person-11111111/"],
            "emptyText": None,
        })

        verified, evidence, urls = _verify_candidate(
            tab,
            source,
            "linkedin",
            "followers",
            {"href": target, "count": 193655},
            ("main a[href*='/in/']",),
            0,
        )

        self.assertFalse(verified)
        self.assertEqual(evidence["verification_reason"], "no_profile_rows_in_relationship_context")
        self.assertEqual(urls, [])
        self.assertEqual(tab.navigations, [])
        self.assertEqual(tab.current_url(), source)

    def test_explicit_relationship_route_remains_verifiable(self):
        source = "https://x.com/source"
        target = "https://x.com/source/followers"
        tab = _FakeDiscoveryTab(source, {
            "dialogVisible": False,
            "hrefs": ["https://x.com/someone"],
            "emptyText": None,
        })

        verified, evidence, urls = _verify_candidate(
            tab,
            source,
            "x",
            "followers",
            {"href": target, "count": 1},
            ("main a[href]",),
            0,
        )

        self.assertTrue(verified)
        self.assertEqual(evidence["verification_reason"], "verified_profile_rows")
        self.assertEqual(urls, ["https://x.com/someone"])


if __name__ == "__main__":
    unittest.main()
