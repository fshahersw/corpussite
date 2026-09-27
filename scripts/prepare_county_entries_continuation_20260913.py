"""Offline prepare/reconcile of an authorized finite county-entry continuation."""
from collections import Counter, defaultdict
from contextlib import closing
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

import prepare_county_entries_resume_20260913 as util
import prepare_county_entries_resume_pass2_20260913 as prior_tools

ROOT = util.ROOT
OUT = ROOT / "corpus/county_entries_continuation_20260913"
OLD = ROOT / "corpus/county_entries_resume_20260913"
PRIORITY_URLS = ("https://www.huntington.in.us/county", "https://www.lagrangecounty.org/")
MAX_ENTRIES = 600
MAX_ATTEMPTS = 800
MAX_SECONDS = 600


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl(name, data):
    (OUT / name).write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in data), encoding="utf-8")


def disposition(records, barriers):
    if any(row["status"] in ("downloaded", "indexed_saved_capture") for row in records):
        return "excluded_existing_saved_capture"
    if barriers:
        return "excluded_preserved_host_or_robots_barrier"
    if records:
        if all(row["status"] == "pending" and row.get("attempts") == 0 and not row.get("raw_path") and not row.get("raw_complete") for row in records):
            return "selected_unattempted_pending_transfer"
        return "excluded_prior_attempt_or_nonpending_state"
    return "selected_new_observed_entry"


def validate_disposition():
    pending = {"status": "pending", "attempts": 0, "raw_path": None, "raw_complete": 0}
    assert disposition([pending], []) == "selected_unattempted_pending_transfer"
    assert disposition([pending], [{"pause_reason": "robots_disallowed"}]).startswith("excluded")
    assert disposition([{**pending, "attempts": 1}], []).startswith("excluded")
    assert disposition([{**pending, "raw_path": "raw/payload"}], []).startswith("excluded")
    assert disposition([{**pending, "status": "retry_wait"}], []).startswith("excluded")
    assert disposition([pending, {"status": "downloaded"}], []) == "excluded_existing_saved_capture"
    assert disposition([pending, {"status": "forbidden"}], []).startswith("excluded")
    assert disposition([], []) == "selected_new_observed_entry"
    assert disposition([], [{"saved_robots_control": {"problem": "robots_redirect_outside_host"}}]).startswith("excluded")


def prepare():
    validate_disposition()
    if OUT.exists():
        raise RuntimeError("Continuation collection already exists; preserve its evidence")
    geo_path = ROOT / "reports/geography/summary.json"
    seeds_path = ROOT / "reports/geography/official_county_website_seeds.jsonl"
    pages_path = ROOT / "sources/trellis/catalog/downloaded_pages.jsonl"
    geo = util.read(geo_path)
    originals = util.rows(seeds_path)
    pages = {row["url"]: row for row in util.rows(pages_path) if row["category"] == "coverage_county" and 200 <= (row.get("status") or 0) < 300}
    urls = {row["url"] for row in originals}
    prior, barriers, controls, databases = prior_tools.inspect_existing(urls)
    pending_databases = {r["database"] for url, records in prior.items() if disposition(records, barriers.get(util.host_group(urlsplit(url).netloc), [])) == "selected_unattempted_pending_transfer" for r in records}
    assert pending_databases <= {"corpus/county_sites/corpus.sqlite3", "corpus/official_courts/corpus.sqlite3", "corpus/county_entries_resume_20260913/corpus.sqlite3"}
    probe_cmd = "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -like '*corpus_crawler.py*' -and ($_.CommandLine -like '*county_sites*' -or $_.CommandLine -like '*official_courts*' -or $_.CommandLine -like '*county_entries_resume_20260913*') } | Select-Object ProcessId, CreationDate | ConvertTo-Json -Compress"
    probe = subprocess.run(["powershell", "-NoProfile", "-Command", probe_cmd], capture_output=True, text=True, check=True)
    assert not probe.stdout.strip(), "A source collection crawler process is still active"
    source_activity = {"process_probe_at": util.now(), "matching_source_crawler_processes": [], "method": "Win32_Process python.exe command-line filter for the three explicit source collection names and corpus_crawler.py", "databases": []}
    for name in sorted(pending_databases):
        with closing(util.db_read(ROOT / name)) as db:
            source_activity["databases"].append({"database": name, "unfinished_run_rows": [dict(r) for r in db.execute("SELECT id,started_at,ended_at,stop_reason,processed FROM runs WHERE ended_at IS NULL")], "fetching_resource_rows_preserved": db.execute("SELECT COUNT(*) FROM resources WHERE status='fetching'").fetchone()[0]})
    # Read-only snapshots of old queues remain unchanged. Only genuinely
    # unattempted pending entries can be transferred under the renewed request.
    hashes = {}
    protected = [OLD / "corpus.sqlite3", OLD / "config.json", OLD / "pass5/final_checkpoint.json", OLD / "pass5/preparation.json", ROOT / "corpus/county_sites/corpus.sqlite3", ROOT / "corpus/county_sites/config.json"]
    protected += [Path(ROOT / row["path"]) for row in controls]
    for path in protected:
        hashes[util.relative(path)] = util.sha(path)
    selected, decisions, checked_profiles = [], [], {}
    chosen_urls = set()
    priority = {url: idx for idx, url in enumerate(PRIORITY_URLS)}
    originals.sort(key=lambda s: (priority.get(s["url"], 2), s["jurisdiction"]["geoid"], s["url"]))
    for number, original in enumerate(originals, 1):
        seed = copy.deepcopy(original)
        url = seed["url"]
        records = prior.get(url, [])
        blocks = barriers.get(util.host_group(urlsplit(url).netloc), [])
        decision = disposition(records, blocks)
        errors = []
        page = pages.get(seed["discovered_from"])
        if not page:
            errors.append("missing_successful_saved_county_profile")
        else:
            raw = (ROOT / page["source_path"]).resolve()
            if not raw.is_relative_to(ROOT) or not raw.is_file():
                errors.append("profile_path_outside_workspace_or_missing")
            else:
                key = util.relative(raw)
                checked_profiles.setdefault(key, util.sha(raw))
                if checked_profiles[key] != page["content_sha256"]:
                    errors.append("profile_hash_mismatch")
                seed["provenance"]["source_profile_evidence"] = {"source_url": page["url"], "provider_response_path": key, "provider_response_sha256": page["content_sha256"], "provider": page["provider"], "observed_at": page["observed_at"], "source_http_status_reported_by_provider": page["status"]}
        if seed["provenance"].get("county_profiles_snapshot_sha256") != geo["inputs"]["county_profiles"]["sha256"]:
            errors.append("geography_profile_snapshot_mismatch")
        if seed["provenance"].get("observed_href") != url or util.engine.canonical_url(url) != url:
            errors.append("not_exact_canonical_observed_href")
        if seed.get("site_authority_verified") is not False:
            errors.append("unexpected_authority_claim")
        if errors:
            decision = "excluded_source_integrity_error"
        elif decision == "selected_unattempted_pending_transfer":
            for record in records:
                path = ROOT / record["database"]
                with closing(util.db_read(path)) as db:
                    assert db.execute("SELECT COUNT(*) FROM fetches WHERE resource_id=?", (record["id"],)).fetchone()[0] == 0
                    # Historical unfinished runs can remain after an earlier
                    # process died. The independent process probe is empty;
                    # never clear those rows or transfer their fetching URLs.
        if decision.startswith("selected") and url not in chosen_urls and len(chosen_urls) >= MAX_ENTRIES:
            decision = "deferred_by_600_entry_limit"
        if decision.startswith("selected"):
            seed.update(source_url_basis="observed_Trellis_Website_field_href", continuation_pass=OUT.name, collection_decision=decision)
            seed["provenance"]["prior_queue_lineage"] = records
            seed["provenance"]["old_queue_rows_preserved"] = True
            selected.append(seed)
            chosen_urls.add(url)
        decisions.append({"candidate_record": number, "url": url, "geoid": seed["jurisdiction"]["geoid"], "decision": decision, "source_url_basis": "observed_Trellis_Website_field_href", "prior_records": records, "barriers": blocks, "source_integrity_errors": errors})
    assert all(url in chosen_urls for url in PRIORITY_URLS), "Priority pending URLs must pass all current checks"
    assert len(chosen_urls) <= MAX_ENTRIES
    cfg = util.read(OLD / "config.json")
    cfg["allow"] = [{"host": host, "path_prefixes": ["/"]} for host in sorted({urlsplit(s["url"]).netloc for s in selected})]
    cfg.update(workers=8, max_retries=0, follow_links=False, follow_external_allowed_links=False, max_depth=0, shared_host_dir="corpus/_shared_hosts", per_host_delay=2.0)
    config = util.engine.Config.from_dict(cfg)
    assert all(config.allowed(s["url"], s["scope"])[0] for s in selected)
    with closing(util.db_read(ROOT / "corpus/_shared_hosts/hosts.sqlite3")) as db:
        shared = [dict(r) for r in db.execute("SELECT * FROM host_state WHERE host IN (?,?)", tuple(urlsplit(u).netloc for u in PRIORITY_URLS))]
    assert len(shared) == 2 and all(not r["pause_reason"] and not r["owner_pid"] and max(r["next_start_at"], r["cooldown_until"]) <= time.time() for r in shared)
    OUT.mkdir(parents=True)
    (OUT / "observed_county_seeds.snapshot.jsonl").write_bytes(seeds_path.read_bytes())
    write("geography_summary.snapshot.json", geo)
    jsonl("saved_county_profiles.snapshot.jsonl", pages.values())
    jsonl("seeds.jsonl", selected)
    jsonl("decisions.jsonl", decisions)
    jsonl("saved_controls_reviewed.jsonl", controls)
    jsonl("deferred_eligible_entries.jsonl", [r for r in decisions if r["decision"].startswith("deferred")])
    write("config.json", cfg)
    write("priority_pending_shared_policy.snapshot.json", {"observed_at": util.now(), "states": shared, "note": "Prior next-permitted times have elapsed. The standard new run will apply fresh robots checks and retain shared pacing."})
    write("source_queue_activity.snapshot.json", source_activity)
    prep = {"prepared_at": util.now(), "collection": util.relative(OUT), "geography_generated_at": geo["generated_at"], "observed_associations": len(originals), "observed_unique_urls": len(urls), "selected_associations": len(selected), "selected_unique_urls": len(chosen_urls), "selected_geoids": len({s["jurisdiction"]["geoid"] for s in selected}), "decision_counts": dict(Counter(r["decision"] for r in decisions)), "unique_selected_decision_counts": dict(Counter(disposition(prior.get(u, []), barriers.get(util.host_group(urlsplit(u).netloc), [])) for u in chosen_urls)), "profile_evidence_hashes_verified": len(checked_profiles), "source_integrity_errors": sum(bool(r["source_integrity_errors"]) for r in decisions), "max_distinct_entries": MAX_ENTRIES, "max_resource_attempts": MAX_ATTEMPTS, "max_run_seconds": MAX_SECONDS, "workers": 8, "follow_links": False, "max_depth": 0, "automatic_retries": 0, "site_authority_verified": False, "registry_only_candidates_selected": 0, "prior_evidence_hashes": hashes, "inputs": [{"path": util.relative(p), "sha256": util.sha(p)} for p in [geo_path, seeds_path, pages_path, Path(__file__)]], "database_inputs": databases, "network_requests_in_preparation": 0}
    assert not prep["source_integrity_errors"]
    write("preparation.json", prep)
    print(json.dumps({k: v for k, v in prep.items() if k not in ("prior_evidence_hashes", "inputs", "database_inputs")}, indent=2))


def reconcile():
    prep = util.read(OUT / "preparation.json")
    seeds = util.rows(OUT / "seeds.jsonl")
    with closing(util.db_read(OUT / "corpus.sqlite3")) as db:
        resources = [dict(r) for r in db.execute("SELECT * FROM resources ORDER BY id")]
        runs = [dict(r) for r in db.execute("SELECT id,started_at,ended_at,stop_reason,processed FROM runs ORDER BY rowid")]
        links = [dict(r) for r in db.execute("SELECT c.seed_url,c.jurisdiction_json,rc.resource_id FROM resource_contexts rc JOIN contexts c ON c.id=rc.context_id")]
        fetches = [dict(r) for r in db.execute("SELECT id,run_id,resource_id,status,http_status,raw_path,sha256,text_path,metadata_path FROM fetches")]
    assert runs and all(r["ended_at"] for r in runs) and not any(r["status"] == "fetching" for r in resources)
    prior_issues = [p for p, digest in prep["prior_evidence_hashes"].items() if util.sha(ROOT / p) != digest]
    assert not prior_issues, prior_issues
    references = {}
    def verify(path_value, kind, expected=None):
        path = (OUT / path_value).resolve()
        assert path.is_relative_to(OUT) and path.is_file()
        key = util.relative(path)
        if key not in references:
            references[key] = {"path": key, "sha256": util.sha(path), "bytes": path.stat().st_size, "kinds": []}
        ref = references[key]
        assert expected is None or ref["sha256"] == expected
        if kind not in ref["kinds"]:
            ref["kinds"].append(kind)
        return ref
    for row in resources + fetches:
        for field, kind in (("raw_path", "raw"), ("text_path", "text"), ("metadata_path", "metadata")):
            if row.get(field):
                verify(row[field], kind, row.get("sha256") if field == "raw_path" else None)
    for path in sorted((OUT / "controls/robots").glob("*.json")):
        verify(path.relative_to(OUT), "robots_control")
        for observation in util.read(path).get("observations", []):
            if observation.get("raw_path"):
                verify(observation["raw_path"], "robots_response", observation.get("sha256"))
    successes, gaps = [], []
    for row in resources:
        if row["status"] == "downloaded" and row["raw_complete"] == 1 and 200 <= (row["last_http_status"] or 0) < 300:
            row.update(raw_workspace_path=util.relative(OUT / row["raw_path"]), text_workspace_path=util.relative(OUT / row["text_path"]) if row["text_path"] else None, text_sha256=verify(row["text_path"], "text")["sha256"] if row["text_path"] else None)
            successes.append(row)
        else:
            gaps.append(row)
    by_id = {r["id"]: r for r in successes}
    by_url = {r["url"]: r for r in resources}
    association = defaultdict(set)
    for link in links:
        association[(link["seed_url"], json.loads(link["jurisdiction_json"]).get("geoid"))].add(link["resource_id"])
    outcomes = []
    for seed in seeds:
        saved = sorted(association[(seed["url"], seed["jurisdiction"]["geoid"])] & by_id.keys())
        outcomes.append({"url": seed["url"], "jurisdiction": seed["jurisdiction"], "site_authority_verified": False, "source_url_basis": seed["source_url_basis"], "status": by_url[seed["url"]]["status"], "resource_id": by_url[seed["url"]]["id"], "successful_resource_ids": saved, "raw_paths": [by_id[i]["raw_workspace_path"] for i in saved], "text_paths": [by_id[i]["text_workspace_path"] for i in saved if by_id[i]["text_workspace_path"]], "seed_evidence": seed})
    waiting = [r for r in gaps if r["status"] in ("pending", "retry_wait", "fetching")]
    with closing(util.db_read(ROOT / "corpus/_shared_hosts/hosts.sqlite3")) as db:
        for row in waiting:
            state = db.execute("SELECT * FROM host_state WHERE host=?", (row["host"],)).fetchone()
            row["shared_policy_at_checkpoint"] = dict(state) if state else None
            row["note"] = "Persisted acquisition gap; host timing and access barriers were not cleared"
    assert len(outcomes) == prep["selected_associations"] and len({r["url"] for r in outcomes}) == prep["selected_unique_urls"]
    jsonl("new_fetches.jsonl", fetches)
    jsonl("new_successes.jsonl", successes)
    jsonl("entry_outcomes.jsonl", outcomes)
    jsonl("remaining_failures_and_gaps.jsonl", gaps)
    jsonl("waiting_resources.jsonl", waiting)
    jsonl("validated_file_references.jsonl", sorted(references.values(), key=lambda r: r["path"]))
    summary = {"reconciled_at": util.now(), "collection": util.relative(OUT), "runs": runs, "resource_rows": len(resources), "fetch_attempts": len(fetches), "status_counts": dict(Counter(r["status"] for r in resources)), "successful_resources": len(successes), "successful_raw_bytes": sum(r["byte_count"] or 0 for r in successes), "successful_text_files": sum(bool(r["text_path"]) for r in successes), "successful_extraction_status_counts": dict(Counter(r["extraction_status"] for r in successes)), "entry_associations_with_saved_capture": sum(bool(r["successful_resource_ids"]) for r in outcomes), "geoids_with_saved_capture": len({r["jurisdiction"]["geoid"] for r in outcomes if r["successful_resource_ids"]}), "priority_pending_outcomes": [{k: r[k] for k in ("url", "jurisdiction", "status", "successful_resource_ids", "raw_paths", "text_paths")} for r in outcomes if r["url"] in PRIORITY_URLS], "waiting_resources": len(waiting), "deferred_eligible_entries": len(util.rows(OUT / "deferred_eligible_entries.jsonl")), "prior_evidence_unchanged": True, "validated_saved_references": len(references), "validation_issues": 0, "full_source_corpus_complete": False}
    write("pass_summary.json", summary)
    write("validation.json", {"validated_at": util.now(), "passed": True, "new_saved_file_references_verified": len(references), "successful_raw_hashes_verified": len(successes), "successful_text_files_checksummed": sum(bool(r["text_path"]) for r in successes), "source_integrity_errors": 0, "old_evidence_hashes_unchanged": len(prep["prior_evidence_hashes"]), "association_rows_preserved": len(outcomes), "no_active_resources": True, "issues": []})
    (OUT / "README.md").write_text("# County entry continuation\n\nThis finite batch follows the renewed request to collect remaining county entry pages. It uses exact Website hrefs from successfully saved Trellis county profiles, with Census geography and profile hashes. Prior unattempted pending entries retain their old queue lineage; the old rows and controls are unchanged. Already captured or attempted URLs, saved robots problems, and paused hosts were excluded. Registry-only candidates were excluded; current governmental ownership remains independently unverified.\n\nThe standard crawler uses eight workers, shared host coordination, a two-second floor plus source crawl delays, no link following, depth zero, no automatic retries, and ordinary in-scope redirects. The bounded run permits at most 600 distinct seeds, 800 attempts, and 600 seconds. Raw bodies, collector text, metadata, and robots evidence are retained. `validated_file_references.jsonl` checks all new saved file references and raw hashes; extracted text and metadata receive separate SHA-256 checksums.\n\nSee `preparation.json`, `decisions.jsonl`, `seeds.jsonl`, `entry_outcomes.jsonl`, `pass_summary.json`, and `waiting_resources.jsonl`. The full corpus is not complete. For integration, add this exact collection to the county delivery builder's `COUNTY_COLLECTIONS` list, rebuild the main document index using the generic corpus adapter, and regenerate county exports from existing geography. Preserve prior queue records as historical checkpoints: `prior_queue_lineage` joins their pending entries to this continuation. No consolidated package, index, UI, or primary RUNBOOK was modified by this task.\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reconcile":
        reconcile()
    else:
        prepare()
