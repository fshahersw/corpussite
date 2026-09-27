r"""Normaliser: toxicology directory + curated source catalogs -> unified URL directory rows.

Owns (per BUILD instructions, 2026-09-19):
  sources/url_directory_20260919/normalize_toxicology_and_catalogs.py           (this file)
  sources/url_directory_20260919/test_normalize_toxicology_and_catalogs.py
  sources/url_directory_20260919/normalized/toxicology_and_catalogs.jsonl
  sources/url_directory_20260919/normalized/toxicology_and_catalogs.rejected.jsonl
  sources/url_directory_20260919/normalized/toxicology_and_catalogs.report.json

Inputs (read-only, in place, never modified):
  C:/Users/firas/Downloads/returnedfiles/tier1_toxicology_directory.jsonl (+ .csv, .md)
  C:/Users/firas/Downloads/SW-BULK/source_gap_reconciliation_2026-08-22/
      sources_fda_medical.jsonl, sources_legal_corporate.jsonl, sources_science_environment.jsonl,
      sources_state_courts_zero_coverage.jsonl, direct_machine_endpoints.jsonl

Design (documented so the mapping choices are auditable, not hidden in code):

1. Toxicology directory (tier1_toxicology_directory.jsonl, 15,073 rows). Row schema measured directly:
   {url, title, group, content_kind, discovered_via, domain}. No substance-name / CAS field exists in this
   file at all -- "keep substance names the source gives as topics" is implemented as: when a row's own
   non-empty `title` looks like it names a specific chemical/substance (not a generic nav/boilerplate
   string -- heuristic: appears on a per-document path such as ToxProfiles/toxfaqs/tox-profiles/SEM-for
   AND is not one of a short list of known generic nav titles), that title text is also added to `topics`.
   This never invents data -- it only re-files text the source already gave (the link title) as a topic tag
   when the URL shape says it is document-specific. `group` (ATSDR/NTP/IARC/EPA IRIS/other) is always kept
   as a topic and as `org_name`. layer is always `science_toxicology` per the task's own instruction.
   The sibling .csv is row-count-identical to the jsonl (15,073 == 15,073, same fields minus the list-typed
   `discovered_via`) -- verified at build time and used only for a row-count cross-check, not for
   enrichment (there is nothing in it the jsonl lacks).

2. Curated source catalogs (134 rows across sources_fda_medical.jsonl [40], sources_legal_corporate.jsonl
   [44], sources_science_environment.jsonl [28], sources_state_courts_zero_coverage.jsonl [22]). Each
   record describes ONE source-product, not one URL; it carries `canonical_landing_url`, `evidence_urls`
   (always a superset containing the canonical URL -- measured: canonical_landing_url appears in
   evidence_urls for 100% of rows) and, for the state-court file only, `access_endpoints[]`. Per record we
   emit one row per DISTINCT url string (canonical, then each access_endpoint, then any evidence_urls not
   already covered) -- this satisfies "one output row per input URL occurrence" without emitting an exact
   duplicate row for the same URL appearing in two fields of the same record. `title` = the source's own
   `source_product` (its own wording, never invented). `topics` = the source's own `topical_domains` list.
   `org_name` = `publisher`. `state`/`jurisdiction_level` come only from the source's own `jurisdictions`
   list: a single non-US 2-letter code is a state; `["US"]` is federal; anything else is left None (never
   inferred). layer: `state_court` for the state-courts file; `science_toxicology` when the record's own
   `source_family`/`topical_domains` says `toxicology`/`toxicology_assessment`; else `federal_agency` when
   jurisdictions is exactly `["US"]` (matches the task's "SEC EDGAR, openFDA, etc." framing -- these are
   federal-agency source records); else `other`. doc_kind is extension-based by default; only the
   access_endpoint URLs get overridden to `data` when the extension gives `page` and the endpoint's own
   `endpoint_type` is `api`/`bulk_file`/`bulk_index`/`feed` (this is the task's "mark doc_kind data for
   API/bulk endpoints" instruction) -- canonical/evidence landing pages are NOT overridden, since those are
   documentation pages, not the endpoint itself.

3. direct_machine_endpoints.jsonl (49 rows): one row per record's own `url`, joined to the 134-row catalog
   above by exact `source_id` (34 of 34 ids present in this file are found there) to inherit
   publisher/org_name/topics/layer/state -- this is the "join document rows to URL rows ... to enrich
   doc_kind/title rather than emitting duplicates" step for this half of the slice (there is no separate
   CSV for these catalogs; source_id is the exact-match join key the source data actually provides).
   `title` = the record's own `notes` when non-empty (most specific), else the joined `source_product`,
   else None. doc_kind: extension-based, overridden to `data` when still `page` and `endpoint_type` is one
   of api/bulk_file/bulk_index/feed (same rule as #2). A source_id with no catalog match still emits a row
   (org_name/title/state/layer stay None/`other`) -- never dropped silently.

Streaming: every input is read one JSON line (or one CSV row) at a time; nothing is loaded whole except the
small catalog index (134 + 49 records, a few hundred KB) needed for the join.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import urlnorm  # noqa: E402

TOX_JSONL = r"C:/Users/firas/Downloads/returnedfiles/tier1_toxicology_directory.jsonl"
TOX_CSV = r"C:/Users/firas/Downloads/returnedfiles/tier1_toxicology_directory.csv"
GAP_DIR = r"C:/Users/firas/Downloads/SW-BULK/source_gap_reconciliation_2026-08-22"
CATALOG_FILES = [
    ("sources_fda_medical.jsonl", None),
    ("sources_legal_corporate.jsonl", None),
    ("sources_science_environment.jsonl", None),
    ("sources_state_courts_zero_coverage.jsonl", "state_court"),
]
ENDPOINTS_FILE = "direct_machine_endpoints.jsonl"

OUT_DIR = os.path.join(HERE, "normalized")
OUT_ROWS = os.path.join(OUT_DIR, "toxicology_and_catalogs.jsonl")
OUT_REJECTED = os.path.join(OUT_DIR, "toxicology_and_catalogs.rejected.jsonl")
OUT_REPORT = os.path.join(OUT_DIR, "toxicology_and_catalogs.report.json")

_ENDPOINT_DATA_TYPES = {"api", "bulk_file", "bulk_index", "feed"}
_GENERIC_NAV_TITLES = {
    "about atsdr", "about cdc / atsdr", "contact information", "funding", "espanol", "espa\u00f1ol",
    "atsdr en espa\u00f1ol", "agency for toxic substances and disease registry",
    "agency for toxic substance and disease registrationagency for toxic substance and disease registration",
    "camp lejeune, north carolina", "interaction profiles", "home",
}
_SUBSTANCE_PATH_HINT = ("toxprofiles", "toxfaqs", "tox-profiles", "sem-for", "toxguide", "mrls")


class RejectedRow(Exception):
    def __init__(self, reason, url=None):
        super().__init__(reason)
        self.reason = reason
        self.url = url


def _clean_title(title):
    title = (title or "").strip()
    return title or None


def _looks_like_substance_title(url, title):
    if not title:
        return False
    key = title.strip().lower()
    if key in _GENERIC_NAV_TITLES:
        return False
    if len(title) > 80:
        return False
    path = (urlnorm.split(url) or type("_", (), {"path": ""})()).path.lower()
    return any(h in path for h in _SUBSTANCE_PATH_HINT)


def toxicology_row(rec, source_file):
    url = rec.get("url")
    parts = urlnorm.split(url)
    if not url or not str(url).strip():
        raise RejectedRow("empty", url)
    if parts is None:
        raise RejectedRow("not_http_or_malformed", url)
    title = _clean_title(rec.get("title"))
    group = rec.get("group") or None
    topics = []
    if group:
        topics.append(group)
    if _looks_like_substance_title(url, title):
        topics.append(title)
    row = urlnorm.make_row(
        url=url,
        source_list="tier1_toxicology_directory",
        source_file=source_file,
        layer="science_toxicology",
        org_name=group,
        doc_kind=_tox_doc_kind(url, rec.get("content_kind")),
        title=title,
        topics=topics,
    )
    return row


def _tox_doc_kind(url, content_kind):
    base = urlnorm.doc_kind(url)
    if base != "page":
        return base
    hint_map = {
        "pdf": "pdf", "xlsx": "spreadsheet", "xls": "spreadsheet", "docx": "word", "doc": "word",
        "csv": "data", "zip": "archive", "html": "page",
    }
    return hint_map.get((content_kind or "").lower(), base)


def _jurisdiction_from_list(jurisdictions):
    if not jurisdictions or len(jurisdictions) != 1:
        return None, None
    code = jurisdictions[0]
    if code == "US":
        return None, "federal"
    if isinstance(code, str) and len(code) == 2 and code.isalpha() and code.isupper():
        return code, "state"
    return None, None


def _catalog_layer(rec, forced_layer=None):
    family = (rec.get("source_family") or "").lower()
    domains = [d.lower() for d in (rec.get("topical_domains") or [])]
    if family == "state_courts":
        return "state_court"
    if forced_layer:
        return forced_layer
    if family in ("toxicology", "toxicology_assessment") or "toxicology" in domains:
        return "science_toxicology"
    if (rec.get("jurisdictions") or []) == ["US"]:
        return "federal_agency"
    return "other"


def _endpoint_doc_kind(url, endpoint_type, formats):
    hinted = " ".join(formats or [])
    base = urlnorm.doc_kind(url, hinted=hinted)
    if base == "page" and (endpoint_type or "").lower() in _ENDPOINT_DATA_TYPES:
        return "data"
    return base


def catalog_rows(rec, source_file, forced_layer=None):
    """One row per distinct URL string found on this catalog record (canonical, then endpoints, then any
    remaining evidence urls), never re-emitting the same URL twice for the same record."""
    canonical = rec.get("canonical_landing_url")
    endpoints = rec.get("access_endpoints") or []
    evidence = rec.get("evidence_urls") or []
    layer = _catalog_layer(rec, forced_layer)
    state, jlevel = _jurisdiction_from_list(rec.get("jurisdictions"))
    org_name = rec.get("publisher") or None
    title = rec.get("source_product") or None
    topics = list(rec.get("topical_domains") or [])

    seen = set()
    rows = []

    def emit(url, doc_kind, parent_url):
        if not url or url in seen:
            return
        seen.add(url)
        if urlnorm.split(url) is None:
            return  # malformed catalog URL: dropped from this record's emission, not a hard reject
        rows.append(urlnorm.make_row(
            url=url, source_list=os.path.splitext(source_file)[0], source_file=source_file, layer=layer,
            jurisdiction_level=jlevel, state=state, org_name=org_name, doc_kind=doc_kind, title=title,
            topics=topics, parent_url=parent_url,
            source_date=rec.get("last_verified_at"),
        ))

    if canonical:
        emit(canonical, urlnorm.doc_kind(canonical), None)
    for ep in endpoints:
        emit(ep.get("url"), _endpoint_doc_kind(ep.get("url"), ep.get("endpoint_type"), ep.get("formats")), canonical)
    for ev in evidence:
        emit(ev, urlnorm.doc_kind(ev), canonical)
    return rows


def endpoint_row(rec, source_file, catalog_index):
    url = rec.get("url")
    parts = urlnorm.split(url)
    if not url or not str(url).strip():
        raise RejectedRow("empty", url)
    if parts is None:
        raise RejectedRow("not_http_or_malformed", url)
    src = catalog_index.get(rec.get("source_id")) or {}
    layer = _catalog_layer(src, None) if src else "other"
    state, jlevel = _jurisdiction_from_list(src.get("jurisdictions")) if src else (None, None)
    org_name = src.get("publisher") or None
    title = _clean_title(rec.get("notes")) or (src.get("source_product") or None)
    topics = list(src.get("topical_domains") or [])
    return urlnorm.make_row(
        url=url, source_list="direct_machine_endpoints", source_file=source_file, layer=layer,
        jurisdiction_level=jlevel, state=state, org_name=org_name,
        doc_kind=_endpoint_doc_kind(url, rec.get("endpoint_type"), rec.get("formats")),
        title=title, topics=topics, source_date=rec.get("verified_at"),
    )


# --- streaming build --------------------------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_stat(path):
    size = os.path.getsize(path)
    if size > 200 * 1024 * 1024:
        return {"path": path, "size": size, "mtime": os.path.getmtime(path)}
    return {"path": path, "sha256": _sha256(path)}


def _iter_jsonl(path, malformed_counter):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                malformed_counter[0] += 1


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows_in = 0
    rows_out = 0
    rejected = []
    by_layer = Counter()
    by_doc_kind = Counter()
    by_state = Counter()
    by_host = Counter()
    noise_counts = Counter()
    malformed = [0]

    catalog_index = {}
    catalog_rows_by_file = {}
    for fname, forced_layer in CATALOG_FILES:
        path = os.path.join(GAP_DIR, fname)
        recs = list(_iter_jsonl(path, malformed))
        catalog_rows_by_file[fname] = (recs, forced_layer)
        for rec in recs:
            sid = rec.get("source_id")
            if sid:
                catalog_index[sid] = rec

    with open(OUT_ROWS, "w", encoding="utf-8") as out, open(OUT_REJECTED, "w", encoding="utf-8") as rej:

        def write_row(row):
            nonlocal rows_out
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows_out += 1
            by_layer[row["layer"]] += 1
            by_doc_kind[row["doc_kind"]] += 1
            if row["state"]:
                by_state[row["state"]] += 1
            by_host[row["host"]] += 1
            for n in row["noise"]:
                noise_counts[n] += 1

        def write_reject(source_file, url, reason):
            rej.write(json.dumps({"source_file": source_file, "url": url, "reason": reason}, ensure_ascii=False) + "\n")
            rejected.append(reason)

        # 1. toxicology directory (stream, one line at a time)
        source_file = os.path.basename(TOX_JSONL)
        tox_rows_in = 0
        for rec in _iter_jsonl(TOX_JSONL, malformed):
            rows_in += 1
            tox_rows_in += 1
            try:
                write_row(toxicology_row(rec, source_file))
            except RejectedRow as e:
                write_reject(source_file, rec.get("url"), e.reason)

        # cross-check the sibling CSV row count (used for validation only, not enrichment -- see module docstring)
        with open(TOX_CSV, encoding="utf-8", newline="") as f:
            csv_rows = sum(1 for _ in csv.reader(f)) - 1  # minus header

        # 2. curated catalogs (134 rows total)
        for fname, (recs, forced_layer) in catalog_rows_by_file.items():
            for rec in recs:
                rows_in += 1
                for row in catalog_rows(rec, fname, forced_layer):
                    write_row(row)

        # 3. direct machine endpoints (49 rows), joined to the catalog index by source_id
        ep_path = os.path.join(GAP_DIR, ENDPOINTS_FILE)
        for rec in _iter_jsonl(ep_path, malformed):
            rows_in += 1
            try:
                write_row(endpoint_row(rec, ENDPOINTS_FILE, catalog_index))
            except RejectedRow as e:
                write_reject(ENDPOINTS_FILE, rec.get("url"), e.reason)

    top_hosts = [{"host": h, "rows": c} for h, c in by_host.most_common(25)]

    inputs = [
        {"path": TOX_JSONL, "sha256": _sha256(TOX_JSONL)},
        {"path": TOX_CSV, "sha256": _sha256(TOX_CSV)},
    ]
    for fname, _ in CATALOG_FILES:
        p = os.path.join(GAP_DIR, fname)
        inputs.append({"path": p.replace("\\", "/"), "sha256": _sha256(p)})
    inputs.append({"path": ep_path.replace("\\", "/"), "sha256": _sha256(ep_path)})

    report = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rejected": len(rejected),
        "rejected_reasons": dict(Counter(rejected)),
        "malformed_json_lines_skipped": malformed[0],
        "toxicology_csv_row_count_crosscheck": {
            "jsonl_input_rows": tox_rows_in, "csv_rows": csv_rows, "match": tox_rows_in == csv_rows,
        },
        "counts": {
            "by_layer": dict(by_layer),
            "by_doc_kind": dict(by_doc_kind),
            "by_state": dict(by_state),
            "states_covered": len(by_state),
            "top_25_hosts": top_hosts,
            "noise_flag_counts": dict(noise_counts),
        },
        "inputs": inputs,
        "data_files": [
            {"path": os.path.relpath(OUT_ROWS, HERE).replace("\\", "/"), "sha256": _sha256(OUT_ROWS), "rows": rows_out},
            {"path": os.path.relpath(OUT_REJECTED, HERE).replace("\\", "/"), "sha256": _sha256(OUT_REJECTED), "rows": len(rejected)},
        ],
    }
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


if __name__ == "__main__":
    r = build()
    print(json.dumps({k: v for k, v in r.items() if k != "counts"}, indent=2))
    print(json.dumps(r["counts"], indent=2))
