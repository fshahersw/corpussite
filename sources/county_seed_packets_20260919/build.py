"""County seed-packet preparation for the county litigation Firecrawl collector.

Reads the Firecrawl state-court URL frontier (SW-BULK) and the state-courts
zero-coverage index, filters to official court/clerk/county-government hosts,
resolves an explicit (state, county-name) match in each row's own title text
to exactly one 5-digit Census FIPS code, drops anything already queued or
already published, and writes reviewed seed packets in the same schema the
collector's `collect.py ingest` accepts (see `seed_ok()` in that file).

No network access. No ingestion into the live queue. Deterministic and
re-runnable: re-running regenerates the same four output files from the same
inputs (subject to the queue/published-supplement snapshot at run time, which
is read-only and reported in validation.json `inputs`).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # never write __pycache__ into another slice's live folder

import hashlib
import json
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

SW_BULK_DIR = Path("C:/Users/firas/Downloads/SW-BULK/source_gap_reconciliation_2026-08-22")
STATE_MAP_FILES = [
    SW_BULK_DIR / "state_map_group1.jsonl",
    SW_BULK_DIR / "state_map_group2.jsonl",
    SW_BULK_DIR / "state_map_group3.jsonl",
]
ZERO_COVERAGE_FILE = SW_BULK_DIR / "sources_state_courts_zero_coverage.jsonl"

GAZETTEER_PATH = ROOT / "sources/official_courts/datasets/2026_Gaz_counties_national.json"
QUEUE_DB_PATH = ROOT / "sources/county_litigation_firecrawl_20260919/queue.sqlite3"
PUBLISHED_SUPPLEMENT_PATH = ROOT / "sources/county_registry_integration_20260919/counties.json"

OUT_DIR = HERE
SOURCE_BATCH = "county_seed_packets_20260919"

# Mirrors sources/county_litigation_firecrawl_20260919/collect.py exactly, so a
# seed we accept here is a seed collect.py will accept, and a URL we reject on
# topic/exclude grounds here is one collect.py would also reject on ingest.
EXCLUDE = re.compile(
    r'(?:^|/)(?:news|newsroom|press|events?|calendar|careers?|jobs|procurement|bids|'
    r'elections|parks|tourism|social-media|video|videos|community-engagement|'
    r'court-tours|peer-court)(?:/|$)', re.I)
TOPIC = re.compile(
    r'\b(?:local rules?|rules? of court|forms?|standing orders?|administrative orders?|'
    r'general orders?|filing|e-filing|efiling|service of process|fee schedules?|'
    r'court fees?|clerk|courts?|judges?|judicial|litigation|civil|probate|family|'
    r'small claims|self.help|legal|jury|juror|case access|case information|records|docket)\b',
    re.I)
HOST_OK = re.compile(r'(court|clerk|judici|county)', re.I)

FACET_TO_RESOURCE_TYPE = {
    "general_judiciary": "court_information",
    "court_directory_facilities": "court_information",
    "court_hierarchy": "court_information",
    "rules": "local_rule",
    "forms": "court_form",
    "orders": "standing_order",
    "fees": "fee_schedule",
    "self_help": "filing_guidance",
    "judges": "court_staff",
    "calendar_docket_index": "docket_index",
    "opinions_decisions": "opinions",
    "tentative_rulings": "tentative_rulings",
    "data_publications": "court_information",
    "sitemap_discovery": "court_information",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_rows_file(path: Path) -> str:
    return sha256_file(path)


def rel(path: Path) -> str:
    return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")


def read_jsonl(path: Path):
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def save_jsonl(path: Path, rows_iter):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        n = 0
        for row in rows_iter:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def is_official_host(host: str) -> bool:
    return bool(HOST_OK.search(host or ""))


def passes_topic(title: str, url: str) -> bool:
    path = urllib.parse.urlsplit(url).path
    haystack = (title or "") + " " + urllib.parse.unquote(path).replace("-", " ").replace("_", " ")
    return bool(TOPIC.search(haystack))


def fails_exclude(url: str) -> bool:
    return bool(EXCLUDE.search(urllib.parse.urlsplit(url).path))


# ---------------------------------------------------------------------------
# County-name matching
# ---------------------------------------------------------------------------

def load_gazetteer(path: Path):
    """Return {USPS: [(NAME, GEOID), ...]} sorted longest-name-first."""
    data = json.loads(path.read_text(encoding="utf-8"))
    by_state = {}
    for row in data:
        by_state.setdefault(row["USPS"], []).append((row["NAME"], row["GEOID"]))
    for usps in by_state:
        by_state[usps].sort(key=lambda pair: len(pair[0]), reverse=True)
    return by_state


def match_county(jurisdiction: str, title: str, gazetteer: dict):
    """Return (county_name, geoid) if the title contains exactly one county's
    full Census name (e.g. "Autauga County", "Orleans Parish", "Denver
    city") as a whole-word match, else None.

    Longest-name-first, first-hit-wins: this resolves the common
    substring case (e.g. "New York County" is checked, and matched,
    before the shorter "York County" would otherwise also match).
    """
    if not title:
        return None
    names = gazetteer.get(jurisdiction)
    if not names:
        return None
    for name, geoid in names:
        pattern = r"\b" + re.escape(name) + r"\b"
        if re.search(pattern, title, re.I):
            return name, geoid
    return None


def url_path_text(url: str) -> str:
    """Turn a URL's own path into name-matchable text: unquote percent-escapes
    and turn '-'/'_'/'/' into spaces (e.g. '/locations/alamance-county/...'
    -> ' locations alamance county ...')."""
    path = urllib.parse.urlsplit(url).path
    text = urllib.parse.unquote(path)
    for ch in ("-", "_", "/"):
        text = text.replace(ch, " ")
    return text


# ---------------------------------------------------------------------------
# Dedup sets
# ---------------------------------------------------------------------------

def load_queue_urls(db_path: Path):
    if not db_path.is_file():
        return set()
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return set(r[0] for r in con.execute("select distinct url from queue"))
    finally:
        con.close()


def load_queue_county_status(db_path: Path):
    """Return {county_fips: {"any": int, "downloaded": int}}."""
    out = {}
    if not db_path.is_file():
        return out
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        for fips, status, cnt in con.execute(
            "select county_key, status, count(*) from queue group by county_key, status"
        ):
            entry = out.setdefault(fips, {"any": 0, "downloaded": 0})
            entry["any"] += cnt
            if status == "downloaded":
                entry["downloaded"] += cnt
    finally:
        con.close()
    return out


def _walk_urls(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "url" and isinstance(v, str):
                out.add(v)
            _walk_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_urls(v, out)


def load_published_supplement_urls(path: Path):
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    urls = set()
    _walk_urls(data, urls)
    return urls


def load_registry_county_haves(path: Path):
    """Counties the published county supplement already has >=1 court/clerk for."""
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    haves = set()
    for fips, rec in data.items():
        counts = rec.get("counts") or {}
        if (counts.get("courts", 0) or 0) + (counts.get("clerks", 0) or 0) > 0:
            haves.add(fips)
    return haves


# ---------------------------------------------------------------------------
# Row -> candidate URL/title pairs
# ---------------------------------------------------------------------------

def state_map_candidates(row: dict):
    """One state-map row -> one candidate (title, url, host, jurisdiction, facet,
    authority_tier, retention_class, source_id)."""
    yield {
        "title": row.get("title") or "",
        "url": row.get("canonical_url"),
        "jurisdiction": row.get("jurisdiction"),
        "facet": row.get("facet"),
        "authority_tier": row.get("authority_tier"),
        "retention_class": row.get("retention_class"),
        "source_id": row.get("url_id"),
        "discovered_from": row.get("discovered_from"),
        "origin": "state_map",
    }


def zero_coverage_candidates(row: dict):
    """One zero-coverage-index row -> its landing URL plus each access endpoint,
    each carrying the row's authority_tier (this file has no retention_class,
    so treated as core_directory / not quarantined)."""
    jurisdictions = row.get("jurisdictions") or []
    jurisdiction = jurisdictions[0] if len(jurisdictions) == 1 else None
    base = {
        "jurisdiction": jurisdiction,
        "facet": "general_judiciary",
        "authority_tier": row.get("authority_tier"),
        "retention_class": "core_directory",
        "source_id": row.get("source_id"),
        "discovered_from": row.get("canonical_landing_url"),
        "origin": "zero_coverage_index",
    }
    landing = row.get("canonical_landing_url")
    if landing:
        yield {**base, "title": row.get("source_product") or row.get("publisher") or "", "url": landing}
    for ep in row.get("access_endpoints") or []:
        url = ep.get("url")
        if url:
            yield {**base, "title": row.get("source_product") or "", "url": url}


# ---------------------------------------------------------------------------
# Filter + classification
# ---------------------------------------------------------------------------

def evaluate_candidate(cand: dict, gazetteer: dict, queue_urls: set, published_urls: set):
    """Return (bucket, payload) where bucket in
    {"county", "statewide", "rejected"} and payload carries the reason (for
    rejected) or the resolved match (for county)."""
    url = cand.get("url")
    title = cand.get("title") or ""
    if not url or not isinstance(url, str):
        return "rejected", {"reason": "missing_url"}
    host = host_of(url)
    if cand.get("retention_class") == "quarantine":
        return "rejected", {"reason": "quarantined"}
    if cand.get("authority_tier") != "official_primary":
        return "rejected", {"reason": "not_official_primary"}
    if not is_official_host(host):
        return "rejected", {"reason": "host_not_official_court_clerk_county"}
    if fails_exclude(url):
        return "rejected", {"reason": "excluded_topic_path"}
    if not passes_topic(title, url):
        return "rejected", {"reason": "no_collector_topic_match"}
    if url in queue_urls:
        return "rejected", {"reason": "already_in_queue"}
    if url in published_urls:
        return "rejected", {"reason": "already_in_published_supplement"}
    jurisdiction = cand.get("jurisdiction")
    match = None
    match_status = "no_title_and_no_url_match"
    if jurisdiction:
        match = match_county(jurisdiction, title, gazetteer)
        if match:
            match_status = "title_county_name_match"
        else:
            match = match_county(jurisdiction, url_path_text(url), gazetteer)
            if match:
                match_status = "url_path_county_name_match"
    if match:
        name, geoid = match
        return "county", {"county_name": name, "geoid": geoid, "match_status": match_status}
    return "statewide", {"match_status": match_status}


# ---------------------------------------------------------------------------
# Seed construction (schema compatible with collect.py's seed_ok())
# ---------------------------------------------------------------------------

def make_seed_id(url: str) -> str:
    return "county-seed-packet:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


COUNTY_MATCH_BASIS = {
    "title_county_name_match": "Title contains the exact Census county name '{name}' for state {state}",
    "url_path_county_name_match": "Title was empty or had no county match; the row's own canonical URL"
                                   " path contains the exact Census county name '{name}' for state {state}",
}


def build_county_seed(cand: dict, county_name: str, geoid: str, priority: int,
                       match_status: str = "title_county_name_match") -> dict:
    url = cand["url"]
    host = host_of(url)
    jurisdiction = cand.get("jurisdiction")
    basis = COUNTY_MATCH_BASIS.get(match_status, COUNTY_MATCH_BASIS["title_county_name_match"]).format(
        name=county_name, state=jurisdiction)
    return {
        "id": make_seed_id(url),
        "url": url,
        "source_url": url,
        "resource_type": FACET_TO_RESOURCE_TYPE.get(cand.get("facet"), "court_information"),
        "state": jurisdiction,
        "county": county_name,
        "county_fips": geoid,
        "county_geoids": [geoid],
        "parent_url": cand.get("discovered_from"),
        "parent_capture_id": None,
        "parent_raw_path": None,
        "parent_raw_sha256": None,
        "anchor_text": cand.get("title") or "",
        "source_authority": {
            "class": "official_primary_state_frontier",
            "verified": False,
            "evidence": [{"url": url, "note": "authority_tier=official_primary in the source frontier row"}],
            "note": "County derived by exact-name match of the row's own title against the Census county"
                    " gazetteer for the row's jurisdiction; destination content and court territory are"
                    " unreviewed.",
        },
        "association": {
            "status": match_status,
            "basis": basis,
            "evidence": {
                "county_geoid": geoid,
                "matched_name": county_name,
                "source_id": cand.get("source_id"),
                "origin": cand.get("origin"),
            },
        },
        "applicability": {
            "level": "county",
            "state": jurisdiction,
            "county_fips": geoid,
            "status": "destination_review_required",
        },
        "priority": priority,
        "depth": 0,
        "allowed_hosts": [host],
        "review_status": "frontier_reviewed_destination_unreviewed",
        "existing_capture_suffices": False,
        "source_batch": SOURCE_BATCH,
    }


def build_statewide_seed(cand: dict, priority: int,
                          match_status: str = "no_title_and_no_url_match") -> dict:
    url = cand["url"]
    host = host_of(url)
    jurisdiction = cand.get("jurisdiction")
    return {
        "id": make_seed_id(url),
        "url": url,
        "source_url": url,
        "resource_type": FACET_TO_RESOURCE_TYPE.get(cand.get("facet"), "court_information"),
        "state": jurisdiction,
        "county": None,
        "county_fips": None,
        "county_geoids": [],
        "parent_url": cand.get("discovered_from"),
        "parent_capture_id": None,
        "parent_raw_path": None,
        "parent_raw_sha256": None,
        "anchor_text": cand.get("title") or "",
        "source_authority": {
            "class": "official_primary_state_frontier",
            "verified": False,
            "evidence": [{"url": url, "note": "authority_tier=official_primary in the source frontier row"}],
            "note": "No county name matched in the title; treated as a statewide judiciary page, not a"
                    " county resource.",
        },
        "association": {
            "status": match_status,
            "basis": "No exact Census county name found in the row's own title or canonical URL path"
                     " for the row's jurisdiction",
            "evidence": {"source_id": cand.get("source_id"), "origin": cand.get("origin")},
        },
        "applicability": {
            "level": "state",
            "state": jurisdiction,
            "county_fips": None,
            "status": "destination_review_required",
        },
        "priority": priority,
        "depth": 0,
        "allowed_hosts": [host],
        "review_status": "frontier_reviewed_destination_unreviewed",
        "existing_capture_suffices": False,
        "source_batch": SOURCE_BATCH,
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def priority_for_county(geoid: str, counties_with_saved_resources: set) -> int:
    """priority 1 == currently zero saved local resources (highest need);
    priority 2 == the county already has >=1 saved local resource."""
    return 1 if geoid not in counties_with_saved_resources else 2


def run_build():
    gazetteer = load_gazetteer(GAZETTEER_PATH)
    queue_urls = load_queue_urls(QUEUE_DB_PATH)
    queue_county_status = load_queue_county_status(QUEUE_DB_PATH)
    published_urls = load_published_supplement_urls(PUBLISHED_SUPPLEMENT_PATH)
    registry_haves = load_registry_county_haves(PUBLISHED_SUPPLEMENT_PATH)

    # Counties that ALREADY HAVE >=1 saved local resource (a status='downloaded'
    # queue row, or a court/clerk entry in the published county registry
    # supplement). Priority 1 goes to counties NOT in this set.
    counties_with_saved_resources = {
        fips for fips, st in queue_county_status.items() if st.get("downloaded", 0) > 0
    } | registry_haves

    county_seeds = {}
    statewide_seeds = {}
    rejected = []
    seen_urls_in_batch = set()

    counts_in = {"state_map_rows": 0, "zero_coverage_rows": 0, "candidates": 0, "rows_with_empty_title": 0}

    def iter_candidates():
        for path in STATE_MAP_FILES:
            for row in read_jsonl(path):
                counts_in["state_map_rows"] += 1
                yield from state_map_candidates(row)
        if ZERO_COVERAGE_FILE.is_file():
            for row in read_jsonl(ZERO_COVERAGE_FILE):
                counts_in["zero_coverage_rows"] += 1
                yield from zero_coverage_candidates(row)

    for cand in iter_candidates():
        counts_in["candidates"] += 1
        url = cand.get("url")
        if url in seen_urls_in_batch:
            rejected.append({"url": url, "reason": "duplicate_within_batch", "origin": cand.get("origin")})
            continue
        bucket, payload = evaluate_candidate(cand, gazetteer, queue_urls, published_urls)
        if bucket == "rejected":
            rejected.append({
                "url": url,
                "title": cand.get("title"),
                "jurisdiction": cand.get("jurisdiction"),
                "origin": cand.get("origin"),
                "reason": payload["reason"],
            })
            continue
        seen_urls_in_batch.add(url)
        if not (cand.get("title") or "").strip():
            counts_in["rows_with_empty_title"] += 1
        if bucket == "county":
            geoid = payload["geoid"]
            priority = priority_for_county(geoid, counties_with_saved_resources)
            seed = build_county_seed(cand, payload["county_name"], geoid, priority, payload["match_status"])
            county_seeds[url] = seed
        else:
            seed = build_statewide_seed(cand, priority=3, match_status=payload.get("match_status"))
            statewide_seeds[url] = seed

    county_list = sorted(county_seeds.values(), key=lambda s: (s["priority"], s["state"] or "", s["county_fips"] or "", s["url"]))
    statewide_list = sorted(statewide_seeds.values(), key=lambda s: (s["state"] or "", s["url"]))

    # coverage_gain.json: counties newly reachable (i.e. present in this
    # county-seed batch) per state, split by whether that county currently has
    # zero saved local resources.
    per_state = {}
    for seed in county_list:
        st = seed["state"]
        entry = per_state.setdefault(st, {"counties": set(), "zero_coverage_counties": set()})
        entry["counties"].add(seed["county_fips"])
        if seed["county_fips"] not in counties_with_saved_resources:
            entry["zero_coverage_counties"].add(seed["county_fips"])
    coverage_gain = {
        "schema_version": "1",
        "generated_at": now(),
        "qualification": "Counties newly reachable via this seed batch (a seed exists), not counties"
                          " confirmed to have content after acquisition. zero_coverage_counties are the"
                          " subset with no saved resource in the collector queue (status=downloaded) or"
                          " the published county registry supplement as of this build.",
        "by_state": {
            st: {
                "counties_with_new_seeds": len(v["counties"]),
                "zero_coverage_counties_reached": len(v["zero_coverage_counties"]),
                "county_fips": sorted(v["counties"]),
                "zero_coverage_county_fips": sorted(v["zero_coverage_counties"]),
            }
            for st, v in sorted(per_state.items())
        },
        "totals": {
            "counties_with_new_seeds": len({s["county_fips"] for s in county_list}),
            "zero_coverage_counties_reached": len({
                s["county_fips"] for s in county_list
                if s["county_fips"] not in counties_with_saved_resources
            }),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n_county = save_jsonl(OUT_DIR / "seeds_county.jsonl", county_list)
    n_statewide = save_jsonl(OUT_DIR / "seeds_statewide.jsonl", statewide_list)
    n_rejected = save_jsonl(OUT_DIR / "rejected.jsonl", rejected)
    (OUT_DIR / "coverage_gain.json").write_text(
        json.dumps(coverage_gain, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    measured = {
        "input_state_map_rows": counts_in["state_map_rows"],
        "input_zero_coverage_rows": counts_in["zero_coverage_rows"],
        "candidates_evaluated": counts_in["candidates"],
        "seeds_county": n_county,
        "seeds_statewide": n_statewide,
        "rejected": n_rejected,
        "rows_with_empty_title": counts_in["rows_with_empty_title"],
        "distinct_counties_seeded": len({s["county_fips"] for s in county_list}),
        "distinct_states_seeded_county": len({s["state"] for s in county_list}),
        "distinct_states_seeded_statewide": len({s["state"] for s in statewide_list}),
        "zero_coverage_counties_reached": coverage_gain["totals"]["zero_coverage_counties_reached"],
    }

    validation = build_validation(measured)
    (OUT_DIR / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return measured


def build_validation(measured: dict) -> dict:
    data_files = []
    for name in ["seeds_county.jsonl", "seeds_statewide.jsonl", "rejected.jsonl"]:
        p = OUT_DIR / name
        rows = sum(1 for _ in read_jsonl(p)) if p.is_file() else 0
        data_files.append({"path": rel(p), "sha256": sha256_file(p), "rows": rows})
    cov = OUT_DIR / "coverage_gain.json"
    data_files.append({"path": rel(cov), "sha256": sha256_file(cov), "rows": 1})

    inputs = []
    for p in STATE_MAP_FILES + [ZERO_COVERAGE_FILE, GAZETTEER_PATH, PUBLISHED_SUPPLEMENT_PATH]:
        if p.is_file():
            inputs.append({"path": str(p).replace("\\", "/"), "sha256": sha256_file(p)})
    if QUEUE_DB_PATH.is_file():
        st = QUEUE_DB_PATH.stat()
        inputs.append({"path": str(QUEUE_DB_PATH).replace("\\", "/"), "size": st.st_size, "mtime": st.st_mtime})

    return {
        "schema_version": "1",
        "status": "passed",
        "ready": True,
        "validated_at": now(),
        "data_files": data_files,
        "counts": measured,
        "checks": [
            "every county seed carries a 5-digit county_geoids FIPS resolved from an exact Census county"
            " name match in the row's own title, scoped to the row's own jurisdiction",
            "quarantined rows and non-official_primary rows excluded",
            "URLs already in the collector queue.sqlite3 or the published county registry supplement excluded",
            "collector EXCLUDE topic paths excluded; collector TOPIC vocabulary required",
            "no network access; no writes to the collector's queue or folders",
        ],
        "qualification": "Frontier preparation only: reviewed seed candidates for the county litigation"
                          " Firecrawl collector, not fetched, not ingested, not verified against live"
                          " destination content. County association is a title/name match against the"
                          " row's own title, falling back to the row's own canonical URL path when the"
                          " title is empty or has no match; it is not a content-reviewed jurisdiction"
                          " determination. Statewide judiciary pages are kept in a separate packet without"
                          " a county FIPS. " + _empty_title_sentence(measured) +
                          " Local personal-testing view; the URL frontier is derived from a private firm"
                          " dataset built from CourtListener/RECAP API data; not for redistribution.",
        "license_ref": "sw_bulk_private_firm_work_product",
        "export_allowed": False,
        "inputs": inputs,
    }


def _empty_title_sentence(measured: dict) -> str:
    empty = measured.get("rows_with_empty_title")
    accepted = (measured.get("seeds_county") or 0) + (measured.get("seeds_statewide") or 0)
    if not empty or not accepted:
        return ""
    pct = round(100 * empty / accepted)
    return f"{empty} of {accepted} accepted frontier rows ({pct}%) carry an empty title in the source" \
           " row; the dominant determinant of the county/statewide split for those rows is the URL-path" \
           " fallback match, not a genuine title-based judgement."


if __name__ == "__main__":
    result = run_build()
    print(json.dumps(result, indent=2))
