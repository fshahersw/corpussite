"""Prepare and reconcile a finite pass of previously staged Trellis county hrefs."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "corpus/county_entries_resume_20260913"
STAGED = ROOT / "reports/geography/county_sites_batch_20260913T090635Z"
sys.path.insert(0, str(ROOT / "pipeline"))
import corpus_crawler as engine


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl(name, values):
    (OUT / name).write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def db_read(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def host_group(value):
    return value.lower().removeprefix("www.")


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "preparation.json").exists() or (OUT / "corpus.sqlite3").exists():
        raise RuntimeError("Prepared collection already exists; preserve it and resume the persisted queue explicitly.")
    seeds = rows(STAGED / "seeds.jsonl")
    urls = {engine.canonical_url(row["url"]) for row in seeds}
    assert len(seeds) == 340 and len(urls) == 334 and None not in urls
    groups = {host_group(urlsplit(url).netloc) for url in urls}
    aliases = {host for group in groups for host in (group, "www." + group)}
    url_marks = ",".join("?" for _ in urls)
    host_marks = ",".join("?" for _ in aliases)
    prior, barriers, controls = defaultdict(list), defaultdict(list), []
    inputs = [{"path": relative(STAGED / "seeds.jsonl"), "sha256": sha(STAGED / "seeds.jsonl")},
              {"path": relative(STAGED / "config.json"), "sha256": sha(STAGED / "config.json")}]
    databases = sorted(path for path in (ROOT / "corpus").glob("*/corpus.sqlite3") if path.parent != OUT)
    for path in databases:
        with closing(db_read(path)) as db:
            selected = [dict(row) for row in db.execute(f"SELECT id,url,status,attempts,last_http_status,raw_path,sha256,raw_complete,metadata_path FROM resources WHERE url IN ({url_marks})", sorted(urls))]
            host_rows = [dict(row) for row in db.execute(f"SELECT * FROM hosts WHERE host IN ({host_marks}) AND (coalesce(pause_reason,'')!='' OR cooldown_until>?)", [*sorted(aliases), time.time()])]
        inputs.append({"path": relative(path), "read_only": True, "selected_resource_rows": len(selected), "selected_host_barriers": len(host_rows)})
        for row in selected:
            prior[row["url"]].append({"database": relative(path), **row})
        for row in host_rows:
            barriers[host_group(row["host"])].append({"database": relative(path), **row})
        # Saved negative robots outcomes remain barriers even if not host-paused.
        for group in groups:
            for host in (group, "www." + group):
                for scheme in ("http", "https"):
                    origin = scheme + "://" + host
                    control = path.parent / "controls/robots" / (hashlib.sha256(origin.encode()).hexdigest() + ".json")
                    if not control.exists():
                        continue
                    data = read(control)
                    evidence = {"path": relative(control), "sha256": sha(control), "origin": data.get("origin"), "problem": data.get("problem"), "checked_at": data.get("checked_at"), "in_progress": data.get("in_progress", False)}
                    controls.append(evidence)
                    if data.get("problem") or data.get("in_progress"):
                        barriers[group].append({"saved_robots_control": evidence})
    shared = ROOT / "corpus/_shared_hosts/hosts.sqlite3"
    if not shared.exists():
        shared = ROOT / "corpus/_shared_hosts/coordination.sqlite3"
    if not shared.exists():
        raise FileNotFoundError("Existing shared host coordinator database was not found")
    with closing(db_read(shared)) as db:
        shared_rows = [dict(row) for row in db.execute(f"SELECT * FROM host_state WHERE host IN ({host_marks}) AND (coalesce(pause_reason,'')!='' OR cooldown_until>?)", [*sorted(aliases), time.time()])]
    inputs.append({"path": relative(shared), "read_only": True, "selected_host_barriers": len(shared_rows)})
    for row in shared_rows:
        barriers[host_group(row["host"])].append({"database": relative(shared), **row})
    catalog = ROOT / "catalog/documents.sqlite3"
    with closing(db_read(catalog)) as db:
        captures = [dict(row) for row in db.execute(f"SELECT source_url,raw_path,raw_sha256,version_id,capture_kind FROM latest_documents WHERE source_url IN ({url_marks})", sorted(urls))]
    inputs.append({"path": relative(catalog), "read_only": True, "selected_saved_captures": len(captures)})
    for row in captures:
        prior[row["source_url"]].append({"database": relative(catalog), "status": "indexed_saved_capture", **row})
    decisions, selected = [], []
    profile_hashes = {}
    for number, seed in enumerate(seeds, 1):
        url = engine.canonical_url(seed["url"])
        evidence = seed["provenance"]["source_profile_evidence"]
        path = Path(evidence["provider_response_path"])
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError("Provider evidence escaped the workspace")
        if path not in profile_hashes:
            profile_hashes[path] = sha(path) if path.is_file() else None
        verified = profile_hashes[path] == evidence["provider_response_sha256"]
        assert seed["provenance"]["observed_href"] == url
        assert seed["site_authority_verified"] is False
        if not verified:
            decision = "source_profile_integrity_mismatch"
        elif prior.get(url):
            decision = "already_saved_or_enqueued"
        elif barriers.get(host_group(urlsplit(url).netloc)):
            decision = "preserved_existing_host_or_robots_barrier"
        else:
            decision = "selected_new_observed_county_entry"
            selected.append(seed)
        decisions.append({"staged_record": number, "url": url, "geoid": seed["jurisdiction"]["geoid"], "decision": decision,
                          "source_profile_path": relative(path), "source_profile_hash_verified": verified,
                          "prior_records": prior.get(url, []), "barriers": barriers.get(host_group(urlsplit(url).netloc), [])})
    selected.sort(key=lambda row: (row["url"], row["jurisdiction"]["geoid"]))
    cfg = read(STAGED / "config.json")
    chosen_hosts = {urlsplit(row["url"]).netloc for row in selected}
    cfg["allow"] = [rule for rule in cfg["allow"] if rule["host"] in chosen_hosts]
    cfg.update(workers=8, max_retries=0, follow_links=False, follow_external_allowed_links=False, max_depth=0,
               shared_host_dir="corpus/_shared_hosts", per_host_delay=2.0)
    config = engine.Config.from_dict(cfg)
    assert all(config.allowed(row["url"], row["scope"])[0] for row in selected)
    assert len({row["url"] for row in selected}) <= 334
    (OUT / "staged_seeds.snapshot.jsonl").write_bytes((STAGED / "seeds.jsonl").read_bytes())
    jsonl("seeds.jsonl", selected)
    jsonl("decisions.jsonl", decisions)
    jsonl("saved_controls_reviewed.jsonl", sorted(controls, key=lambda row: row["path"]))
    write("config.json", cfg)
    summary = {"prepared_at": now(), "staged_associations": 340, "staged_unique_urls": 334,
               "selected_associations": len(selected), "selected_unique_urls": len({row["url"] for row in selected}),
               "selected_geoids": len({row["jurisdiction"]["geoid"] for row in selected}), "selected_hosts": len(chosen_hosts),
               "decision_counts": dict(Counter(row["decision"] for row in decisions)), "saved_controls_reviewed": len(controls),
               "profile_evidence_files_hash_checked": len(profile_hashes), "inputs": inputs,
               "workers": 8, "max_resource_attempts": 400, "max_run_seconds": 600, "automatic_retries": 0,
               "follow_links": False, "max_depth": 0, "same_host_in_scope_http_redirects_allowed": True,
               "shared_host_coordinator": relative(shared), "per_host_delay_floor_seconds": 2.0,
               "site_authority_verified": False, "preparation_network_requests": 0}
    write("preparation.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "inputs"}, indent=2))


def reconcile():
    preparation = read(OUT / "preparation.json")
    selected = rows(OUT / "seeds.jsonl")
    with closing(db_read(OUT / "corpus.sqlite3")) as db:
        resources = [dict(row) for row in db.execute("SELECT * FROM resources ORDER BY id")]
        runs = [dict(row) for row in db.execute("SELECT id,started_at,ended_at,stop_reason,processed FROM runs ORDER BY rowid")]
        context_links = [dict(row) for row in db.execute("SELECT c.seed_url,c.jurisdiction_json,rc.resource_id FROM resource_contexts rc JOIN contexts c ON c.id=rc.context_id ORDER BY rc.resource_id,rc.context_id")]
        attempts = db.execute("SELECT count(*) FROM fetches").fetchone()[0]
    assert runs and all(row["ended_at"] for row in runs), "Wait for the bounded pass to checkpoint before final reconciliation"
    failures, successes = [], []
    for row in resources:
        if row["status"] == "downloaded" and row["raw_complete"] == 1 and 200 <= (row["last_http_status"] or 0) < 300:
            raw = OUT / row["raw_path"]
            assert raw.is_file() and sha(raw) == row["sha256"]
            text = OUT / row["text_path"] if row["text_path"] else None
            row["raw_workspace_path"] = relative(raw)
            row["text_workspace_path"] = relative(text) if text else None
            row["text_sha256"] = sha(text) if text else None
            successes.append(row)
        else:
            failures.append(row)
    by_url = {row["url"]: row for row in resources}
    associations = defaultdict(set)
    successful_ids = {row["id"]: row for row in successes}
    for link in context_links:
        key = (link["seed_url"], json.loads(link["jurisdiction_json"]).get("geoid"))
        associations[key].add(link["resource_id"])
    outcomes = []
    for row in selected:
        associated_ids = sorted(associations[(row["url"], row["jurisdiction"]["geoid"])])
        saved_ids = [resource_id for resource_id in associated_ids if resource_id in successful_ids]
        outcomes.append({"url": row["url"], "jurisdiction": row["jurisdiction"], "site_authority_verified": False,
                         "resource_id": by_url[row["url"]]["id"], "status": by_url[row["url"]]["status"],
                         "redirect_url": by_url[row["url"]]["redirect_url"], "provenance": row["provenance"],
                         "associated_resource_ids": associated_ids, "successful_resource_ids": saved_ids,
                         "saved_raw_paths": [successful_ids[resource_id]["raw_workspace_path"] for resource_id in saved_ids],
                         "saved_text_paths": [successful_ids[resource_id]["text_workspace_path"] for resource_id in saved_ids if successful_ids[resource_id]["text_workspace_path"]],
                         "association_basis": "Recorded collector seed context; includes in-scope redirects; does not independently verify website authority"})
    jsonl("entry_outcomes.jsonl", outcomes)
    jsonl("new_successes.jsonl", successes)
    jsonl("remaining_failures_and_gaps.jsonl", failures)
    assert len(outcomes) == preparation["selected_associations"]
    assert len({row["url"] for row in outcomes}) == preparation["selected_unique_urls"]
    assert all(row["site_authority_verified"] is False for row in outcomes)
    assert all(set(row["successful_resource_ids"]) <= successful_ids.keys() for row in outcomes)
    assert not any(row["status"] == "fetching" for row in resources)
    write("pass_summary.json", {"reconciled_at": now(), "preparation": {key: value for key, value in preparation.items() if key != "inputs"},
          "runs": runs, "resource_rows": len(resources), "fetch_attempts": attempts,
          "status_counts": dict(Counter(row["status"] for row in resources)), "successful_resources": len(successes),
          "entry_associations_with_saved_capture": sum(bool(row["successful_resource_ids"]) for row in outcomes),
          "unique_entry_urls_with_saved_capture": len({row["url"] for row in outcomes if row["successful_resource_ids"]}),
          "successful_raw_bytes": sum(row["byte_count"] or 0 for row in successes),
          "successful_text_files": sum(bool(row["text_path"]) for row in successes),
          "successful_extraction_status_counts": dict(Counter(row["extraction_status"] for row in successes)),
          "success_hash_validation_issues": 0, "full_source_corpus_complete": False})
    write("validation.json", {"validated_at": now(), "passed": True, "unique_resource_urls": len({row["url"] for row in resources}),
          "entry_associations_preserved": len(outcomes), "unique_staged_entry_urls_preserved": len({row["url"] for row in outcomes}),
          "successful_raw_hashes_verified": len(successes), "successful_text_hashes_computed": sum(bool(row["text_path"]) for row in successes),
          "all_successful_context_references_resolve": True, "all_ownership_claims_remain_unverified": True,
          "no_in_flight_resources": True, "all_runs_checkpointed": True, "issues": []})
    (OUT / "README.md").write_text("# County entry resume pass\n\nThis finite pass uses only the existing 334-URL staged Trellis Website-field delta. `preparation.json`, `decisions.jsonl` and `saved_controls_reviewed.jsonl` preserve exclusions and evidence. The standard crawler ran with eight workers, shared host pacing, a two-second host delay floor, no link following, depth zero, no automatic retries, and a 400-attempt / 600-second limit. Ordinary in-scope HTTP redirects remain recorded and may resolve to a saved page. Previously saved/enqueued URLs and known host or robots barriers were excluded without altering their controls.\n\n`pass_summary.json`, `entry_outcomes.jsonl`, `new_successes.jsonl` and `remaining_failures_and_gaps.jsonl` record the outcome. Successful raw and extracted text hashes are checked. County associations are retained from the staged Census reconciliation; website ownership/authority remains Trellis-reported and independently unverified. This is an entry-page pass, not complete county or court record coverage. Completed delivery files, indexes and source collections are unchanged.\n", encoding="utf-8")
    print(json.dumps({"resource_rows": len(resources), "fetch_attempts": attempts, "successful_resources": len(successes), "status_counts": dict(Counter(row["status"] for row in resources))}, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reconcile":
        reconcile()
    else:
        prepare()
