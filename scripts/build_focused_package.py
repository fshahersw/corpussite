"""Build a small, offline selection over the existing legal archive search index."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "delivery/focused_legal_corpus"
LOCAL_COUNTY_COLLECTIONS = {"corpus/county_local_documents_20260914", "corpus/county_local_rules_washington_20260914"}

def county_source_row(row, sources):
    """Join direct or derivative records to an exact reviewed county original."""
    parent = next((c for c in LOCAL_COUNTY_COLLECTIONS if row["collection"] == c or row["collection"].startswith(c + "/")), None)
    if parent is None:
        return None
    source = sources.get((parent, row["source_url"], row["raw_sha256"]))
    if source is None:
        raise RuntimeError("County capture lacks its exact collection/URL/original-hash evidence: " + row["source_url"])
    return source


def json_read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def jsonl_read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def json_write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    PACKAGE.mkdir(parents=True, exist_ok=True)
    # These delivery manifests are produced by the bounded law and judge packagers.
    law_rows = jsonl_read(PACKAGE / "laws/official_resources.jsonl") + jsonl_read(PACKAGE / "laws/trellis_law_captures.jsonl")
    law_info = {r["source_url"]: r for r in law_rows}
    judge_rows = jsonl_read(PACKAGE / "judges/sources.jsonl")
    judge_urls = {r["source_url"] for r in judge_rows}
    judge_capture_urls = set(judge_urls)
    for row in judge_rows:
        for capture in row.get("original_capture_records", []):
            judge_capture_urls.update(capture[key] for key in ("source_url", "final_url") if capture.get(key))
    county_rows = jsonl_read(PACKAGE / "counties/captures.jsonl")
    county_urls = {r["source_url"] for r in county_rows}
    county_local_manifest = PACKAGE / "county_local_resources/captures.jsonl"
    local_rows = jsonl_read(county_local_manifest) if county_local_manifest.exists() else []
    local_sources = {(r["collection"], r["source_url"], r["raw_sha256"]): r for r in local_rows}
    if len(local_sources) != len(local_rows):
        raise RuntimeError("Duplicate county-local collection/URL/hash keys")
    index_summary = json_read(ROOT / "catalog/summary.json")
    with closing(sqlite3.connect((ROOT / "catalog/documents.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = []
        for item in db.execute("SELECT * FROM latest_documents ORDER BY collection,source_url"):
            row = dict(item)
            collection, url, category = row["collection"], row["source_url"], row["category"]
            local = county_source_row(row, local_sources)
            groups = []
            if url in law_info or row.get("final_url") in law_info or collection.startswith(("official_laws", "corpus/official_law_")) or collection == "corpus/trellis_browser_laws" or (collection == "trellis_public" and category == "state_rule"):
                groups.append("laws")
            if url in judge_capture_urls or row.get("final_url") in judge_capture_urls or (collection == "trellis_public" and category in {"judge_directory", "judge_profile"}):
                groups.append("judges")
            if local is not None or url in county_urls or row.get("final_url") in county_urls or collection == "corpus/county_sites" or (collection == "trellis_public" and category == "coverage_county"):
                groups.append("counties")
            if not groups:
                continue
            info = (law_info.get(url) or law_info.get(row.get("final_url")) or {}) if "laws" in groups else {}
            row.update(package_group=groups[0], package_groups_json=json.dumps(groups), content_kind=info.get("content_kind"), body_status=info.get("body_status"))
            row.update(county_geoids_json=json.dumps(local["geoid_associations"]) if local else None,
                       county_court_labels_json=json.dumps(local.get("court_labels", []), ensure_ascii=False) if local else None,
                       county_contexts_json=json.dumps(local["contexts"], ensure_ascii=False) if local else None,
                       county_discovery_categories_json=json.dumps(local["discovery_categories"]) if local else None,
                       county_reviewed_resource_kind=local.get("reviewed_resource_kind") if local else None,
                       county_semantic_review_json=json.dumps(local.get("semantic_review"), ensure_ascii=False) if local else None,
                       county_source_metadata_sha256=local.get("metadata_sha256") if local else None,
                       county_detected_type=local.get("detected_type") if local else None)
            rows.append(row)
        selected_texts = {r["content_id"] for r in rows if r["content_id"] is not None}
        text_characters = sum(db.execute("SELECT characters FROM contents WHERE id=?", (cid,)).fetchone()[0] for cid in selected_texts)

    columns = list(rows[0])
    temporary = PACKAGE / "focused.building.sqlite3"
    with closing(sqlite3.connect(temporary)) as db:
        db.execute("DROP TABLE IF EXISTS documents")
        db.execute("DROP TABLE IF EXISTS package_metadata")
        numeric = {"content_id", "raw_bytes"}
        db.execute("CREATE TABLE documents(" + ",".join('"' + col + '" ' + ("INTEGER" if col in numeric else "TEXT") for col in columns) + ",PRIMARY KEY(version_id))")
        db.executemany("INSERT INTO documents VALUES(" + ",".join("?" for _ in columns) + ")", [[r[c] for c in columns] for r in rows])
        db.execute("CREATE INDEX documents_group ON documents(package_group)")
        db.execute("CREATE INDEX documents_content ON documents(content_id)")
        db.execute("CREATE INDEX documents_url ON documents(source_url)")
        db.execute("CREATE TABLE package_metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.executemany("INSERT INTO package_metadata VALUES(?,?)", [
            ("shared_text_index", "catalog/documents.sqlite3"),
            ("shared_index_generated_at", index_summary["generated_at"]),
            ("scope", "saved laws; available judge rosters/profiles; saved county profiles/entries"),
            ("full_underlying_corpus_complete", "false")])
        db.commit()
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
    temporary.replace(PACKAGE / "focused.sqlite3")

    with (PACKAGE / "documents.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (PACKAGE / "documents.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in row.items()})

    issues = []
    if integrity != "ok":
        issues.append({"issue": "sqlite_integrity", "detail": integrity})
    for row in rows:
        for key in ("raw_path", "text_path", "metadata_path"):
            if row.get(key):
                path = (ROOT / row[key]).resolve()
                if not path.is_relative_to(ROOT) or not path.is_file():
                    issues.append({"issue": "invalid_reference", "version_id": row["version_id"], "key": key, "path": row[key]})
    expected_law_urls = set(law_info)
    indexed_law_urls = {r[key] for r in rows if "laws" in json.loads(r["package_groups_json"]) for key in ("source_url", "final_url") if r.get(key)}
    missing_laws = sorted(expected_law_urls - indexed_law_urls)
    missing_judges = sorted(judge_urls - {r[key] for r in rows if "judges" in json.loads(r["package_groups_json"]) for key in ("source_url", "final_url") if r.get(key)})
    missing_counties = sorted(county_urls - {r[key] for r in rows for key in ("source_url", "final_url") if r.get(key)})
    missing_local_captures = sorted(set(local_sources) - {(r["collection"], r["source_url"], r["raw_sha256"]) for r in rows})
    if missing_local_captures:
        issues.append({"issue": "county_local_originals_missing_from_index", "keys": missing_local_captures})

    spec = importlib.util.spec_from_file_location("focused_search", PACKAGE / "search.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    search_checks = []
    for query, group in [("due process", "laws"), ("judge", "judges"), ("Cocke", "counties")]:
        results = module.search(query, group=group, limit=3, exact=True)
        search_checks.append({"query": query, "group": group, "matches_returned": len(results), "source_urls": [r["source_url"] for r in results], "group_filter_correct": all(r["package_group"] == group for r in results)})
        if not results or any(r["package_group"] != group for r in results):
            issues.append({"issue": "search_smoke_check", "query": query, "group": group})

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "status": "validated" if not issues else "validation_failed",
        "focused_package_complete": False, "full_underlying_corpus_complete": False,
        "selected_capture_records": len(rows), "selected_records_by_group": dict(Counter(group for r in rows for group in json.loads(r["package_groups_json"]))),
        "group_count_note": "A record may belong to more than one component; group memberships can exceed unique capture records.",
        "selected_records_by_collection": dict(Counter(r["collection"] for r in rows)),
        "distinct_searchable_texts": len(selected_texts), "indexed_text_characters": text_characters,
        "records_without_searchable_text": sum(r["content_id"] is None for r in rows),
        "archive_index_generated_at": index_summary["generated_at"],
        "archive_index_reference_issues": index_summary.get("validation_file_reference_issues"),
        "law_manifest_urls_without_search_index_record": missing_laws,
        "judge_manifest_urls_without_search_index_record": missing_judges,
        "county_capture_urls_without_search_index_record": missing_counties,
        "county_local_capture_keys_without_search_index_record": missing_local_captures,
        "validation_issues": issues, "search_checks": search_checks,
        "storage": "Original files and the shared full-text index remain under this SCRAPE workspace. This folder contains the focused selection and manifests; it is not a standalone copy of all bytes.",
        "network_requests": 0,
        "notes": ["Source records include directories, ancillary pages and separately verified derivatives; they are not a count of unique statutes or judges.", "Any unavailable extracted text remains in manifests with its original-file reference.", "Full-text search is restricted to this package's selected records, even though the shared archive retains earlier broader captures."]}
    json_write(PACKAGE / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("generated_at", "status", "selected_capture_records", "selected_records_by_group", "distinct_searchable_texts", "records_without_searchable_text", "validation_issues")}, indent=2))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
