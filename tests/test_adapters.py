import unittest

from contactanalyzer_app.adapters import SPECS, count_href_fragments, facebook_friend_filter_route, network_keywords, normalize_profile_link, platform_for, relation_url, relations_for, source_identity, threads_display_name, tiktok_canonical_url
from contactanalyzer_app.platform_catalog import default_relations, graph_mode
from contactanalyzer_app.util import extract_name, extract_urls, parse_count


class AdapterTests(unittest.TestCase):
    def test_threads_display_name_rejects_row_control_text(self):
        self.assertEqual(
            threads_display_name("alex_example", "alex_example Alex Example Follow"),
            "Alex Example",
        )
        self.assertEqual(
            threads_display_name("casey.sample", "casey.sample Casey Sample Following"),
            "Casey Sample",
        )

    def test_x_rows_are_scoped_to_relationship_timeline(self):
        selectors = SPECS["x"].row_selectors
        self.assertIn("main [aria-label='Timeline: Followers'] [data-testid='UserCell'] a[href][tabindex='-1']:not(:has(img))", selectors)
        self.assertIn("main [aria-label='Timeline: Following'] [data-testid='UserCell'] a[href][tabindex='-1']:not(:has(img))", selectors)
        self.assertNotIn("main [aria-label='Timeline: Followers'] [data-testid='UserCell'] a[href]", selectors)
        self.assertNotIn("main [aria-label='Timeline: Following'] [data-testid='UserCell'] a[href]", selectors)
        self.assertNotIn("main [data-testid='UserCell'] a[href]", selectors)

    def test_platform_detection(self):
        self.assertEqual(platform_for("https://x.com/source_account"), "x")
        self.assertEqual(platform_for("https://github.com/source_account"), "github")
        self.assertEqual(platform_for("https://mastodon.social/@user"), "mastodon")
        self.assertEqual(platform_for("https://www.threads.com/@user"), "threads")

    def test_only_canonical_relations(self):
        for platform in ["instagram", "github", "youtube", "linkedin", "generic"]:
            self.assertTrue(set(relations_for(platform)).issubset({"followers", "following", "friends"}))

    def test_x_normalization(self):
        value = normalize_profile_link("x", "https://x.com/example", "https://x.com/source_account")
        self.assertEqual(value, ("example", "https://x.com/example", None))
        self.assertIsNone(normalize_profile_link("x", "https://x.com/home", "https://x.com/source_account"))

    def test_github_normalization(self):
        value = normalize_profile_link("github", "https://github.com/octocat", "https://github.com/source_account")
        self.assertEqual(value, ("octocat", "https://github.com/octocat", None))
        self.assertIsNone(normalize_profile_link("github", "https://github.com/topics/osint", "https://github.com/source_account"))

    def test_bluesky_normalization_rejects_non_profile_routes(self):
        source = "https://bsky.app/profile/source.bsky.social"
        self.assertEqual(
            normalize_profile_link("bluesky", "https://bsky.app/profile/example.bsky.social", source),
            ("example.bsky.social", "https://bsky.app/profile/example.bsky.social", None),
        )
        self.assertIsNone(
            normalize_profile_link("bluesky", "https://bsky.app/profile/example.bsky.social/post/123", source)
        )

    def test_x_follower_count_controls_are_exact_routes(self):
        fragments = count_href_fragments("x", "https://x.com/source_account", "followers")
        self.assertIn("/source_account/followers", fragments)
        self.assertIn("/source_account/verified_followers", fragments)

    def test_relation_routes(self):
        self.assertEqual(relation_url("x", "https://x.com/source_account", "following"), "https://x.com/source_account/following")
        self.assertEqual(relation_url("github", "https://github.com/source_account", "followers"), "https://github.com/source_account?tab=followers")
        self.assertEqual(
            relation_url("facebook", "https://www.facebook.com/ExampleProfile", "friends"),
            "https://www.facebook.com/ExampleProfile/friends_all",
        )
        self.assertEqual(
            relation_url(
                "facebook",
                "https://www.facebook.com/profile.php?id=123456789012345",
                "friends",
            ),
            "https://www.facebook.com/profile.php?id=123456789012345&sk=friends_all",
        )
        self.assertEqual(
            relation_url(
                "quora",
                "https://www.quora.com/profile/Example-Writer",
                "followers",
            ),
            "https://www.quora.com/profile/Example-Writer/followers",
        )
        self.assertIsNone(relation_url(
            "quora",
            "https://www.quora.com/profile/Example-Writer",
            "following",
        ))

    def test_quora_rows_do_not_require_a_semantic_main(self):
        self.assertEqual(SPECS["quora"].row_selectors, ("a[href*='/profile/']",))
        self.assertIsNone(normalize_profile_link(
            "quora",
            "https://www.quora.com/topic/Investigations",
            "https://www.quora.com/profile/Example-Writer",
        ))

    def test_intake(self):
        text = "heres my next subject example person\nhttps://x.com/source_account\nhttps://github.com/source_account"
        self.assertEqual(extract_name(text), "example person")
        self.assertEqual(len(extract_urls(text)), 2)

    def test_counts(self):
        self.assertEqual(parse_count("1,234"), 1234)
        self.assertEqual(parse_count("12.4K"), 12400)

    def test_tiktok_only_followers_and_following(self):
        self.assertEqual(relations_for("tiktok"), ("followers", "following"))
        self.assertNotIn("friends", relations_for("tiktok"))

    def test_facebook_accepts_every_canonical_relationship(self):
        expected = ("friends", "followers", "following")
        self.assertEqual(relations_for("facebook"), expected)
        self.assertEqual(default_relations("facebook"), expected)
        self.assertEqual(network_keywords("facebook", "followers"), tuple())

    def test_facebook_count_routes_are_source_specific_not_learned_per_host(self):
        source = "https://www.facebook.com/ExampleCreator"
        self.assertEqual(
            count_href_fragments("facebook", source, "followers"),
            ("/ExampleCreator/followers",),
        )
        self.assertEqual(
            count_href_fragments("facebook", source, "friends"),
            ("/ExampleCreator/friends", "/ExampleCreator/friends_all"),
        )
        self.assertNotIn(
            "unrelated.account",
            " ".join(count_href_fragments("facebook", source, "following")).casefold(),
        )
        numeric_source = "https://www.facebook.com/profile.php?id=123456789012345"
        self.assertEqual(
            count_href_fragments("facebook", numeric_source, "friends"),
            (
                "/profile.php?id=123456789012345&sk=friends",
                "/profile.php?id=123456789012345&sk=friends_all",
            ),
        )

    def test_facebook_friend_filters_are_source_scoped_and_exclude_following(self):
        numeric = "https://www.facebook.com/profile.php?id=234567890123456"
        city = numeric + "&sk=friends_current_city"
        self.assertEqual(facebook_friend_filter_route(numeric, city), city)
        self.assertIsNone(facebook_friend_filter_route(numeric, numeric + "&sk=friends_all"))
        self.assertIsNone(facebook_friend_filter_route(numeric, numeric + "&sk=following"))
        self.assertIsNone(facebook_friend_filter_route(
            numeric,
            "https://www.facebook.com/profile.php?id=999&sk=friends_hometown",
        ))

        vanity = "https://www.facebook.com/example.person"
        hometown = vanity + "/friends_hometown"
        self.assertEqual(facebook_friend_filter_route(vanity, hometown), hometown)
        self.assertIsNone(facebook_friend_filter_route(vanity, vanity + "/following"))

    def test_non_graph_profiles_are_skipped(self):
        self.assertEqual(default_relations("ebay"), tuple())
        self.assertEqual(graph_mode("ebay"), "none")
        self.assertEqual(graph_mode("cashapp"), "none")
        self.assertEqual(default_relations("google_maps"), tuple())
        self.assertEqual(graph_mode("google_maps"), "none")
        self.assertEqual(graph_mode("snapchat"), "private")
        self.assertEqual(graph_mode("generic"), "codex")

    def test_private_messaging_and_non_graph_profiles_are_ignored(self):
        expected = {
            "https://t.me/examplecreator": ("telegram", "private"),
            "https://kik.me/examplecreator": ("kik", "private"),
            "https://cash.app/$ExampleCreator": ("cashapp", "none"),
            "https://www.snapchat.com/@examplecreator": ("snapchat", "private"),
            "https://www.pscp.tv/ExampleBroadcast/1ExampleBroadcast": ("periscope", "none"),
            "https://www.tumblr.com/examplecreator-blog": ("tumblr", "none"),
            "https://www.reddit.com/user/ExampleCreator/": ("reddit", "none"),
            "https://open.spotify.com/user/examplecreator": ("spotify", "none"),
            "https://soundbetter.com/profiles/123456-examplecreator": ("soundbetter", "none"),
            "https://www.google.com/maps/contrib/123456789012345678901/reviews/@0.000000": ("google_maps", "none"),
            "https://www.gofundme.com/f/example-fundraiser": ("gofundme", "none"),
        }
        for url, (platform, mode) in expected.items():
            with self.subTest(url=url):
                self.assertEqual(platform_for(url), platform)
                self.assertEqual(relations_for(platform), tuple())
                self.assertEqual(graph_mode(platform), mode)

    def test_new_enumerable_platform_routes_and_relations(self):
        source = "https://poshmark.com/closet/examplecreator"
        self.assertEqual(source_identity("poshmark", source)[0], "examplecreator")
        self.assertEqual(
            relation_url("poshmark", source, "followers"),
            "https://poshmark.com/user/examplecreator/followers",
        )
        self.assertEqual(
            relation_url("disqus", "https://disqus.com/by/examplecreator/", "following"),
            "https://disqus.com/by/examplecreator/following/",
        )
        self.assertIsNone(
            relation_url("pinterest", "https://www.pinterest.com/examplecreator/", "following")
        )
        self.assertEqual(count_href_fragments("poshmark", source, "followers"), tuple())
        self.assertEqual(network_keywords("poshmark", "followers"), ("/followers?",))
        self.assertEqual(network_keywords("poshmark", "following"), ("/following?",))
        for platform in ("soundcloud", "pinterest", "depop", "poshmark", "disqus"):
            self.assertEqual(relations_for(platform), ("followers", "following"))

    def test_soundcloud_and_pinterest_do_not_require_semantic_main(self):
        self.assertEqual(
            SPECS["soundcloud"].row_selectors,
            (".userBadgeListItem a.userBadgeListItem__heading[href]",),
        )
        self.assertEqual(SPECS["soundcloud"].control_selectors, ("a[href]",))
        self.assertIn("[role='button']", SPECS["pinterest"].control_selectors)
        self.assertNotIn("main [role='button']", SPECS["pinterest"].control_selectors)
        self.assertEqual(
            count_href_fragments("soundcloud", "https://soundcloud.com/examplecreator", "followers"),
            ("/examplecreator/followers",),
        )

    def test_new_platform_canonical_profile_urls_reject_non_profile_routes(self):
        cases = (
            (
                "pinterest", "https://ca.pinterest.com/person/", "https://ca.pinterest.com/examplecreator/",
                "https://www.pinterest.com/person/",
            ),
            (
                "depop", "https://www.depop.com/person/", "https://www.depop.com/examplecreator/",
                "https://www.depop.com/person/",
            ),
            (
                "poshmark", "https://poshmark.com/closet/person", "https://poshmark.com/closet/examplecreator",
                "https://poshmark.com/closet/person",
            ),
            (
                "disqus", "https://disqus.com/by/person/", "https://disqus.com/by/examplecreator/",
                "https://disqus.com/by/person/",
            ),
        )
        for platform, href, source, canonical in cases:
            with self.subTest(platform=platform):
                self.assertEqual(normalize_profile_link(platform, href, source)[1], canonical)
        self.assertIsNone(normalize_profile_link(
            "depop", "https://www.depop.com/products/person-item/", "https://www.depop.com/examplecreator/"
        ))
        self.assertIsNone(normalize_profile_link(
            "poshmark", "https://poshmark.com/listing/item", "https://poshmark.com/closet/examplecreator"
        ))

    def test_dedicated_facebook_normalizer_rejection_is_authoritative(self):
        source = "https://www.facebook.com/ExampleCreator"
        self.assertIsNone(
            normalize_profile_link("facebook", "https://www.facebook.com/photo.php", source)
        )
        self.assertIsNone(
            normalize_profile_link("facebook", "https://www.facebook.com/friends/", source)
        )

    def test_hudl_accepts_only_canonical_athlete_profiles(self):
        source = "https://www.hudl.com/profile/7654321/Example-Athlete"
        self.assertEqual(
            normalize_profile_link(
                "hudl",
                "https://www.hudl.com/profile/1234567/Another-Athlete",
                source,
            ),
            (
                "Another-Athlete",
                "https://www.hudl.com/profile/1234567/Another-Athlete",
                "1234567",
            ),
        )
        self.assertIsNone(normalize_profile_link("hudl", "https://www.hudl.com/cookies", source))
        self.assertIsNone(normalize_profile_link("hudl", "https://www.hudl.com/profile/not-an-id/person", source))

    def test_threads_uses_current_canonical_host(self):
        self.assertEqual(relations_for("threads"), ("followers", "following"))
        self.assertNotIn("friends", relations_for("threads"))
        value = normalize_profile_link(
            "threads", "https://www.threads.com/@person", "https://www.threads.com/@source"
        )
        self.assertEqual(value, ("person", "https://www.threads.com/@person", None))

    def test_tiktok_canonical_generation_and_source_rejection(self):
        source = "https://www.tiktok.com/@source_account"
        self.assertEqual(tiktok_canonical_url("@person_1", source), "https://www.tiktok.com/@person_1")
        self.assertIsNone(tiktok_canonical_url("source_account", source))
        self.assertIsNone(normalize_profile_link("tiktok", "https://www.tiktok.com/@person/video/1", source))

    def test_tiktok_route_and_duplicates(self):
        source = "https://www.tiktok.com/@source_account"
        self.assertEqual(normalize_profile_link("tiktok", "https://www.tiktok.com/@person", source)[1], "https://www.tiktok.com/@person")
        self.assertEqual(tiktok_canonical_url("Person", source).casefold(), tiktok_canonical_url("person", source).casefold())


if __name__ == "__main__":
    unittest.main()
