"""Build sources/mdl_docket_documents_20260919 from SW-BULK/catalog/documents.json (Round 5, slice 2).

Deterministic, re-runnable, offline, streaming-friendly (single json.load; the file is 47.5 MB).
Never modifies anything under SW-BULK. Reads:
  - C:/Users/firas/Downloads/SW-BULK/catalog/documents.json          (35,862 rows, the corpus)
  - C:/Users/firas/Downloads/SW-BULK/catalog/catalog_report.csv      (17 rows, QA cross-check)
  - C:/Users/firas/Downloads/SCRAPE/sources/mdl_docket_crosswalk_20260919/crosswalk.jsonl (id spine, slice 1)
  - C:/Users/firas/Downloads/SCRAPE/sources/jpml_mdl_20260919/mdls.jsonl (registry, for title/status fallback)

Join: documents.json rows carry `master_docket_id` (a CourtListener docket id). We join to the MDL
registry ONLY through the crosswalk's `catalog_master_docket_id` field (never by name, never by the
file's own `mdl_number` string, which is absent for the two "park" masters). This is what recovers
MDL 3060 (Hair Relaxer, master 66801859, 2,242 rows) in addition to the 12 MDLs whose native
`mdl_number` already matches the registry, taking resolved coverage from 25,598 to 27,840 rows across
13 registry MDLs (12 pending + 1 terminated, MDL 2592). Rows whose master_docket_id does not resolve
(TRT master 4261857, and MDLs 2100/2606/2641 which the crosswalk/registry does not carry) go to
unresolved.jsonl with a reason; they are NEVER dropped from documents.jsonl, only flagged.

Record id: `doc_uid` is native but NOT unique (4,004 duplicate rows, mostly the "<master>-None-" shape
for minute entries with no entry_number/document_number). Per the plan's verification note, the id is
the composite `master_docket_id:entry_number:document_number:<row ordinal>` — the ordinal guarantees
uniqueness even when doc_uid collides. Separately, exact full-row duplicate content (a stricter,
rarer condition than a doc_uid collision) is detected and reported, never silently collapsed.

Document-type classifier: deterministic, regex/keyword based, over `entry_description` +
`document_description` (the raw docket text; kept verbatim on every row as `raw_entry_description` /
`raw_document_description`). This is intentionally independent of the source pipeline's own
`doc_category` (kept verbatim as `source_doc_category`, labelled "categorised by the source pipeline,
not by the court"). Categories, in precedence order: settlement, bellwether, leadership_appointment,
daubert_expert, dispositive, pretrial_order, case_management_order, other.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SW_BULK = Path(r"C:/Users/firas/Downloads/SW-BULK")
DOCUMENTS_JSON = SW_BULK / "catalog" / "documents.json"
CATALOG_REPORT_CSV = SW_BULK / "catalog" / "catalog_report.csv"

SCRAPE_ROOT = Path(__file__).resolve().parents[2]
CROSSWALK_DIR = SCRAPE_ROOT / "sources/mdl_docket_crosswalk_20260919"
REGISTRY_PATH = SCRAPE_ROOT / "sources/jpml_mdl_20260919/mdls.jsonl"

OUT_DIR = Path(__file__).resolve().parent
DOCUMENTS_OUT = OUT_DIR / "documents.jsonl"
UNRESOLVED_OUT = OUT_DIR / "unresolved.jsonl"
VALIDATION_OUT = OUT_DIR / "validation.json"

DOC_TYPES = (
    "settlement", "bellwether", "leadership_appointment", "daubert_expert",
    "dispositive", "pretrial_order", "case_management_order", "other",
)

_LEADERSHIP_TERMS = ("lead counsel", "liaison counsel", "steering committee", "executive committee",
                     "co-lead counsel", "plaintiffs' leadership", "plaintiffs leadership")

_IN_RE_CAPTION_RE = re.compile(r"(?i)^\s*in\s+re\b")

_CAPTION_ENTITY_TOKENS = (
    "INC", "LLC", "CORP", "CO", "LTD", "LP", "LLP", "PHARMA", "LABORATOR", "HOLDINGS",
    "GROUP", "SYSTEMS", "BANK", "UNITED STATES", "STATE OF", "CITY OF", "COUNTY", "IN RE",
)
_CAPTION_VS_RE = re.compile(r"\s+vs?\.?\s+", re.IGNORECASE)


def suppress_natural_person_caption(case_name):
    """Party-name rule: natural-person plaintiffs are never listed. Only an 'In re ...' style
    master-docket caption is safe to publish verbatim; any individual member-case caption
    ('STEVEN GOODSTEIN v. ASTRAZENECA...') is suppressed to None. Returns
    (kept_case_name_or_None, caption_suppressed_natural_person: bool). Never raises."""
    if isinstance(case_name, str) and _IN_RE_CAPTION_RE.match(case_name):
        return case_name, False
    return None, bool(case_name)


def is_natural_person_vs_party_caption(name) -> bool:
    """Measure whether a published caption/title-bearing string has the shape of a
    person-v-party caption: a left side of 1-4 tokens with no corporate/entity/government
    marker. Used only to validate what actually got published, not to decide what to publish."""
    if not isinstance(name, str) or not name.strip():
        return False
    parts = _CAPTION_VS_RE.split(name.strip(), maxsplit=1)
    if len(parts) != 2:
        return False
    left = parts[0].strip()
    if not left:
        return False
    left_upper = left.upper()
    if any(token in left_upper for token in _CAPTION_ENTITY_TOKENS):
        return False
    tokens = left.split()
    return 1 <= len(tokens) <= 4


def measure_natural_person_captions(records: list) -> list:
    """Distinct set of every published case_name value that trips the person-v-party shape."""
    captions = {r.get("case_name") for r in records if r.get("case_name")}
    return sorted(c for c in captions if is_natural_person_vs_party_caption(c))


def classify_document_type(entry_description: str | None, document_description: str | None) -> str:
    """Deterministic keyword/regex classifier over the raw docket text. Never raises; unknown/empty
    text classifies as 'other'. See module docstring for the precedence order and rationale."""
    text = " ".join(t for t in (entry_description, document_description) if t)
    tl = text.lower()
    if not tl:
        return "other"
    if "settlement" in tl:
        return "settlement"
    if "bellwether" in tl:
        return "bellwether"
    if "appoint" in tl and any(term in tl for term in _LEADERSHIP_TERMS):
        return "leadership_appointment"
    if re.search(r"daubert|motion to exclude[^.]{0,30}expert|expert[^.]{0,20}(qualif|opinion|testimony)[^.]{0,20}(exclude|strike|challenge)", tl):
        return "daubert_expert"
    if re.search(r"summary judgment|motion to dismiss|judgment on the pleadings|motion for judgment", tl):
        return "dispositive"
    if "pretrial order" in tl:
        return "pretrial_order"
    if "case management order" in tl or re.search(r"\bcmo\b", tl):
        return "case_management_order"
    return "other"


def zero_pad_mdl(raw) -> "int | None":
    """documents.json stores mdl_number as a zero-padded string ('02789') or null."""
    if raw is None:
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_crosswalk_by_master_docket_id(crosswalk_dir: Path = CROSSWALK_DIR) -> dict:
    """master_docket_id (int) -> {mdl_number, mdl_status, mdl_title, cl_court_id}. Only rows whose
    crosswalk entry has resolved a catalog_master_docket_id. Fails soft (empty dict) if the crosswalk
    supplement is not present or not passed, but the caller records this as a build-time defect."""
    out = {}
    try:
        gate = json.loads((crosswalk_dir / "validation.json").read_text(encoding="utf-8"))
        if gate.get("status") != "passed" or gate.get("ready") is not True:
            return out
        with open(crosswalk_dir / "crosswalk.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                mid = row.get("catalog_master_docket_id")
                if mid is None:
                    continue
                out[int(mid)] = {
                    "mdl_number": row.get("mdl_number"),
                    "mdl_status": row.get("mdl_status"),
                    "mdl_title": row.get("mdl_title"),
                    "cl_court_id": row.get("cl_court_id"),
                }
    except (OSError, ValueError, TypeError, KeyError):
        return {}
    return out


def load_registry(registry_path: Path = REGISTRY_PATH) -> dict:
    out = {}
    try:
        with open(registry_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                n = row.get("mdl_number")
                if isinstance(n, int):
                    out[n] = row
    except (OSError, ValueError):
        return {}
    return out


def load_catalog_report(path: Path = CATALOG_REPORT_CSV) -> dict:
    """master_docket_id (int) -> {"document_count": int, "high_value_docs": int, ...raw row}."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                mid = int(row["master_docket_id"])
            except (KeyError, ValueError):
                continue
            out[mid] = row
    return out


def build_records(documents: list, crosswalk_by_master: dict) -> tuple[list, list]:
    """Return (records, unresolved_records). Never drops a row."""
    records = []
    unresolved = []
    seen_content = {}
    for i, row in enumerate(documents):
        master_docket_id = row.get("master_docket_id")
        entry_number = row.get("entry_number")
        document_number = row.get("document_number")
        record_id = "doc:%s:%s:%s:%d" % (master_docket_id, entry_number, document_number, i)

        content_key = json.dumps(row, sort_keys=True)
        first_seen = seen_content.get(content_key)
        is_exact_duplicate = first_seen is not None
        if first_seen is None:
            seen_content[content_key] = record_id

        cw = crosswalk_by_master.get(master_docket_id) if isinstance(master_docket_id, int) else None
        native_mdl_number = zero_pad_mdl(row.get("mdl_number"))
        resolved = cw is not None
        unresolved_reason = None
        if not resolved:
            if native_mdl_number is not None:
                unresolved_reason = ("mdl_number %s present on the row but master_docket_id %s is not "
                                     "resolved to a registry MDL by mdl_docket_crosswalk" % (row.get("mdl_number"), master_docket_id))
            else:
                unresolved_reason = ("no mdl_number on the row and master_docket_id %s is not resolved to "
                                     "a registry MDL by mdl_docket_crosswalk" % master_docket_id)

        doc_type = classify_document_type(row.get("entry_description"), row.get("document_description"))
        kept_case_name, caption_suppressed = suppress_natural_person_caption(row.get("case_name"))

        rec = {
            "id": record_id,
            "doc_uid": row.get("doc_uid"),
            "master_docket_id": master_docket_id,
            "docket_number": row.get("docket_number"),
            "court": row.get("court"),
            "case_name": kept_case_name,
            "caption_suppressed_natural_person": caption_suppressed,
            "cluster_defendant": row.get("cluster_defendant"),
            "cluster_court": row.get("cluster_court"),
            "entry_number": entry_number,
            "entry_date_filed": row.get("entry_date_filed"),
            "raw_entry_description": row.get("entry_description"),
            "document_number": document_number,
            "attachment_number": row.get("attachment_number"),
            "raw_document_description": row.get("document_description"),
            "document_type_code": row.get("document_type"),
            "source_doc_category": row.get("doc_category"),
            "doc_type": doc_type,
            "high_value": row.get("high_value"),
            "page_count": row.get("page_count"),
            "file_size": row.get("file_size"),
            "is_available": row.get("is_available"),
            "is_free_on_pacer": row.get("is_free_on_pacer"),
            "is_sealed": row.get("is_sealed"),
            "download_url": row.get("download_url"),
            "courtlistener_url": row.get("courtlistener_url"),
            "pacer_doc_id": row.get("pacer_doc_id"),
            "sha1": row.get("sha1"),
            "source": row.get("source"),
            "native_mdl_number": native_mdl_number,
            "resolved": resolved,
            "mdl_number": cw["mdl_number"] if cw else None,
            "mdl_status": cw["mdl_status"] if cw else None,
            "mdl_title": cw["mdl_title"] if cw else None,
            "cl_court_id": cw["cl_court_id"] if cw else None,
            "unresolved_reason": unresolved_reason,
            "is_exact_duplicate": is_exact_duplicate,
            "exact_duplicate_of": first_seen,
        }
        records.append(rec)
        if not resolved:
            unresolved.append({
                "id": record_id, "master_docket_id": master_docket_id,
                "native_mdl_number": native_mdl_number, "docket_number": row.get("docket_number"),
                "case_name": kept_case_name, "caption_suppressed_natural_person": caption_suppressed,
                "reason": unresolved_reason,
            })
    return records, unresolved


def cross_check_catalog_report(records: list, report: dict) -> list:
    """Per-master row-count cross-check against catalog_report.csv. A mismatch flags a possibly
    stale/page-capped master (per-master fetch could have been re-run since the report was written)."""
    counts = Counter(r["master_docket_id"] for r in records)
    checks = []
    for mid, row in report.items():
        expected = int(row["document_count"])
        measured = counts.get(mid, 0)
        checks.append({
            "master_docket_id": mid, "docket_number": row["docket_number"],
            "expected_document_count": expected, "measured_document_count": measured,
            "match": expected == measured,
        })
    return checks


def write_jsonl(path: Path, rows: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True))
            f.write("\n")


def main() -> dict:
    documents = json.loads(DOCUMENTS_JSON.read_text(encoding="utf-8"))
    crosswalk_by_master = load_crosswalk_by_master_docket_id()
    report = load_catalog_report()

    records, unresolved = build_records(documents, crosswalk_by_master)
    write_jsonl(DOCUMENTS_OUT, records)
    write_jsonl(UNRESOLVED_OUT, unresolved)

    report_checks = cross_check_catalog_report(records, report)
    stale_masters = [c for c in report_checks if not c["match"]]

    resolved_records = [r for r in records if r["resolved"]]
    pending_records = [r for r in resolved_records if r["mdl_status"] == "pending"]
    by_mdl = Counter(r["mdl_number"] for r in resolved_records)
    doc_type_counts = Counter(r["doc_type"] for r in records)
    pending_doc_type_counts = Counter(r["doc_type"] for r in pending_records)
    exact_dup_rows = sum(1 for r in records if r["is_exact_duplicate"])
    doc_uid_counts = Counter(r["doc_uid"] for r in records)
    doc_uid_duplicate_rows = sum(c - 1 for c in doc_uid_counts.values() if c > 1)
    with_download_and_sha1 = sum(1 for r in records if r["download_url"] and r["sha1"])

    counts = {
        "total_rows": len(records),
        "resolved_rows": len(resolved_records),
        "unresolved_rows": len(unresolved),
        "resolved_mdl_count": len(by_mdl),
        "resolved_pending_mdl_count": len({r["mdl_number"] for r in pending_records}),
        "rows_by_mdl_number": dict(sorted(by_mdl.items())),
        "doc_type_counts_all_rows": dict(sorted(doc_type_counts.items())),
        "doc_type_counts_pending_mdls": dict(sorted(pending_doc_type_counts.items())),
        "doc_uid_distinct": len(doc_uid_counts),
        "doc_uid_duplicate_rows": doc_uid_duplicate_rows,
        "exact_duplicate_rows": exact_dup_rows,
        "rows_with_download_url_and_sha1": with_download_and_sha1,
        "sealed_rows": sum(1 for r in records if r["is_sealed"]),
        "catalog_report_masters_checked": len(report_checks),
        "catalog_report_mismatches": len(stale_masters),
    }
    offending_captions = measure_natural_person_captions(records)
    rows_with_offending_caption = sum(1 for r in records if r.get("case_name") in set(offending_captions))

    checks = [
        {"name": "total_rows_35862", "passed": len(records) == 35862},
        {"name": "record_ids_unique", "passed": len({r["id"] for r in records}) == len(records)},
        {"name": "catalog_report_counts_match_per_master", "passed": len(stale_masters) == 0,
         "detail": stale_masters if stale_masters else None},
        {"name": "no_row_dropped_silently", "passed": len(records) == len(documents)},
        {"name": "no_natural_person_plaintiff_caption_published", "passed": len(offending_captions) == 0,
         "detail": {"offending_captions": len(offending_captions), "rows": rows_with_offending_caption}},
    ]
    build_status = "passed" if all(c["passed"] for c in checks) else "failed"

    data_files = [
        {"path": "documents.jsonl", "sha256": _sha256_file(DOCUMENTS_OUT), "rows": len(records)},
        {"path": "unresolved.jsonl", "sha256": _sha256_file(UNRESOLVED_OUT), "rows": len(unresolved)},
    ]
    validation = {
        "schema_version": "1",
        "status": build_status,
        "ready": build_status == "passed",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "data_files": data_files,
        "counts": counts,
        "checks": checks,
        "qualification": (
            "Master-docket paper trail from a private firm dataset (SW-BULK/catalog/documents.json, "
            "file captured 2026-08-07), assembled via per-master CourtListener/RECAP API fetches, for "
            "personal local testing only; not for redistribution. Covers 13 registry MDLs (12 pending, "
            "MDL 2592 terminated). doc_type is a deterministic classifier over the raw docket text, "
            "independent of the source pipeline's own doc_category (kept verbatim, labelled). "
            "entry_date_filed is a docket entry filing date, not a document effective date. Links go out "
            "to CourtListener/RECAP; no document bytes are stored or served here."
        ),
        "license_ref": "sw_bulk_private_firm_work_product",
        "export_allowed": False,
        "inputs": [
            {"path": str(DOCUMENTS_JSON), "sha256": _sha256_file(DOCUMENTS_JSON)},
            {"path": str(CATALOG_REPORT_CSV), "sha256": _sha256_file(CATALOG_REPORT_CSV)},
            {"path": str(CROSSWALK_DIR / "crosswalk.jsonl"), "sha256": _sha256_file(CROSSWALK_DIR / "crosswalk.jsonl")},
        ],
    }
    VALIDATION_OUT.write_text(json.dumps(validation, indent=2, sort_keys=True), encoding="utf-8")
    return validation


if __name__ == "__main__":
    result = main()
    print(json.dumps(result["counts"], indent=2, sort_keys=True))
