"""Close the bounded county/law expansion after its refreshed package is validated."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from finalize_focused_package import verify as verify_package
from build_county_law_resume_progress import main as refresh_progress

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "delivery/focused_legal_corpus"
BASE = ROOT / "sources/trellis/resume_20260913"
OUT = PACKAGE / "expansion_20260913"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preserve_baseline():
    target = BASE / "prior_delivery_snapshot.json"
    if target.exists():
        return read(target)
    paths = ["summary.json", "validation.json", "package_files.json", "laws/summary.json", "counties/summary.json", "judges/summary.json"]
    result = {"preserved_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    for relative in paths:
        path = PACKAGE / relative
        result["files"][relative] = {"sha256": sha(path), "bytes": path.stat().st_size, "content": read(path)}
    write(target, result)
    return result


def selection_rows():
    selected = [json.loads(line) for line in (BASE / "paid_selection_950.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    urls = [row["url"] for row in selected]
    if len(urls) != len(set(urls)):
        raise RuntimeError("Selection has duplicate URLs")
    with closing(sqlite3.connect((ROOT / "sources/trellis/worker/frontier.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        found = {row["url"]: dict(row) for row in db.execute("SELECT url,category,status,attempts,response_path,error FROM frontier WHERE url IN (" + ",".join("?" for _ in urls) + ")", urls)}
    if set(found) != set(urls):
        raise RuntimeError("Selected URLs are missing from the frontier")
    checks = read(BASE / "law_content_checks.json")
    result = []
    for number, url in enumerate(urls, 1):
        row = found[url]
        row["selection_order"] = number
        row["raw_sha256"] = None
        row["body_status"] = checks.get(url, {}).get("body_status")
        if row.get("response_path"):
            path = Path(row["response_path"])
            path = path if path.is_absolute() else ROOT / path
            path = path.resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise RuntimeError("Invalid response reference: " + url)
            row["response_path"] = path.relative_to(ROOT).as_posix()
            row["raw_sha256"] = sha(path)
        elif row["status"] == "downloaded":
            raise RuntimeError("Downloaded selection has no response: " + url)
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preserve-baseline", action="store_true")
    args = parser.parse_args()
    if args.preserve_baseline:
        baseline = preserve_baseline()
        print(json.dumps({"preserved_at": baseline["preserved_at"], "path": str(BASE / "prior_delivery_snapshot.json")}))
        return
    if not (BASE / "prior_delivery_snapshot.json").exists():
        raise RuntimeError("Preserve the old delivery metadata before rebuilding")
    checkpoint = read(BASE / "acquisition_checkpoint.json")
    if not checkpoint.get("local_collectors_stopped"):
        raise RuntimeError("Local collectors have not been confirmed stopped")
    refresh_progress()
    summary, validation = verify_package()
    scope = read(ROOT / "reports/active_county_law_resume.json")
    worker = read(ROOT / "sources/trellis/worker/batch_status.json")
    progress = read(ROOT / "reports/county_law_resume_progress.json")
    rows = selection_rows()
    active = [r for r in rows if r["status"] in {"pending", "fetching", "batch_held"}]
    if active:
        raise RuntimeError(f"{len(active)} selected URLs still lack a terminal outcome")
    if worker.get("status") == "running" or worker.get("active_batch"):
        raise RuntimeError("The paid worker is still active")
    if progress.get("provider_credits_used_selected_pass", 0) > scope["paid_page_selection_budget"]:
        raise RuntimeError("Provider reported usage above the selected pass budget")
    saved = [r for r in rows if r["status"] == "downloaded"]
    with closing(sqlite3.connect((PACKAGE / "focused.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        indexed = set(db.execute("SELECT collection,source_url,raw_sha256 FROM documents"))
    component_pairs = {}
    for category, relative in (("rules", "laws/trellis_law_captures.jsonl"), ("county", "counties/captures.jsonl")):
        component_pairs[category] = {(r["source_url"], r["raw_sha256"]) for line in (PACKAGE / relative).read_text(encoding="utf-8").splitlines() if line.strip() for r in [json.loads(line)]}
    for row in saved:
        identity = (row["url"], row["raw_sha256"])
        if identity not in component_pairs[row["category"]] or ("trellis_public", *identity) not in indexed:
            raise RuntimeError("New Trellis URL/hash missing from component manifest or index: " + row["url"])
    direct_path = ROOT / "corpus/county_entries_resume_20260913/corpus.sqlite3"
    with closing(sqlite3.connect(direct_path.as_uri() + "?mode=ro", uri=True)) as db:
        direct_rows = list(db.execute("SELECT url,sha256,raw_path FROM resources WHERE status='downloaded'"))
    for url, digest, raw_path in direct_rows:
        path = (direct_path.parent / raw_path).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or sha(path) != digest:
            raise RuntimeError("New direct county response hash is invalid: " + url)
        if (url, digest) not in component_pairs["county"] or ("corpus/county_entries_resume_20260913", url, digest) not in indexed:
            raise RuntimeError("New direct county URL/hash missing from component manifest or index: " + url)
    if progress["direct_official_county_entries"]["downloaded_resources"] != len(direct_rows):
        raise RuntimeError("Direct county progress differs from the stopped collection")
    law_counts = Counter(r["body_status"] for r in saved if r["category"] == "rules")
    if None in law_counts:
        raise RuntimeError("Some new law pages have not been classified")
    law_audit = read(BASE / "law_final350_validation.json")
    if law_audit.get("unresolved_issue_count") != 0 or law_audit.get("exact_source_url_matches") != sum(law_counts.values()):
        raise RuntimeError("Final selected-law source consistency audit is incomplete")
    laws = read(PACKAGE / "laws/summary.json")
    counties = read(PACKAGE / "counties/summary.json")
    credit = read(BASE / "credit_checkpoint.json")
    balance = credit.get("response", {}).get("data", {}).get("remainingCredits")
    if credit.get("api_status") != 200 or balance is None:
        raise RuntimeError("Final provider credit observation is missing")
    now = datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    write(OUT / "law_source_validation.json", law_audit)
    write(OUT / "county_source_validation.json", read(ROOT / "reports/geography/county_resume_quality_20260913.json"))
    with (OUT / "trellis_attempts.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (OUT / "trellis_attempts.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for key, value in row.items()})
    expansion = {"completed_at": now, "status": "delivered", "selected_urls": len(rows),
        "successful_selected_urls": len(saved), "outcomes": dict(Counter(r["status"] for r in rows)),
        "saved_by_category": dict(Counter(r["category"] for r in saved)), "new_law_body_status_counts": dict(law_counts),
        "direct_official_county_entries": progress["direct_official_county_entries"],
        "provider_credits_used": progress.get("provider_credits_used_selected_pass"), "provider_remaining_credits": balance,
        "credit_checked_at": credit.get("checked_at"), "full_underlying_corpus_complete": False,
        "county_inventory_rows": counties["county_equivalent_rows"], "matched_counties": counties["matched_counties"],
        "counties_without_clear_trellis_match": counties["counties_without_clear_trellis_match"],
        "trellis_law_pending_urls": laws["trellis"]["observed_uncollected_urls"],
        "scope_note": "This completed bounded expansion does not mean all remaining nationwide law and county URLs were downloaded.",
        "quality_evidence": ["law_source_validation.json", "county_source_validation.json"],
        "prior_snapshot": "sources/trellis/resume_20260913/prior_delivery_snapshot.json", "acquisition_checkpoint": checkpoint}
    write(OUT / "summary.json", expansion)
    write(OUT / "credit_checkpoint.json", credit)
    summary.update(status="complete", focused_package_complete=True, full_underlying_corpus_complete=False,
                   completed_at=now, county_law_expansion="expansion_20260913/summary.json", continuation_automation="none", package_file_manifest="package_files.json")
    write(PACKAGE / "summary.json", summary)
    validation.update(county_law_selection_outcomes_complete=True, new_successful_trellis_urls_selected=len(saved),
                      new_law_pages_classified=sum(law_counts.values()), new_direct_county_url_hashes_selected=len(direct_rows),
                      selected_provider_pass_credits_recomputed=progress["provider_credits_used_selected_pass"],
                      local_collectors_stopped=True, final_credit_observation=balance)
    write(PACKAGE / "validation.json", validation)
    scope.update(status="delivered", completed_at=now, focused_package_complete=True, full_underlying_corpus_complete=False,
                 expansion_summary="delivery/focused_legal_corpus/expansion_20260913/summary.json", latest_credits_verified=balance,
                 credit_verified_at=credit.get("checked_at"), automation="none", acquisition_checkpointed=True,
                 direct_official_county_entries=progress['direct_official_county_entries']['downloaded_resources'])
    write(ROOT / "reports/active_county_law_resume.json", scope)
    write(BASE / "scope.json", scope)
    index = read(ROOT / "catalog/summary.json")
    write(ROOT / "reports/continuation_checkpoint.json", {
        "scope_version": scope["scope_version"], "completed_at": now, "status": "bounded_county_law_expansion_delivered",
        "complete": True, "completion_scope": "The fixed selected URL pass and refreshed focused package only",
        "full_underlying_corpus_complete": False, "indexed_source_records": index["source_records"],
        "indexed_distinct_texts": index["unique_searchable_texts"], "index_build_at": index["generated_at"],
        "remaining_credits": balance, "credit_checked_at": credit.get("checked_at"), "continuation_automation": "none",
        "active_firecrawl_pid_last_reported": None, "acquisition_checkpoint": "sources/trellis/resume_20260913/acquisition_checkpoint.json",
        "next_work": "Known uncollected URLs and barriers are preserved; no collector is scheduled or authorized to expand beyond this completed fixed selection."})
    for name in ("REQUEST.md", "RUNBOOK.md"):
        path = ROOT / name
        first, rest = path.read_text(encoding="utf-8").split("\n", 1)
        note = f"County/law expansion checkpointed and delivered at {now}. All {len(rows):,} selected URLs reached a recorded outcome; the full underlying corpus remains incomplete. Current result: `delivery/focused_legal_corpus/expansion_20260913/summary.json`. No collector or continuation automation is running. The earlier active-resume notes below are historical.\n\n"
        path.write_text(first + "\n\n" + note + rest.lstrip("\n"), encoding="utf-8")
    official = laws["official"]
    trellis = laws["trellis"]
    body = Counter(trellis["provider_body_status_counts"]) + Counter(trellis["browser_body_status_counts"])
    readme = f'''# Focused legal corpus package

The package was refreshed on {now} after the additional county/law pass. The pass saved **{len(saved):,} Trellis pages** plus **{progress['direct_official_county_entries']['downloaded_resources']:,} county website resources**. The full nationwide law and county content corpus remains incomplete.

| Component | Saved scope | Files |
|---|---|---|
| Official laws | {official['downloaded_resource_urls']:,} source URL entries, including directories and ancillary material | [CSV](laws/official_resources.csv) · [JSONL](laws/official_resources.jsonl) · [Guide](laws/README.md) |
| Trellis laws | {trellis['provider_captures']:,} provider and {trellis['browser_captures']:,} browser captures | [CSV](laws/trellis_law_captures.csv) · [JSONL](laws/trellis_law_captures.jsonl) |
| Judges | Previously saved roster/profile evidence; no judge acquisition in this expansion | [CSV](judges/sources.csv) · [Guide](judges/README.md) |
| Counties | {counties['county_equivalent_rows']:,} unique Census county-equivalent rows across 50 states and DC | [CSV](counties/counties.csv) · [JSONL](counties/counties.jsonl) · [Guide](counties/README.md) |

The county package has {counties['trellis_profile_rows']:,} saved Trellis profiles, {counties['matched_counties']:,} clear Census matches, {counties['ambiguous_profile_rows']:,} ambiguous profiles and {counties['special_jurisdiction_rows']:,} special jurisdictions. {counties['counties_without_clear_trellis_match']:,} Census rows still lack a clear saved profile. Website hrefs, registry candidates and derived canonical origins retain their separate evidence; county authority is not assumed from a name or domain match. See [website evidence](counties/website_evidence.csv) and [ambiguities](counties/ambiguities.csv).

Trellis law captures contain {body.get('observed_nonempty_legal_container', 0):,} observed legal-body representations, {body.get('directory_navigation', 0):,} directories and {body.get('missing_body/storage_placeholder', 0):,} missing-body placeholders. {trellis['observed_uncollected_urls']:,} discovered Trellis law URLs remain uncollected. Official [jurisdiction coverage](laws/jurisdiction_coverage.csv), [source gaps](laws/official_resource_gaps.csv) and [Trellis gaps](laws/trellis_gaps.csv) preserve incomplete coverage and source editions. Saved representations are not a count of unique laws or proof of legal currency.

The combined selection contains {summary['selected_capture_records']:,} capture records and {summary['distinct_searchable_texts']:,} distinct searchable texts. Search from the SCRAPE workspace:

```powershell
python -X utf8 delivery/focused_legal_corpus/search.py "due process" --exact --group laws
python -X utf8 delivery/focused_legal_corpus/search.py "Cocke" --group counties
```

[documents.csv](documents.csv), [documents.jsonl](documents.jsonl) and [focused.sqlite3](focused.sqlite3) contain the selection. The [shared text index](../../catalog/README.md) supplies full text. Original response/document bytes, text, dates and hashes remain under `sources/` and `corpus/`. Keep this package within the workspace; it references originals rather than duplicating the archive. Twelve preserved official source-entry pages have no verified text and remain outside full-text search, as recorded in the [count reconciliation](laws/provenance/index_count_reconciliation.json).

The [expansion report](expansion_20260913/summary.json) and [950 URL outcomes](expansion_20260913/trellis_attempts.csv) distinguish successful downloads and failures. All selected URLs reached an outcome. Acquisition is checkpointed with **{balance:,} Firecrawl credits** observed at {credit.get('checked_at')}; no collection automation is active. Larger pending queues are preserved.

[Summary](summary.json) · [Validation](validation.json) · [File hashes](package_files.json). CSV exports use UTF-8 and escape spreadsheet formula prefixes; JSONL preserves nested values. Retain county GEOID/FIPS fields as strings.
'''
    (PACKAGE / "README.md").write_text(readme, encoding="utf-8")
    root_readme = ROOT / "README.md"
    original = root_readme.read_text(encoding="utf-8")
    table = original.find("| Location |")
    if table < 0:
        raise RuntimeError("Root archive location table missing")
    intro = f'''# US legal corpus archive

The **[focused package](delivery/focused_legal_corpus/README.md)** now includes the completed county/law expansion: {expansion['saved_by_category'].get('county', 0):,} additional Trellis county profiles, {expansion['saved_by_category'].get('rules', 0):,} additional Trellis law pages and {progress['direct_official_county_entries']['downloaded_resources']:,} additional county website resources.

All {counties['county_equivalent_rows']:,} saved Census county-equivalent rows are inventoried; {counties['matched_counties']:,} have clear saved Trellis profile matches. The full underlying nationwide content corpus remains incomplete. The package contains {summary['selected_capture_records']:,} capture records and {summary['distinct_searchable_texts']:,} distinct searchable texts, with originals preserved in this workspace.

Acquisition is checkpointed; no continuation automation is active. [Expansion results](delivery/focused_legal_corpus/expansion_20260913/summary.json) · [Measured status](reports/STATUS.md) · [Machine-readable status](reports/status.json).

'''
    root_readme.write_text(intro + original[table:], encoding="utf-8")
    broken = []
    for path in (PACKAGE / "README.md", root_readme):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if not re.match(r"^[a-zA-Z]+://", target) and not (path.parent / target.split("#")[0]).exists():
                broken.append({"file": str(path), "target": target})
    if broken:
        raise RuntimeError("Broken delivery links: " + json.dumps(broken))
    validation["readme_links_valid"] = True
    write(PACKAGE / "validation.json", validation)
    inventory = [{"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
                 for path in sorted(PACKAGE.rglob("*")) if path.is_file() and path.name != "package_files.json" and "__pycache__" not in path.parts]
    write(PACKAGE / "package_files.json", {"generated_at": now, "files": inventory, "file_count": len(inventory),
        "total_bytes": sum(r["bytes"] for r in inventory), "exclusions": ["This inventory itself", "Python bytecode caches"],
        "scope": "Focused delivery folder; referenced originals and shared search index remain in SCRAPE."})
    print(json.dumps({"status": "delivered", "new_saved_trellis": len(saved), "new_official_county_resources": progress['direct_official_county_entries']['downloaded_resources'],
        "selected_records": summary['selected_capture_records'], "searchable_texts": summary['distinct_searchable_texts'],
        "county_matches": counties['matched_counties'], "remaining_credits": balance, "validation_issues": 0}, indent=2))


if __name__ == "__main__":
    main()
