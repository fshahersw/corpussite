import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # never write __pycache__ into another slice's live folder

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402


GAZETTEER = {
    "AL": [("Autauga County", "01001"), ("Baldwin County", "01003")],
    "NY": [("New York County", "36061"), ("York County", "99999")],  # fake collision fixture
    "LA": [("Orleans Parish", "22071")],
    "NC": [("Alamance County", "37001")],
}


class TestCountyMatch(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(
            build.match_county("AL", "Autauga County Circuit Clerk", GAZETTEER),
            ("Autauga County", "01001"),
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(build.match_county("AL", "State Judiciary Home", GAZETTEER))

    def test_unknown_jurisdiction_returns_none(self):
        self.assertIsNone(build.match_county("ZZ", "Autauga County", GAZETTEER))

    def test_longest_match_wins_over_substring(self):
        # "New York County" must win over the shorter "York County" that is
        # textually contained inside it.
        self.assertEqual(
            build.match_county("NY", "New York County Supreme Court", GAZETTEER),
            ("New York County", "36061"),
        )

    def test_parish_suffix_matches(self):
        self.assertEqual(
            build.match_county("LA", "Orleans Parish Clerk of Court", GAZETTEER),
            ("Orleans Parish", "22071"),
        )

    def test_word_boundary_not_substring(self):
        # "Autaugaville" must not match "Autauga County".
        self.assertIsNone(build.match_county("AL", "Autaugaville Town Hall", GAZETTEER))


class TestHostAndTopicFilters(unittest.TestCase):
    def test_official_host_accepts_court_terms(self):
        self.assertTrue(build.is_official_host("arcourts.gov"))
        self.assertTrue(build.is_official_host("occourts.org"))
        self.assertTrue(build.is_official_host("crawfordcountyar.gov"))
        self.assertTrue(build.is_official_host("someclerk.example.us"))

    def test_official_host_rejects_unrelated(self):
        self.assertFalse(build.is_official_host("example.com"))
        self.assertFalse(build.is_official_host("news.state.xx.us"))

    def test_topic_requires_collector_vocabulary(self):
        self.assertTrue(build.passes_topic("Local Rules of Court", "https://x.gov/rules"))
        self.assertTrue(build.passes_topic("", "https://x.gov/self-help/forms"))
        self.assertFalse(build.passes_topic("Photo Gallery", "https://x.gov/gallery"))

    def test_exclude_matches_collector_exclude(self):
        self.assertTrue(build.fails_exclude("https://x.gov/news/2026/story"))
        self.assertTrue(build.fails_exclude("https://x.gov/careers/apply"))
        self.assertFalse(build.fails_exclude("https://x.gov/clerk/forms"))


class TestEvaluateCandidate(unittest.TestCase):
    def setUp(self):
        self.gaz = GAZETTEER
        self.queue_urls = {"https://autaugacountyclerk.gov/clerk/already-queued"}
        self.published_urls = {"https://autaugacountyclerk.gov/clerk/already-published"}

    def _cand(self, **kw):
        base = {
            "title": "Autauga County Clerk Forms",
            "url": "https://autaugacountyclerk.gov/forms",
            "jurisdiction": "AL",
            "facet": "forms",
            "authority_tier": "official_primary",
            "retention_class": "core_directory",
            "source_id": "state.al.000001",
            "discovered_from": "https://autaugacountyclerk.gov",
            "origin": "state_map",
        }
        base.update(kw)
        return base

    def test_accepts_good_county_row(self):
        bucket, payload = build.evaluate_candidate(self._cand(), self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "county")
        self.assertEqual(payload["geoid"], "01001")

    def test_rejects_quarantined(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(retention_class="quarantine"), self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "rejected")
        self.assertEqual(payload["reason"], "quarantined")

    def test_rejects_non_official_tier(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(authority_tier="official_link_derived"), self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "rejected")
        self.assertEqual(payload["reason"], "not_official_primary")

    def test_rejects_bad_host(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(url="https://example.com/forms"), self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "rejected")
        self.assertEqual(payload["reason"], "host_not_official_court_clerk_county")

    def test_rejects_excluded_topic_path(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(url="https://autaugacountyclerk.gov/news/story"), self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "rejected")
        self.assertEqual(payload["reason"], "excluded_topic_path")

    def test_rejects_no_topic_match(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(title="Photo Gallery", url="https://autaugacountyclerk.gov/gallery"),
            self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "rejected")
        self.assertEqual(payload["reason"], "no_collector_topic_match")

    def test_rejects_already_in_queue(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(url="https://autaugacountyclerk.gov/clerk/already-queued", title="Clerk Forms"),
            self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "rejected")
        self.assertEqual(payload["reason"], "already_in_queue")

    def test_rejects_already_published(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(url="https://autaugacountyclerk.gov/clerk/already-published", title="Clerk Forms"),
            self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "rejected")
        self.assertEqual(payload["reason"], "already_in_published_supplement")

    def test_falls_back_to_statewide_when_no_county_match(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(title="State Judiciary Rules of Court", url="https://arcourts.gov/rules"),
            self.gaz, self.queue_urls, self.published_urls)
        self.assertEqual(bucket, "statewide")


class TestPrioritisation(unittest.TestCase):
    """Priority must be inverted: a county already covered gets 2, a county with
    zero saved local resources gets 1 (the brief: 'Prioritise counties that
    currently have zero saved local resources')."""

    def test_fips_with_downloaded_queue_row_gets_priority_2(self):
        covered = {"31177"}  # e.g. a fips with a status='downloaded' queue row
        self.assertEqual(build.priority_for_county("31177", covered), 2)

    def test_fips_with_no_saved_resource_gets_priority_1(self):
        covered = {"31177"}
        self.assertEqual(build.priority_for_county("47089", covered), 1)


class TestUrlPathCountyMatch(unittest.TestCase):
    """When the row's own title is empty (64% of accepted frontier rows are),
    the row's own canonical_url path must still be checked for an exact county
    name before giving up and calling it statewide."""

    def _cand(self, **kw):
        base = {
            "title": "",
            "url": "https://www.nccourts.gov/locations/alamance-county/alamance-county-local-rules-and-forms",
            "jurisdiction": "NC",
            "facet": "rules",
            "authority_tier": "official_primary",
            "retention_class": "core_directory",
            "source_id": "state.nc.000001",
            "discovered_from": "https://www.nccourts.gov",
            "origin": "state_map",
        }
        base.update(kw)
        return base

    def test_empty_title_falls_back_to_url_path_match(self):
        bucket, payload = build.evaluate_candidate(self._cand(), GAZETTEER, set(), set())
        self.assertEqual(bucket, "county")
        self.assertEqual(payload["geoid"], "37001")
        self.assertEqual(payload["match_status"], "url_path_county_name_match")

    def test_no_title_and_no_url_match_is_statewide(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(url="https://www.nccourts.gov/rules-and-forms"), GAZETTEER, set(), set())
        self.assertEqual(bucket, "statewide")
        self.assertEqual(payload["match_status"], "no_title_and_no_url_match")

    def test_title_match_preferred_over_url_match(self):
        bucket, payload = build.evaluate_candidate(
            self._cand(title="Alamance County Circuit Clerk", url="https://www.nccourts.gov/rules"),
            GAZETTEER, set(), set())
        self.assertEqual(bucket, "county")
        self.assertEqual(payload["match_status"], "title_county_name_match")

    def test_county_seed_records_url_path_provenance(self):
        cand = self._cand()
        seed = build.build_county_seed(cand, "Alamance County", "37001", priority=1,
                                        match_status="url_path_county_name_match")
        self.assertEqual(seed["association"]["status"], "url_path_county_name_match")
        self.assertIn("canonical", seed["association"]["basis"].lower())


class TestLicenseAndQualification(unittest.TestCase):
    """This slice's inputs are derived from SW-BULK; the private-firm-work-product
    licence and export restriction must be published, not silently overridden."""

    def test_validation_carries_sw_bulk_licence(self):
        validation = build.build_validation({})
        self.assertEqual(validation["license_ref"], "sw_bulk_private_firm_work_product")
        self.assertIs(validation["export_allowed"], False)
        self.assertIn("private firm dataset", validation["qualification"])
        self.assertIn("not for redistribution", validation["qualification"])

    def test_validation_reports_empty_title_rate(self):
        measured = {"rows_with_empty_title": 6361, "seeds_county": 80, "seeds_statewide": 9844}
        validation = build.build_validation(measured)
        self.assertEqual(validation["counts"]["rows_with_empty_title"], 6361)


class TestSeedShape(unittest.TestCase):
    """The seed dict must satisfy collect.py's seed_ok() gate."""

    def test_county_seed_passes_collector_seed_ok(self):
        collector_path = build.ROOT / "sources/county_litigation_firecrawl_20260919"
        sys.path.insert(0, str(collector_path.parent))
        sys.path.insert(0, str(build.ROOT / "pipeline"))
        sys.path.insert(0, str(build.ROOT / "scripts"))
        sys.path.insert(0, str(collector_path))
        assert sys.dont_write_bytecode is True  # never write __pycache__ into the collector's live folder
        import collect  # noqa: E402

        cand = {
            "title": "Autauga County Clerk Forms",
            "url": "https://autaugacountyclerk.gov/forms",
            "jurisdiction": "AL",
            "facet": "forms",
            "discovered_from": "https://autaugacountyclerk.gov",
            "origin": "state_map",
            "source_id": "state.al.000001",
        }
        seed = build.build_county_seed(cand, "Autauga County", "01001", priority=1)
        url = collect.seed_ok(seed)
        self.assertEqual(url, "https://autaugacountyclerk.gov/forms")

    def test_statewide_seed_has_no_fips(self):
        cand = {
            "title": "Arkansas Judiciary",
            "url": "https://arcourts.gov/rules",
            "jurisdiction": "AR",
            "facet": "rules",
            "discovered_from": "https://arcourts.gov",
            "origin": "state_map",
            "source_id": "state.ar.000005",
        }
        seed = build.build_statewide_seed(cand, priority=3)
        self.assertIsNone(seed["county_fips"])
        self.assertEqual(seed["county_geoids"], [])


if __name__ == "__main__":
    unittest.main()
