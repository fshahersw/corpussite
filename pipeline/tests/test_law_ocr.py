"""Official-law OCR input fixtures reject unsuccessful or out-of-scope sources."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import fitz
from pipeline import prepare_law_ocr as laws
from pipeline import prepare_court_ocr as prep


class LawOcrTests(unittest.TestCase):
    def test_manifest_selection_preserves_originals_and_checks_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("metadata", "documents", "text", "model"):
                (root / name).mkdir()
            pdf = root / "documents/rules.pdf"
            document = fitz.open()
            document.new_page().insert_text((50, 50), "Repealed.")
            document.save(pdf)
            document.close()
            raw_before = pdf.read_bytes()
            good = {"verification_status": "retrieved", "format": "pdf", "http_status": 200,
                    "evidence_path": "documents/rules.pdf", "source_url": "https://example.gov/rules.pdf",
                    "sha256": prep.digest(pdf), "bytes": pdf.stat().st_size}
            (root / "metadata/good.json").write_text(json.dumps(good), encoding="utf-8")
            (root / "metadata/failure.json").write_text(json.dumps({**good, "verification_status": "retrieval_error"}), encoding="utf-8")
            (root / "metadata/outside.json").write_text(json.dumps({**good, "evidence_path": "../outside.pdf"}), encoding="utf-8")
            metadata_before = {p.name: p.read_bytes() for p in (root / "metadata").glob("*.json")}
            resources, errors = laws.snapshot(root)
            self.assertEqual(len(resources), 1)
            self.assertEqual(len(errors), 1)
            model = root / "model/eng.traineddata"
            model.write_bytes(b"fixture only")
            (root / "model/eng.provenance.json").write_text(json.dumps({"sha256": prep.digest(model)}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                report = prep.prepare(root, root / "ocr", root / "model", resource_snapshot=resources,
                                      snapshot_kind="official_law_success_metadata")
            self.assertEqual(report["documents"], 1)
            self.assertIsNone(report["database_read_only"])
            self.assertFalse((root / "corpus.sqlite3").exists())
            self.assertEqual(pdf.read_bytes(), raw_before)
            self.assertEqual(metadata_before, {p.name: p.read_bytes() for p in (root / "metadata").glob("*.json")})
            pdf.write_bytes(raw_before + b"tamper")
            with contextlib.redirect_stdout(io.StringIO()):
                changed = prep.prepare(root, root / "changed-ocr", root / "model", resource_snapshot=resources,
                                       snapshot_kind="official_law_success_metadata")
            self.assertEqual(changed["documents"], 0)
            self.assertEqual(changed["preparation_errors"], 1)


if __name__ == "__main__":
    unittest.main()
