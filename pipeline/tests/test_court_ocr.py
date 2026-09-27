"""Offline fixtures for selective OCR, integrity checks, and cache reuse."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

import fitz
from pipeline import prepare_court_ocr as prep


class CourtOcrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="court-ocr-test-")
        self.base = Path(self.temp.name)
        self.root = self.base / "corpus"
        self.root.mkdir()
        self.output = self.root / "ocr"
        self.model = self.base / "model"
        self.model.mkdir()
        (self.model / "eng.traineddata").write_bytes(b"local model fixture; never loaded by OCR")
        self.model_sha = prep.digest(self.model / "eng.traineddata")
        (self.model / "eng.provenance.json").write_text(json.dumps({"sha256": self.model_sha, "source_url": "local-fixture"}), encoding="utf-8")
        with sqlite3.connect(self.root / "corpus.sqlite3") as db:
            db.execute("CREATE TABLE resources(id INTEGER,url TEXT,raw_path TEXT,text_path TEXT,metadata_path TEXT,sha256 TEXT,byte_count INTEGER,last_fetch_id TEXT,extraction_status TEXT,status TEXT,raw_complete INTEGER)")
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    def pdf(self):
        path = self.root / "fixture.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_textbox(fitz.Rect(30, 30, 550, 750), "Readable embedded court rules. " * 30)
        document.new_page()  # True empty page.
        raster_source = fitz.open()
        scanned = raster_source.new_page()
        scanned.insert_textbox(fitz.Rect(30, 30, 550, 750), "This page contains only a raster scan of court text. " * 15)
        raster = scanned.get_pixmap(dpi=90).tobytes("png")
        image_page = document.new_page()
        image_page.insert_image(image_page.rect, stream=raster)
        blank_raster = fitz.open()
        blank = blank_raster.new_page()
        blank_image = document.new_page()
        blank_image.insert_image(blank_image.rect, stream=blank.get_pixmap(dpi=36).tobytes("png"))
        sparse = document.new_page()
        sparse.insert_text((100, 100), "Signed")
        document.save(path)
        document.close()
        raster_source.close()
        blank_raster.close()
        sha = prep.digest(path)
        with sqlite3.connect(self.root / "corpus.sqlite3") as db:
            for number in (1, 2):
                db.execute("INSERT INTO resources VALUES(?,?,?,?,?,?,?,?,?,?,?)", (number, f"https://example.gov/{number}.pdf", "fixture.pdf", None, None, sha, path.stat().st_size, "fixture", "extracted", "downloaded", 1))
        db.close()
        return path, sha

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return prep.prepare(self.root, self.output, self.model)

    def test_selective_page_queue_and_same_hash_resume(self):
        path, sha = self.pdf()
        original = path.read_bytes()
        first = self.prepare()
        self.assertEqual((first["documents"], first["pdf_pages_in_documents"], first["pages"]), (1, 5, 1))
        self.assertEqual(first["page_screening_status_counts"]["blank_page_skipped"], 2)
        tasks = prep.read_lines(self.output / "pages.jsonl")
        self.assertEqual(tasks[0]["page_number"], 3)
        before = Path(tasks[0]["image_path"]).stat().st_mtime_ns
        second = self.prepare()
        self.assertEqual(second["rendered_pages_this_run"], 0)
        self.assertEqual(second["newly_screened_documents_this_run"], 0)
        self.assertEqual(Path(tasks[0]["image_path"]).stat().st_mtime_ns, before)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(prep.digest(path), sha)
        doc = prep.read_lines(self.output / "documents.jsonl")[0]
        self.assertEqual(len(doc["source_urls"]), 2)
        self.assertEqual(doc["manual_review_page_numbers"], [5])

    def test_source_hash_mismatch_is_recorded_without_rendering(self):
        path, _ = self.pdf()
        with path.open("ab") as handle:
            handle.write(b"changed source")
        result = self.prepare()
        self.assertEqual(result["documents"], 0)
        self.assertEqual(result["pages"], 0)
        self.assertEqual(result["preparation_errors"], 2)
        self.assertIn("SHA-256 mismatch", prep.read_lines(self.output / "preparation_errors.jsonl")[0]["error"])

    def test_worker_selected_page_completion_and_success_cache(self):
        self.pdf()
        self.prepare()
        task = prep.read_lines(self.output / "pages.jsonl")[0]
        text_path = self.output / task["source_id"] / "cached_ocr.txt"
        text_path.write_text("Cached OCR page text", encoding="utf-8")
        record = {**task, "status": "succeeded", "text_path": str(text_path),
                  "text_sha256": prep.digest(text_path), "confidence": 91,
                  "language_model_sha256": self.model_sha}
        prep.atomic_lines(self.output / "page_results.jsonl", [record])
        environment = {**os.environ, "OFFICIAL_OCR_ROOT": str(self.output)}
        subprocess.run(["node", str(prep.WORKSPACE / "sources/official_courts/ocr_worker.cjs")],
                       env=environment, capture_output=True, check=True, text=True, timeout=30)
        manifest = prep.read_lines(self.output / "pdf_ocr_manifest.jsonl")[0]
        self.assertEqual(manifest["ocr_status"], "complete")
        self.assertEqual(manifest["ocr_scope"], "selected_pages")
        self.assertEqual(manifest["pdf_pages"], 5)
        self.assertEqual(manifest["ocr_pages_completed"], 1)
        self.assertEqual(manifest["ocr_pages_required"], 1)
        self.assertEqual(len(prep.read_lines(self.output / "page_results.jsonl")), 1)
        status = json.loads((self.output / "status.json").read_text())
        self.assertEqual(status["remaining_pages"], 0)
        self.assertEqual(status["pdf_pages_in_documents"], 5)


if __name__ == "__main__":
    unittest.main()
