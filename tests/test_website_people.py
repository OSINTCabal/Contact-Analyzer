import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contactanalyzer_app.website_people import (
    WebsitePeopleCollector,
    facebook_post_author,
    human_facebook_author_record,
    is_article_page,
    is_facebook_content_url,
    is_instagram_content_url,
    merge_associated_people,
    select_about_url,
    subject_display_name,
    subject_is_visible,
    validate_codex_people,
)


class _FakeTab:
    def __init__(self, pages):
        self.pages = pages
        self.url = "about:blank"
        self.navigations = []

    def navigate(self, url, settle_seconds):
        self.url = url
        self.navigations.append(url)

    def evaluate(self, expression):
        return self.pages[self.url]

    def save_html(self, path):
        path.write_text("<html></html>", encoding="utf-8")

    def screenshot(self, path):
        path.write_bytes(b"png")

    def close(self):
        pass


class _FakeBrowser:
    def __init__(self, tab):
        self.tab = tab

    def new_tab(self, url):
        return self.tab


def _evidence(url, body, *, anchors=None, title="Example", page_signals=None):
    return {
        "current_url": url,
        "title": title,
        "body_text": body,
        "anchors": anchors or [],
        "headings": [],
        "page_signals": page_signals or {},
    }


class WebsitePeopleTests(unittest.TestCase):
    def test_subject_name_strips_case_number_and_splits_camel_case(self):
        self.assertEqual(subject_display_name("000000-SubjectPerson"), "Subject Person")
        self.assertTrue(subject_is_visible("000000-SubjectPerson", "Meet Subject Person today"))

    def test_about_fallback_is_same_site_and_bounded(self):
        anchors = [
            {"text": "About", "href": "https://example.com/about"},
            {"text": "Our Team", "href": "https://elsewhere.example/team"},
            {"text": "Products", "href": "https://example.com/products"},
        ]
        self.assertEqual(select_about_url("https://example.com/start", anchors), "https://example.com/about")

    def test_facebook_content_routes_are_not_profile_routes(self):
        self.assertTrue(is_facebook_content_url("https://www.facebook.com/page/photos/name/123/"))
        self.assertFalse(is_facebook_content_url("https://www.facebook.com/example.author"))

    def test_instagram_content_routes_are_not_profile_relationship_sources(self):
        self.assertTrue(is_instagram_content_url("https://www.instagram.com/p/EXAMPLEPOST1/"))
        self.assertTrue(is_instagram_content_url("https://instagram.com/reel/ABC123/"))
        self.assertFalse(is_instagram_content_url("https://www.instagram.com/exampleartist/"))

    def test_business_facebook_author_is_rejected(self):
        evidence = _evidence(
            "https://www.facebook.com/page/photos/name/123/",
            "Master Barbers welcomes Subject Person to our team",
            anchors=[{"text": "Master Barbers", "href": "https://www.facebook.com/examplebusiness", "context_text": "Master Barbers"}],
        )
        self.assertEqual(facebook_post_author(evidence)[1], "organization")

    def test_person_facebook_author_is_accepted_conservatively(self):
        evidence = _evidence(
            "https://www.facebook.com/person/posts/123/",
            "Example Author worked with Subject Person",
            anchors=[{"text": "Example Author", "href": "https://www.facebook.com/example.author", "context_text": "Example Author"}],
        )
        self.assertEqual(facebook_post_author(evidence)[1], "person")

    def test_human_facebook_author_becomes_one_canonical_contact(self):
        url = "https://www.facebook.com/person/posts/123/"
        author_url = "https://www.facebook.com/example.author"
        evidence = _evidence(
            url,
            "Example Author posted about Subject Person joining the team.",
            anchors=[{
                "text": "Example Author",
                "href": author_url,
                "context_text": "Example Author posted about Subject Person",
            }],
        )
        record = human_facebook_author_record(
            "Subject Person", url, evidence, "Example Author", author_url
        )
        self.assertEqual(record["canonical_profile_url"], author_url)
        self.assertEqual(record["canonical_platform"], "facebook")
        self.assertEqual(record["username"], "example.author")

    def test_explicit_editorial_metadata_is_skipped(self):
        url = "https://news.example/random-article"
        evidence = _evidence(
            url,
            "Subject Person and Associate Person were quoted in this article.",
            page_signals={"og_type": "article", "jsonld_types": ["NewsArticle"]},
        )
        self.assertTrue(is_article_page(evidence))
        tab = _FakeTab({url: evidence})
        collector = WebsitePeopleCollector("http://127.0.0.1:9222")
        with tempfile.TemporaryDirectory() as directory, \
             patch("contactanalyzer_app.website_people.CDPBrowser", return_value=_FakeBrowser(tab)), \
             patch.object(collector, "_run_codex") as codex:
            outcome = collector.collect(
                subject_name="Subject Person",
                source_url=url,
                platform="generic",
                diagnostics_dir=Path(directory),
            )
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.reason, "editorial_article_not_people_source")
        self.assertEqual(outcome.people, [])
        codex.assert_not_called()

    def test_codex_people_validation_keeps_name_only_evidence(self):
        evidence = _evidence(
            "https://example.com/team",
            "Subject Person works with Associate Person, Master Barber.",
        )
        people = validate_codex_people(
            {
                "subject_present": True,
                "people": [{
                    "display_name": "Associate Person",
                    "role": "Master Barber",
                    "organization": "Example",
                    "evidence_quote": "Subject Person works with Associate Person, Master Barber.",
                    "canonical_profile_url": None,
                    "canonical_platform": None,
                }],
            },
            subject_name="000000-SubjectPerson",
            source_url="https://example.com/team",
            evidence=evidence,
            extraction_source="codex_rendered_direct",
        )
        self.assertEqual(len(people), 1)
        self.assertIsNone(people[0]["canonical_profile_url"])

    def test_codex_people_validation_recovers_nearby_full_name_heading(self):
        body = (
            "Subject Person. Taylor Example, Master Barber. "
            "Taylor is excited to continue providing barbering services at Example Shop."
        )
        people = validate_codex_people(
            {
                "subject_present": True,
                "people": [{
                    "display_name": "Taylor Example",
                    "role": "Master Barber",
                    "organization": "Example Shop",
                    "evidence_quote": (
                        "Taylor is excited to continue providing barbering services at Example Shop."
                    ),
                    "canonical_profile_url": None,
                    "canonical_platform": None,
                }],
            },
            subject_name="000000-SubjectPerson",
            source_url="https://example.com/team",
            evidence=_evidence("https://example.com/team", body),
            extraction_source="codex_rendered_direct",
        )
        self.assertEqual([person["display_name"] for person in people], ["Taylor Example"])
        self.assertIn("Taylor Example", people[0]["evidence_text"])

    def test_codex_people_validation_rejects_quote_not_in_rendered_body(self):
        people = validate_codex_people(
            {
                "subject_present": True,
                "people": [{
                    "display_name": "Taylor Example",
                    "evidence_quote": "Taylor Example works here.",
                }],
            },
            subject_name="Subject Person",
            source_url="https://example.com/team",
            evidence=_evidence(
                "https://example.com/team",
                "Subject Person and Taylor Example are named on this page.",
            ),
            extraction_source="codex_rendered_direct",
        )
        self.assertEqual(people, [])

    def test_canonical_profile_requires_visible_person_context_anchor(self):
        profile_url = "https://www.instagram.com/associate.person/"
        base = {
            "subject_present": True,
            "people": [{
                "display_name": "Associate Person",
                "role": "Master Barber",
                "organization": "Example Shop",
                "evidence_quote": "Subject Person works with Associate Person.",
                "canonical_profile_url": profile_url,
                "canonical_platform": "instagram",
            }],
        }
        evidence = _evidence(
            "https://example.com/team",
            "Subject Person works with Associate Person.",
            anchors=[{"href": profile_url, "text": "Associate Person", "context_text": "Associate Person Master Barber"}],
        )
        accepted = validate_codex_people(
            base,
            subject_name="Subject Person",
            source_url="https://example.com/team",
            evidence=evidence,
            extraction_source="codex_rendered_direct",
        )
        self.assertEqual(accepted[0]["canonical_profile_url"], profile_url)

        rejected = validate_codex_people(
            base,
            subject_name="Subject Person",
            source_url="https://example.com/team",
            evidence={**evidence, "anchors": []},
            extraction_source="codex_rendered_direct",
        )
        self.assertIsNone(rejected[0]["canonical_profile_url"])

    def test_direct_page_collection_does_not_navigate_further(self):
        url = "https://example.com/team"
        tab = _FakeTab({url: _evidence(url, "Subject Person works with Associate Person.")})
        collector = WebsitePeopleCollector("http://127.0.0.1:9222")
        codex_result = {
            "subject_present": True,
            "page_assessment": "Team page",
            "people": [{
                "display_name": "Associate Person", "role": None, "organization": "Example",
                "evidence_quote": "Subject Person works with Associate Person.",
                "canonical_profile_url": None, "canonical_platform": None,
            }],
        }
        with tempfile.TemporaryDirectory() as directory, \
             patch("contactanalyzer_app.website_people.CDPBrowser", return_value=_FakeBrowser(tab)), \
             patch.object(collector, "_run_codex", return_value=codex_result) as codex:
            outcome = collector.collect(
                subject_name="000000-SubjectPerson", source_url=url, platform="generic",
                diagnostics_dir=Path(directory),
            )
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(len(outcome.people), 1)
        self.assertEqual(tab.navigations, [url])
        codex.assert_called_once()

    def test_about_fallback_only_when_subject_missing(self):
        direct = "https://example.com/"
        about = "https://example.com/about"
        tab = _FakeTab({
            direct: _evidence(direct, "Example business", anchors=[{"text": "About", "href": about}]),
            about: _evidence(about, "Subject Person works with Associate Person."),
        })
        collector = WebsitePeopleCollector("http://127.0.0.1:9222")
        result = {
            "subject_present": True, "page_assessment": "About team",
            "people": [{
                "display_name": "Associate Person", "role": None, "organization": "Example",
                "evidence_quote": "Subject Person works with Associate Person.",
                "canonical_profile_url": None, "canonical_platform": None,
            }],
        }
        with tempfile.TemporaryDirectory() as directory, \
             patch("contactanalyzer_app.website_people.CDPBrowser", return_value=_FakeBrowser(tab)), \
             patch.object(collector, "_run_codex", return_value=result):
            outcome = collector.collect(
                subject_name="Subject Person", source_url=direct, platform="generic",
                diagnostics_dir=Path(directory),
            )
        self.assertEqual(outcome.analysis_mode, "about_fallback")
        self.assertEqual(tab.navigations, [direct, about])

    def test_no_subject_and_no_about_skips_without_codex(self):
        url = "https://example.com/"
        tab = _FakeTab({url: _evidence(url, "Unrelated business")})
        collector = WebsitePeopleCollector("http://127.0.0.1:9222")
        with tempfile.TemporaryDirectory() as directory, \
             patch("contactanalyzer_app.website_people.CDPBrowser", return_value=_FakeBrowser(tab)), \
             patch.object(collector, "_run_codex") as codex:
            outcome = collector.collect(
                subject_name="Subject Person", source_url=url, platform="generic",
                diagnostics_dir=Path(directory),
            )
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.reason, "subject_not_visible_and_no_about_page")
        codex.assert_not_called()

    def test_business_facebook_post_skips_without_codex(self):
        url = "https://www.facebook.com/page/photos/name/123/"
        tab = _FakeTab({url: _evidence(
            url,
            "Master Barbers welcomes Subject Person",
            anchors=[{"text": "Master Barbers", "href": "https://www.facebook.com/examplebusiness", "context_text": "Master Barbers"}],
        )})
        collector = WebsitePeopleCollector("http://127.0.0.1:9222")
        with tempfile.TemporaryDirectory() as directory, \
             patch("contactanalyzer_app.website_people.CDPBrowser", return_value=_FakeBrowser(tab)), \
             patch.object(collector, "_run_codex") as codex:
            outcome = collector.collect(
                subject_name="Subject Person", source_url=url, platform="facebook",
                diagnostics_dir=Path(directory),
            )
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.reason, "business_authored_facebook_post")
        codex.assert_not_called()

    def test_human_facebook_post_saves_only_author_without_codex(self):
        url = "https://www.facebook.com/person/posts/123/"
        author_url = "https://www.facebook.com/example.author"
        tab = _FakeTab({url: _evidence(
            url,
            "Example Author posted about Subject Person joining the team.",
            anchors=[{
                "text": "Example Author",
                "href": author_url,
                "context_text": "Example Author posted about Subject Person",
            }],
        )})
        collector = WebsitePeopleCollector("http://127.0.0.1:9222")
        with tempfile.TemporaryDirectory() as directory, \
             patch("contactanalyzer_app.website_people.CDPBrowser", return_value=_FakeBrowser(tab)), \
             patch.object(collector, "_run_codex") as codex:
            outcome = collector.collect(
                subject_name="Subject Person",
                source_url=url,
                platform="facebook",
                diagnostics_dir=Path(directory),
            )
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(outcome.reason, "human_facebook_post_author_validated")
        self.assertEqual([person["canonical_profile_url"] for person in outcome.people], [author_url])
        codex.assert_not_called()

    def test_associated_people_merge_deduplicates_names_and_keeps_sources(self):
        rows = [
            {"normalized_name": "example person", "display_name": "Example Person", "role": "Barber", "source_url": "https://one", "source_platform": "generic"},
            {"normalized_name": "example person", "display_name": "Example Person", "role": "Master Barber", "source_url": "https://two", "source_platform": "generic"},
        ]
        merged = merge_associated_people(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["sources"]), 2)
        self.assertEqual(merged[0]["roles"], ["Barber", "Master Barber"])


if __name__ == "__main__":
    unittest.main()
