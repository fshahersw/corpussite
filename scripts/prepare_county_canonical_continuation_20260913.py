"""Review exact county www/apex redirect evidence and prepare one bounded pass."""
from collections import Counter, defaultdict
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit, urljoin
import prepare_county_entries_resume_20260913 as util
import prepare_county_entries_resume_pass2_20260913 as passes
import prepare_county_entries_continuation_20260913 as observed

ROOT = util.ROOT
COLLECTION = observed.OUT
OUT = COLLECTION / "canonical_pass2"
ROBOTS_FAMILY = "derived_county_entry_from_observed_robots_redirect_origin"
HTTP_FAMILY = "observed_county_entry_from_same_domain_http_redirect"


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl(name, values):
    (OUT / name).write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in values), encoding="utf-8")


def exact_alias(a, b):
    return bool(a and b and (a == "www." + b or b == "www." + a))


def checked_local(path, digest=None):
    p = path.resolve()
    assert p.is_relative_to(ROOT) and p.is_file()
    actual = util.sha(p)
    assert digest is None or actual == digest
    return {"path": util.relative(p), "sha256": actual}


def alias_target(source_url, requested_url, location, kind):
    source = urlsplit(source_url)
    absolute = util.engine.canonical_url(urljoin(requested_url, location))
    if not absolute:
        return None
    target = urlsplit(absolute)
    if not exact_alias(source.hostname, target.hostname) or source.port or target.port or target.username or target.password:
        return None
    if kind == "robots":
        if target.path != "/robots.txt" or target.query or target.fragment:
            return None
        return util.engine.canonical_url(urlunsplit((target.scheme, target.netloc, source.path, source.query, "")))
    if source.path != target.path or source.query != target.query:
        return None
    return absolute


def prepare():
    assert alias_target("https://www.example.gov/county?q=1", "https://www.example.gov/robots.txt", "https://example.gov/robots.txt", "robots") == "https://example.gov/county?q=1"
    assert alias_target("https://example.gov/", "https://example.gov/", "https://www.example.gov/", "http") == "https://www.example.gov/"
    assert alias_target("https://example.gov/", "https://example.gov/robots.txt", "https://other.gov/robots.txt", "robots") is None
    assert alias_target("https://example.gov/", "https://example.gov/", "https://www.example.gov/login", "http") is None
    assert alias_target("https://example.gov/", "https://example.gov/robots.txt", "https://www.example.gov/login", "robots") is None
    if OUT.exists():
        raise RuntimeError("Canonical pass already exists; preserve its evidence")
    with closing(util.db_read(COLLECTION / "corpus.sqlite3")) as db:
        runs = [dict(r) for r in db.execute("SELECT id,started_at,ended_at,stop_reason,processed FROM runs")]
        resources = [dict(r) for r in db.execute("SELECT * FROM resources")]
        contexts = [dict(r) for r in db.execute("SELECT c.seed_json,rc.resource_id FROM contexts c JOIN resource_contexts rc ON rc.context_id=c.id")]
    assert runs and all(r["ended_at"] for r in runs) and not any(r["status"] == "fetching" for r in resources)
    seed_map = defaultdict(list)
    for seed in util.rows(COLLECTION / "seeds.jsonl"):
        seed_map[seed["url"]].append(seed)
    review, candidates, source_hashes = [], [], {}
    for resource in resources:
        if resource["status"] not in ("robots_redirect_outside_host", "redirect"):
            continue
        if resource["url"] not in seed_map:
            review.append({"resource_id": resource["id"], "source_url": resource["url"], "decision": "not_an_original_entry_seed"})
            continue
        kind = "robots" if resource["status"] == "robots_redirect_outside_host" else "http"
        origin = urlsplit(resource["url"])
        proof = {"source_collection": util.relative(COLLECTION), "source_resource_id": resource["id"], "source_entry_url": resource["url"]}
        if kind == "robots":
            control_path = COLLECTION / "controls/robots" / (hashlib.sha256((origin.scheme + "://" + origin.netloc).encode()).hexdigest() + ".json")
            control_ref = checked_local(control_path)
            control = util.read(control_path)
            assert control["problem"] == "robots_redirect_outside_host" and not control.get("in_progress")
            observations = control["observations"]
            response = observations[-1]
            if any(o.get("http_status") in (401, 403, 429) for o in observations):
                review.append({"resource_id": resource["id"], "source_url": resource["url"], "decision": "access_denial_in_control_history"})
                continue
            proof.update(robots_control_path=control_ref["path"], robots_control_sha256=control_ref["sha256"], requested_robots_url=response["requested_url"])
            source_hashes[control_ref["path"]] = control_ref["sha256"]
        else:
            metadata_ref = checked_local(COLLECTION / resource["metadata_path"])
            response = util.read(COLLECTION / resource["metadata_path"])
            assert response["requested_url"] == resource["url"] and response["status"] == "redirect"
            proof.update(redirect_metadata_path=metadata_ref["path"], redirect_metadata_sha256=metadata_ref["sha256"], requested_url=response["requested_url"])
            source_hashes[metadata_ref["path"]] = metadata_ref["sha256"]
        location = response.get("headers", {}).get("location", "")
        destination = alias_target(resource["url"], response["requested_url"], location, kind)
        if not destination or response.get("http_status") not in (301, 302, 303, 307, 308) or not response.get("raw_complete"):
            review.append({"resource_id": resource["id"], "source_url": resource["url"], "location_header": location, "decision": "not_a_complete_exact_www_alias_canonical_redirect"})
            continue
        raw_ref = checked_local(COLLECTION / response["raw_path"], response["sha256"])
        source_hashes[raw_ref["path"]] = raw_ref["sha256"]
        proof.update(http_status=response["http_status"], location_header=location, observed_redirect_target=util.engine.canonical_url(urljoin(response["requested_url"], location)), fetched_at=response["fetched_at"], raw_path=raw_ref["path"], raw_sha256=raw_ref["sha256"], raw_sha256_verified=True)
        for original in seed_map[resource["url"]]:
            seed = copy.deepcopy(original)
            profile = seed["provenance"]["source_profile_evidence"]
            checked_local(ROOT / profile["provider_response_path"], profile["provider_response_sha256"])
            seed.update(url=destination, discovered_from=resource["url"], source_family=ROBOTS_FAMILY if kind == "robots" else HTTP_FAMILY, category="county_government_entry", scope={"host": urlsplit(destination).netloc, "path_prefixes": ["/"]}, source_url_basis="derived_from_exact_observed_robots_redirect_origin; not an observed page href" if kind == "robots" else "observed_HTTP_Location_redirect_target", entry_url_observed_as_redirect_target=kind == "http", known_access_denial_in_reviewed_evidence=False, observed_redirect_target=proof["observed_redirect_target"], reviewed_at_utc=util.now(), target_robots_status="must pass unchanged crawler robots controls", trellis_profile_evidence=profile, resume_pass="canonical_pass2")
            seed["provenance"] = {**seed["provenance"], **proof}
            if kind == "robots":
                seed["entry_url_derivation_kind"] = "root_from_observed_robots_redirect_origin" if origin.path == "/" and not origin.query else "same_path_at_observed_robots_redirect_origin"
                seed["entry_url_derivation"] = "Original entry path and query preserved at the exact observed robots redirect destination origin; the entry URL itself was not observed in that Location header."
            else:
                seed["entry_url_derivation_kind"] = "observed_same_domain_http_location"
                seed["entry_url_derivation"] = "Exact HTTP Location target observed in a complete saved entry-page response; source and target are apex/www aliases with identical path and query."
            candidates.append(seed)
        review.append({"resource_id": resource["id"], "source_url": resource["url"], "candidate_url": destination, "evidence_kind": kind, "decision": "verified_exact_alias_candidate", "provenance": proof})
    prior, barriers, controls, inputs = passes.inspect_existing({s["url"] for s in candidates})
    selected, decisions = [], []
    for seed in candidates:
        url = seed["url"]
        active, lineage = [], []
        for barrier in barriers.get(util.host_group(urlsplit(url).netloc), []):
            control = barrier.get("saved_robots_control")
            proof = seed["provenance"]
            if seed["source_family"] == ROBOTS_FAMILY and control and control["path"] == proof["robots_control_path"] and control["sha256"] == proof["robots_control_sha256"] and control["problem"] == "robots_redirect_outside_host" and not control["in_progress"]:
                lineage.append(barrier)
            else:
                active.append(barrier)
        decision = observed.disposition(prior.get(url, []), active)
        if decision.startswith("selected"):
            seed["provenance"]["target_prior_queue_lineage"] = prior.get(url, [])
            selected.append(seed)
        decisions.append({"url": url, "geoid": seed["jurisdiction"]["geoid"], "source_family": seed["source_family"], "decision": decision, "prior_records": prior.get(url, []), "barriers": active, "reviewed_source_redirect_lineage_preserved": lineage})
    pending_ids = {r["id"] for r in resources if r["status"] == "pending" and not r["attempts"]}
    continued = {}
    for row in contexts:
        if row["resource_id"] in pending_ids:
            seed = json.loads(row["seed_json"])
            continued[(seed["url"], seed["jurisdiction"]["geoid"])] = seed
    all_seeds = list(continued.values()) + selected
    assert len({s["url"] for s in all_seeds}) <= 100
    cfg = util.read(COLLECTION / "config.json")
    allowed = {rule["host"] for rule in cfg["allow"]}
    allowed.update(urlsplit(s["url"]).netloc for s in all_seeds)
    cfg["allow"] = [{"host": host, "path_prefixes": ["/"]} for host in sorted(allowed)]
    assert not cfg["follow_links"] and cfg["max_depth"] == 0 and cfg["max_retries"] == 0
    config = util.engine.Config.from_dict(cfg)
    assert all(config.allowed(s["url"], s["scope"])[0] for s in all_seeds)
    OUT.mkdir(parents=True)
    preserved = ["preparation.json", "seeds.jsonl", "entry_outcomes.jsonl", "new_successes.jsonl", "pass_summary.json", "validation.json", "run_process.json", "validated_file_references.jsonl", "remaining_failures_and_gaps.jsonl", "waiting_resources.jsonl", "README.md"]
    prep = {"pass_name": "canonical_pass2", "prepared_at": util.now(), "selected_associations": len(all_seeds), "selected_unique_urls": len({s["url"] for s in all_seeds}), "reviewed_resources": len(review), "verified_candidate_associations": len(candidates), "selected_alias_associations": len(selected), "selected_alias_urls": len({s["url"] for s in selected}), "selected_alias_family_counts": dict(Counter(s["source_family"] for s in selected)), "carried_pending_contexts": len(continued), "carried_pending_resource_ids": sorted(pending_ids), "decision_counts": dict(Counter(r["decision"] for r in decisions)), "prior_run_ids": [r["id"] for r in runs], "baseline_resources": len(resources), "max_run_seconds": 600, "max_resource_attempts": 200, "pass1_evidence_hashes": {name: util.sha(COLLECTION / name) for name in preserved}, "previous_pass_evidence_hashes": {}, "redirect_source_hashes": source_hashes, "inputs": inputs, "network_requests_in_preparation": 0, "canonical_scope_fixture_count": 5}
    write("preparation.json", prep)
    write("config_before.json", util.read(COLLECTION / "config.json"))
    write("config.json", cfg)
    jsonl("all_redirects_reviewed.jsonl", review)
    jsonl("canonical_entry_candidates.jsonl", candidates)
    jsonl("decisions.jsonl", decisions)
    jsonl("seeds.jsonl", all_seeds)
    jsonl("saved_controls_reviewed.jsonl", controls)
    examples = {family: next((s for s in selected if s["source_family"] == family), None) for family in (ROBOTS_FAMILY, HTTP_FAMILY)}
    write("integration_schema_examples.json", examples)
    print(json.dumps({k: v for k, v in prep.items() if k not in ("pass1_evidence_hashes", "previous_pass_evidence_hashes", "redirect_source_hashes", "inputs")}, indent=2))


def reconcile():
    prep = util.read(OUT / "preparation.json")
    assert all(util.sha(ROOT / p) == h for p, h in prep["redirect_source_hashes"].items())
    original = util.read(COLLECTION / "preparation.json")
    assert all(util.sha(ROOT / p) == h for p, h in original["prior_evidence_hashes"].items())
    passes.COLLECTION, passes.OUT, passes.PASS_NAME = COLLECTION, OUT, "canonical_pass2"
    passes.reconcile()
    refs = {}
    with closing(util.db_read(COLLECTION / "corpus.sqlite3")) as db:
        fetches = [dict(r) for r in db.execute("SELECT run_id,raw_path,sha256,text_path,metadata_path FROM fetches") if r["run_id"] not in prep["prior_run_ids"]]
    for row in fetches:
        for field in ("raw_path", "text_path", "metadata_path"):
            if row[field]:
                p = (COLLECTION / row[field]).resolve()
                assert p.is_relative_to(COLLECTION)
                ref = checked_local(p, row["sha256"] if field == "raw_path" else None)
                refs[ref["path"]] = ref
    jsonl("validated_new_file_references.jsonl", sorted(refs.values(), key=lambda r: r["path"]))
    validation = util.read(OUT / "validation.json")
    validation.update(new_saved_file_references_verified=len(refs), redirect_source_hashes_unchanged=True, earlier_collections_unchanged=True)
    write("validation.json", validation)
    (OUT / "README.md").write_text("# Reviewed county canonical-entry continuation\n\nThis pass uses saved exact apex/www redirect evidence from the preceding county-entry run. Robots redirects supply explicitly derived entries at the observed origin while preserving each original path/query. Page HTTP redirects supply their exact observed Location targets, with separate response metadata and raw hashes. The two evidence families remain distinct in seeds and county associations. All old hrefs, source controls, raw responses, queue namespaces, and first-pass reports are preserved.\n\nUncaptured canonical targets pass unchanged destination robots and shared host controls. Only the precise reviewed source robots control is treated as derivation lineage; other barriers, prior failed attempts, and saved captures remain excluded. Two pending contexts continue without clearing or retrying terminal states. Standard collector: eight workers, no link following, depth zero, no automatic retries, 200 attempts / 600 seconds maximum. `canonical_entry_candidates.jsonl`, `decisions.jsonl`, `integration_schema_examples.json`, `entry_outcomes.jsonl`, `pass_summary.json`, and `validation.json` retain evidence and results.\n", encoding="utf-8")


if __name__ == "__main__":
    reconcile() if len(sys.argv) > 1 and sys.argv[1] == "reconcile" else prepare()
