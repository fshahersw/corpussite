import json
import unittest
from pathlib import Path

from build import (
    build_records, classify_document_type, cross_check_catalog_report,
    is_natural_person_vs_party_caption, load_catalog_report,
    load_crosswalk_by_master_docket_id, measure_natural_person_captions,
    suppress_natural_person_caption, zero_pad_mdl,
    DOCUMENTS_JSON, VALIDATION_OUT,
)

HERE = Path(__file__).resolve().parent


class ClassifierTests(unittest.TestCase):
    def test_settlement(self):
        self.assertEqual(classify_document_type("Order granting preliminary approval of class settlement", None), "settlement")

    def test_bellwether(self):
        self.assertEqual(classify_document_type("MDL ORDER - selection of bellwether cases", None), "bellwether")

    def test_leadership_appointment(self):
        self.assertEqual(
            classify_document_type("Order appointing plaintiffs' lead counsel and liaison counsel", None),
            "leadership_appointment")

    def test_leadership_requires_appoint_verb(self):
        # mentions "lead counsel" without "appoint" -> should not be misclassified as leadership
        self.assertEqual(classify_document_type("Letter from lead counsel regarding scheduling", None), "other")

    def test_daubert_expert(self):
        self.assertEqual(classify_document_type("Motion to exclude the opinion of plaintiffs' expert under Daubert", None), "daubert_expert")

    def test_dispositive(self):
        self.assertEqual(classify_document_type("Order granting defendants' motion for summary judgment", None), "dispositive")
        self.assertEqual(classify_document_type("Motion to dismiss the master complaint", None), "dispositive")

    def test_pretrial_order(self):
        self.assertEqual(classify_document_type("Pretrial Order No. 12 regarding depositions", None), "pretrial_order")

    def test_case_management_order(self):
        self.assertEqual(classify_document_type("MDL Initial Case Management Order", None), "case_management_order")
        self.assertEqual(classify_document_type("Letter regarding CMO 3 protective order status", None), "case_management_order")

    def test_other_fallback(self):
        self.assertEqual(classify_document_type("Notice of appearance", None), "other")

    def test_empty_text_is_other_never_raises(self):
        self.assertEqual(classify_document_type(None, None), "other")
        self.assertEqual(classify_document_type("", ""), "other")

    def test_settlement_precedence_over_case_management_order(self):
        # a text with both phrases should classify as settlement (higher-priority category)
        text = "Case management order regarding the settlement conference schedule"
        self.assertEqual(classify_document_type(text, None), "settlement")


class NaturalPersonCaptionTests(unittest.TestCase):
    """Party-name rule: individual member case captions must never be published as a title."""

    def test_person_v_corporation_is_suppressed(self):
        kept, suppressed = suppress_natural_person_caption("STEVEN GOODSTEIN v. ASTRAZENECA PHARMACEUTICALS LP")
        self.assertIsNone(kept)
        self.assertTrue(suppressed)

    def test_in_re_caption_is_kept(self):
        kept, suppressed = suppress_natural_person_caption("In re: Proton Pump Inhibitor Products Liability Litigation")
        self.assertEqual(kept, "In re: Proton Pump Inhibitor Products Liability Litigation")
        self.assertFalse(suppressed)

    def test_none_case_name_is_not_flagged_suppressed(self):
        kept, suppressed = suppress_natural_person_caption(None)
        self.assertIsNone(kept)
        self.assertFalse(suppressed)

    def test_is_natural_person_vs_party_caption_detects_person_shape(self):
        self.assertTrue(is_natural_person_vs_party_caption("STEVEN GOODSTEIN v. ASTRAZENECA PHARMACEUTICALS LP"))

    def test_is_natural_person_vs_party_caption_ignores_entity_left_side(self):
        self.assertFalse(is_natural_person_vs_party_caption("UNITED STATES v. SMITH"))

    def test_is_natural_person_vs_party_caption_ignores_in_re(self):
        self.assertFalse(is_natural_person_vs_party_caption("In re: Example Products Liability Litigation"))

    def test_measure_natural_person_captions_measures_published_records_only(self):
        records = [
            {"case_name": None},  # suppressed by build_records already
            {"case_name": "In re: Example"},
        ]
        self.assertEqual(measure_natural_person_captions(records), [])

    def test_measure_natural_person_captions_flags_a_regression(self):
        records = [{"case_name": "STEVEN GOODSTEIN v. ASTRAZENECA PHARMACEUTICALS LP"}]
        self.assertEqual(
            measure_natural_person_captions(records),
            ["STEVEN GOODSTEIN v. ASTRAZENECA PHARMACEUTICALS LP"])

    def test_build_records_never_publishes_a_person_caption(self):
        crosswalk = {1: {"mdl_number": 2789, "mdl_status": "pending", "mdl_title": "IN RE PPI", "cl_court_id": "njd"}}
        documents = [
            {"doc_uid": "1-1-1", "master_docket_id": 1, "entry_number": 1, "document_number": "1",
             "entry_description": "Complaint", "document_description": "", "mdl_number": "02789",
             "case_name": "STEVEN GOODSTEIN v. ASTRAZENECA PHARMACEUTICALS LP", "docket_number": "2:17-md-02789",
             "court": "njd"},
        ]
        records, _unresolved = build_records(documents, crosswalk)
        self.assertEqual(measure_natural_person_captions(records), [])
        self.assertIsNone(records[0]["case_name"])
        self.assertTrue(records[0]["caption_suppressed_natural_person"])


class ZeroPadTests(unittest.TestCase):
    def test_zero_padded_string(self):
        self.assertEqual(zero_pad_mdl("02789"), 2789)

    def test_none(self):
        self.assertIsNone(zero_pad_mdl(None))

    def test_garbage_never_raises(self):
        self.assertIsNone(zero_pad_mdl("not-a-number"))


class CompositeIdAndJoinTests(unittest.TestCase):
    """Synthetic fixture reproducing the doc_uid-collision shape from the real corpus:
    two distinct docket entries with entry_number=None and document_number="" that would collapse
    under the native doc_uid ("<master>-None-") but must remain two separate records."""

    def setUp(self):
        self.crosswalk = {1001: {"mdl_number": 2789, "mdl_status": "pending", "mdl_title": "IN RE X", "cl_court_id": "njd"}}
        self.documents = [
            {"doc_uid": "1001-None-", "master_docket_id": 1001, "entry_number": None, "document_number": "",
             "entry_description": "Minute entry one", "document_description": "", "mdl_number": "02789"},
            {"doc_uid": "1001-None-", "master_docket_id": 1001, "entry_number": None, "document_number": "",
             "entry_description": "Minute entry two", "document_description": "", "mdl_number": "02789"},
            {"doc_uid": "9999-1-1", "master_docket_id": 9999, "entry_number": 1, "document_number": "1",
             "entry_description": "Complaint", "document_description": "Complaint", "mdl_number": None},
        ]

    def test_ids_unique_despite_doc_uid_collision(self):
        records, _unresolved = build_records(self.documents, self.crosswalk)
        ids = [r["id"] for r in records]
        self.assertEqual(len(ids), len(set(ids)))

    def test_join_resolves_via_master_docket_id(self):
        records, unresolved = build_records(self.documents, self.crosswalk)
        resolved = [r for r in records if r["master_docket_id"] == 1001]
        self.assertTrue(all(r["resolved"] for r in resolved))
        self.assertEqual(resolved[0]["mdl_number"], 2789)
        unresolved_ids = {r["master_docket_id"] for r in unresolved}
        self.assertIn(9999, unresolved_ids)

    def test_unresolved_rows_are_not_dropped(self):
        records, _unresolved = build_records(self.documents, self.crosswalk)
        self.assertEqual(len(records), len(self.documents))

    def test_exact_duplicate_detection(self):
        docs = self.documents + [dict(self.documents[0])]  # exact repeat of row 0
        records, _unresolved = build_records(docs, self.crosswalk)
        dup_flags = [r["is_exact_duplicate"] for r in records]
        self.assertEqual(dup_flags, [False, False, False, True])
        self.assertEqual(records[3]["exact_duplicate_of"], records[0]["id"])


class CatalogReportCrossCheckTests(unittest.TestCase):
    def test_mismatch_detected(self):
        records = [
            {"master_docket_id": 1}, {"master_docket_id": 1}, {"master_docket_id": 2},
        ]
        report = {1: {"document_count": "2", "docket_number": "1:00-md-1"},
                  2: {"document_count": "5", "docket_number": "1:00-md-2"}}
        checks = cross_check_catalog_report(records, report)
        by_master = {c["master_docket_id"]: c for c in checks}
        self.assertTrue(by_master[1]["match"])
        self.assertFalse(by_master[2]["match"])
        self.assertEqual(by_master[2]["measured_document_count"], 1)


class RealDataSmokeTest(unittest.TestCase):
    """Runs only if the real corpus is present (it is, per the build contract's read-only inputs)."""

    def test_real_join_recovers_hair_relaxer(self):
        if not DOCUMENTS_JSON.exists():
            self.skipTest("SW-BULK input not present in this environment")
        crosswalk = load_crosswalk_by_master_docket_id()
        self.assertIn(66801859, crosswalk, "Hair Relaxer master should resolve via the crosswalk")
        self.assertEqual(crosswalk[66801859]["mdl_number"], 3060)

    def test_validation_json_built_and_passed(self):
        if not VALIDATION_OUT.exists():
            self.skipTest("build.py has not been run yet in this environment")
        gate = json.loads(VALIDATION_OUT.read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["ready"])
        self.assertEqual(gate["counts"]["total_rows"], 35862)
        self.assertEqual(gate["counts"]["catalog_report_mismatches"], 0)
        checks_by_name = {c["name"]: c for c in gate["checks"]}
        self.assertIn("no_natural_person_plaintiff_caption_published", checks_by_name)
        self.assertTrue(checks_by_name["no_natural_person_plaintiff_caption_published"]["passed"])
        self.assertEqual(
            checks_by_name["no_natural_person_plaintiff_caption_published"]["detail"]["offending_captions"], 0)


if __name__ == "__main__":
    unittest.main()
