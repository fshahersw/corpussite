"""Verify and close the user-approved September 2026 focused archive package."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "delivery/focused_legal_corpus"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify():
    summary = read(PACKAGE / "summary.json")
    laws = read(PACKAGE / "laws/validation.json")
    judges = read(PACKAGE / "judges/validation.json")
    counties = read(PACKAGE / "counties/validation.json")
    lineage = read(PACKAGE / "laws/provenance/index_count_reconciliation.json")
    expected_text_gaps = {r["source_url"] for r in lineage["package_only_resources"] if r["addition_kind"] == "source_entry_representation_without_verified_text"}
    checks = {
        "focused_search_and_file_checks_passed": summary["status"] == "validated" and not summary["validation_issues"],
        "archive_index_references_valid": summary["archive_index_reference_issues"] == 0,
        "law_references_valid": laws["issue_count"] == 0,
        "judge_references_valid": judges["issues"] == 0,
        "county_references_valid": counties["passed"] and not counties["issues"],
        "all_3144_geoids_present_and_unique": counties["checks"]["county_rows"] == counties["checks"]["unique_geoids"] == 3144,
        "all_judge_sources_selected": not summary["judge_manifest_urls_without_search_index_record"],
        "all_county_capture_urls_selected": not summary["county_capture_urls_without_search_index_record"],
        "only_documented_empty_law_entries_omit_full_text_index": set(summary["law_manifest_urls_without_search_index_record"]) == expected_text_gaps and len(expected_text_gaps) == 12,
        "no_active_remote_batch": not (ROOT / "sources/trellis/worker/batch_active.json").exists(),
        "new_trellis_batches_disabled": (ROOT / "sources/trellis/worker/STOP").exists(),
    }
    with closing(sqlite3.connect((PACKAGE / "focused.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        for filename in ("judgebiographies.pdf", "judgtara.pdf"):
            rows = db.execute("SELECT package_groups_json,content_id FROM documents WHERE source_url=?", ("https://www.njcourts.gov/sites/default/files/courts/" + filename,)).fetchall()
            checks[filename + "_law_and_judge_membership"] = bool(rows) and all({"laws", "judges"}.issubset(json.loads(groups)) and content_id is not None for groups, content_id in rows)
    failed = [name for name, passed in checks.items() if not passed]
    result = {"validated_at": datetime.now(timezone.utc).isoformat(), "passed": not failed, "checks": checks, "issues": failed, "accepted_search_gaps": sorted(expected_text_gaps), "source_hash_validation": "Component validation reports and the final archive index verify the underlying artifacts; this step validates the combined selection and cutoff."}
    if failed:
        raise RuntimeError("Focused package checks failed: " + ", ".join(failed))
    return summary, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    summary, validation = verify()
    if args.check_only:
        print(json.dumps(validation, indent=2))
        return
    automation = read(ROOT / "reports/focused_package_automation_shutdown.json")
    if automation.get("status") != "deleted":
        raise RuntimeError("The obsolete continuation automation has not been deleted.")
    now = datetime.now(timezone.utc).isoformat()
    summary.update(status="complete", focused_package_complete=True, completed_at=now, continuation_automation="deleted", package_file_manifest="package_files.json")
    write(PACKAGE / "summary.json", summary)
    write(PACKAGE / "validation.json", validation)
    write(PACKAGE / "package_files.json", {"status": "building"})

    for name in ("focused_package_scope.json", "continuation_checkpoint.json"):
        path = ROOT / "reports" / name
        data = read(path)
        data.update(focused_package_complete=True, full_underlying_corpus_complete=False, completed_at=now, status="focused_package_complete", continuation_automation="deleted", active_firecrawl_pid_last_reported=None, next_work="None for the accepted focused package. Further acquisition requires a new user instruction.")
        if name == "continuation_checkpoint.json":
            data.update(complete=True, focused_package_status="complete", indexed_source_records=13375, indexed_distinct_texts=12911, index_build_at=summary["archive_index_generated_at"], firecrawl_worker_status="checkpointed_focused_package")
        write(path, data)

    path = ROOT / "README.md"
    original = path.read_text(encoding="utf-8")
    start = original.find("| Location |")
    if start < 0:
        raise RuntimeError("Expected root archive guide table was not found.")
    intro = """# US legal corpus archive

The user-approved **focused package is complete**: saved laws, available official judge rosters and the complete saved 3,144-row Census county-equivalent inventory, with document/profile/edition gaps explicitly documented.

**[Open the focused package](delivery/focused_legal_corpus/README.md)** for CSV/JSONL files, coverage tables, provenance and local search. Its combined selection contains 10,984 capture records and 10,694 distinct searchable texts. Original files remain in this workspace. The full underlying nationwide corpus remains incomplete; earlier broader downloads are preserved as history.

Collection and its recurring continuation automation have stopped. The last verified Firecrawl balance was 951 credits. [Measured status](reports/STATUS.md) · [Machine-readable status](reports/status.json).

"""
    path.write_text(intro + original[start:], encoding="utf-8")
    for name in ("REQUEST.md", "RUNBOOK.md"):
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        note = "The focused package is complete at `delivery/focused_legal_corpus/README.md`; the continuation automation was deleted. No collector should restart without a new user instruction.\n\n"
        if note not in text:
            first, rest = text.split("\n", 1)
            path.write_text(first + "\n\n" + note + rest.lstrip("\n"), encoding="utf-8")

    broken = []
    for path in (PACKAGE / "README.md", ROOT / "README.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if not re.match(r"^[a-zA-Z]+://", target) and not (path.parent / target.split("#")[0]).exists():
                broken.append({"file": path.relative_to(ROOT).as_posix(), "target": target})
    if broken:
        raise RuntimeError("Broken delivery links: " + json.dumps(broken))
    validation["readme_links_valid"] = True
    validation["continuation_automation_deleted"] = True
    write(PACKAGE / "validation.json", validation)

    files = []
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file() or path.name == "package_files.json" or "__pycache__" in path.parts:
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    write(PACKAGE / "package_files.json", {"generated_at": now, "files": files, "file_count": len(files), "total_bytes": sum(r["bytes"] for r in files), "exclusions": ["This inventory itself", "Python bytecode caches"], "scope": "Files in the focused delivery folder. Referenced originals and the shared search index remain in the SCRAPE workspace."})
    print(json.dumps({"focused_package_complete": True, "selected_records": summary["selected_capture_records"], "searchable_texts": summary["distinct_searchable_texts"], "package_files": len(files), "package_bytes": sum(r["bytes"] for r in files), "validation_issues": 0}, indent=2))


if __name__ == "__main__":
    main()
