"""Offline searchable index of explicitly successful legal-corpus captures."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.4.0"
REQUIRES_VERIFIED_TEXT = {"ocr_derivative", "offline_parser_repair", "document_text_derivative", "browser_rendered_dom"}
OCR_MANIFESTS = (
    ("sources/official_courts/ocr/pdf_ocr_manifest.jsonl", "sources/official_courts/raw", "sources/official_courts/ocr"),
    ("corpus/official_courts/ocr/pdf_ocr_manifest.jsonl", "corpus/official_courts/raw", "corpus/official_courts/ocr"),
    ("sources/official_laws/ocr/pdf_ocr_manifest.jsonl", ["sources/official_laws/raw", "sources/official_laws/documents"], "sources/official_laws/ocr"),
    ("corpus/official_law_recovery_pass_1/ocr/pdf_ocr_manifest.jsonl", "corpus/official_law_recovery_pass_1/raw", "corpus/official_law_recovery_pass_1/ocr"),
    ("corpus/official_law_pages/ocr/pdf_ocr_manifest.jsonl", "corpus/official_law_pages/raw", "corpus/official_law_pages/ocr"),
    ("corpus/county_local_rules_washington_20260914/ocr/pdf_ocr_manifest.jsonl", "corpus/county_local_rules_washington_20260914/raw", "corpus/county_local_rules_washington_20260914/ocr"),
    ("corpus/county_local_rules_washington_20260914/ocr_passes/20260918T162345Z/pdf_ocr_manifest.jsonl", "corpus/county_local_rules_washington_20260914/raw", "corpus/county_local_rules_washington_20260914/ocr_passes/20260918T162345Z"),
)
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS contents(
 id INTEGER PRIMARY KEY, text_sha256 TEXT NOT NULL UNIQUE,
 text TEXT NOT NULL, characters INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
 text,content='contents',content_rowid='id',tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS contents_ai AFTER INSERT ON contents BEGIN
 INSERT INTO content_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE TABLE IF NOT EXISTS versions(
 version_id TEXT PRIMARY KEY, record_key TEXT NOT NULL,
 collection TEXT NOT NULL, source_url TEXT NOT NULL, final_url TEXT,
 jurisdiction TEXT, jurisdictions_json TEXT, category TEXT, categories_json TEXT,
 title TEXT, capture_kind TEXT, content_type TEXT, raw_path TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL, raw_bytes INTEGER NOT NULL,
 text_path TEXT, text_file_sha256 TEXT, indexed_text_sha256 TEXT,
 content_id INTEGER REFERENCES contents(id), retrieved_at TEXT,
 retrieval_time_basis TEXT, extraction_status TEXT, index_text_status TEXT,
 metadata_path TEXT, source_record_locator TEXT,
 source_evidence_json TEXT NOT NULL, indexed_at TEXT NOT NULL,
 raw_hash_verified_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS versions_record ON versions(record_key,retrieved_at);
CREATE INDEX IF NOT EXISTS versions_collection ON versions(collection);
CREATE INDEX IF NOT EXISTS versions_content ON versions(content_id);
CREATE TABLE IF NOT EXISTS records(
 record_key TEXT PRIMARY KEY, collection TEXT NOT NULL, source_url TEXT NOT NULL,
 latest_version_id TEXT NOT NULL REFERENCES versions(version_id), last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_cache(
 path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
 sha256 TEXT NOT NULL, indexed_text_sha256 TEXT, verified_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS input_cache(
 path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
 adapter_version TEXT NOT NULL, normalized_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS builds(
 build_id TEXT PRIMARY KEY, started_at TEXT, completed_at TEXT,
 builder_version TEXT, summary_json TEXT
);
CREATE VIEW IF NOT EXISTS latest_documents AS
 SELECT v.* FROM records r JOIN versions v ON v.version_id=r.latest_version_id;
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_db(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def listify(value):
    return value if isinstance(value, list) else ([] if value is None or value == "" else [value])


def distinct(values):
    result = {}
    for value in values:
        if value is not None and value != "":
            result[stable(value)] = value
    return [result[key] for key in sorted(result)]


def jurisdiction_label(value):
    if isinstance(value, dict):
        ordered = [value.get(key) for key in ("country", "state", "county", "court")]
        return " / ".join(str(part) for part in ordered if part) or stable(value)
    return str(value)


def public_url(value):
    parsed = urlsplit(value or "")
    return bool(parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password)


def success(code):
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def trellis_law_url(value):
    if not isinstance(value, str):
        raise ValueError("A recorded Trellis law URL is required")
    parsed = urlsplit(value)
    decoded_path = unquote(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "trellis.law" or parsed.query or parsed.fragment
            or not (parsed.path == "/state-rules" or parsed.path.startswith("/state-rules/"))
            or "\\" in value or "\\" in decoded_path or any(ord(character) < 32 for character in value)
            or any(part in (".", "..") for part in decoded_path.split("/")) or "//" in decoded_path):
        raise ValueError("Only exact observed HTTPS Trellis state-rule URLs are allowed")
    return value


class Builder:
    def __init__(self, root=ROOT, output=None, rehash=False):
        self.root = Path(root).resolve()
        self.output = Path(output or self.root / "catalog").resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.output / "documents.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.rehash = rehash
        self.stats = Counter()
        self.issues = []
        self.started = now()
        self.indexed_version_ids = set()

    def relative(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path is outside the archive workspace")
        return path.relative_to(self.root).as_posix()

    def allowed_path(self, base, value, allowed, required=True):
        if not value:
            if required:
                raise ValueError("Required saved artifact path is missing")
            return None
        path = (base / value).resolve()
        if not any(path.is_relative_to((self.root / prefix).resolve()) for prefix in allowed):
            raise ValueError("Saved artifact is outside the explicit collection allowlist")
        return path

    def issue(self, locator, kind, detail):
        self.issues.append({"source_record_locator": str(locator), "kind": kind, "detail": str(detail)})
        self.stats["excluded_or_unavailable_records"] += 1

    def cached_input(self, path, transform, lines=False, object_only=False):
        """Cache selected manifest fields, never arbitrary workspace contents."""
        if not path.exists():
            return []
        relative = self.relative(path)
        stat = path.stat()
        cached = self.db.execute("SELECT * FROM input_cache WHERE path=?", (relative,)).fetchone()
        if cached and not self.rehash and cached["size"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns and cached["adapter_version"] == VERSION:
            self.stats["unchanged_manifest_files_skipped"] += 1
            return json.loads(cached["normalized_json"])
        before = stat
        raw = path.read_text(encoding="utf-8-sig")
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("Manifest changed while being read; retry next build")
        values = [json.loads(line) for line in raw.splitlines() if line.strip()] if lines else json.loads(raw)
        if object_only and not isinstance(values, dict):
            raise ValueError("Expected a single saved JSON object")
        values = values if isinstance(values, list) else [values]
        normalized = [transform(value, number) for number, value in enumerate(values, 1)]
        self.db.execute("INSERT OR REPLACE INTO input_cache VALUES(?,?,?,?,?)", (relative, after.st_size, after.st_mtime_ns, VERSION, stable(normalized)))
        self.stats["manifest_files_read"] += 1
        return normalized

    def laws(self):
        base = self.root / "sources/official_laws"
        associations = defaultdict(list)
        for name in ("source_index.json", "document_manifest.json", "document_candidates.json", "supplemental_sources_manifest.json"):
            path = base / "indexes" / name
            def context(value, number):
                return {key: value.get(key) for key in ("official_url", "source_url", "final_url", "jurisdiction", "category", "label")}
            for item in self.cached_input(path, context):
                for url in distinct([item.get("official_url"), item.get("source_url"), item.get("final_url")]):
                    associations[url].append(item)
        for path in sorted((base / "metadata").glob("*.json")):
            def normalize(value, number):
                fields = ("source_url", "final_url", "retrieved_at_utc", "verification_status", "http_status", "evidence_path", "text_path", "sha256", "document_title", "content_type", "kind", "format", "text_characters", "extraction_error")
                return {key: value.get(key) for key in fields}
            try:
                for item in self.cached_input(path, normalize):
                    if item.get("verification_status") != "retrieved" or not success(item.get("http_status")):
                        self.stats["unsuccessful_source_records_skipped"] += 1
                        continue
                    related = associations.get(item.get("source_url"), []) + associations.get(item.get("final_url"), [])
                    yield {"collection": "official_laws", "source_url": item.get("source_url"), "final_url": item.get("final_url"),
                        "jurisdictions": distinct([record.get("jurisdiction") for record in related]),
                        "categories": distinct([record.get("category") for record in related]),
                        "title": item.get("document_title") or next((record.get("label") for record in related if record.get("label")), ""),
                        "raw_path": self.allowed_path(base, item.get("evidence_path"), ["sources/official_laws/raw", "sources/official_laws/documents"]),
                        "text_path": self.allowed_path(base, item.get("text_path"), ["sources/official_laws/text"], False),
                        "raw_sha256": item.get("sha256"), "retrieved_at": item.get("retrieved_at_utc"), "retrieval_time_basis": "source_reported",
                        "content_type": item.get("content_type"), "capture_kind": "direct_public_capture",
                        "extraction_status": "extraction_error" if item.get("extraction_error") else ("extracted" if item.get("text_path") else "no_text_recorded"),
                        "metadata_path": self.relative(path), "source_record_locator": self.relative(path),
                        "evidence": {"verification_status": "retrieved", "http_status": item["http_status"], "kind": item.get("kind"), "format": item.get("format")}}
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                self.issue(self.relative(path), "manifest_error", exc)

    def courts(self):
        base = self.root / "sources/official_courts"
        manifest = base / "manifests/sources.jsonl"
        fields = ("requested_url", "url", "final_url", "jurisdiction", "category", "title", "label", "raw_path", "text_path", "sha256", "fetched_at_utc", "verification_status", "http_status", "content_type", "pdf_text_status", "source_id", "source_authority")
        for item in self.cached_input(manifest, lambda value, number: {**{key: value.get(key) for key in fields}, "line_number": number}, True):
            locator = self.relative(manifest) + ":" + str(item["line_number"])
            if item.get("verification_status") != "retrieved" or not success(item.get("http_status")):
                self.stats["unsuccessful_source_records_skipped"] += 1
                continue
            try:
                yield {"collection": "official_courts", "source_url": item.get("requested_url") or item.get("url"), "final_url": item.get("final_url"),
                    "jurisdictions": listify(item.get("jurisdiction")), "categories": listify(item.get("category")), "title": item.get("title") or item.get("label") or "",
                    "raw_path": self.allowed_path(base, item.get("raw_path"), ["sources/official_courts/raw"]),
                    "text_path": self.allowed_path(base, item.get("text_path"), ["sources/official_courts/text"], False),
                    "raw_sha256": item.get("sha256"), "retrieved_at": item.get("fetched_at_utc"), "retrieval_time_basis": "source_reported",
                    "content_type": item.get("content_type"), "capture_kind": "direct_public_capture",
                    "extraction_status": item.get("pdf_text_status") or ("extracted" if item.get("text_path") else "no_text_recorded"),
                    "metadata_path": self.relative(manifest), "source_record_locator": locator,
                    "evidence": {"source_id": item.get("source_id"), "verification_status": "retrieved", "http_status": item["http_status"], "source_authority": item.get("source_authority")}}
            except (OSError, ValueError, TypeError) as exc:
                self.issue(locator, "invalid_artifact_reference", exc)

    def generic(self):
        for database in sorted((self.root / "corpus").glob("*/corpus.sqlite3")):
            base = database.parent
            base_relative = self.relative(base)
            with closing(read_db(database)) as source:
                source.execute("BEGIN")
                rows = [dict(row) for row in source.execute("SELECT r.*,f.fetched_at FROM resources r LEFT JOIN fetches f ON f.id=r.last_fetch_id WHERE r.status='downloaded' AND r.raw_complete=1").fetchall()]
                contexts = defaultdict(list)
                for row in source.execute("SELECT rc.resource_id,c.jurisdiction_json,c.category,c.source_family FROM resource_contexts rc JOIN contexts c ON c.id=rc.context_id"):
                    contexts[row["resource_id"]].append(dict(row))
                source.rollback()
            for item in rows:
                locator = self.relative(database) + "#resources/" + str(item["id"])
                if not success(item.get("last_http_status")):
                    self.stats["unsuccessful_source_records_skipped"] += 1
                    continue
                try:
                    related = contexts[item["id"]]
                    yield {"collection": "corpus/" + base.name, "source_url": item.get("url"), "final_url": item.get("url"),
                        "jurisdictions": distinct([json.loads(record["jurisdiction_json"]) for record in related]), "categories": distinct([record["category"] for record in related]),
                        "title": item.get("title") or "", "raw_path": self.allowed_path(base, item.get("raw_path"), [base_relative + "/raw"]),
                        "text_path": self.allowed_path(base, item.get("text_path"), [base_relative + "/text"], False),
                        "raw_sha256": item.get("sha256"), "retrieved_at": item.get("fetched_at"), "retrieval_time_basis": "source_reported",
                        "content_type": None, "capture_kind": "direct_public_capture", "extraction_status": item.get("extraction_status"),
                        "metadata_path": self.relative(self.allowed_path(base, item.get("metadata_path"), [base_relative + "/metadata"])), "source_record_locator": locator,
                        "evidence": {"status": "downloaded", "raw_complete": True, "http_status": item["last_http_status"], "resource_id": item["id"], "fetch_id": item.get("last_fetch_id"), "source_families": distinct([record["source_family"] for record in related])}}
                except (OSError, ValueError, TypeError) as exc:
                    self.issue(locator, "invalid_artifact_reference", exc)

    def trellis_browser(self):
        manifest = self.root / "sources/trellis/browser/downloads_manifest.json"
        fields = ("url", "case_url", "jurisdiction", "county", "kind", "case_number", "document_id", "status", "sha256", "raw_path", "text_path", "archived_at")
        for item in self.cached_input(manifest, lambda value, number: {**{key: value.get(key) for key in fields}, "item_number": number}):
            if item.get("status") != "downloaded":
                self.stats["unsuccessful_source_records_skipped"] += 1
                continue
            locator = self.relative(manifest) + "#" + str(item["item_number"])
            try:
                raw = self.allowed_path(self.root, item.get("raw_path"), ["sources/trellis/browser"])
                text = self.allowed_path(self.root, item.get("text_path"), ["sources/trellis/browser"], False)
                if raw.suffix.lower() != ".pdf" or (text and text.suffix.lower() != ".txt"):
                    raise ValueError("Unexpected browser document artifact type")
                yield {"collection": "trellis_browser", "source_url": item.get("url"), "final_url": item.get("url"),
                    "jurisdictions": [{"state": item.get("jurisdiction"), "county": item.get("county")}], "categories": listify(item.get("kind")),
                    "title": " ".join(part for part in [item.get("case_number"), item.get("kind"), item.get("document_id")] if part),
                    "raw_path": raw, "text_path": text, "raw_sha256": item.get("sha256"), "retrieved_at": item.get("archived_at"), "retrieval_time_basis": "archived_at",
                    "content_type": "application/pdf", "capture_kind": "authenticated_browser_download", "extraction_status": "extracted" if text else "no_text_recorded",
                    "metadata_path": self.relative(manifest), "source_record_locator": locator,
                    "evidence": {"status": "downloaded", "case_url": item.get("case_url"), "case_number": item.get("case_number"), "document_id": item.get("document_id")}}
            except (OSError, ValueError, TypeError) as exc:
                self.issue(locator, "invalid_artifact_reference", exc)

    def trellis_browser_laws(self):
        """Selected browser law DOM captures, with no original-HTTP claim."""
        base = self.root / "corpus/trellis_browser_laws"
        manifest = base / "manifest.jsonl"
        prefix = "corpus/trellis_browser_laws"
        fields = ("status", "capture_kind", "source_url", "title", "heading", "state_code", "category", "content_kind",
                  "captured_at", "archived_at", "archive_version", "dom_selector", "signed_in_observed", "content_sha256",
                  "raw_path", "raw_sha256", "raw_bytes", "text_path", "text_sha256", "text_bytes", "observed_law_links",
                  "source_http_status", "source_network_requests_by_archiver", "cookies_or_credentials_exported", "limitations")

        def normalized(value, number):
            if not isinstance(value, dict):
                raise ValueError("Browser-law manifest records must be objects")
            return {**{key: value.get(key) for key in fields}, "line_number": number, "record_fields": sorted(value)}

        def raw_proof(value, number):
            allowed = {"url", "title", "heading", "legal_text", "legal_html", "observed_law_links",
                       "captured_at", "content_kind", "dom_selector", "signed_in_observed"}
            if not isinstance(value, dict) or set(value) - allowed or not (allowed - {"observed_law_links"}).issubset(value):
                raise ValueError("Raw browser-law capture contains unsupported or missing fields")
            trellis_law_url(value["url"])
            for name in ("title", "heading", "legal_text", "legal_html", "captured_at", "dom_selector"):
                if not isinstance(value[name], str) or not value[name].strip():
                    raise ValueError("Raw browser-law capture has no nonempty " + name)
            allowed_selectors = {"law_text": {"div.rule-header"}, "law_directory": {"div.rule-header", "div.profileBillingContainer"}}
            if value["content_kind"] not in allowed_selectors or value["dom_selector"] not in allowed_selectors[value["content_kind"]]:
                raise ValueError("Raw capture is not the reviewed legal DOM scope")
            if value["content_kind"] == "law_text" and len(value["legal_text"].strip()) < 30:
                raise ValueError("Raw law-text capture is too short for the archive contract")
            if type(value["signed_in_observed"]) is not bool:
                raise ValueError("Raw capture must record sign-in state without account identity")
            captured = datetime.fromisoformat(value["captured_at"].replace("Z", "+00:00"))
            if captured.tzinfo is None:
                raise ValueError("Browser capture time must include a time zone")
            links = value.get("observed_law_links", [])
            if not isinstance(links, list) or len(links) > 10000:
                raise ValueError("Invalid observed browser-law link list")
            for link in links:
                if not isinstance(link, dict) or set(link) != {"url", "text"} or not isinstance(link["text"], str):
                    raise ValueError("Invalid observed browser-law link record")
                trellis_law_url(link["url"])
            # Archive v1.0.x uses JSON's default separators, unlike stable().
            content = json.dumps({key: val for key, val in value.items() if key != "captured_at"}, ensure_ascii=False, sort_keys=True).encode("utf-8")
            text_bytes = value["legal_text"].encode("utf-8")
            return {**{key: value[key] for key in ("url", "title", "heading", "captured_at", "content_kind", "dom_selector", "signed_in_observed")},
                    "observed_law_links": links, "content_sha256": sha(content), "legal_text_sha256": sha(text_bytes), "legal_text_bytes": len(text_bytes)}

        for item in self.cached_input(manifest, normalized, True):
            locator = self.relative(manifest) + ":" + str(item["line_number"])
            if item.get("status") != "captured":
                self.stats["unsuccessful_browser_law_records_skipped"] += 1
                continue
            try:
                if set(item["record_fields"]) != set(fields) or item.get("archive_version") not in ("1.0.0", "1.0.1"):
                    raise ValueError("Unknown or incomplete browser-law archive schema/version")
                if item.get("capture_kind") != "browser_rendered_dom" or item.get("category") != "state_rule":
                    raise ValueError("The manifest is not a browser-rendered state-rule capture")
                selector_pairs = {("law_text", "div.rule-header"), ("law_directory", "div.rule-header")}
                if item["archive_version"] == "1.0.1":
                    selector_pairs = {("law_text", "div.rule-header"), ("law_directory", "div.profileBillingContainer")}
                if (item["content_kind"], item["dom_selector"]) not in selector_pairs:
                    raise ValueError("The legal content kind and selector do not match the recorded archive version")
                if item["source_http_status"] is not None or type(item["source_network_requests_by_archiver"]) is not int or item["source_network_requests_by_archiver"] != 0 or item["cookies_or_credentials_exported"] is not False:
                    raise ValueError("Browser provenance must record no source-HTTP assertion, archiver source request, or exported credentials")
                if not isinstance(item["limitations"], list) or not item["limitations"] or not all(isinstance(value, str) and value.strip() for value in item["limitations"]):
                    raise ValueError("Browser capture limitations must be recorded")
                source_url = trellis_law_url(item["source_url"])
                path_parts = urlsplit(source_url).path.split("/")
                state_code = path_parts[2].upper() if len(path_parts) > 2 else None
                if item["state_code"] != state_code:
                    raise ValueError("Recorded browser jurisdiction does not match the observed URL")
                if not isinstance(item["archived_at"], str) or datetime.fromisoformat(item["archived_at"].replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("Browser archive time must include a time zone")
                hashes = {}
                for name in ("raw_sha256", "text_sha256", "content_sha256"):
                    value = item[name]
                    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
                        raise ValueError("A valid browser capture " + name + " is required")
                    hashes[name] = value.lower()
                raw = self.allowed_path(base, item["raw_path"], [prefix + "/raw"])
                text = self.allowed_path(base, item["text_path"], [prefix + "/text"])
                if item["raw_path"] != "raw/" + hashes["raw_sha256"][:2] + "/" + hashes["raw_sha256"] + ".json" or item["text_path"] != "text/" + hashes["text_sha256"][:2] + "/" + hashes["text_sha256"] + ".txt":
                    raise ValueError("Browser artifacts do not use their recorded content-addressed paths")
                raw_artifact = self.artifact(raw, expected_sha=hashes["raw_sha256"])
                if type(item["raw_bytes"]) is not int or item["raw_bytes"] != raw_artifact["size"] or type(item["text_bytes"]) is not int or item["text_bytes"] != text.stat().st_size:
                    raise ValueError("Browser artifact sizes differ from recorded provenance")
                proofs = self.cached_input(raw, raw_proof, object_only=True)
                if len(proofs) != 1:
                    raise ValueError("Expected one raw browser-law capture object")
                proof = proofs[0]
                if proof["url"] != source_url:
                    raise ValueError("The raw browser capture URL differs from the manifest source URL")
                for name in ("title", "heading", "captured_at", "content_kind", "dom_selector", "signed_in_observed", "observed_law_links"):
                    if item[name] != proof[name] or (name == "signed_in_observed" and type(item[name]) is not bool):
                        raise ValueError("The raw browser capture and manifest disagree on " + name)
                if proof["content_sha256"] != hashes["content_sha256"]:
                    raise ValueError("Browser content hash does not match the timestamp-excluded raw capture")
                if proof["legal_text_sha256"] != hashes["text_sha256"] or proof["legal_text_bytes"] != item["text_bytes"]:
                    raise ValueError("The recorded text does not equal raw legal_text")
                yield {"collection": prefix, "source_url": source_url, "final_url": source_url,
                    "jurisdictions": [{"country": "US", "state": state_code}] if state_code else [{"country": "US"}],
                    "categories": ["state_rule", item["content_kind"]], "title": item["title"],
                    "raw_path": raw, "text_path": text, "raw_sha256": hashes["raw_sha256"], "expected_text_file_sha256": hashes["text_sha256"],
                    "retrieved_at": item["captured_at"], "retrieval_time_basis": "browser_dom_captured_at",
                    "content_type": "application/json", "capture_kind": "browser_rendered_dom", "extraction_status": "selected_legal_dom_text",
                    "metadata_path": self.relative(manifest), "source_record_locator": locator,
                    "evidence": {**{key: item[key] for key in ("status", "capture_kind", "archive_version", "heading", "state_code", "content_kind",
                        "captured_at", "archived_at", "dom_selector", "signed_in_observed", "content_sha256", "source_http_status",
                        "source_network_requests_by_archiver", "cookies_or_credentials_exported", "observed_law_links", "limitations")},
                        "text_equals_raw_legal_text": True, "content_sha256_verified": True,
                        "raw_representation": "selected browser legal DOM JSON; original HTTP response not captured"}}
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                self.issue(locator, "browser_law_capture_not_indexed", exc)

    def trellis_public(self):
        database = self.root / "sources/trellis/catalog/catalog.sqlite3"
        if not database.exists():
            return
        allowed = {"coverage_state", "coverage_county", "coverage_index", "state_rule", "judge_directory", "judge_profile", "case", "filing", "motion_dictionary", "motion"}
        with closing(read_db(database)) as source:
            rows = [dict(row) for row in source.execute("SELECT * FROM pages WHERE status BETWEEN 200 AND 299").fetchall()]
        for item in rows:
            category = item.get("category")
            if category not in allowed and urlsplit(item["url"]).path.rstrip("/") == "/ca/motion-type":
                category = "motion"
            if category not in allowed:
                self.stats["nonlegal_public_pages_skipped"] += 1
                continue
            locator = self.relative(database) + "#pages/" + item["url"]
            try:
                raw = self.allowed_path(self.root, item.get("source_path"), ["sources/trellis"])
                if not raw.name.endswith(".firecrawl.json") or "/browser/" in raw.as_posix():
                    raise ValueError("Unexpected public-provider capture path")
                yield {"collection": "trellis_public", "source_url": item["url"], "final_url": item["url"],
                    "jurisdictions": listify(item.get("jurisdiction")), "categories": [category], "title": item.get("title") or "",
                    "raw_path": raw, "text_path": self.allowed_path(self.root, item.get("markdown_path"), ["sources/trellis/catalog/extracted"], False),
                    "raw_sha256": item.get("content_sha256"), "retrieved_at": item.get("observed_at"), "retrieval_time_basis": "provider_capture_file_mtime",
                    "content_type": "application/json", "capture_kind": "provider_rendered_public_page", "extraction_status": "provider_markdown",
                    "metadata_path": self.relative(database), "source_record_locator": locator,
                    "evidence": {"http_status": item["status"], "provider": item.get("provider"), "scrape_id": item.get("scrape_id"), "sha256_scope": "saved provider response; not original HTTP payload"}}
            except (OSError, ValueError, TypeError) as exc:
                self.issue(locator, "invalid_artifact_reference", exc)

    def ocr_derivatives(self):
        manifests = OCR_MANIFESTS
        fields = ("source_id", "source_url", "source_pdf_path", "source_pdf_sha256", "pdf_pages", "ocr_status", "ocr_scope", "ocr_pages_required", "ocr_pages_completed", "ocr_text_path", "ocr_text_sha256", "ocr_engine", "language_model_sha256", "mean_page_confidence", "pages_below_70_confidence", "page_coverage_path")
        for manifest_name, raw_prefix, text_prefix in manifests:
            manifest = self.root / manifest_name
            for item in self.cached_input(manifest, lambda value, number: {**{key: value.get(key) for key in fields}, "item_number": number}, True):
                if item.get("ocr_status") != "complete":
                    self.stats["incomplete_or_unneeded_ocr_records_skipped"] += 1
                    continue
                locator = manifest_name + ":" + str(item["item_number"])
                try:
                    required = item.get("ocr_pages_required") if item.get("ocr_pages_required") is not None else item.get("pdf_pages")
                    if not required or item.get("ocr_pages_completed") != required:
                        raise ValueError("OCR completion and required-page counts disagree")
                    raw = self.allowed_path(self.root, item.get("source_pdf_path"), listify(raw_prefix))
                    if raw.suffix.lower() != ".pdf":
                        raise ValueError("OCR parent capture is not a saved PDF")
                    text = self.allowed_path(self.root, item.get("ocr_text_path"), [text_prefix])
                    if text.name != "combined_ocr.txt":
                        raise ValueError("OCR text is not the explicitly recorded combined derivative")
                    parents = [dict(row) for row in self.db.execute(
                        "SELECT * FROM latest_documents WHERE raw_path=? AND raw_sha256=? AND capture_kind<>'ocr_derivative'",
                        (self.relative(raw), item.get("source_pdf_sha256"))).fetchall()]
                    if not parents:
                        raise ValueError("No successful allowed parent capture matches this PDF path and SHA-256")
                    for parent in parents:
                        yield {"collection": parent["collection"] + "/ocr", "source_url": parent["source_url"], "final_url": parent["final_url"],
                            "jurisdictions": json.loads(parent["jurisdictions_json"]), "categories": distinct(json.loads(parent["categories_json"]) + ["ocr_text"]),
                            "title": parent["title"] + " [OCR]", "raw_path": raw, "text_path": text,
                            "raw_sha256": item["source_pdf_sha256"], "expected_text_file_sha256": item.get("ocr_text_sha256"),
                            "retrieved_at": parent["retrieved_at"], "retrieval_time_basis": parent["retrieval_time_basis"],
                            "content_type": "application/pdf", "capture_kind": "ocr_derivative", "extraction_status": "ocr_complete_for_required_pages",
                            "metadata_path": manifest_name, "source_record_locator": locator,
                            "evidence": {"parent_version_id": parent["version_id"], "source_pdf_sha256": item["source_pdf_sha256"],
                                "ocr_source_id": item.get("source_id"), "ocr_manifest_source_url": item.get("source_url"),
                                "ocr_text_sha256": item.get("ocr_text_sha256"), "ocr_status": "complete", "pdf_pages": item.get("pdf_pages"),
                                "ocr_scope": item.get("ocr_scope") or "all_pages", "ocr_pages_required": required, "ocr_pages_completed": item["ocr_pages_completed"],
                                "ocr_engine": item.get("ocr_engine"), "language_model_sha256": item.get("language_model_sha256"),
                                "mean_page_confidence": item.get("mean_page_confidence"), "pages_below_70_confidence": item.get("pages_below_70_confidence"),
                                "page_coverage_path": item.get("page_coverage_path"), "original_pdf_and_embedded_text_preserved": True}}
                except (OSError, ValueError, TypeError) as exc:
                    self.issue(locator, "ocr_derivative_not_indexed", exc)

    def document_text_derivatives(self):
        """Index only explicit, parent-linked document-text manifests."""
        families = (
            ("corpus/official_law_pages", "document_derivatives", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "DOCX", "word/document.xml"),
            ("corpus/official_law_pages", "xml_document_derivatives", "application/msword", "Word 2003 XML", "[direct-xml]/w:wordDocument/w:body"),
            ("corpus/official_law_pages", "epub_document_derivatives", "application/octet-stream", "EPUB", "META-INF/container.xml"),
            ("corpus/official_law_resume_quick_20260914", "offline_docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "DOCX", "word/document.xml"),
            ("corpus/official_law_resume_quick_20260914", "offline_docx_pass2", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "DOCX", "word/document.xml"),
        )
        for collection, directory, declared_type, label, required_part in families:
            try:
                yield from self.document_text_manifest(directory, declared_type, label, required_part, collection=collection)
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                self.issue(collection + "/" + directory + "/manifest.jsonl", "document_derivative_manifest_error", exc)

    def document_text_manifest(self, directory, declared_type, label, required_part, collection="corpus/official_law_pages"):
        derivative_root = collection + "/" + directory
        manifest_name = derivative_root + "/manifest.jsonl"
        manifest = self.root / manifest_name
        wordml_namespace = "http://schemas.microsoft.com/office/word/2003/wordml"
        wordml_root = "{" + wordml_namespace + "}wordDocument"
        fields = ("resource_id", "source_url", "parent_raw_path", "parent_raw_sha256", "parent_byte_count",
                  "parent_metadata_path", "parent_metadata_sha256", "parent_fetch_id", "parent_retrieved_at",
                  "parent_extraction_status", "parent_collector_text_path", "source_contexts", "snapshot_at_utc",
                  "parent_sha256_verified", "declared_format", "status", "extractor_version", "extraction_method",
                  "text_path", "text_sha256", "text_bytes", "paragraph_text_characters", "text_paragraphs",
                  "package_parts", "macro_parts_present_not_read_or_executed", "unexpanded_altchunk_elements",
                  "automatic_numbering_paragraphs_not_reconstructed", "tracked_deleted_characters_excluded",
                  "derived_at_utc", "network_requests", "originals_modified", "limitations",
                  "detected_format", "xml_root_qname", "wordprocessingml_namespace", "container_mimetype",
                  "package_document", "opf_version", "opf_metadata", "spine_items", "extracted_spine_items",
                  "parser_library", "lxml_version", "libxml_version", "xml_security", "source_document_title",
                  "source_document_description", "literal_text_conservation_verified", "literal_wt_sha256", "literal_wt_sha256_basis",
                  "literal_wt_characters", "paragraph_coverage_verified", "nonempty_text_paragraphs", "text_characters",
                  "table_count", "table_rows", "table_cells", "merged_cell_span_elements_not_visually_reconstructed")
        def normalized(value, number):
            if not isinstance(value, dict):
                raise ValueError("Document derivative manifest records must be objects")
            return {**{key: value.get(key) for key in fields}, "line_number": number}
        for item in self.cached_input(manifest, normalized, True):
            locator = manifest_name + ":" + str(item["line_number"])
            if item.get("status") != "extracted":
                self.stats["incomplete_document_text_derivatives_skipped"] += 1
                continue
            try:
                if item.get("declared_format") != declared_type:
                    raise ValueError("The declared format does not match this explicit document derivative family")
                if item.get("parent_sha256_verified") is not True or item.get("network_requests") != 0 or item.get("originals_modified") is not False:
                    raise ValueError("The derivative does not record verified, offline extraction with preserved originals")
                for name in ("parent_raw_sha256", "parent_metadata_sha256", "text_sha256"):
                    digest = item.get(name)
                    if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
                        raise ValueError("A valid recorded " + name + " is required")
                for name in ("extractor_version", "extraction_method"):
                    if not isinstance(item.get(name), str) or not item[name].strip():
                        raise ValueError("The derivative has no recorded " + name)
                if not isinstance(item.get("limitations"), list) or not item["limitations"] or not all(isinstance(value, str) for value in item["limitations"]):
                    raise ValueError("Recorded extraction limitations are required")
                parts = item.get("package_parts")
                if not isinstance(parts, list) or not parts or not all(isinstance(part, dict) for part in parts):
                    raise ValueError("Recorded document-part provenance is required")
                if not any(part.get("package_part") == required_part for part in parts):
                    raise ValueError("The derivative has no main-document part provenance")
                for part in parts:
                    digest = part.get("xml_sha256") or (part.get("sha256") if directory == "epub_document_derivatives" else None)
                    if not isinstance(part.get("package_part"), str) or not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
                        raise ValueError("Package-part name or SHA-256 provenance is invalid")
                if directory == "xml_document_derivatives":
                    if item.get("detected_format") != "wordprocessingml_2003_xml" or item.get("xml_root_qname") != wordml_root or item.get("wordprocessingml_namespace") != wordml_namespace:
                        raise ValueError("The XML derivative does not identify the supported Word 2003 XML root and namespace")
                    if len(parts) != 1 or parts[0].get("xml_sha256") != item["parent_raw_sha256"] or parts[0].get("xml_bytes_read") != item.get("parent_byte_count"):
                        raise ValueError("Direct XML part hash or byte count does not match the preserved parent payload")
                    if parts[0].get("xml_root_qname") != wordml_root or parts[0].get("wordprocessingml_namespace") != wordml_namespace:
                        raise ValueError("Direct XML part root and namespace provenance disagree")
                if directory == "epub_document_derivatives":
                    if item.get("source_url") != "https://www.legis.iowa.gov/docs/IACODE/IowaCodeWithActs.epub":
                        raise ValueError("The EPUB manifest is restricted to the explicitly verified Iowa code book")
                    if item.get("detected_format") != "epub" or item.get("container_mimetype") != "application/epub+zip":
                        raise ValueError("The EPUB derivative does not record the verified container format")
                    package_document = item.get("package_document")
                    if not isinstance(package_document, str) or not package_document.endswith(".opf"):
                        raise ValueError("A recorded EPUB package document is required")
                    part_names = [part["package_part"] for part in parts]
                    if not {"mimetype", "META-INF/container.xml", package_document}.issubset(part_names):
                        raise ValueError("EPUB mimetype, container and OPF part provenance is required")
                    if any(name.startswith("/") or "\\" in name or ":" in name or ".." in name.split("/") for name in part_names):
                        raise ValueError("An EPUB package-part name is outside the archive namespace")
                    spine_count = item.get("spine_items")
                    if type(spine_count) is not int or spine_count < 1 or type(item.get("extracted_spine_items")) is not int or item["extracted_spine_items"] != spine_count:
                        raise ValueError("The EPUB derivative does not record complete extraction of its declared spine")
                    spine = [part for part in parts if part.get("role") == "spine"]
                    if len(spine) != spine_count or any(type(part.get("spine_index")) is not int for part in spine):
                        raise ValueError("EPUB spine part provenance does not match the declared count")
                    if sorted(part["spine_index"] for part in spine) != list(range(1, spine_count + 1)):
                        raise ValueError("EPUB spine indexes are not a complete ordered sequence")
                    if any(part.get("status") != "extracted" or not isinstance(part.get("idref"), str) or not part["idref"] for part in spine):
                        raise ValueError("An EPUB spine item lacks successful extraction or OPF identity provenance")
                raw = self.allowed_path(self.root, item.get("parent_raw_path"), [collection + "/raw"])
                text = self.allowed_path(self.root, item.get("text_path"), [derivative_root + "/text"])
                metadata = self.allowed_path(self.root, item.get("parent_metadata_path"), [collection + "/metadata"])
                if text.name != item["parent_raw_sha256"].lower() + ".txt":
                    raise ValueError("The text path is not the explicitly named parent-hash derivative")
                if text.stat().st_size != item.get("text_bytes"):
                    raise ValueError("The derivative text size differs from its recorded provenance")
                parents = [dict(row) for row in self.db.execute(
                    "SELECT * FROM latest_documents WHERE collection=? AND source_url=? AND raw_path=? AND raw_sha256=? AND capture_kind='direct_public_capture'",
                    (collection, item.get("source_url"), self.relative(raw), item["parent_raw_sha256"].lower())).fetchall()
                    if row["version_id"] in self.indexed_version_ids]
                if len(parents) != 1:
                    raise ValueError("No successful parent validated in this build matches the source URL, raw path and SHA-256")
                parent = parents[0]
                parent_evidence = json.loads(parent["source_evidence_json"])
                if parent["metadata_path"] != self.relative(metadata) or parent["raw_bytes"] != item.get("parent_byte_count"):
                    raise ValueError("Parent metadata path or payload size differs from its recorded provenance")
                if parent_evidence.get("resource_id") != item.get("resource_id") or parent_evidence.get("fetch_id") != item.get("parent_fetch_id"):
                    raise ValueError("Parent resource or fetch identity differs from the recorded extraction input")
                if parent["extraction_status"] != item.get("parent_extraction_status") or parent["retrieved_at"] != item.get("parent_retrieved_at"):
                    raise ValueError("Parent extraction status or retrieval time differs from the recorded extraction input")
                self.artifact(metadata, expected_sha=item["parent_metadata_sha256"].lower())
                metadata_fields = ("http_status", "raw_complete", "sha256", "detected_type", "requested_url", "fetch_id")
                originals = self.cached_input(metadata, lambda value, number: {key: value.get(key) for key in metadata_fields} if isinstance(value, dict) else {})
                if len(originals) != 1:
                    raise ValueError("Expected exactly one recorded parent metadata object")
                original = originals[0]
                if not success(original.get("http_status")) or original.get("raw_complete") is not True or original.get("sha256") != parent["raw_sha256"]:
                    raise ValueError("Parent metadata does not record a complete successful capture with the matching SHA-256")
                if original.get("detected_type") != declared_type or original.get("requested_url") != parent["source_url"] or original.get("fetch_id") != item["parent_fetch_id"]:
                    raise ValueError("Parent metadata format or source/fetch identity does not match the document derivative")
                yield {"collection": derivative_root, "source_url": parent["source_url"], "final_url": parent["final_url"],
                    "jurisdictions": json.loads(parent["jurisdictions_json"]), "categories": distinct(json.loads(parent["categories_json"]) + ["document_text"]),
                    "title": parent["title"] + " [" + label + " text]", "raw_path": raw, "text_path": text,
                    "raw_sha256": parent["raw_sha256"], "expected_text_file_sha256": item["text_sha256"].lower(),
                    "retrieved_at": parent["retrieved_at"], "retrieval_time_basis": parent["retrieval_time_basis"],
                    "content_type": item.get("container_mimetype") or declared_type, "capture_kind": "document_text_derivative", "extraction_status": "document_text_extracted_offline",
                    "metadata_path": manifest_name, "source_record_locator": locator,
                    "evidence": {**{key: item.get(key) for key in fields}, "parent_version_id": parent["version_id"],
                        "parent_source_record_locator": parent["source_record_locator"],
                        "derivative_manifest_record_sha256": sha(stable({key: item.get(key) for key in fields}).encode("utf-8")),
                        "package_part_hash_basis": "recorded by the offline extractor; parent payload and derivative text hashes verified by the index",
                        "original_payload_and_collector_text_preserved": True}}
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                self.issue(locator, "document_text_derivative_not_indexed", exc)

    def parser_repairs(self):
        """The explicit Nebraska repair is verified without legitimizing failures."""
        base = self.root / "sources/official_laws"
        recovery = base / "recovery_pass_1"
        manifest = recovery / "local_repairs.json"
        if not manifest.exists():
            return
        from bs4 import BeautifulSoup, __version__ as bs4_version
        from lxml.etree import LXML_VERSION
        source_url = "https://supremecourt.nebraska.gov/supreme-court-rules"
        triage_path = recovery / "triage.jsonl"
        triage_fields = ("source_url", "decision", "original_metadata_path", "original_metadata_sha256", "original_payload_path", "original_http_status", "original_error_class")
        triage = {item["source_url"]: item for item in self.cached_input(triage_path, lambda value, number: {key: value.get(key) for key in triage_fields}, True)}
        fields = ("source_url", "source_metadata_path", "raw_path", "raw_sha256_verified", "text_path", "text_sha256", "text_characters", "links_path", "link_observations", "skipped_malformed_hrefs", "network_requests_made", "original_metadata_unchanged")
        for item in self.cached_input(manifest, lambda value, number: {**{key: value.get(key) for key in fields}, "item_number": number}):
            locator = self.relative(manifest) + "#" + str(item["item_number"])
            if item.get("source_url") != source_url:
                self.stats["unlisted_local_repairs_skipped"] += 1
                continue
            try:
                assessment = triage.get(source_url, {})
                if assessment.get("decision") != "payload_saved_parser_repaired_locally" or assessment.get("original_error_class") != "ValueError":
                    raise ValueError("The explicit saved-payload parser-repair decision is missing")
                if item.get("network_requests_made") != 0 or item.get("original_metadata_unchanged") is not True:
                    raise ValueError("The repair does not record offline extraction with unchanged original metadata")
                original_metadata = self.allowed_path(self.root, item.get("source_metadata_path"), ["sources/official_laws/metadata"])
                assessed_metadata = self.allowed_path(self.root, assessment.get("original_metadata_path"), ["sources/official_laws/metadata"])
                if original_metadata != assessed_metadata or not assessment.get("original_metadata_sha256"):
                    raise ValueError("Repair metadata path or original metadata hash is missing from triage")
                metadata_integrity = self.artifact(original_metadata, expected_sha=assessment["original_metadata_sha256"])
                metadata_body = original_metadata.read_bytes()
                if sha(metadata_body) != assessment["original_metadata_sha256"]:
                    raise ValueError("Original failure metadata changed while being read")
                original = json.loads(metadata_body)
                if original.get("source_url") != source_url or original.get("verification_status") != "retrieval_error" or not success(original.get("http_status")) or original.get("format") != "html":
                    raise ValueError("The preserved original is not the expected complete HTTP-success HTML/parser-failure record")
                raw = self.allowed_path(self.root, item.get("raw_path"), ["sources/official_laws/raw"])
                recorded_raw = self.allowed_path(base, original.get("evidence_path"), ["sources/official_laws/raw"])
                assessed_raw = self.allowed_path(self.root, assessment.get("original_payload_path"), ["sources/official_laws/raw"])
                if raw != recorded_raw or raw != assessed_raw or raw.suffix.lower() != ".html":
                    raise ValueError("The repair does not identify the original recorded HTML payload")
                expected_raw = item.get("raw_sha256_verified")
                if not expected_raw or expected_raw != original.get("sha256"):
                    raise ValueError("The repair and original HTML SHA-256 provenance disagree")
                raw_body = raw.read_bytes()
                if sha(raw_body) != expected_raw or len(raw_body) != original.get("bytes"):
                    raise ValueError("The saved original HTML hash or byte count does not verify")
                text = self.allowed_path(self.root, item.get("text_path"), ["sources/official_laws/recovery_pass_1/local_repairs"])
                if text.name != "nebraska_supreme_court_rules.txt":
                    raise ValueError("The repaired text path is not the explicit Nebraska derivative")
                text_body = text.read_bytes()
                text_hash = sha(text_body)
                if item.get("text_sha256") and item["text_sha256"] != text_hash:
                    raise ValueError("Repaired text SHA-256 differs from its recorded hash")
                soup = BeautifulSoup(raw_body, "lxml")
                for tag in soup(["script", "style", "noscript", "svg"]):
                    tag.decompose()
                reproduced = soup.get_text("\n", strip=True)
                saved_normalized = text_body.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                if saved_normalized != reproduced or len(reproduced) != item.get("text_characters"):
                    raise ValueError("Repaired text does not reproduce from the verified original HTML")
                manifest_hash = self.artifact(manifest)["sha256"]
                triage_hash = self.artifact(triage_path)["sha256"]
                self.stats["verified_offline_parser_repairs"] += 1
                yield {"collection": "official_laws/parser_repairs", "source_url": source_url, "final_url": original.get("final_url"),
                    "jurisdictions": ["Nebraska"], "categories": ["court_rules"], "title": original.get("document_title") or "Nebraska Supreme Court Rules",
                    "raw_path": raw, "text_path": text, "raw_sha256": expected_raw, "expected_text_file_sha256": text_hash,
                    "retrieved_at": original.get("retrieved_at_utc"), "retrieval_time_basis": "original_saved_HTTP_capture",
                    "content_type": original.get("content_type"), "capture_kind": "offline_parser_repair", "extraction_status": "repaired_offline_verified_against_original_html",
                    "metadata_path": self.relative(manifest), "source_record_locator": locator,
                    "evidence": {"original_metadata_path": self.relative(original_metadata), "original_metadata_sha256": metadata_integrity["sha256"],
                        "original_verification_status": original["verification_status"], "original_error": original.get("error"), "original_http_status": original["http_status"],
                        "original_html_sha256": expected_raw, "original_html_bytes": len(raw_body), "repair_manifest_sha256": manifest_hash,
                        "triage_path": self.relative(triage_path), "triage_sha256": triage_hash, "triage_decision": assessment["decision"],
                        "text_file_sha256_verified": text_hash, "reproduced_text_utf8_sha256": sha(reproduced.encode("utf-8")),
                        "text_hash_provenance": "Exact reproduction from verified original HTML; CRLF/CR normalized to LF only for comparison",
                        "text_characters": len(reproduced), "parser": "BeautifulSoup " + bs4_version + " / lxml " + ".".join(map(str, LXML_VERSION)),
                        "extraction_method": "Remove script/style/noscript/svg; get_text(newline, strip=True)",
                        "links_path": item.get("links_path"), "link_observations": item.get("link_observations"),
                        "skipped_malformed_href_count": len(item.get("skipped_malformed_hrefs") or []), "original_failure_and_payload_preserved": True, "network_requests": 0}}
            except (OSError, ValueError, TypeError) as exc:
                self.issue(locator, "parser_repair_not_indexed", exc)

    def artifact(self, path, text=False, expected_sha=None):
        relative = self.relative(path)
        stat = path.stat()
        cached = self.db.execute("SELECT * FROM file_cache WHERE path=?", (relative,)).fetchone()
        if cached and not self.rehash and cached["size"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns:
            if not text or cached["indexed_text_sha256"] is not None:
                if expected_sha is not None and cached["sha256"] != expected_sha:
                    raise ValueError("Saved artifact SHA-256 does not match recorded provenance")
                self.stats["unchanged_artifact_files_skipped"] += 1
                return dict(cached)
        before = stat
        if text:
            body = path.read_bytes()
            actual_sha = sha(body)
        else:
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    hasher.update(chunk)
            actual_sha = hasher.hexdigest()
        if expected_sha is not None and actual_sha != expected_sha:
            raise ValueError("Saved artifact SHA-256 does not match recorded provenance")
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("Saved artifact changed while hashing; retry next build")
        text_sha = None
        if text:
            content = body.decode("utf-8-sig", errors="strict")
            text_sha = sha(content.encode("utf-8"))
            if content.strip():
                cursor = self.db.execute("INSERT OR IGNORE INTO contents(text_sha256,text,characters) VALUES(?,?,?)", (text_sha, content, len(content)))
                self.stats["new_unique_texts"] += cursor.rowcount
        record = {"path": relative, "size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": actual_sha, "indexed_text_sha256": text_sha, "verified_at": now()}
        self.db.execute("INSERT OR REPLACE INTO file_cache VALUES(:path,:size,:mtime_ns,:sha256,:indexed_text_sha256,:verified_at)", record)
        self.stats["artifact_files_hashed"] += 1
        return record

    def ingest(self, item):
        self.stats["successful_candidates"] += 1
        locator = item["source_record_locator"]
        try:
            if not public_url(item.get("source_url")):
                raise ValueError("Source URL is missing or contains authentication userinfo")
            expected = item.get("raw_sha256") or ""
            if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected.lower()):
                raise ValueError("A valid recorded payload SHA-256 is required")
            raw = self.artifact(item["raw_path"])
            if raw["sha256"] != expected.lower():
                raise ValueError("Saved raw payload SHA-256 does not match source metadata")
            text = None
            text_status = "no_text_recorded"
            if item.get("text_path"):
                if item["text_path"].is_file():
                    if item["capture_kind"] in REQUIRES_VERIFIED_TEXT and not item.get("expected_text_file_sha256"):
                        raise ValueError("Verified text capture has no recorded text SHA-256")
                    text = self.artifact(item["text_path"], True, item.get("expected_text_file_sha256"))
                    text_status = "searchable" if self.db.execute("SELECT 1 FROM contents WHERE text_sha256=?", (text["indexed_text_sha256"],)).fetchone() else "empty_text"
                else:
                    if item["capture_kind"] in REQUIRES_VERIFIED_TEXT:
                        raise ValueError("Verified text capture file is missing")
                    text_status = "recorded_text_file_missing"
                    self.issue(locator, "text_file_missing", self.relative(item["text_path"]))
            contents = self.db.execute("SELECT id FROM contents WHERE text_sha256=?", (text["indexed_text_sha256"],)).fetchone() if text else None
            jurisdictions = distinct(item.get("jurisdictions", []))
            categories = distinct(item.get("categories", []))
            record_key = sha(stable([item["collection"], item["source_url"], item["capture_kind"]]).encode("utf-8"))
            version = {"record_key": record_key, "collection": item["collection"], "source_url": item["source_url"], "final_url": item.get("final_url"),
                "jurisdiction": "; ".join(jurisdiction_label(value) for value in jurisdictions), "jurisdictions_json": stable(jurisdictions),
                "category": "; ".join(str(value) for value in categories), "categories_json": stable(categories), "title": item.get("title") or "",
                "capture_kind": item["capture_kind"], "content_type": item.get("content_type"), "raw_path": raw["path"], "raw_sha256": raw["sha256"], "raw_bytes": raw["size"],
                "text_path": self.relative(item["text_path"]) if item.get("text_path") else None, "text_file_sha256": text["sha256"] if text else None,
                "indexed_text_sha256": text["indexed_text_sha256"] if text else None, "content_id": contents["id"] if contents else None,
                "retrieved_at": item.get("retrieved_at"), "retrieval_time_basis": item.get("retrieval_time_basis"), "extraction_status": item.get("extraction_status"), "index_text_status": text_status,
                "metadata_path": item.get("metadata_path"), "source_record_locator": locator, "source_evidence_json": stable(item.get("evidence", {}))}
            version_id = sha(stable(version).encode("utf-8"))
            version.update(version_id=version_id, indexed_at=now(), raw_hash_verified_at=raw["verified_at"])
            columns = list(version)
            cursor = self.db.execute("INSERT OR IGNORE INTO versions(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")", [version[column] for column in columns])
            self.stats["new_capture_versions"] += cursor.rowcount
            self.stats["unchanged_capture_versions"] += not cursor.rowcount
            current = self.db.execute("SELECT v.retrieved_at,v.indexed_at FROM records r JOIN versions v ON v.version_id=r.latest_version_id WHERE r.record_key=?", (record_key,)).fetchone()
            if not current or (version.get("retrieved_at") or "", version["indexed_at"]) >= (current["retrieved_at"] or "", current["indexed_at"]):
                self.db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)", (record_key, item["collection"], item["source_url"], version_id, now()))
            self.stats["indexed_candidates"] += 1
            self.indexed_version_ids.add(version_id)
        except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
            self.issue(locator, "capture_not_indexed", exc)

    def export(self):
        for filename, query in (("documents", "SELECT * FROM latest_documents ORDER BY collection,source_url"), ("document_versions", "SELECT * FROM versions ORDER BY collection,source_url,retrieved_at,indexed_at")):
            json_path = self.output / (filename + ".jsonl")
            csv_path = self.output / (filename + ".csv")
            temporary_json = json_path.with_suffix(".jsonl.tmp")
            temporary_csv = csv_path.with_suffix(".csv.tmp")
            with temporary_json.open("w", encoding="utf-8") as json_handle, temporary_csv.open("w", encoding="utf-8-sig", newline="") as csv_handle:
                cursor = self.db.execute(query)
                columns = [column[0] for column in cursor.description]
                writer = csv.DictWriter(csv_handle, fieldnames=columns)
                writer.writeheader()
                for row in cursor:
                    record = dict(row)
                    json_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    writer.writerow({key: ("'" + str(value) if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value) for key, value in record.items()})
            temporary_json.replace(json_path)
            temporary_csv.replace(csv_path)

    def validate(self, verify_hashes=False):
        counts = Counter()
        issues = []
        checked = {}
        for row in self.db.execute("SELECT version_id,raw_path,raw_sha256,text_path,text_file_sha256,metadata_path FROM versions"):
            counts["capture_versions_checked"] += 1
            for field, digest_field in (("raw_path", "raw_sha256"), ("text_path", "text_file_sha256"), ("metadata_path", None)):
                relative = row[field]
                if not relative:
                    continue
                path = self.root / relative
                cache_key = (relative, digest_field and row[digest_field])
                if cache_key not in checked:
                    if not path.is_file():
                        checked[cache_key] = "missing_file"
                    elif verify_hashes and digest_field and row[digest_field]:
                        found = self.artifact(path)["sha256"]
                        checked[cache_key] = "ok" if found == row[digest_field] else "source_path_now_has_different_hash"
                    else:
                        checked[cache_key] = "ok"
                result = checked[cache_key]
                if result != "ok":
                    issues.append({"version_id": row["version_id"], "field": field, "path": relative, "status": result})
        counts["unique_file_references_checked"] = len(checked)
        counts["file_reference_issues"] = len(issues)
        counts["orphan_fts_rows"] = self.db.execute("SELECT count(*) FROM contents c WHERE NOT EXISTS(SELECT 1 FROM versions v WHERE v.content_id=c.id)").fetchone()[0]
        counts["searchable_versions"] = self.db.execute("SELECT count(*) FROM versions WHERE content_id IS NOT NULL").fetchone()[0]
        counts["fts_rows"] = self.db.execute("SELECT count(*) FROM content_fts").fetchone()[0]
        return {"checked_at": now(), "verification_scope": "all indexed capture versions", "source_hashes_compared": verify_hashes, **counts, "issues": issues}

    def run(self):
        begin = time.monotonic()
        self.indexed_version_ids.clear()
        for adapter in (self.laws, self.courts, self.generic, self.trellis_browser, self.trellis_browser_laws, self.trellis_public, self.ocr_derivatives, self.parser_repairs, self.document_text_derivatives):
            try:
                for item in adapter():
                    self.ingest(item)
                    if self.stats["indexed_candidates"] % 100 == 0:
                        self.db.commit()
            except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
                self.issue(adapter.__name__, "collection_snapshot_error", exc)
        self.db.commit()
        self.export()
        validation = self.validate(True)
        atomic_json(self.output / "validation.json", validation)
        atomic_json(self.output / "indexing_issues.json", {"generated_at": now(), "issues": self.issues})
        summary = {"generated_at": now(), "builder_version": VERSION, "workspace_root": str(self.root), "database": str(self.output / "documents.sqlite3"),
            "source_records": self.db.execute("SELECT count(*) FROM records").fetchone()[0], "capture_versions": self.db.execute("SELECT count(*) FROM versions").fetchone()[0],
            "unique_searchable_texts": self.db.execute("SELECT count(*) FROM contents").fetchone()[0], "indexed_text_characters": self.db.execute("SELECT coalesce(sum(characters),0) FROM contents").fetchone()[0],
            "latest_records_by_collection": {row[0]: row[1] for row in self.db.execute("SELECT collection,count(*) FROM latest_documents GROUP BY collection")},
            "latest_records_by_text_status": {row[0]: row[1] for row in self.db.execute("SELECT index_text_status,count(*) FROM latest_documents GROUP BY index_text_status")},
            "records_without_recorded_jurisdiction": self.db.execute("SELECT count(*) FROM latest_documents WHERE jurisdiction='' ").fetchone()[0],
            "records_without_recorded_category": self.db.execute("SELECT count(*) FROM latest_documents WHERE category='' ").fetchone()[0],
            "this_build": dict(self.stats), "validation_file_reference_issues": validation["file_reference_issues"], "elapsed_seconds": round(time.monotonic() - begin, 3),
            "network_requests": 0, "source_collections_modified": False, "full_corpus_complete": False,
            "notes": ["Only explicitly successful captures and separately verified derivatives from the configured source manifests are indexed.", "Identical UTF-8 decoded text is indexed once; source URLs, capture hashes and observed versions remain separate.", "Capture versions persist when source metadata changes. A raw path can later change or disappear; its recorded hash and indexing time identify the version originally verified.", "Public Trellis payload hashes identify saved provider JSON representations, not original HTTP responses.", "Completed derivatives in the configured court/law OCR manifests require a matching successful parent PDF path/hash and a verified OCR-text hash. They are separate records with page coverage and engine provenance.", "The explicit Nebraska parser repair is separately indexed only after verifying preserved failure metadata, original HTTP-success HTML hash/size, and exact offline reproduction of its saved repaired text.", "Search coverage does not establish complete enumeration, legal currency, or official certification."]}
        summary["notes"].insert(-1, "The three explicit document-text manifests (DOCX, Word 2003 XML, and the verified Iowa EPUB) require a successful parent validated in this build, matching raw/metadata provenance, and verified derivative text. Separate document_text_derivative records preserve parser, part, coverage and limitation evidence.")
        summary["notes"].insert(-1, "The explicit Trellis browser-law manifest records selected browser-rendered DOM captures, not original HTTP or Firecrawl responses. Its content-addressed raw/text hashes, timestamp-excluded content hash, legal URL scope, archive flags and exact raw legal_text equality are verified separately.")
        atomic_json(self.output / "summary.json", summary)
        build_id = sha((self.started + now()).encode())
        self.db.execute("INSERT INTO builds VALUES(?,?,?,?,?)", (build_id, self.started, now(), VERSION, stable(summary)))
        self.db.commit()
        return summary


def search(output, query, limit=10, history=False, exact=False):
    if exact:
        query = '"' + query.replace('"', '""') + '"'
    with closing(read_db(output / "documents.sqlite3")) as db:
        table = "versions" if history else "latest_documents"
        return [dict(row) for row in db.execute(
            "SELECT v.version_id,v.collection,v.jurisdiction,v.category,v.title,v.source_url,v.raw_path,v.text_path,v.retrieved_at,"
            "snippet(content_fts,0,'[',']',' … ',24) AS excerpt,bm25(content_fts) AS score "
            "FROM content_fts JOIN " + table + " v ON v.content_id=content_fts.rowid WHERE content_fts MATCH ? ORDER BY score,v.source_url LIMIT ?", (query, limit)).fetchall()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "search", "verify", "status"), nargs="?", default="build")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rehash", action="store_true", help="Re-read unchanged files instead of trusting their size/mtime cache")
    parser.add_argument("--exact", action="store_true", help="Treat the entire search query as one phrase")
    parser.add_argument("--history", action="store_true", help="Search every observed capture version")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    output = args.output or args.root / "catalog"
    if args.command == "search":
        if not args.query:
            parser.error("search requires a query")
        result = search(output, args.query, args.limit, args.history, args.exact)
    elif args.command == "status":
        result = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    else:
        output.mkdir(parents=True, exist_ok=True)
        with (output / ".document_index.lock").open("a+b") as lock:
            if os.name == "nt":
                import msvcrt
                lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            builder = Builder(args.root, output, args.rehash)
            try:
                result = builder.run() if args.command == "build" else builder.validate(True)
                if args.command == "verify":
                    atomic_json(output / "validation.json", result)
                    builder.db.commit()
            finally:
                builder.db.close()
                if os.name == "nt":
                    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
