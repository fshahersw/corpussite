"""Offline provenance, exclusion, deduplication, and versioning fixtures."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from scripts import build_document_index as index


class DocumentIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="legal-index-test-")
        self.root = Path(self.temp.name)
        self.base = self.root / "sources/official_laws"
        for directory in ("raw", "text", "metadata", "indexes"):
            (self.base / directory).mkdir(parents=True)
        self.output = self.root / "catalog"

    def tearDown(self):
        self.temp.cleanup()

    def source(self, name, text="quartz statute protects the river", success=True, retrieved="2026-01-01T00:00:00Z"):
        raw = ("<article>" + text + "</article>").encode()
        (self.base / "raw" / (name + ".html")).write_bytes(raw)
        (self.base / "text" / (name + ".txt")).write_text(text, encoding="utf-8")
        metadata = {"source_url": "https://example.gov/" + name, "final_url": "https://example.gov/" + name,
                    "retrieved_at_utc": retrieved, "verification_status": "retrieved" if success else "blocked_http_401",
                    "http_status": 200 if success else 401, "evidence_path": "raw/" + name + ".html",
                    "text_path": "text/" + name + ".txt", "sha256": hashlib.sha256(raw).hexdigest(),
                    "document_title": name, "content_type": "text/html", "kind": "seed", "format": "html"}
        (self.base / "metadata" / (name + ".json")).write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def build(self):
        builder = index.Builder(self.root, self.output)
        try:
            return builder.run()
        finally:
            builder.db.close()

    def test_deduplicated_text_retains_source_captures_and_skips_failures(self):
        self.source("law-one")
        self.source("law-two")
        self.source("failure", "private error sentinel", success=False)
        summary = self.build()
        self.assertEqual(summary["source_records"], 2)
        self.assertEqual(summary["capture_versions"], 2)
        self.assertEqual(summary["unique_searchable_texts"], 1)
        self.assertEqual(len(index.search(self.output, "quartz statute", exact=True)), 2)
        self.assertFalse(index.search(self.output, "private error sentinel", exact=True))
        self.assertEqual(summary["validation_file_reference_issues"], 0)

    def test_incremental_reuse_and_changed_version_retention(self):
        self.source("law-one")
        self.build()
        repeat = self.build()
        self.assertEqual(repeat["capture_versions"], 1)
        self.assertEqual(repeat["this_build"].get("artifact_files_hashed", 0), 0)
        self.assertGreater(repeat["this_build"]["unchanged_artifact_files_skipped"], 0)
        self.assertEqual(repeat["this_build"].get("manifest_files_read", 0), 0)
        self.source("law-one", "amended statute protects the mountain", retrieved="2026-02-01T00:00:00Z")
        changed = self.build()
        self.assertEqual(changed["source_records"], 1)
        self.assertEqual(changed["capture_versions"], 2)
        self.assertEqual(changed["unique_searchable_texts"], 2)
        self.assertTrue(index.search(self.output, "amended statute", exact=True))
        self.assertFalse(index.search(self.output, "quartz statute", exact=True))
        self.assertTrue(index.search(self.output, "quartz statute", history=True, exact=True))
        self.assertEqual(changed["validation_file_reference_issues"], 2)

    def test_hash_mismatch_and_unlisted_private_path_are_not_indexed(self):
        metadata = self.source("mismatch")
        metadata["sha256"] = "0" * 64
        (self.base / "metadata/mismatch.json").write_text(json.dumps(metadata), encoding="utf-8")
        private = self.root / ".auth/private.txt"
        private.parent.mkdir()
        private.write_text("private settings sentinel", encoding="utf-8")
        metadata = self.source("escaped")
        metadata["evidence_path"] = "../../../.auth/private.txt"
        (self.base / "metadata/escaped.json").write_text(json.dumps(metadata), encoding="utf-8")
        summary = self.build()
        self.assertEqual(summary["source_records"], 0)
        self.assertEqual(summary["unique_searchable_texts"], 0)
        self.assertFalse(index.search(self.output, "private settings sentinel", exact=True))

    def test_completed_ocr_requires_parent_and_text_hash(self):
        base = self.root / "sources/official_courts"
        for directory in ("raw", "text", "manifests", "ocr/scanned"):
            (base / directory).mkdir(parents=True)
        raw = base / "raw/scanned.pdf"
        raw.write_bytes(b"%PDF- local test capture")
        text = base / "text/scanned.txt"
        text.write_text("Preserved embedded text", encoding="utf-8")
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        source = {"requested_url": "https://courts.example.gov/scanned.pdf", "final_url": "https://courts.example.gov/scanned.pdf",
                  "jurisdiction": "Test State", "category": "court_rules", "raw_path": "raw/scanned.pdf", "text_path": "text/scanned.txt",
                  "verification_status": "retrieved", "http_status": 200, "sha256": raw_sha, "fetched_at_utc": "2026-01-01T00:00:00Z"}
        (base / "manifests/sources.jsonl").write_text(json.dumps(source) + "\n", encoding="utf-8")
        derived = base / "ocr/scanned/combined_ocr.txt"
        derived.write_text("Recognized quartz judicial text", encoding="utf-8")
        ocr = {"source_id": "scanned", "source_url": source["requested_url"], "source_pdf_path": str(raw), "source_pdf_sha256": raw_sha,
               "pdf_pages": 5, "ocr_status": "complete", "ocr_scope": "selected_pages", "ocr_pages_required": 1, "ocr_pages_completed": 1,
               "ocr_text_path": str(derived), "ocr_text_sha256": "0" * 64, "ocr_engine": "fixture", "language_model_sha256": "1" * 64}
        manifest = base / "ocr/pdf_ocr_manifest.jsonl"
        manifest.write_text(json.dumps(ocr) + "\n", encoding="utf-8")
        rejected = self.build()
        self.assertEqual(rejected["source_records"], 1)
        self.assertFalse(index.search(self.output, "Recognized quartz", exact=True))
        ocr["ocr_text_sha256"] = hashlib.sha256(derived.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(ocr) + "\n", encoding="utf-8")
        accepted = self.build()
        self.assertEqual(accepted["source_records"], 2)
        results = index.search(self.output, "Recognized quartz", exact=True)
        self.assertEqual(results[0]["collection"], "official_courts/ocr")
        self.assertTrue(index.search(self.output, "Preserved embedded text", exact=True))
        self.assertEqual(accepted["validation_file_reference_issues"], 0)

    def parser_repair_fixture(self):
        recovery = self.base / "recovery_pass_1"
        derivatives = recovery / "local_repairs"
        derivatives.mkdir(parents=True)
        raw = self.base / "raw/nebraska.html"
        raw.write_bytes(b"<html><body><h1>Nebraska court rules</h1><script>ignored script marker</script><p>A repaired appellate procedure.</p></body></html>")
        source_url = "https://supremecourt.nebraska.gov/supreme-court-rules"
        metadata = {"source_url": source_url, "final_url": "https://nebraskajudicial.gov/supreme-court-rules",
                    "retrieved_at_utc": "2026-01-01T00:00:00Z", "verification_status": "retrieval_error", "http_status": 200,
                    "format": "html", "evidence_path": "raw/nebraska.html", "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                    "bytes": raw.stat().st_size, "document_title": "Nebraska Court Rules", "error": "ValueError: malformed href"}
        metadata_path = self.base / "metadata/nebraska.json"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        text = derivatives / "nebraska_supreme_court_rules.txt"
        expected_text = "Nebraska court rules\nA repaired appellate procedure."
        # A Windows CRLF artifact must reproduce the same Unicode text.
        text.write_bytes(expected_text.replace("\n", "\r\n").encode("utf-8"))
        repair = {"source_url": source_url, "source_metadata_path": str(metadata_path), "raw_path": str(raw),
                  "raw_sha256_verified": metadata["sha256"], "text_path": str(text), "text_characters": len(expected_text),
                  "network_requests_made": 0, "original_metadata_unchanged": True}
        (recovery / "local_repairs.json").write_text(json.dumps([repair]), encoding="utf-8")
        triage = {"source_url": source_url, "decision": "payload_saved_parser_repaired_locally", "original_error_class": "ValueError",
                  "original_http_status": 200, "original_payload_path": str(raw), "original_metadata_path": str(metadata_path),
                  "original_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest()}
        (recovery / "triage.jsonl").write_text(json.dumps(triage) + "\n", encoding="utf-8")
        return raw, metadata_path, text

    def test_law_ocr_manifest_joins_successful_document_parent(self):
        (self.base / "documents").mkdir()
        raw = self.base / "documents/law.pdf"
        raw.write_bytes(b"%PDF- local law OCR fixture")
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        original_text = self.base / "text/law.txt"
        original_text.write_text("Preserved statutory text", encoding="utf-8")
        metadata = {"source_url": "https://legislature.example.gov/law.pdf", "final_url": "https://legislature.example.gov/law.pdf",
                    "retrieved_at_utc": "2026-01-01T00:00:00Z", "verification_status": "retrieved", "http_status": 200,
                    "format": "pdf", "evidence_path": "documents/law.pdf", "text_path": "text/law.txt", "sha256": raw_sha,
                    "bytes": raw.stat().st_size, "document_title": "A saved law"}
        (self.base / "metadata/law.json").write_text(json.dumps(metadata), encoding="utf-8")
        derived = self.base / "ocr/law/combined_ocr.txt"
        derived.parent.mkdir(parents=True)
        derived.write_text("Recognized supplementary statutory wording", encoding="utf-8")
        ocr = {"source_id": "law", "source_url": metadata["source_url"], "source_pdf_path": str(raw), "source_pdf_sha256": raw_sha,
               "pdf_pages": 4, "ocr_status": "complete", "ocr_scope": "selected_pages", "ocr_pages_required": 2, "ocr_pages_completed": 2,
               "ocr_text_path": str(derived), "ocr_text_sha256": hashlib.sha256(derived.read_bytes()).hexdigest()}
        (self.base / "ocr/pdf_ocr_manifest.jsonl").write_text(json.dumps(ocr) + "\n", encoding="utf-8")
        summary = self.build()
        self.assertEqual(summary["source_records"], 2)
        self.assertEqual(index.search(self.output, "Recognized supplementary statutory wording", exact=True)[0]["collection"], "official_laws/ocr")
        self.assertTrue(index.search(self.output, "Preserved statutory text", exact=True))

    def test_nebraska_parser_repair_requires_reproducible_text_and_preserves_failure(self):
        raw, metadata, text = self.parser_repair_fixture()
        original_raw, original_metadata, good_text = raw.read_bytes(), metadata.read_bytes(), text.read_bytes()
        text.write_text("Unproven repaired text sentinel", encoding="utf-8")
        rejected = self.build()
        self.assertEqual(rejected["source_records"], 0)
        self.assertFalse(index.search(self.output, "Unproven repaired text sentinel", exact=True))
        text.write_bytes(good_text)
        accepted = self.build()
        self.assertEqual(accepted["source_records"], 1)
        matches = index.search(self.output, "A repaired appellate procedure", exact=True)
        self.assertEqual(matches[0]["collection"], "official_laws/parser_repairs")
        self.assertFalse(index.search(self.output, "ignored script marker", exact=True))
        self.assertEqual(raw.read_bytes(), original_raw)
        self.assertEqual(metadata.read_bytes(), original_metadata)
        with index.closing(index.read_db(self.output / "documents.sqlite3")) as db:
            record = dict(db.execute("SELECT * FROM latest_documents").fetchone())
        evidence = json.loads(record["source_evidence_json"])
        self.assertEqual(record["raw_sha256"], hashlib.sha256(original_raw).hexdigest())
        self.assertEqual(record["text_file_sha256"], hashlib.sha256(good_text).hexdigest())
        self.assertEqual(evidence["original_metadata_sha256"], hashlib.sha256(original_metadata).hexdigest())
        self.assertEqual(evidence["original_verification_status"], "retrieval_error")
        self.assertEqual(accepted["validation_file_reference_issues"], 0)

    def test_nebraska_parser_repair_rejects_changed_original_html_or_failure_metadata(self):
        raw, metadata, _ = self.parser_repair_fixture()
        original_raw = raw.read_bytes()
        with raw.open("ab") as handle:
            handle.write(b"changed HTML")
        self.assertEqual(self.build()["source_records"], 0)
        raw.write_bytes(original_raw)
        with metadata.open("ab") as handle:
            handle.write(b" ")
        self.assertEqual(self.build()["source_records"], 0)

    def document_derivative_fixture(self, xml=False, epub=False):
        base = self.root / "corpus/official_law_pages"
        derivative_directory = "epub_document_derivatives" if epub else ("xml_document_derivatives" if xml else "document_derivatives")
        for directory in ("raw", "text", "metadata", derivative_directory + "/text"):
            (base / directory).mkdir(parents=True, exist_ok=True)
        raw = base / "raw/statute.bin"
        if epub:
            epub_parts = {"mimetype": b"application/epub+zip", "META-INF/container.xml": b'<container><rootfile full-path="OEBPS/book.opf"/></container>',
                "OEBPS/book.opf": b'<package version="2.0"><manifest><item id="chapter" href="chapter.xhtml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
                "OEBPS/chapter.xhtml": b"<html><body><p>Extracted quartz statutory wording</p></body></html>"}
            with zipfile.ZipFile(raw, "w") as package:
                for name, content in epub_parts.items():
                    package.writestr(name, content)
        elif xml:
            raw.write_bytes(b'<w:wordDocument xmlns:w="http://schemas.microsoft.com/office/word/2003/wordml"><w:body><w:p><w:r><w:t>Extracted quartz statutory wording</w:t></w:r></w:p></w:body></w:wordDocument>')
        else:
            with zipfile.ZipFile(raw, "w") as package:
                package.writestr("[Content_Types].xml", "<Types/>")
                package.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Extracted quartz statutory wording</w:t></w:r></w:p></w:body></w:document>')
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        source_url = "https://www.legis.iowa.gov/docs/IACODE/IowaCodeWithActs.epub" if epub else "https://legislature.example.gov/statute.docx"
        fetched_at = "2026-01-01T00:00:00Z"
        metadata = base / "metadata/source.json"
        declared_type = "application/octet-stream" if epub else ("application/msword" if xml else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        metadata.write_text(json.dumps({"requested_url": source_url, "fetch_id": "fixture-fetch", "http_status": 200,
            "raw_complete": True, "sha256": raw_sha, "byte_count": raw.stat().st_size,
            "detected_type": declared_type, "extraction_status": "unsupported_format"}), encoding="utf-8")
        database = base / "corpus.sqlite3"
        with index.closing(sqlite3.connect(database)) as db, db:
            db.executescript("""
                CREATE TABLE resources(id INTEGER PRIMARY KEY,url TEXT,status TEXT,raw_complete INTEGER,last_http_status INTEGER,
                    raw_path TEXT,text_path TEXT,sha256 TEXT,byte_count INTEGER,metadata_path TEXT,last_fetch_id TEXT,extraction_status TEXT,title TEXT);
                CREATE TABLE fetches(id TEXT PRIMARY KEY,fetched_at TEXT);
                CREATE TABLE contexts(id INTEGER PRIMARY KEY,jurisdiction_json TEXT,category TEXT,source_family TEXT);
                CREATE TABLE resource_contexts(resource_id INTEGER,context_id INTEGER);
            """)
            db.execute("INSERT INTO resources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (1, source_url, "downloaded", 1, 200,
                "raw/statute.bin", None, raw_sha, raw.stat().st_size, "metadata/source.json", "fixture-fetch", "unsupported_format", "Saved statute"))
            db.execute("INSERT INTO fetches VALUES(?,?)", ("fixture-fetch", fetched_at))
            db.execute("INSERT INTO contexts VALUES(?,?,?,?)", (1, json.dumps({"state": "Test State"}), "statutes", "official_laws"))
            db.execute("INSERT INTO resource_contexts VALUES(?,?)", (1, 1))
        text = base / derivative_directory / "text" / (raw_sha + ".txt")
        text.write_text("Extracted quartz statutory wording", encoding="utf-8")
        if epub:
            body = epub_parts["OEBPS/chapter.xhtml"]
        elif xml:
            body = raw.read_bytes()
        else:
            with zipfile.ZipFile(raw) as package:
                body = package.read("word/document.xml")
        derivative = {"resource_id": 1, "source_url": source_url, "parent_raw_path": str(raw), "parent_raw_sha256": raw_sha,
            "parent_byte_count": raw.stat().st_size, "parent_metadata_path": str(metadata), "parent_metadata_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
            "parent_fetch_id": "fixture-fetch", "parent_retrieved_at": fetched_at, "parent_extraction_status": "unsupported_format", "parent_collector_text_path": None,
            "parent_sha256_verified": True, "declared_format": declared_type, "status": "extracted", "extractor_version": "fixture-1",
            "extraction_method": "offline WordprocessingML fixture", "text_path": str(text), "text_sha256": hashlib.sha256(text.read_bytes()).hexdigest(),
            "text_bytes": text.stat().st_size, "paragraph_text_characters": 33, "text_paragraphs": 1,
            "package_parts": [{"package_part": "word/document.xml", "xml_sha256": hashlib.sha256(body).hexdigest(), "xml_bytes_read": len(body)}],
            "derived_at_utc": "2026-01-02T00:00:00Z", "network_requests": 0, "originals_modified": False,
            "limitations": ["Layout and automatic numbering are not reconstructed."]}
        if xml:
            namespace = "http://schemas.microsoft.com/office/word/2003/wordml"
            root_qname = "{" + namespace + "}wordDocument"
            derivative.update(detected_format="wordprocessingml_2003_xml", xml_root_qname=root_qname, wordprocessingml_namespace=namespace)
            derivative["package_parts"][0].update(package_part="[direct-xml]/w:wordDocument/w:body", xml_root_qname=root_qname, wordprocessingml_namespace=namespace)
        elif epub:
            derivative.update(detected_format="epub", container_mimetype="application/epub+zip", package_document="OEBPS/book.opf",
                opf_version="2.0", opf_metadata={"title": "An Iowa code fixture"}, spine_items=1, extracted_spine_items=1)
            derivative["package_parts"] = [{"package_part": name, "sha256": hashlib.sha256(content).hexdigest(), "xml_bytes_read": len(content)}
                for name, content in epub_parts.items()]
            derivative["package_parts"][-1].update(role="spine", status="extracted", spine_index=1, idref="chapter", linear="yes")
        manifest = base / derivative_directory / "manifest.jsonl"
        manifest.write_text(json.dumps(derivative) + "\n", encoding="utf-8")
        return {"raw": raw, "metadata": metadata, "text": text, "database": database, "manifest": manifest, "derivative": derivative}

    def test_document_derivative_preserves_parent_provenance_and_incremental_dedup(self):
        fixture = self.document_derivative_fixture()
        originals = {name: fixture[name].read_bytes() for name in ("raw", "metadata", "database")}
        first = self.build()
        self.assertEqual(first["source_records"], 2)
        self.assertEqual(first["capture_versions"], 2)
        self.assertEqual(first["unique_searchable_texts"], 1)
        results = index.search(self.output, "Extracted quartz statutory wording", exact=True)
        self.assertEqual(results[0]["collection"], "corpus/official_law_pages/document_derivatives")
        with index.closing(index.read_db(self.output / "documents.sqlite3")) as db:
            parent = dict(db.execute("SELECT * FROM latest_documents WHERE capture_kind='direct_public_capture'").fetchone())
            derived = dict(db.execute("SELECT * FROM latest_documents WHERE capture_kind='document_text_derivative'").fetchone())
        evidence = json.loads(derived["source_evidence_json"])
        self.assertEqual(evidence["parent_version_id"], parent["version_id"])
        self.assertEqual(evidence["package_parts"], fixture["derivative"]["package_parts"])
        self.assertEqual(evidence["limitations"], fixture["derivative"]["limitations"])
        self.assertEqual(evidence["extractor_version"], "fixture-1")
        self.assertEqual(evidence["extraction_method"], fixture["derivative"]["extraction_method"])
        self.assertEqual(derived["raw_path"], parent["raw_path"])
        self.assertEqual(derived["raw_sha256"], parent["raw_sha256"])
        self.assertEqual(derived["text_file_sha256"], fixture["derivative"]["text_sha256"])
        self.assertEqual(parent["extraction_status"], "unsupported_format")
        self.assertIsNone(parent["text_path"])
        repeat = self.build()
        self.assertEqual(repeat["capture_versions"], 2)
        self.assertEqual(repeat["this_build"].get("artifact_files_hashed", 0), 0)
        self.assertEqual(repeat["this_build"].get("manifest_files_read", 0), 0)
        self.source("same-wording", "Extracted quartz statutory wording")
        dedup = self.build()
        self.assertEqual(dedup["source_records"], 3)
        self.assertEqual(dedup["unique_searchable_texts"], 1)
        self.assertEqual(dedup["validation_file_reference_issues"], 0)
        for name, original in originals.items():
            self.assertEqual(fixture[name].read_bytes(), original)

    def test_document_derivative_reuses_cache_without_new_optional_proof_fields(self):
        fixture = self.document_derivative_fixture()
        first = self.build()
        path = fixture["manifest"].relative_to(self.root).as_posix()
        with index.closing(sqlite3.connect(self.output / "documents.sqlite3")) as db, db:
            cached = json.loads(db.execute("SELECT normalized_json FROM input_cache WHERE path=?", (path,)).fetchone()[0])
            for item in cached:
                item.pop("literal_wt_sha256_basis", None)
            db.execute("UPDATE input_cache SET normalized_json=? WHERE path=?", (json.dumps(cached), path))
        repeat = self.build()
        self.assertEqual(repeat["source_records"], first["source_records"])
        self.assertEqual(repeat["capture_versions"], first["capture_versions"])
        self.assertEqual(repeat["validation_file_reference_issues"], 0)
        self.assertEqual(repeat["this_build"].get("manifest_files_read", 0), 0)
        self.assertTrue(index.search(self.output, "Extracted quartz statutory wording", exact=True))

    def test_document_derivative_requires_a_current_successful_parent(self):
        fixture = self.document_derivative_fixture()
        with index.closing(sqlite3.connect(fixture["database"])) as db, db:
            db.execute("UPDATE resources SET status='failed'")
        absent = self.build()
        self.assertEqual(absent["source_records"], 0)
        self.assertFalse(index.search(self.output, "Extracted quartz", exact=True))
        issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
        self.assertTrue(any(item["kind"] == "document_text_derivative_not_indexed" and "No successful parent" in item["detail"] for item in issues))
        with index.closing(sqlite3.connect(fixture["database"])) as db, db:
            db.execute("UPDATE resources SET status='downloaded'")
        self.assertEqual(self.build()["source_records"], 2)
        # A retained historical parent may not validate a new derivative after it fails this build.
        with index.closing(sqlite3.connect(fixture["database"])) as db, db:
            db.execute("UPDATE resources SET status='failed'")
        changed_text = "Unverifiable new derivative wording"
        fixture["text"].write_text(changed_text, encoding="utf-8")
        fixture["derivative"].update(text_sha256=hashlib.sha256(fixture["text"].read_bytes()).hexdigest(), text_bytes=fixture["text"].stat().st_size)
        fixture["manifest"].write_text(json.dumps(fixture["derivative"]) + "\n", encoding="utf-8")
        rejected = self.build()
        self.assertEqual(rejected["capture_versions"], 2)
        self.assertFalse(index.search(self.output, changed_text, exact=True))

    def test_document_derivative_retains_changed_text_versions(self):
        fixture = self.document_derivative_fixture()
        self.build()
        fixture["text"].write_text("Revised quartz statutory wording", encoding="utf-8")
        fixture["derivative"].update(text_sha256=hashlib.sha256(fixture["text"].read_bytes()).hexdigest(),
            text_bytes=fixture["text"].stat().st_size, extractor_version="fixture-2")
        fixture["manifest"].write_text(json.dumps(fixture["derivative"]) + "\n", encoding="utf-8")
        changed = self.build()
        self.assertEqual(changed["source_records"], 2)
        self.assertEqual(changed["capture_versions"], 3)
        self.assertEqual(changed["unique_searchable_texts"], 2)
        self.assertTrue(index.search(self.output, "Revised quartz", exact=True))
        self.assertFalse(index.search(self.output, "Extracted quartz", exact=True))
        self.assertTrue(index.search(self.output, "Extracted quartz", exact=True, history=True))

    def test_xml_document_derivative_requires_supported_root_and_direct_part_provenance(self):
        fixture = self.document_derivative_fixture(xml=True)
        accepted = self.build()
        self.assertEqual(accepted["source_records"], 2)
        results = index.search(self.output, "Extracted quartz", exact=True)
        self.assertEqual(results[0]["collection"], "corpus/official_law_pages/xml_document_derivatives")
        bad_parts = json.loads(json.dumps(fixture["derivative"]["package_parts"]))
        bad_parts[0]["xml_sha256"] = "0" * 64
        changes = ({"xml_root_qname": "{urn:unknown}document"}, {"detected_format": "unknown_xml"},
                   {"wordprocessingml_namespace": "urn:unknown"}, {"package_parts": bad_parts},
                   {"text_path": str(self.root / "corpus/official_law_pages/document_derivatives/text" / fixture["text"].name)})
        for number, change in enumerate(changes):
            with self.subTest(change=change):
                fixture["manifest"].write_text(json.dumps({**fixture["derivative"], **change}) + "\n", encoding="utf-8")
                self.output = self.root / ("catalog_xml_rejection_" + str(number))
                rejected = self.build()
                self.assertEqual(rejected["source_records"], 1)
                self.assertEqual(rejected["unique_searchable_texts"], 0)
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertTrue(any(item["kind"] == "document_text_derivative_not_indexed" for item in issues))

    def test_epub_document_derivative_requires_verified_container_and_complete_spine(self):
        fixture = self.document_derivative_fixture(epub=True)
        accepted = self.build()
        self.assertEqual(accepted["source_records"], 2)
        results = index.search(self.output, "Extracted quartz", exact=True)
        self.assertEqual(results[0]["collection"], "corpus/official_law_pages/epub_document_derivatives")
        with index.closing(index.read_db(self.output / "documents.sqlite3")) as db:
            record = dict(db.execute("SELECT * FROM latest_documents WHERE capture_kind='document_text_derivative'").fetchone())
        self.assertEqual(record["content_type"], "application/epub+zip")
        evidence = json.loads(record["source_evidence_json"])
        self.assertEqual(evidence["package_parts"], fixture["derivative"]["package_parts"])
        self.assertEqual(evidence["opf_metadata"], fixture["derivative"]["opf_metadata"])
        missing_mimetype = fixture["derivative"]["package_parts"][1:]
        incomplete_spine = json.loads(json.dumps(fixture["derivative"]["package_parts"]))
        incomplete_spine[-1]["status"] = "failed"
        gapped_spine = json.loads(json.dumps(fixture["derivative"]["package_parts"]))
        gapped_spine[-1]["spine_index"] = 2
        changes = ({"container_mimetype": "application/zip"}, {"detected_format": "zip"}, {"package_parts": missing_mimetype},
                   {"package_document": "../book.opf"}, {"extracted_spine_items": 0}, {"package_parts": incomplete_spine},
                   {"package_parts": gapped_spine}, {"source_url": "https://legislature.example.gov/unlisted.epub"})
        for number, change in enumerate(changes):
            with self.subTest(change=change):
                fixture["manifest"].write_text(json.dumps({**fixture["derivative"], **change}) + "\n", encoding="utf-8")
                self.output = self.root / ("catalog_epub_rejection_" + str(number))
                rejected = self.build()
                self.assertEqual(rejected["source_records"], 1)
                self.assertEqual(rejected["unique_searchable_texts"], 0)
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertTrue(any(item["kind"] == "document_text_derivative_not_indexed" for item in issues))

    def test_document_derivative_rejects_missing_or_tampered_parent_and_text(self):
        fixture = self.document_derivative_fixture()
        original = {name: fixture[name].read_bytes() for name in ("raw", "metadata", "text", "manifest")}
        cases = ("missing_raw", "tampered_raw", "missing_metadata", "tampered_metadata", "missing_text", "tampered_text", "mismatched_parent_hash")
        for case in cases:
            with self.subTest(case=case):
                for name, body in original.items():
                    fixture[name].write_bytes(body)
                if case.startswith("missing_"):
                    fixture[case.removeprefix("missing_")].unlink()
                elif case.startswith("tampered_"):
                    target = fixture[case.removeprefix("tampered_")]
                    # Preserve size to exercise hash verification rather than only size checks.
                    body = target.read_bytes()
                    target.write_bytes(b"X" + body[1:])
                else:
                    record = json.loads(original["manifest"])
                    record["parent_raw_sha256"] = "0" * 64
                    fixture["manifest"].write_text(json.dumps(record) + "\n", encoding="utf-8")
                self.output = self.root / ("catalog_" + case)
                summary = self.build()
                with index.closing(index.read_db(self.output / "documents.sqlite3")) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM versions WHERE capture_kind='document_text_derivative'").fetchone()[0], 0)
                self.assertFalse(index.search(self.output, "Extracted quartz", exact=True))
                self.assertEqual(summary["unique_searchable_texts"], 0)
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertTrue(any("document_derivatives/manifest.jsonl:" in item["source_record_locator"] for item in issues))

    def test_document_derivative_rejects_unlisted_paths_and_incomplete_provenance(self):
        fixture = self.document_derivative_fixture()
        private = self.root / ".auth/private.txt"
        private.parent.mkdir()
        private.write_text("private sentinel text", encoding="utf-8")
        changes = ({"text_path": str(private)}, {"parent_raw_path": str(private)}, {"parent_metadata_path": str(private)},
                   {"text_sha256": None}, {"extractor_version": None}, {"package_parts": []}, {"limitations": []},
                   {"source_url": "https://legislature.example.gov/different.docx"})
        for number, change in enumerate(changes):
            with self.subTest(change=change):
                fixture["manifest"].write_text(json.dumps({**fixture["derivative"], **change}) + "\n", encoding="utf-8")
                self.output = self.root / ("catalog_provenance_" + str(number))
                summary = self.build()
                self.assertEqual(summary["source_records"], 1)
                self.assertEqual(summary["unique_searchable_texts"], 0)
                self.assertFalse(index.search(self.output, "private sentinel", exact=True))
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertTrue(any(item["kind"] == "document_text_derivative_not_indexed" for item in issues))

    def browser_law_fixture(self, payload_changes=None, record_changes=None, text_override=None, append=False):
        base = self.root / "corpus/trellis_browser_laws"
        legal_text = "Section 4 — Due process of law\nA quartz law protects every person's due process."
        payload = {"url": "https://trellis.law/state-rules/az/constitution/section-4-due-process",
            "title": "Section 4 — Arizona Code | Trellis Law", "heading": "Arizona Constitution|Due process of law",
            "legal_text": legal_text, "legal_html": "<h1>Section 4 — Due process of law</h1><p>A quartz law protects every person's due process.</p>",
            "observed_law_links": [{"url": "https://trellis.law/state-rules/az/constitution", "text": "Arizona Constitution"}],
            "captured_at": "2026-01-01T00:00:00Z", "content_kind": "law_text", "dom_selector": "div.rule-header", "signed_in_observed": True}
        payload.update(payload_changes or {})
        raw_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        text_bytes = (payload["legal_text"] if text_override is None else text_override).encode("utf-8")
        content_bytes = json.dumps({key: value for key, value in payload.items() if key != "captured_at"}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        raw_sha, text_sha = hashlib.sha256(raw_bytes).hexdigest(), hashlib.sha256(text_bytes).hexdigest()
        raw_relative, text_relative = "raw/" + raw_sha[:2] + "/" + raw_sha + ".json", "text/" + text_sha[:2] + "/" + text_sha + ".txt"
        raw, text = base / raw_relative, base / text_relative
        raw.parent.mkdir(parents=True, exist_ok=True)
        text.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(raw_bytes)
        text.write_bytes(text_bytes)
        url_parts = index.urlsplit(payload["url"]).path.split("/")
        record = {"status": "captured", "capture_kind": "browser_rendered_dom", "source_url": payload["url"],
            "title": payload["title"], "heading": payload["heading"], "state_code": url_parts[2].upper() if len(url_parts) > 2 else None,
            "category": "state_rule", "content_kind": payload["content_kind"], "captured_at": payload["captured_at"],
            "archived_at": "2026-01-03T00:00:00+00:00", "archive_version": "1.0.0", "dom_selector": payload["dom_selector"],
            "signed_in_observed": payload["signed_in_observed"], "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "raw_path": raw_relative, "raw_sha256": raw_sha, "raw_bytes": len(raw_bytes),
            "text_path": text_relative, "text_sha256": text_sha, "text_bytes": len(text_bytes),
            "observed_law_links": payload["observed_law_links"], "source_http_status": None,
            "source_network_requests_by_archiver": 0, "cookies_or_credentials_exported": False,
            "limitations": ["Selected browser legal DOM; original HTTP response not captured."]}
        record.update(record_changes or {})
        manifest = base / "manifest.jsonl"
        with manifest.open("a" if append else "w", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"raw": raw, "text": text, "manifest": manifest, "payload": payload, "record": record}

    def test_browser_law_capture_preserves_dom_provenance_and_exact_unicode_text(self):
        fixture = self.browser_law_fixture()
        originals = {name: fixture[name].read_bytes() for name in ("raw", "text", "manifest")}
        accepted = self.build()
        self.assertEqual(accepted["source_records"], 1)
        self.assertEqual(accepted["unique_searchable_texts"], 1)
        result = index.search(self.output, "A quartz law protects", exact=True)[0]
        self.assertEqual(result["collection"], "corpus/trellis_browser_laws")
        with index.closing(index.read_db(self.output / "documents.sqlite3")) as db:
            record = dict(db.execute("SELECT * FROM latest_documents").fetchone())
            stored_text = db.execute("SELECT text FROM contents").fetchone()[0]
        evidence = json.loads(record["source_evidence_json"])
        self.assertEqual(record["capture_kind"], "browser_rendered_dom")
        self.assertEqual(record["retrieval_time_basis"], "browser_dom_captured_at")
        self.assertEqual(record["retrieved_at"], fixture["payload"]["captured_at"])
        self.assertEqual(record["raw_sha256"], fixture["record"]["raw_sha256"])
        self.assertEqual(record["text_file_sha256"], fixture["record"]["text_sha256"])
        self.assertEqual(stored_text, fixture["payload"]["legal_text"])
        self.assertIsNone(evidence["source_http_status"])
        self.assertNotIn("http_status", evidence)
        self.assertFalse(evidence["cookies_or_credentials_exported"])
        self.assertEqual(evidence["source_network_requests_by_archiver"], 0)
        self.assertTrue(evidence["text_equals_raw_legal_text"])
        self.assertTrue(evidence["content_sha256_verified"])
        self.assertEqual(evidence["observed_law_links"], fixture["payload"]["observed_law_links"])
        self.assertEqual(evidence["limitations"], fixture["record"]["limitations"])
        self.assertEqual(accepted["validation_file_reference_issues"], 0)
        repeat = self.build()
        self.assertEqual(repeat["capture_versions"], 1)
        self.assertEqual(repeat["this_build"].get("artifact_files_hashed", 0), 0)
        self.assertEqual(repeat["this_build"].get("manifest_files_read", 0), 0)
        for name, original in originals.items():
            self.assertEqual(fixture[name].read_bytes(), original)

    def test_browser_law_capture_retains_append_only_versions_and_text_dedup(self):
        original = self.browser_law_fixture()
        self.build()
        changed = self.browser_law_fixture(payload_changes={"legal_text": "Section 4 — Amended process\nThe amended quartz law provides a hearing.",
            "legal_html": "<p>The amended quartz law provides a hearing.</p>", "captured_at": "2026-01-02T00:00:00Z"}, append=True)
        updated = self.build()
        self.assertEqual(updated["source_records"], 1)
        self.assertEqual(updated["capture_versions"], 2)
        self.assertEqual(updated["unique_searchable_texts"], 2)
        self.assertTrue(index.search(self.output, "amended quartz law", exact=True))
        self.assertFalse(index.search(self.output, "A quartz law protects", exact=True))
        self.assertTrue(index.search(self.output, "A quartz law protects", exact=True, history=True))
        self.assertEqual(original["text"].read_text(encoding="utf-8"), original["payload"]["legal_text"])
        self.source("duplicate-browser-wording", changed["payload"]["legal_text"])
        # Match literal LF bytes; Path.write_text otherwise translates newlines on Windows.
        (self.base / "text/duplicate-browser-wording.txt").write_bytes(changed["text"].read_bytes())
        dedup = self.build()
        self.assertEqual(dedup["source_records"], 2)
        self.assertEqual(dedup["unique_searchable_texts"], 2)

    def test_browser_law_directory_and_capture_time_excluded_content_hash(self):
        first = self.browser_law_fixture(payload_changes={"content_kind": "law_directory", "url": "https://trellis.law/state-rules"})
        self.build()
        second = self.browser_law_fixture(payload_changes={"content_kind": "law_directory", "url": "https://trellis.law/state-rules", "captured_at": "2026-01-02T00:00:00Z"}, append=True)
        self.assertEqual(first["record"]["content_sha256"], second["record"]["content_sha256"])
        self.assertNotEqual(first["record"]["raw_sha256"], second["record"]["raw_sha256"])
        accepted = self.build()
        self.assertEqual(accepted["source_records"], 1)
        self.assertEqual(accepted["capture_versions"], 2)
        self.assertEqual(accepted["unique_searchable_texts"], 1)
        with index.closing(index.read_db(self.output / "documents.sqlite3")) as db:
            record = dict(db.execute("SELECT * FROM latest_documents").fetchone())
        self.assertIn("law_directory", json.loads(record["categories_json"]))
        self.assertEqual(json.loads(record["jurisdictions_json"]), [{"country": "US"}])

    def test_browser_law_capture_rejects_raw_text_and_content_hash_mismatches(self):
        cases = ("missing_raw", "missing_text", "tampered_raw", "tampered_text", "wrong_content_hash", "different_text", "different_source")
        for case in cases:
            with self.subTest(case=case):
                self.output = self.root / ("browser_integrity_" + case)
                kwargs = {}
                if case == "wrong_content_hash":
                    kwargs["record_changes"] = {"content_sha256": "0" * 64}
                elif case == "different_text":
                    kwargs["text_override"] = "Unverifiable replacement text with its own valid file hash."
                elif case == "different_source":
                    kwargs["record_changes"] = {"source_url": "https://trellis.law/state-rules/az/constitution/different-section"}
                fixture = self.browser_law_fixture(**kwargs)
                if case.startswith("missing_"):
                    fixture[case.removeprefix("missing_")].unlink()
                elif case.startswith("tampered_"):
                    target = fixture[case.removeprefix("tampered_")]
                    body = target.read_bytes()
                    target.write_bytes(b"X" + body[1:])
                rejected = self.build()
                self.assertEqual(rejected["source_records"], 0)
                self.assertEqual(rejected["unique_searchable_texts"], 0)
                self.assertFalse(index.search(self.output, "quartz", exact=True))
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertTrue(any("trellis_browser_laws/manifest.jsonl:" in item["source_record_locator"] for item in issues))

    def test_browser_law_selector_pairs_follow_the_recorded_archive_version(self):
        cases = (("1.0.1", "law_directory", "div.profileBillingContainer", True),
                 ("1.0.1", "law_text", "div.rule-header", True),
                 ("1.0.0", "law_directory", "div.rule-header", True),
                 ("1.0.1", "law_directory", "div.rule-header", False),
                 ("1.0.1", "law_text", "div.profileBillingContainer", False),
                 ("1.0.0", "law_directory", "div.profileBillingContainer", False))
        for number, (version, kind, selector, accepted) in enumerate(cases):
            with self.subTest(version=version, kind=kind, selector=selector):
                self.output = self.root / ("browser_selector_pair_" + str(number))
                self.browser_law_fixture(payload_changes={"content_kind": kind, "dom_selector": selector}, record_changes={"archive_version": version})
                result = self.build()
                self.assertEqual(result["source_records"], int(accepted))
                self.assertEqual(result["unique_searchable_texts"], int(accepted))
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertEqual(bool(issues), not accepted)

    def test_browser_law_capture_rejects_provenance_claims_and_unlisted_paths(self):
        cases = ({"source_http_status": 200}, {"source_network_requests_by_archiver": 1}, {"cookies_or_credentials_exported": True},
                 {"archive_version": "unreviewed-version"}, {"capture_kind": "provider_rendered_public_page"}, {"dom_selector": "body"},
                 {"signed_in_observed": "account identity"}, {"raw_path": "../../.auth/private.json"}, {"text_path": "../../.auth/private.txt"},
                 {"state_code": "FL"}, {"limitations": []})
        for number, changes in enumerate(cases):
            with self.subTest(changes=changes):
                self.output = self.root / ("browser_provenance_" + str(number))
                self.browser_law_fixture(record_changes=changes)
                rejected = self.build()
                self.assertEqual(rejected["source_records"], 0)
                self.assertEqual(rejected["unique_searchable_texts"], 0)
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertTrue(any(item["kind"] == "browser_law_capture_not_indexed" for item in issues))

    def test_browser_law_capture_rejects_case_links_and_unrelated_raw_fields(self):
        cases = ({"url": "https://trellis.law/case/123/example"}, {"url": "https://trellis.law/state-rules/%2e%2e/case/123"},
                 {"url": "https://trellis.law.example.com/state-rules/az"}, {"url": "https://trellis.law/state-rules/az?token=fixture"},
                 {"observed_law_links": [{"url": "https://trellis.law/case/123/example", "text": "Case preview"}]},
                 {"account_identity": "unrelated fixture identity"}, {"content_kind": "case_text"}, {"dom_selector": "body"})
        for number, changes in enumerate(cases):
            with self.subTest(changes=changes):
                self.output = self.root / ("browser_scope_" + str(number))
                self.browser_law_fixture(payload_changes=changes)
                rejected = self.build()
                self.assertEqual(rejected["source_records"], 0)
                self.assertEqual(rejected["unique_searchable_texts"], 0)
                issues = json.loads((self.output / "indexing_issues.json").read_text(encoding="utf-8"))["issues"]
                self.assertTrue(any(item["kind"] == "browser_law_capture_not_indexed" for item in issues))


if __name__ == "__main__":
    unittest.main()
