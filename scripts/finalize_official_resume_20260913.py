"""Validate a stable official-only continuation snapshot and publish its handoff."""
from collections import Counter
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from finalize_focused_package import verify
from build_document_index import OCR_MANIFESTS

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "reports/remaining_resume_20260913"
PACKAGE = ROOT / "delivery/focused_legal_corpus"
OUT = PACKAGE / "official_expansion_20260913"
sys.path.insert(0, str(ROOT / "pipeline"))
from corpus_crawler import run_lock


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verified_derivative_text(root, collection, kind, metadata_path, text_path, text_sha):
    """Use the index's exact reviewed OCR manifest roots, including separate passes."""
    root = root.resolve()
    if kind == 'ocr_derivative':
        reviewed = {manifest: prefix for manifest, _, prefix in OCR_MANIFESTS}
        prefix = reviewed.get(metadata_path)
        if prefix is None:
            return False
        if not collection.endswith('/ocr'):
            return False
        parent = collection[:-4]
        physical_parent = parent if parent.startswith('corpus/') else 'sources/' + parent
        if not prefix.startswith(physical_parent + '/'):
            return False
        allowed = (root / prefix).resolve()
    else:
        allowed = (root / collection / 'text').resolve()
    text = (root / text_path).resolve()
    return (allowed.is_relative_to(root) and text.is_relative_to(allowed)
            and text.is_file() and sha(text) == text_sha
            and bool(text.read_text(encoding='utf-8-sig').strip()))


def finalize(scope, persist_scope=True):
    if scope["latest_user_direction"] != "Continue official sources only":
        raise RuntimeError("Review the changed user scope before using this finalizer")
    summary, validation = verify()
    with closing(sqlite3.connect((PACKAGE / "focused.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        indexed = set(db.execute("SELECT collection,source_url,raw_sha256 FROM documents"))
        searchable_originals = set(db.execute("SELECT collection,source_url,raw_sha256 FROM documents WHERE capture_kind='direct_public_capture' AND index_text_status='searchable'"))
        derivative_rows = db.execute("SELECT collection,source_url,raw_sha256,text_path,text_file_sha256,capture_kind,metadata_path FROM documents WHERE capture_kind IN ('document_text_derivative','ocr_derivative') AND index_text_status='searchable'").fetchall()
    document_text_pairs = {collection: set() for collection in scope["reviewed_collections"]}
    document_derivative_counts = Counter()
    for derivative_collection, url, raw_sha, text_path, text_sha, kind, metadata_path in derivative_rows:
        parents = [collection for collection in scope["reviewed_collections"] if derivative_collection.startswith(collection + "/")]
        if not parents:
            continue
        if len(parents) != 1 or (parents[0], url, raw_sha) not in indexed:
            raise RuntimeError("Document derivative lacks an unambiguous reviewed parent: " + url)
        if not verified_derivative_text(ROOT, derivative_collection, kind, metadata_path, text_path, text_sha):
            raise RuntimeError("Invalid indexed document derivative: " + url)
        document_text_pairs[parents[0]].add((url, raw_sha))
        document_derivative_counts[parents[0]] += 1
    component_pairs = {}
    for family, filename in [("laws", "laws/official_resources.jsonl"), ("counties", "counties/captures.jsonl")]:
        component_pairs[family] = {(r["source_url"], r["raw_sha256"]) for line in (PACKAGE / filename).read_text(encoding="utf-8").splitlines() if line.strip() for r in [json.loads(line)]}
    local_manifest = PACKAGE / "county_local_resources/captures.jsonl"
    if any(collection.startswith("corpus/county_local_") for collection in scope["reviewed_collections"]):
        component_pairs["counties"].update((r["source_url"], r["raw_sha256"]) for line in local_manifest.read_text(encoding="utf-8").splitlines() if line.strip() for r in [json.loads(line)])
    collections, all_outcomes = {}, []
    for collection in scope["reviewed_collections"]:
        base = ROOT / collection
        with closing(sqlite3.connect((base / "corpus.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            unfinished_runs = [r[0] for r in db.execute("SELECT id FROM runs WHERE ended_at IS NULL")]
            rows = [dict(r) for r in db.execute("SELECT id,url,status,sha256,raw_path,text_path,metadata_path,extraction_status FROM resources ORDER BY id")]
        family = "laws" if collection.startswith("corpus/official_law_") else "counties"
        captures = [r for r in rows if r["status"] == "downloaded"]
        nonempty_raw = nonempty_text = raw_bytes = responses_with_searchable_text = 0
        for row in captures:
            raw = (base / row["raw_path"]).resolve()
            if not raw.is_relative_to(ROOT) or not raw.is_file() or sha(raw) != row["sha256"]:
                raise RuntimeError("Invalid saved original: " + row["url"])
            raw_bytes += raw.stat().st_size
            nonempty_raw += raw.stat().st_size > 0
            has_text = False
            if row["text_path"]:
                text = (base / row["text_path"]).resolve()
                if not text.is_relative_to(ROOT) or not text.is_file():
                    raise RuntimeError("Invalid saved text path: " + row["url"])
                has_text = bool(text.read_text(encoding="utf-8-sig").strip())
            nonempty_text += has_text
            row["raw_bytes"] = raw.stat().st_size
            row["has_nonempty_text"] = has_text
            row["has_indexed_document_text_derivative"] = (row["url"], row["sha256"]) in document_text_pairs[collection]
            row["has_indexed_searchable_text"] = (collection, row["url"], row["sha256"]) in searchable_originals or row["has_indexed_document_text_derivative"]
            responses_with_searchable_text += row["has_indexed_searchable_text"]
            if (collection, row["url"], row["sha256"]) not in indexed:
                raise RuntimeError("Capture is absent from focused index: " + row["url"])
            if (row["url"], row["sha256"]) not in component_pairs[family]:
                raise RuntimeError("Capture is absent from component manifest: " + row["url"])
        collections[collection] = {"family": family, "status_counts": dict(Counter(r["status"] for r in rows)), "saved_response_captures": len(captures), "nonempty_raw_responses": nonempty_raw, "nonempty_text_captures": nonempty_text, "indexed_document_text_derivatives": document_derivative_counts[collection], "captured_responses_with_indexed_searchable_text": responses_with_searchable_text, "saved_raw_bytes": raw_bytes, "pending_rows": sum(r["status"] in {"pending", "fetching"} for r in rows), "all_saved_url_hashes_indexed": True, "exclusive_collection_lock_acquired": True, "historical_unfinished_run_ids": unfinished_runs}
        all_outcomes.extend({"collection": collection, **r} for r in rows)
    stamp = datetime.now(timezone.utc).isoformat()
    result = {"updated_at": stamp, "status": "validated_snapshot_continuation_active", "scope": "Official law sources and county/government entry pages only", "collections": collections, "saved_response_captures": sum(r["saved_response_captures"] for r in collections.values()), "nonempty_raw_responses": sum(r["nonempty_raw_responses"] for r in collections.values()), "nonempty_text_captures": sum(r["nonempty_text_captures"] for r in collections.values()), "indexed_document_text_derivatives": sum(r["indexed_document_text_derivatives"] for r in collections.values()), "captured_responses_with_indexed_searchable_text": sum(r["captured_responses_with_indexed_searchable_text"] for r in collections.values()), "pending_reviewed_rows": sum(r["pending_rows"] for r in collections.values()), "captures_by_family": {family: sum(r["saved_response_captures"] for r in collections.values() if r["family"] == family) for family in ("laws", "counties")}, "validation_issues": 0, "full_underlying_corpus_complete": False, "new_paid_requests": 0, "trellis_acquisition": "deferred_by_user", "automation_id": scope["automation_id"], "count_note": "Captured responses include ancillary, navigation and empty payloads. Nonempty text counts original collector text only; verified offline document text derivatives are counted separately. Searchable response coverage joins either text representation to its original URL/hash without double counting. Text availability does not verify substantive legal completeness or currency."}
    OUT.mkdir(parents=True, exist_ok=True)
    write(OUT / "summary.json", result)
    with (OUT / "outcomes.jsonl").open("w", encoding="utf-8") as stream:
        for row in all_outcomes:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    write(OUT / "checkpoints" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"), result)
    summary.update(status="complete", focused_package_complete=True, full_underlying_corpus_complete=False, completed_at=stamp, acquisition_status="official_continuation_active", official_expansion="official_expansion_20260913/summary.json", continuation_automation=scope["automation_id"])
    validation.update(new_official_capture_url_hashes_verified=result["saved_response_captures"], indexed_official_document_derivative_text_hashes_verified=result["indexed_document_text_derivatives"], stable_acquisition_checkpoint_verified=True)
    write(PACKAGE / "summary.json", summary)
    write(PACKAGE / "validation.json", validation)
    laws, counties, judges = [read(PACKAGE / group / "summary.json") for group in ("laws", "counties", "judges")]
    guide = f'''# Focused legal corpus package

Snapshot updated on {stamp}. The official-only continuation has saved **{result['saved_response_captures']:,} responses**, with **{result['nonempty_text_captures']:,} nonempty collector text files** and **{result['indexed_document_text_derivatives']:,} verified offline document text derivatives**. **{result['captured_responses_with_indexed_searchable_text']:,} saved responses have indexed searchable text** through either representation. Collection continues through the paced reviewed queues. The full nationwide corpus remains incomplete.

| Component | Saved scope | Files |
|---|---|---|
| Official laws | {laws['official']['downloaded_resource_urls']:,} source URL entries, including directories and ancillary material | [CSV](laws/official_resources.csv) · [JSONL](laws/official_resources.jsonl) · [Guide](laws/README.md) |
| Trellis laws | {laws['trellis']['saved_representations']:,} previously saved representations; acquisition deferred | [JSONL](laws/trellis_law_captures.jsonl) |
| Judges | Previously saved official roster/profile evidence | [CSV](judges/sources.csv) · [Guide](judges/README.md) |
| Counties | {counties['county_equivalent_rows']:,} Census county-equivalent rows across 50 states and DC, linked to saved profile/website evidence where available | [CSV](counties/counties.csv) · [JSONL](counties/counties.jsonl) · [Guide](counties/README.md) |
| County local resources | Local rules, court/clerk pages, filing guidance, forms and county data with explicit source and geographic evidence | [Guide](county_local_resources/README.md) · [Ledger](county_local_resources/county_ledger.jsonl) · [Captures](county_local_resources/captures.jsonl) |

The package contains **{summary['selected_capture_records']:,} capture records** and **{summary['distinct_searchable_texts']:,} distinct searchable texts**. Original response bytes, extracted text, timestamps and hashes remain in the workspace under `sources/` and `corpus/`.

```powershell
python -X utf8 delivery/focused_legal_corpus/search.py "due process" --exact --group laws
python -X utf8 delivery/focused_legal_corpus/search.py "Cocke" --group counties
```

[Official continuation results](official_expansion_20260913/summary.json) · [Per-URL outcomes](official_expansion_20260913/outcomes.jsonl) · [Previous paid expansion](expansion_20260913/summary.json).

County inventory coverage does not mean every county's content is downloaded. {counties['matched_counties']:,} rows have clear saved Trellis profile matches. Reported websites, registered government domains, derived origins and observed HTTP redirects retain separate evidence. Agency or district government sites are not automatically county-court websites, and geographic candidates are not verified assignments. Empty responses and files without usable text remain explicit in the capture metadata.

Law source entries include navigation, historical editions and ancillary material. Saved text does not establish complete current law. [Law coverage](laws/jurisdiction_coverage.csv) · [Law gaps](laws/official_resource_gaps.csv) · [County website evidence](counties/website_evidence.csv). Trellis and Firecrawl collection remain deferred under the user's official-only direction; this continuation made no paid requests.

[Summary](summary.json) · [Validation](validation.json) · [File hashes](package_files.json) · [Search selection](focused.sqlite3). Keep this folder within the workspace because it references original files and the shared text index. Retain GEOID/FIPS fields as strings.
'''
    (PACKAGE / "README.md").write_text(guide, encoding="utf-8")
    root_guide = ROOT / "README.md"
    prior_root_guide = root_guide.read_text(encoding="utf-8")
    marker = prior_root_guide.find("| Location |")
    if marker < 0:
        raise RuntimeError("Root archive location guide was not found")
    root_intro = f'''# US legal corpus archive

The **[focused package](delivery/focused_legal_corpus/README.md)** was refreshed on {stamp}. The official-only continuation has saved {result['saved_response_captures']:,} response captures, including {result['nonempty_text_captures']:,} with nonempty collector text and {result['indexed_document_text_derivatives']:,} verified offline document text derivatives. The package now contains {summary['selected_capture_records']:,} capture records and {summary['distinct_searchable_texts']:,} distinct searchable texts.

All 3,144 Census county-equivalent rows are inventoried. Downloaded content remains partial; registered government agencies and candidate county associations remain explicitly labeled. Trellis and paid collection are deferred. Official sources continue through reviewed queues with a 30-minute continuation check.

[Current official continuation](delivery/focused_legal_corpus/official_expansion_20260913/summary.json) · [Active scope and checkpoint](reports/remaining_resume_20260913/scope.json) · [Runbook](RUNBOOK.md).

'''
    root_guide.write_text(root_intro + prior_root_guide[marker:], encoding="utf-8")
    scope.update(status="official_continuation_active", last_validated_checkpoint=stamp, latest_expansion_summary="delivery/focused_legal_corpus/official_expansion_20260913/summary.json", latest_saved_response_captures=result["saved_response_captures"], latest_nonempty_text_captures=result["nonempty_text_captures"], latest_indexed_document_text_derivatives=result["indexed_document_text_derivatives"], latest_captured_responses_with_indexed_searchable_text=result["captured_responses_with_indexed_searchable_text"], latest_pending_reviewed_rows=result["pending_reviewed_rows"])
    if persist_scope:
        write(BASE / "scope.json", scope)
    files = [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(PACKAGE.rglob("*")) if p.is_file() and p.name != "package_files.json" and "__pycache__" not in p.parts]
    write(PACKAGE / "package_files.json", {"generated_at": stamp, "files": files, "file_count": len(files), "total_bytes": sum(r["bytes"] for r in files), "exclusions": ["This inventory itself", "Python bytecode caches"]})
    print(json.dumps(result, indent=2))


def main():
    scope = read(BASE / "scope.json")
    with ExitStack() as locks:
        for collection in scope["reviewed_collections"]:
            path = (ROOT / collection).resolve()
            if not path.is_relative_to(ROOT / "corpus") or not (path / "corpus.sqlite3").is_file():
                raise RuntimeError("Invalid reviewed collection path: " + collection)
            locks.enter_context(run_lock(path))
        finalize(scope)


if __name__ == "__main__":
    main()
