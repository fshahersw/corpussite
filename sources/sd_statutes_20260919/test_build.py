"""Tests for the SD statutes build: parsing rules first (TDD), then the real build output."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as b  # noqa: E402


def _wrap(paragraphs):
    body = "".join("<p>%s</p>" % p for p in paragraphs)
    return "<html><body>%s</body></html>" % body


class TestParseBody(unittest.TestCase):
    def test_simple_chapter_and_sections(self):
        html_str = _wrap([
            "CHAPTER 1-1",
            "STATE SOVEREIGNTY AND JURISDICTION",
            "",
            "1-1-1&#160;&#160;&#160;&#160;Territorial extent of sovereignty and jurisdiction.",
            "1-1-2&#160;&#160;&#160;&#160;Federal jurisdiction over previously acquired land.",
        ])
        chapters, unresolved = b._parse_body(html_str)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0]["chapter_number"], "1-1")
        self.assertEqual(chapters[0]["chapter_title"], "STATE SOVEREIGNTY AND JURISDICTION")
        self.assertEqual(len(chapters[0]["sections"]), 2)
        self.assertEqual(chapters[0]["sections"][0]["section_id"], "1-1-1")
        self.assertEqual(chapters[0]["sections"][0]["text"], "Territorial extent of sovereignty and jurisdiction.")
        self.assertEqual(unresolved, [])

    def test_never_invents_a_range_section_number(self):
        # "1-2-3 to 1-2-7. Repealed." must stay one entry with the literal range text as its id,
        # never split into five invented section numbers 1-2-3..1-2-7.
        html_str = _wrap([
            "CHAPTER 1-2",
            "COMPACTS",
            "",
            "1-2-3 to 1-2-7. Repealed.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        self.assertEqual(len(chapters[0]["sections"]), 1)
        self.assertEqual(chapters[0]["sections"][0]["section_id"], "1-2-3 to 1-2-7")
        self.assertEqual(chapters[0]["sections"][0]["text"], "Repealed.")

    def test_comma_list_kept_as_one_entry(self):
        html_str = _wrap([
            "CHAPTER 1-4",
            "SOMETHING",
            "",
            "1-4-2.1, 1-4-3. Repealed.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        self.assertEqual(len(chapters[0]["sections"]), 1)
        self.assertEqual(chapters[0]["sections"][0]["section_id"], "1-4-2.1, 1-4-3")

    def test_multi_entry_paragraph_split_on_blank_lines(self):
        html_str = _wrap([
            "CHAPTER 1-2",
            "COMPACTS",
            "",
            "1-2-1\r\n    \r\nCommissioners to negotiate boundaries authorized.\r\n\r\n\r\n\r\n\r\n"
            "1-2-2\r\n    \r\nMeeting with commissioners of other participatory state.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        secs = chapters[0]["sections"]
        self.assertEqual(len(secs), 2)
        self.assertEqual(secs[0]["section_id"], "1-2-1")
        self.assertEqual(secs[0]["text"], "Commissioners to negotiate boundaries authorized.")
        self.assertEqual(secs[1]["section_id"], "1-2-2")

    def test_unrecognised_line_kept_verbatim_not_dropped(self):
        html_str = _wrap([
            "CHAPTER 1-9",
            "MISC",
            "",
            "This line has no leading statute id at all.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        self.assertEqual(chapters[0]["sections"], [])
        self.assertEqual(chapters[0]["unparsed"], ["This line has no leading statute id at all."])

    def test_empty_html_yields_no_chapters(self):
        chapters, unresolved = b._parse_body("")
        self.assertEqual(chapters, [])
        self.assertEqual(unresolved, [])

    def test_preamble_toc_before_first_chapter_header_is_skipped_not_double_counted(self):
        html_str = _wrap([
            "TITLE 1",
            "STATE AFFAIRS",
            "Chapter",
            "01    State Sovereignty",
            "CHAPTER 1-1",
            "STATE SOVEREIGNTY",
            "",
            "1-1-1    Territorial extent.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(len(chapters[0]["sections"]), 1)

    def test_bare_ordinal_is_not_a_section_id(self):
        # Numbered paragraphs of embedded pleading/pretrial form text ("1.  Defendant on or about...")
        # must never become a fake SDCL section; a bare ordinal has no chapter prefix.
        html_str = _wrap([
            "CHAPTER 15-6",
            "RULES OF CIVIL PROCEDURE",
            "",
            "1.  Defendant on or about June 1, 1955, executed and delivered to plaintiff a promissory note.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        self.assertEqual(chapters[0]["sections"], [])
        self.assertEqual(len(chapters[0]["unparsed"]), 1)
        self.assertIn("Defendant on or about", chapters[0]["unparsed"][0])

    def test_embedded_entry_without_blank_line_gap_is_split_not_swallowed(self):
        # A trailing catchline run together with the previous entry ("... . 15-3-19 Time allowed...")
        # must become its own section, not text glued onto 15-3-18.
        html_str = _wrap([
            "CHAPTER 15-3",
            "LIMITATION OF ACTIONS",
            "",
            "15-3-18  Tolling of statute during disability--Extension after removal of disability "
            ". 15-3-19  Time allowed for assertion of irregularities.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        ids = [s["section_id"] for s in chapters[0]["sections"]]
        self.assertIn("15-3-18", ids)
        self.assertIn("15-3-19", ids)
        by_id = {s["section_id"]: s["text"] for s in chapters[0]["sections"]}
        self.assertNotIn("15-3-19", by_id["15-3-18"])
        self.assertTrue(by_id["15-3-18"].endswith("."))

    def test_embedded_entry_with_no_space_before_period_is_split(self):
        # "...continue. 31-18-5 Liability..." -- no space before the period, still a real boundary.
        html_str = _wrap([
            "CHAPTER 31-18",
            "HIGHWAY RIGHTS",
            "",
            "31-18-4  Relicted lands--Highway rights continue. 31-18-5  Liability for unimproved section line.",
        ])
        chapters, _unresolved = b._parse_body(html_str)
        ids = [s["section_id"] for s in chapters[0]["sections"]]
        self.assertIn("31-18-4", ids)
        self.assertIn("31-18-5", ids)


class TestRealBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not b.INPUT_DIR.is_dir():
            raise unittest.SkipTest("input directory not present in this environment")
        # Build into a scratch directory so running the test suite never rewrites the delivered
        # titles.jsonl / unresolved.jsonl / validation.json (they would otherwise change validated_at
        # and, given a different unittest run order, the "artifacts unchanged" claim could never hold).
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="sdstat_build_test_")
        tmp = Path(cls._tmpdir.name)
        cls._orig = (b.OUT_DIR, b.TITLES_FILE, b.UNRESOLVED_FILE, b.VALIDATION_FILE)
        b.OUT_DIR = tmp
        b.TITLES_FILE = tmp / "titles.jsonl"
        b.UNRESOLVED_FILE = tmp / "unresolved.jsonl"
        b.VALIDATION_FILE = tmp / "validation.json"
        rc = b.build()
        assert rc == 0

    @classmethod
    def tearDownClass(cls):
        b.OUT_DIR, b.TITLES_FILE, b.UNRESOLVED_FILE, b.VALIDATION_FILE = cls._orig
        cls._tmpdir.cleanup()

    def test_validation_passed(self):
        gate = json.loads(b.VALIDATION_FILE.read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["ready"])
        self.assertEqual(gate["counts"]["titles"], 71)
        self.assertEqual(gate["counts"]["titles_with_html"], 71)

    def test_titles_file_matches_hash(self):
        gate = json.loads(b.VALIDATION_FILE.read_text(encoding="utf-8"))
        entry = next(d for d in gate["data_files"] if d["path"] == "titles.jsonl")
        data = b.TITLES_FILE.read_bytes()
        self.assertEqual(b._sha256(data), entry["sha256"])
        self.assertEqual(entry["rows"], 71)

    def test_every_title_has_a_stable_unique_id(self):
        ids = set()
        for line in b.TITLES_FILE.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            self.assertTrue(row["id"].startswith("sdcl:"))
            self.assertNotIn(row["id"], ids)
            ids.add(row["id"])
            self.assertIsInstance(row["title_number"], str)
        self.assertEqual(len(ids), 71)

    def test_no_section_id_is_a_pure_guess(self):
        # every section_id must be traceable to characters that appeared before the split point;
        # spot-check title 1 (StatuteId 2030342) has a real chapter 1-1 with real sections
        found = False
        for line in b.TITLES_FILE.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["title_number"] == "1":
                found = True
                chapter_1_1 = next((c for c in row["chapters"] if c["chapter_number"] == "1-1"), None)
                self.assertIsNotNone(chapter_1_1)
                self.assertGreater(len(chapter_1_1["sections"]), 5)
                first = chapter_1_1["sections"][0]
                self.assertEqual(first["section_id"], "1-1-1")
        self.assertTrue(found)

    def test_public_url_derived_from_native_statute_field_not_og_url(self):
        # public_url must be a deterministic 1:1 function of the native Statute value, never the
        # Word-export's og:url meta tag (measured wrong on 28 of 71 titles, null on 7 more).
        seen_og_mismatch = False
        for line in b.TITLES_FILE.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            self.assertEqual(row["public_url"], "https://sdlegislature.gov/Statutes/%s" % row["title_number"])
            if row.get("og_url") and row["og_url"] != row["public_url"]:
                seen_og_mismatch = True
        self.assertTrue(seen_og_mismatch, "expected at least one title whose og:url disagrees with the real page")

    def test_all_titles_type_title_check_is_measured(self):
        gate = json.loads(b.VALIDATION_FILE.read_text(encoding="utf-8"))
        check = next(c for c in gate["checks"] if c["name"] == "all_titles_type_title")
        self.assertTrue(check["passed"])

    def test_every_row_has_a_temporal_block(self):
        for line in b.TITLES_FILE.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            temporal = row["temporal"]
            for key in ("captured_at", "source_as_of", "published_at", "effective_from", "effective_to"):
                self.assertIn(key, temporal)
                self.assertIn(key + "_basis", temporal)
            self.assertIsNotNone(temporal["captured_at"])
            self.assertIsNotNone(temporal["captured_at_basis"])

    def test_qualification_states_build_date(self):
        gate = json.loads(b.VALIDATION_FILE.read_text(encoding="utf-8"))
        self.assertIn("2026-09-19", gate["qualification"])


if __name__ == "__main__":
    unittest.main()
