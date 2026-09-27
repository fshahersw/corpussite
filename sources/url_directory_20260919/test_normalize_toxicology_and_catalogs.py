"""Tests for normalize_toxicology_and_catalogs.py — write first, watch fail, then implement.

Sample rows below are copied verbatim (as literals) from the real input files so the tests exercise the
actual schema, not an invented one:
  - tier1_toxicology_directory.jsonl (returnedfiles)
  - sources_fda_medical.jsonl / sources_legal_corporate.jsonl / sources_science_environment.jsonl /
    sources_state_courts_zero_coverage.jsonl / direct_machine_endpoints.jsonl (SW-BULK gap reconciliation)
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import normalize_toxicology_and_catalogs as norm

# --- real sample rows (literals) -------------------------------------------------------------------

TOX_SHARE_LINK = {
    "url": "http://twitter.com/share?url=https%3A%2F%2Fwww.atsdr.cdc.gov%2Ftoxicological-profiles%2Fabout%2Findex.html&text=Toxicological%20Profiles",
    "title": "", "group": "ATSDR", "content_kind": "html", "discovered_via": ["atsdr_docs:link"], "domain": "twitter.com",
}
TOX_PLAIN_HTML = {
    "url": "http://www.atsdr.cdc.gov/DRO/dro_org.html",
    "title": "", "group": "ATSDR", "content_kind": "html", "discovered_via": ["atsdr_tsp:link"], "domain": "www.atsdr.cdc.gov",
}
TOX_PDF = {
    "url": "http://www.atsdr.cdc.gov/toxfaqs/tfacts112.pdf",
    "title": "", "group": "ATSDR", "content_kind": "pdf", "discovered_via": ["atsdr_tsp:link"], "domain": "www.atsdr.cdc.gov",
}
TOX_SUBSTANCE_TITLE = {
    "url": "https://www.atsdr.cdc.gov/ToxProfiles/SEM-for-Trifluralin.pdf",
    "title": "Trifluralin", "group": "ATSDR", "content_kind": "pdf",
    "discovered_via": ["atsdr_tsp:link"], "domain": "www.atsdr.cdc.gov",
}
TOX_NAV_TITLE = {
    "url": "https://www.atsdr.cdc.gov/about/",
    "title": "About ATSDR", "group": "ATSDR", "content_kind": "html",
    "discovered_via": ["atsdr_tsp:link"], "domain": "www.atsdr.cdc.gov",
}

CATALOG_FDA = {
    "source_id": "fda.drugsfda", "source_family": "fda_drug", "source_product": "Drugs@FDA",
    "publisher": "FDA", "authority_tier": "official_primary", "jurisdictions": ["US"],
    "topical_domains": ["fda_drug"],
    "canonical_landing_url": "https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files",
    "access_endpoints": [], "acquisition_class": "bulk_and_api", "update_cadence": "Weekday mornings",
    "stable_identifiers": ["ApplNo", "ProductNo"],
    "coverage_scope": "Source-product level record; artifact coverage is separately reconciled.",
    "known_exclusions": [], "rights_access": "Publisher-specific terms apply; retain rights and disclaimer evidence.",
    "privacy_class": "public_low_risk", "graph_joins": ["ApplNo", "ProductNo"],
    "coverage_state": "present_supabase_verified", "verification_state": "verified_official_landing",
    "last_verified_at": "2026-08-22T21:30:00Z",
    "evidence_urls": ["https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files"],
    "next_action": "Reconcile with native ZIP and linked documents", "notes": "",
}
CATALOG_STATE_COURT = {
    "source_id": "state_courts.ar", "source_family": "state_courts", "source_product": "Arkansas Judiciary",
    "publisher": "Arkansas Judiciary", "authority_tier": "official_primary", "jurisdictions": ["AR"],
    "topical_domains": ["court_directory", "trial_courts", "appellate_courts", "judges_personnel",
                         "rules_orders", "forms_fees", "opinions", "case_portal"],
    "canonical_landing_url": "https://arcourts.gov",
    "access_endpoints": [{"url": "https://arcourts.gov/directories/district-courts",
                           "endpoint_type": "documentation", "formats": ["HTML"]}],
    "acquisition_class": "document_index", "update_cadence": "Event-driven",
    "stable_identifiers": ["official_court_url", "court_name", "jurisdiction"],
    "coverage_scope": "Official judiciary root and hierarchy seed. No documented statewide public trial-court bulk/API was verified.",
    "known_exclusions": ["No verified statewide trial-court bulk/API"],
    "rights_access": "Public judiciary site; robots, portal terms, fees and access controls must be recorded per child source.",
    "privacy_class": "public_sensitive",
    "graph_joins": ["court", "jurisdiction", "judge", "county", "docket"],
    "coverage_state": "unknown_needs_reconciliation", "verification_state": "verified_official_endpoint",
    "last_verified_at": "2026-08-22T21:50:00Z",
    "evidence_urls": ["https://arcourts.gov", "https://arcourts.gov/directories/district-courts"],
    "next_action": "Reconcile existing URL ledgers, then enumerate per-court child sources before payload acquisition.",
    "notes": "Discovery status: sitemap_verified.",
}
CATALOG_TOXICOLOGY_TOPIC = {
    "source_id": "epa.toxvaldb", "source_family": "toxicology", "source_product": "ToxValDB",
    "publisher": "U.S. Environmental Protection Agency", "authority_tier": "official_primary",
    "jurisdictions": ["US"], "topical_domains": ["toxicology"],
    "canonical_landing_url": "https://github.com/USEPA/toxvaldbmain",
    "access_endpoints": [], "acquisition_class": "bulk_and_api", "update_cadence": "Versioned/irregular",
    "stable_identifiers": ["DTXSID", "CASRN", "study_id"],
    "coverage_scope": "Verified source-product record; acquisition status separately reconciled.",
    "known_exclusions": [], "rights_access": "Publisher-specific terms apply; preserve release, license and disclaimer evidence.",
    "privacy_class": "public_low_risk", "graph_joins": ["DTXSID", "CASRN", "study_id"],
    "coverage_state": "not_observed", "verification_state": "verified_official_landing",
    "last_verified_at": "2026-08-22T21:40:00Z", "evidence_urls": ["https://github.com/USEPA/toxvaldbmain"],
    "next_action": "Acquire versioned XLSX/MySQL artifacts and original-source citations", "notes": "",
}
ENDPOINT_ROW = {
    "source_id": "fda.drugsfda", "url": "https://www.fda.gov/media/89850/download?attachment=",
    "endpoint_type": "bulk_file", "formats": ["ZIP", "TSV"], "update_cadence": "Weekday mornings",
    "verified_at": "2026-08-22T21:55:00Z",
    "evidence_url": "https://www.fda.gov/media/89850/download?attachment=",
    "phase_gate": "directory_record_only_no_payload_download",
    "notes": "Publisher-native Drugs@FDA data file",
}
ENDPOINT_ROW_UNMATCHED = {
    "source_id": "nonexistent.source", "url": "https://example.gov/bulk.csv",
    "endpoint_type": "bulk_file", "formats": ["CSV"], "update_cadence": "Daily",
    "verified_at": "2026-08-22T21:55:00Z", "evidence_url": "https://example.gov/bulk.csv",
    "phase_gate": "directory_record_only_no_payload_download", "notes": "",
}


class ToxicologyRowTests(unittest.TestCase):
    def test_share_link_is_flagged_noise_but_still_emitted(self):
        row = norm.toxicology_row(TOX_SHARE_LINK, "tier1_toxicology_directory.jsonl")
        self.assertEqual(row["layer"], "science_toxicology")
        self.assertEqual(row["host"], "twitter.com")
        self.assertIn("social_share", row["noise"])
        self.assertIsNone(row["title"])  # empty string source title -> None, never invented

    def test_plain_html_row(self):
        row = norm.toxicology_row(TOX_PLAIN_HTML, "tier1_toxicology_directory.jsonl")
        self.assertEqual(row["doc_kind"], "page")
        self.assertEqual(row["org_name"], "ATSDR")
        self.assertIn("ATSDR", row["topics"])

    def test_pdf_content_kind_maps_to_pdf_doc_kind(self):
        row = norm.toxicology_row(TOX_PDF, "tier1_toxicology_directory.jsonl")
        self.assertEqual(row["doc_kind"], "pdf")

    def test_substance_named_title_is_added_to_topics(self):
        row = norm.toxicology_row(TOX_SUBSTANCE_TITLE, "tier1_toxicology_directory.jsonl")
        self.assertEqual(row["title"], "Trifluralin")
        self.assertIn("Trifluralin", row["topics"])

    def test_generic_nav_title_is_not_added_as_a_substance_topic(self):
        row = norm.toxicology_row(TOX_NAV_TITLE, "tier1_toxicology_directory.jsonl")
        self.assertEqual(row["title"], "About ATSDR")
        self.assertNotIn("About ATSDR", row["topics"])


class CatalogRowTests(unittest.TestCase):
    def test_fda_canonical_row(self):
        rows = norm.catalog_rows(CATALOG_FDA, "sources_fda_medical.jsonl")
        urls = {r["url"] for r in rows}
        self.assertEqual(urls, {CATALOG_FDA["canonical_landing_url"]})  # canonical == sole evidence url, deduped
        row = rows[0]
        self.assertEqual(row["title"], "Drugs@FDA")
        self.assertEqual(row["layer"], "federal_agency")
        self.assertEqual(row["org_name"], "FDA")
        self.assertEqual(row["topics"], ["fda_drug"])
        self.assertIsNone(row["state"])
        self.assertEqual(row["jurisdiction_level"], "federal")

    def test_toxicology_topic_catalog_row_gets_science_toxicology_layer(self):
        rows = norm.catalog_rows(CATALOG_TOXICOLOGY_TOPIC, "sources_science_environment.jsonl")
        self.assertEqual(rows[0]["layer"], "science_toxicology")

    def test_state_court_row_emits_canonical_and_endpoint_no_duplicate_evidence(self):
        rows = norm.catalog_rows(CATALOG_STATE_COURT, "sources_state_courts_zero_coverage.jsonl")
        urls = [r["url"] for r in rows]
        # canonical + the one access_endpoint; both also appear in evidence_urls but must not duplicate
        self.assertEqual(sorted(urls), sorted([
            "https://arcourts.gov",
            "https://arcourts.gov/directories/district-courts",
        ]))
        by_url = {r["url"]: r for r in rows}
        self.assertEqual(by_url["https://arcourts.gov"]["layer"], "state_court")
        self.assertEqual(by_url["https://arcourts.gov"]["state"], "AR")
        self.assertEqual(by_url["https://arcourts.gov"]["jurisdiction_level"], "state")
        self.assertIsNone(by_url["https://arcourts.gov"]["parent_url"])
        self.assertEqual(by_url["https://arcourts.gov/directories/district-courts"]["parent_url"],
                          "https://arcourts.gov")

    def test_endpoint_row_joined_to_catalog_inherits_publisher_and_gets_data_doc_kind(self):
        catalog_index = {"fda.drugsfda": CATALOG_FDA}
        row = norm.endpoint_row(ENDPOINT_ROW, "direct_machine_endpoints.jsonl", catalog_index)
        self.assertEqual(row["org_name"], "FDA")
        self.assertEqual(row["title"], "Publisher-native Drugs@FDA data file")
        self.assertEqual(row["doc_kind"], "data")  # no extension on the URL; bulk_file endpoint forces 'data'
        self.assertEqual(row["layer"], "federal_agency")

    def test_endpoint_row_unmatched_source_id_still_emits_with_nulls(self):
        row = norm.endpoint_row(ENDPOINT_ROW_UNMATCHED, "direct_machine_endpoints.jsonl", {})
        self.assertIsNone(row["org_name"])
        self.assertIsNone(row["title"])
        self.assertEqual(row["doc_kind"], "data")  # .csv would be 'data' anyway from the extension


class RejectionTests(unittest.TestCase):
    def test_malformed_url_rejected(self):
        bad = dict(TOX_PLAIN_HTML, url="not-a-url")
        with self.assertRaises(norm.RejectedRow):
            norm.toxicology_row(bad, "tier1_toxicology_directory.jsonl")

    def test_empty_url_rejected(self):
        bad = dict(TOX_PLAIN_HTML, url="")
        with self.assertRaises(norm.RejectedRow):
            norm.toxicology_row(bad, "tier1_toxicology_directory.jsonl")


if __name__ == "__main__":
    unittest.main()
