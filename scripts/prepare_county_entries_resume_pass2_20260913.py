"""Append bounded county-entry passes while preserving earlier pass evidence."""
from collections import Counter, defaultdict
from contextlib import closing
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import prepare_county_entries_resume_20260913 as first

ROOT, COLLECTION = first.ROOT, first.OUT
PASS_NAME = "pass2"
OUT = COLLECTION / PASS_NAME
SELECTION = ROOT / "sources/trellis/resume_20260913/paid_selection_950.jsonl"
CANONICAL = ROOT / "reports/geography/county_robots_redirect_review_20260913/canonical_entry_candidates.jsonl"


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl(name, values):
    (OUT / name).write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in values), encoding="utf-8")


def inspect_existing(urls):
    groups = {first.host_group(urlsplit(url).netloc) for url in urls}
    aliases = {host for group in groups for host in (group, "www." + group)}
    placeholders = ",".join("?" for _ in urls)
    host_placeholders = ",".join("?" for _ in aliases)
    prior, barriers, controls, inputs = defaultdict(list), defaultdict(list), [], []
    for path in sorted((ROOT / "corpus").glob("*/corpus.sqlite3")):
        with closing(first.db_read(path)) as db:
            resources = [dict(row) for row in db.execute(f"SELECT id,url,status,attempts,last_http_status,raw_path,sha256,raw_complete FROM resources WHERE url IN ({placeholders})", sorted(urls))]
            hosts = [dict(row) for row in db.execute(f"SELECT * FROM hosts WHERE host IN ({host_placeholders}) AND (coalesce(pause_reason,'')!='' OR cooldown_until>?)", [*sorted(aliases), time.time()])]
        inputs.append({"path": first.relative(path), "read_only": True, "selected_resources": len(resources), "selected_host_barriers": len(hosts)})
        for row in resources:
            prior[row["url"]].append({"database": first.relative(path), **row})
        for row in hosts:
            barriers[first.host_group(row["host"])].append({"database": first.relative(path), **row})
        for group in groups:
            for host in (group, "www." + group):
                for scheme in ("http", "https"):
                    origin = scheme + "://" + host
                    path_control = path.parent / "controls/robots" / (hashlib.sha256(origin.encode()).hexdigest() + ".json")
                    if not path_control.is_file():
                        continue
                    control = first.read(path_control)
                    ref = {"path": first.relative(path_control), "sha256": first.sha(path_control), "origin": control.get("origin"),
                           "problem": control.get("problem"), "in_progress": control.get("in_progress", False), "checked_at": control.get("checked_at")}
                    controls.append(ref)
                    if control.get("problem") or control.get("in_progress"):
                        barriers[group].append({"saved_robots_control": ref})
    shared = ROOT / "corpus/_shared_hosts/hosts.sqlite3"
    with closing(first.db_read(shared)) as db:
        hosts = [dict(row) for row in db.execute(f"SELECT * FROM host_state WHERE host IN ({host_placeholders}) AND (coalesce(pause_reason,'')!='' OR cooldown_until>?)", [*sorted(aliases), time.time()])]
    for row in hosts:
        barriers[first.host_group(row["host"])].append({"database": first.relative(shared), **row})
    inputs.append({"path": first.relative(shared), "read_only": True, "selected_host_barriers": len(hosts)})
    catalog = ROOT / "catalog/documents.sqlite3"
    with closing(first.db_read(catalog)) as db:
        saved = [dict(row) for row in db.execute(f"SELECT source_url,raw_path,raw_sha256,version_id FROM latest_documents WHERE source_url IN ({placeholders})", sorted(urls))]
    for row in saved:
        prior[row["source_url"]].append({"database": first.relative(catalog), "status": "indexed_saved_capture", **row})
    inputs.append({"path": first.relative(catalog), "read_only": True, "selected_saved_captures": len(saved)})
    return prior, barriers, controls, inputs


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "preparation.json").exists():
        raise RuntimeError(PASS_NAME + " preparation already exists; preserve the snapshot")
    geo_path = ROOT / "reports/geography/summary.json"
    geo = first.read(geo_path)
    selection = [row for row in first.rows(SELECTION) if row["category"] == "county"]
    priority = {row["url"]: index for index, row in enumerate(selection)}
    page_path = ROOT / "sources/trellis/catalog/downloaded_pages.jsonl"
    pages = {row["url"]: row for row in first.rows(page_path) if row["url"] in priority and row["category"] == "coverage_county" and 200 <= (row.get("status") or 0) < 300}
    seed_path = ROOT / "reports/geography/official_county_website_seeds.jsonl"
    candidate_seeds = [row for row in first.rows(seed_path) if row["discovered_from"] in pages]
    assert all(row["provenance"]["county_profiles_snapshot_sha256"] == geo["inputs"]["county_profiles"]["sha256"] for row in candidate_seeds)
    canonical = first.rows(CANONICAL)
    assert len(canonical) == 4
    candidates = []
    for original in canonical:
        seed = copy.deepcopy(original)
        provenance = seed["provenance"]
        for field, hash_field in (("robots_control_path", "robots_control_sha256"), ("raw_path", "raw_sha256")):
            path = Path(provenance[field]).resolve()
            assert path.is_relative_to(ROOT) and first.sha(path) == provenance[hash_field]
        source = seed["trellis_profile_evidence"]
        assert first.sha(Path(source["provider_response_path"])) == source["provider_response_sha256"]
        target, original_url = urlsplit(seed["url"]), urlsplit(provenance["source_entry_url"])
        assert first.host_group(target.netloc) == first.host_group(original_url.netloc) and target.netloc != original_url.netloc
        assert seed["url"] == first.engine.canonical_url(seed["url"])
        assert target.path == "/" and seed["known_access_denial_in_reviewed_evidence"] is False
        seed.update(source_family="derived_county_entry_from_observed_robots_redirect_origin", scope={"host": target.netloc, "path_prefixes": ["/"]},
                    resume_pass=PASS_NAME, source_url_basis="derived_from_exact_observed_robots_redirect_origin; not an observed page href")
        candidates.append(seed)
    for original in sorted(candidate_seeds, key=lambda row: (priority[row["discovered_from"]], row["url"], row["jurisdiction"]["geoid"])):
        seed = copy.deepcopy(original)
        page = pages[seed["discovered_from"]]
        raw = (ROOT / page["source_path"]).resolve()
        assert raw.is_relative_to(ROOT) and raw.is_file() and first.sha(raw) == page["content_sha256"]
        assert seed["provenance"]["observed_href"] == seed["url"]
        assert seed["site_authority_verified"] is False
        seed["provenance"]["source_profile_evidence"] = {"source_url": page["url"], "provider_response_path": first.relative(raw),
            "provider_response_sha256": page["content_sha256"], "provider": page["provider"], "observed_at": page["observed_at"],
            "source_http_status_reported_by_provider": page["status"]}
        seed.update(resume_pass=PASS_NAME, source_url_basis="observed_Trellis_Website_field_href")
        candidates.append(seed)
    urls = {row["url"] for row in candidates}
    prior, barriers, controls, db_inputs = inspect_existing(urls)
    selected, selected_urls, decisions = [], set(), []
    for number, seed in enumerate(candidates, 1):
        url = seed["url"]
        all_barriers = barriers.get(first.host_group(urlsplit(url).netloc), [])
        allowed_lineage, active_barriers = [], []
        for barrier in all_barriers:
            control = barrier.get("saved_robots_control")
            # Only the four explicitly reviewed origin changes get this precise
            # lineage exception. Destination robots/denials are never bypassed.
            reviewed = seed["source_family"] == "derived_county_entry_from_observed_robots_redirect_origin"
            expected = seed.get("provenance", {})
            if reviewed and control and control["path"] == first.relative(Path(expected["robots_control_path"])) and control["sha256"] == expected["robots_control_sha256"] and control["problem"] == "robots_redirect_outside_host" and not control["in_progress"]:
                allowed_lineage.append(barrier)
            else:
                active_barriers.append(barrier)
        if prior.get(url):
            decision = "already_saved_or_enqueued"
        elif active_barriers:
            decision = "preserved_existing_host_or_robots_barrier"
        elif url not in selected_urls and len(selected_urls) >= 200:
            decision = "deferred_by_200_new_url_limit"
        else:
            decision = "selected_new_entry"
            selected.append(seed)
            selected_urls.add(url)
        decisions.append({"candidate_record": number, "url": url, "geoid": seed["jurisdiction"]["geoid"], "source_url_basis": seed["source_url_basis"],
            "decision": decision, "prior_records": prior.get(url, []), "barriers": active_barriers, "reviewed_source_redirect_lineage_preserved": allowed_lineage})
    cfg = first.read(COLLECTION / "config.json")
    hosts = {rule["host"]: set(rule["path_prefixes"]) for rule in cfg["allow"]}
    for seed in selected:
        hosts.setdefault(urlsplit(seed["url"]).netloc, set()).update(seed["scope"]["path_prefixes"])
    cfg["allow"] = [{"host": host, "path_prefixes": sorted(paths)} for host, paths in sorted(hosts.items())]
    cfg.update(workers=8, max_retries=0, follow_links=False, follow_external_allowed_links=False, max_depth=0, shared_host_dir="corpus/_shared_hosts")
    config = first.engine.Config.from_dict(cfg)
    assert all(config.allowed(seed["url"], seed["scope"])[0] for seed in selected)
    assert len(selected_urls) <= 200
    coverage = [{"url": row["url"], "downloaded_in_catalog_snapshot": row["url"] in pages,
                 "ready_matched_website_associations": sum(seed["discovered_from"] == row["url"] for seed in candidate_seeds)} for row in selection]
    with closing(first.db_read(COLLECTION / "corpus.sqlite3")) as db:
        prior_run_ids = [row[0] for row in db.execute("SELECT id FROM runs ORDER BY rowid")]
        baseline_resources = db.execute("SELECT count(*) FROM resources").fetchone()[0]
    inputs = [{"path": first.relative(path), "sha256": first.sha(path)} for path in (SELECTION, CANONICAL, geo_path, page_path, seed_path)]
    jsonl("catalog_selected_county_pages.snapshot.jsonl", pages.values())
    jsonl("geography_ready_seeds.snapshot.jsonl", candidate_seeds)
    jsonl("canonical_candidates.snapshot.jsonl", canonical)
    jsonl("profile_selection_coverage.jsonl", coverage)
    jsonl("seeds.jsonl", selected)
    jsonl("decisions.jsonl", decisions)
    jsonl("saved_controls_reviewed.jsonl", sorted(controls, key=lambda row: row["path"]))
    write("geography_summary.snapshot.json", geo)
    write("config_before.json", first.read(COLLECTION / "config.json"))
    write("config.json", cfg)
    preserved = ["preparation.json", "seeds.jsonl", "entry_outcomes.jsonl", "new_successes.jsonl", "pass_summary.json", "validation.json", "run_process.json"]
    previous_names = ["pass" + str(number) for number in range(2, int(PASS_NAME.removeprefix("pass")))]
    previous_pass_evidence = {first.relative(path): first.sha(path) for previous in previous_names for path in sorted((COLLECTION / previous).glob("*")) if path.is_file()}
    summary = {"pass_name": PASS_NAME, "prepared_at": first.now(), "preparation_script_sha256": first.sha(Path(__file__)), "geography_generated_at": geo["generated_at"], "selected_profile_scope": len(selection),
        "selected_profiles_downloaded": len(pages), "ready_observed_href_associations": len(candidate_seeds), "canonical_origin_candidates": len(canonical),
        "selected_associations": len(selected), "selected_unique_urls": len(selected_urls), "selected_geoids": len({row["jurisdiction"]["geoid"] for row in selected}),
        "selected_source_url_basis_counts": dict(Counter(row["source_url_basis"] for row in selected)), "decision_counts": dict(Counter(row["decision"] for row in decisions)),
        "baseline_resources": baseline_resources, "prior_run_ids": prior_run_ids, "max_run_seconds": 180 if PASS_NAME == "pass5" else 600, "max_resource_attempts": 400,
        "network_requests_in_preparation": 0, "pass1_evidence_hashes": {name: first.sha(COLLECTION / name) for name in preserved},
        "previous_pass_evidence_hashes": previous_pass_evidence, "inputs": inputs, "database_inputs": db_inputs}
    write("preparation.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in ("inputs", "database_inputs", "pass1_evidence_hashes", "previous_pass_evidence_hashes")}, indent=2))


def reconcile():
    prep = first.read(OUT / "preparation.json")
    seeds = first.rows(OUT / "seeds.jsonl")
    with closing(first.db_read(COLLECTION / "corpus.sqlite3")) as db:
        runs = [dict(row) for row in db.execute("SELECT id,started_at,ended_at,stop_reason,processed FROM runs ORDER BY rowid") if row["id"] not in prep["prior_run_ids"]]
        assert runs and all(run["ended_at"] for run in runs)
        run_ids = [run["id"] for run in runs]
        attempts = [dict(row) for row in db.execute("SELECT id,run_id,resource_id,status,http_status,raw_path,sha256,text_path FROM fetches") if row["run_id"] in run_ids]
        attempted_ids = {row["resource_id"] for row in attempts}
        resources = {row["id"]: dict(row) for row in db.execute("SELECT * FROM resources")}
        contexts = [dict(row) for row in db.execute("SELECT c.seed_url,c.jurisdiction_json,rc.resource_id FROM resource_contexts rc JOIN contexts c ON c.id=rc.context_id")]
    assert all(first.sha(COLLECTION / name) == digest for name, digest in prep["pass1_evidence_hashes"].items())
    assert all(first.sha(ROOT / name) == digest for name, digest in prep.get("previous_pass_evidence_hashes", {}).items())
    saved, gaps = [], []
    for resource_id in sorted(attempted_ids):
        row = resources[resource_id]
        if row["status"] == "downloaded" and row["raw_complete"] == 1 and 200 <= (row["last_http_status"] or 0) < 300:
            raw = COLLECTION / row["raw_path"]
            assert first.sha(raw) == row["sha256"]
            text = COLLECTION / row["text_path"] if row["text_path"] else None
            row.update(raw_workspace_path=first.relative(raw), text_workspace_path=first.relative(text) if text else None,
                       text_sha256=first.sha(text) if text else None)
            saved.append(row)
        else:
            gaps.append(row)
    by_id = {row["id"]: row for row in saved}
    links = defaultdict(set)
    for context in contexts:
        links[(context["seed_url"], json.loads(context["jurisdiction_json"]).get("geoid"))].add(context["resource_id"])
    by_url = {row["url"]: row for row in resources.values()}
    context_resource_ids = {resource_id for seed in seeds for resource_id in links[(seed["url"], seed["jurisdiction"]["geoid"])]}
    # Time/pacing checkpoints may have no fetch row at all. Preserve those
    # unfetched resources explicitly instead of omitting them from the gap file.
    existing_gap_ids = {row["id"] for row in gaps}
    for resource_id in sorted(context_resource_ids - attempted_ids):
        row = resources[resource_id]
        if row["status"] != "downloaded" and resource_id not in existing_gap_ids:
            gaps.append({**row, "attempted_in_this_pass": False})
    waiting = [row for row in gaps if row["status"] in ("pending", "retry_wait", "fetching")]
    if waiting:
        with closing(first.db_read(ROOT / "corpus/_shared_hosts/hosts.sqlite3")) as db:
            for row in waiting:
                state = db.execute("SELECT * FROM host_state WHERE host=?", (row["host"],)).fetchone()
                row["shared_host_policy_at_checkpoint"] = dict(state) if state else None
                row["gap_note"] = "Preserved at the bounded run checkpoint; no pacing or access controls were accelerated or cleared"
    outcomes = []
    for seed in seeds:
        resource = by_url[seed["url"]]
        saved_ids = sorted(links[(seed["url"], seed["jurisdiction"]["geoid"])] & by_id.keys())
        outcomes.append({"url": seed["url"], "jurisdiction": seed["jurisdiction"], "site_authority_verified": False,
            "source_url_basis": seed["source_url_basis"], "status": resource["status"], "resource_id": resource["id"],
            "successful_resource_ids": saved_ids, "raw_paths": [by_id[i]["raw_workspace_path"] for i in saved_ids],
            "text_paths": [by_id[i]["text_workspace_path"] for i in saved_ids if by_id[i]["text_workspace_path"]], "seed_evidence": seed})
    jsonl("new_fetches.jsonl", attempts)
    jsonl("entry_outcomes.jsonl", outcomes)
    jsonl("new_successes.jsonl", saved)
    jsonl("failures_and_gaps.jsonl", gaps)
    jsonl("waiting_resources.jsonl", waiting)
    summary = {"pass_name": PASS_NAME, "reconciled_at": first.now(), "runs": runs, "resource_attempts": len(attempts), "successful_resources": len(saved),
        "successful_raw_bytes": sum(row["byte_count"] for row in saved), "successful_text_files": sum(bool(row["text_path"]) for row in saved),
        "successful_extraction_status_counts": dict(Counter(row["extraction_status"] for row in saved)),
        "entry_associations_with_saved_capture": sum(bool(row["successful_resource_ids"]) for row in outcomes),
        "unique_entry_urls_with_saved_capture": len({row["url"] for row in outcomes if row["successful_resource_ids"]}),
        PASS_NAME + "_status_counts": dict(Counter(resources[i]["status"] for i in attempted_ids)),
        "cumulative_collection_status_counts": dict(Counter(row["status"] for row in resources.values())),
        "pass_context_resource_rows": len(context_resource_ids), "waiting_resources_checkpointed": len(waiting),
        "maximum_run_seconds": prep["max_run_seconds"],
        "canonical_origin_candidate_outcomes": [{key: row[key] for key in ("url", "status", "successful_resource_ids")} for row in outcomes if row["source_url_basis"].startswith("derived_")],
        "pass1_evidence_unchanged": True, "previous_pass_evidence_unchanged": True, "successful_hash_validation_issues": 0, "full_source_corpus_complete": False}
    write("pass_summary.json", summary)
    write("validation.json", {"passed": True, "successful_raw_hashes_verified": len(saved), "successful_text_hashes_computed": sum(bool(row["text_path"]) for row in saved),
          "entry_associations_preserved": len(outcomes), "pass1_evidence_hashes_unchanged": True, "issues": []})
    (OUT / "README.md").write_text("# County entry " + PASS_NAME + "\n\nThis bounded pass appends newly observed Website hrefs from the 600 selected Trellis county profiles. Four separately reviewed robots-origin candidates remain in the candidate inventory, subject to exact-URL deduplication; they are never silently retried. `preparation.json` records the snapshot and selection; `source_url_basis` distinguishes observed page hrefs from explicitly derived origin entries. Existing attempts and barriers were excluded. Only the exact four reviewed source robots redirects supplied lineage for fresh destination robots checks; no source controls were cleared. Standard crawler: eight workers, shared pacing, no link following, depth zero, no automatic retries, normal in-scope redirects, 400 attempts/" + str(prep["max_run_seconds"]) + " seconds maximum.\n\n`entry_outcomes`, `new_successes`, `failures_and_gaps`, `waiting_resources`, `pass_summary.json` and `validation.json` preserve results, including requests with no completed attempt when the budget ends. All prior pass evidence hashes remain unchanged. Website authority remains unverified. The delivery and main index were not rebuilt by this pass.\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("prepare", "reconcile"), default="prepare")
    parser.add_argument("--pass-name", choices=("pass2", "pass3", "pass4", "pass5"), default="pass2")
    args = parser.parse_args()
    PASS_NAME = args.pass_name
    OUT = COLLECTION / PASS_NAME
    reconcile() if args.action == "reconcile" else prepare()
