"""Offline, bounded decoding of the explicitly saved Crawford County response."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import brotli

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = ROOT / "corpus/county_entries_resume_20260913"
OUT = COLLECTION / "offline_decoding"
EXPECTED_SHA = "7445e0062596b4c587383252cff375123eeb31ecd7c915baadfe380e00496bdf"
URL = "https://crawfordcountymo.net/"
sys.path.insert(0, str(ROOT / "pipeline"))
import corpus_crawler as engine


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT / "manifest.jsonl").exists():
        raise RuntimeError("Offline derivative already exists; preserve the existing evidence")
    with closing(sqlite3.connect((COLLECTION / "corpus.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT r.*,f.response_json FROM resources r JOIN fetches f ON f.id=r.last_fetch_id WHERE r.id=443 AND r.url=?", (URL,)).fetchone())
    response = json.loads(row.pop("response_json"))
    assert row["status"] == "downloaded" and row["raw_complete"] == 1 and row["last_http_status"] == 200
    assert row["sha256"] == EXPECTED_SHA and row["extraction_status"] == "unsupported_content_encoding"
    assert response["headers"]["content-encoding"] == "br" and response["headers"]["content-type"].startswith("text/html")
    raw = (COLLECTION / row["raw_path"]).read_bytes()
    assert len(raw) == row["byte_count"] == 19113 and digest(raw) == EXPECTED_SHA
    decoder = brotli.Decompressor()
    chunks, total = [], 0
    for offset in range(0, len(raw), 64):
        decoded = decoder.process(raw[offset:offset + 64])
        total += len(decoded)
        if total > 16 * 1024 * 1024:
            raise ValueError("Decoded payload exceeds the 16 MiB limit")
        chunks.append(decoded)
    assert decoder.is_finished()
    html = b"".join(chunks)
    source, encoding = engine.decode_text(html, response["headers"]["content-type"])
    parser = engine.TextHTML()
    parser.feed(source)
    parser.close()
    text = parser.text.encode("utf-8")
    assert text and len(text) <= 4 * 1024 * 1024
    for directory in ("decoded", "text"):
        (OUT / directory).mkdir(exist_ok=True)
    decoded_path = OUT / "decoded" / (EXPECTED_SHA + ".html")
    text_path = OUT / "text" / (EXPECTED_SHA + ".txt")
    decoded_path.write_bytes(html)
    text_path.write_bytes(text)
    metadata_path = COLLECTION / row["metadata_path"]
    record = {"status": "extracted", "capture_kind": "document_text_derivative", "source_url": URL,
        "derived_at": datetime.now(timezone.utc).isoformat(), "parent_collection": "corpus/county_entries_resume_20260913", "parent_resource_id": 443,
        "parent_fetch_id": row["last_fetch_id"], "parent_raw_path": (COLLECTION / row["raw_path"]).relative_to(ROOT).as_posix(),
        "parent_raw_sha256": EXPECTED_SHA, "parent_raw_bytes": len(raw), "parent_http_status": 200,
        "parent_metadata_path": metadata_path.relative_to(ROOT).as_posix(), "parent_metadata_sha256": digest(metadata_path.read_bytes()),
        "parent_extraction_status": row["extraction_status"], "http_content_encoding": "br", "mime_type": response["headers"]["content-type"],
        "extraction_method": "Incremental Brotli decompression (64-byte input chunks, 16 MiB decoded limit) followed by existing inert HTML text parser",
        "extractor_version": "1.0.0", "brotli_version": brotli.__version__, "html_parser": "pipeline/corpus_crawler.py TextHTML", "html_parser_crawler_version": engine.VERSION,
        "decoded_path": decoded_path.relative_to(ROOT).as_posix(), "decoded_sha256": digest(html), "decoded_bytes": len(html), "text_encoding": encoding,
        "text_path": text_path.relative_to(ROOT).as_posix(), "text_sha256": digest(text), "text_bytes": len(text), "title": parser.title,
        "limitations": ["Only the saved HTTP body was decoded; no network request or script execution occurred", "Text parser omits script/style/noscript/template contents and preserves its existing whitespace normalization", "This derivative does not independently verify website ownership or legal completeness", "Original raw bytes, metadata and resource extraction status remain unchanged"],
        "network_requests": 0, "source_files_modified": False}
    (OUT / "manifest.jsonl").write_text(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    assert digest(decoded_path.read_bytes()) == record["decoded_sha256"] and digest(text_path.read_bytes()) == record["text_sha256"]
    (OUT / "validation.json").write_text(json.dumps({"passed": True, "parent_raw_hash_verified": True, "parent_metadata_hash": record["parent_metadata_sha256"], "decoder_finished": True, "decoded_bytes": len(html), "text_bytes": len(text), "derivative_hashes_verified": True, "issues": []}, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text("# Offline response decoding\n\nThe single saved Crawford County response used Brotli HTTP encoding, which the collector retained without text extraction. The installed Brotli decoder recovered HTML and the existing inert HTML parser extracted text. `manifest.jsonl` retains parent URL/resource/fetch/raw/metadata hashes, derivative hashes, parser versions and limitations. Original capture bytes and collection database rows were not modified. No network request was made. This is a separate derivative; inclusion in the unified search index requires its explicit verified adapter.\n", encoding="utf-8")
    print(json.dumps({key: record[key] for key in ("source_url", "decoded_bytes", "text_bytes", "title", "text_path", "text_sha256")}, indent=2))


if __name__ == "__main__":
    main()
