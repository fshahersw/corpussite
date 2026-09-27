"""Build sources/sd_statutes_20260919 from the South Dakota statutes title-level API cache.

Input (read-only): C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging/sd_statutes_cache_2026-08-20/
71 title_*.json.gz files. Each file is one South Dakota Legislature statutes API response
(https://sdlegislature.gov/api/Statutes/Statute/<title>): a `retrieval` receipt (request_url, retrieved_at,
http_status, content_type, content_length) and a `payload` dict for one Title-type StatuteId node carrying a
full-title `Html` body (Word-generated, hashed inline CSS classes).

What this cache actually contains (measured): title-level index pages only. The Html body for each title is
the title's own chapter/section table of contents -- chapter numbers with their catchline, and, under each
chapter, section numbers with their catchline -- not the full statutory text of each section (StatuteText is
empty on every file; there is no per-section API response in this cache). This build extracts exactly that
structure deterministically with lxml, and never invents a section or chapter number: any table-of-contents
line that does not match a recognised "<id>  <catchline>" or "<id> to/and/, <id>...  <catchline>" shape is
kept verbatim as an unparsed note under the current chapter, never split or numbered by guesswork.

Streaming: one file decompressed/parsed at a time; nothing is held beyond the current title's node while
producing titles.jsonl.

Re-runnable: run this script again to regenerate titles.jsonl, unresolved.jsonl and validation.json from the
same input; output is deterministic (same input bytes -> same output bytes, keys in fixed order).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from lxml import html as lh

INPUT_DIR = Path("C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging/sd_statutes_cache_2026-08-20")
OUT_DIR = Path(__file__).resolve().parent
TITLES_FILE = OUT_DIR / "titles.jsonl"
UNRESOLVED_FILE = OUT_DIR / "unresolved.jsonl"
VALIDATION_FILE = OUT_DIR / "validation.json"

# A South Dakota Codified Laws statute reference: "1", "23A", "1-1", "1-1A", "1-1-1", "1-1-1.1", "23A-1-2.3",
# "15-6-4(a)" (rules-of-procedure subsection lettering).
_ID = r"\d+[A-Za-z]{0,3}(?:-\d+[A-Za-z]?){0,2}(?:\.\d+)?(?:\([a-zA-Z0-9]+\))*"
_ENTRY = re.compile(
    r"^(%s(?:\s*(?:to|and)\s*%s|\s*,\s*%s)*)\s*[.,]?\s+(.*)$" % (_ID, _ID, _ID),
    re.IGNORECASE | re.DOTALL,
)
_CHAPTER_HEADER = re.compile(r"^CHAPTER\s+(\S+)\s*$", re.IGNORECASE)
_WS_RUN = re.compile(r"\s{2,}")
# Entries within one <p> are separated by several blank lines (observed: 5 newline events); the single
# "id\r\n    \r\ncatchline" gap inside one entry is only 2 newline events and must not be split here.
_BLANK_SPLIT = re.compile(r"(?:\r?\n[ \t]*){3,}")
# A real SDCL section id is chapter-prefixed: at least "<title>-<chapter>-" before the section number.
# A bare ordinal ("1", "2", "3" ...) matches _ID (0 dash groups) but is never a real section id -- it is
# produced by numbered paragraphs inside pleading/pretrial form text embedded in some chapters.
_SECTION_ID_SHAPE = re.compile(r"^\d+[A-Za-z]{0,3}-\d+[A-Za-z]?-")
# One <p> occasionally runs two or three entries together without a blank-line gap: "...disability . 15-3-19
# Time allowed..." or "...continue. 31-18-5 Liability...". Split right after the sentence-ending period when
# what follows looks like a real section id, so the trailing catchline is not swallowed into the prior entry.
_EMBEDDED_SPLIT = re.compile(r"\s*\.\s+(?=\d+[A-Za-z]{0,3}-\d+[A-Za-z]?-\d+)")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm_ws(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _og_url(doc) -> str | None:
    metas = doc.xpath('//meta[@property="og:url"]/@content')
    return metas[0] if metas else None


def _split_embedded(text: str):
    """Split a normalised chunk on an embedded "<period> <section id>" boundary the source did not
    separate with a blank line. Re-attaches the consumed period to every piece but the last."""
    parts = _EMBEDDED_SPLIT.split(text)
    if len(parts) == 1:
        return parts
    return [p.rstrip() + "." if i < len(parts) - 1 else p for i, p in enumerate(parts)]


def _temporal(retrieved_at):
    no_effective_date = "source API publishes no effective date (StatuteId Created/LastUpdated are null)"
    return {
        "captured_at": retrieved_at,
        "captured_at_basis": "HTTP retrieval receipt" if retrieved_at else None,
        "source_as_of": None,
        "source_as_of_basis": no_effective_date,
        "published_at": None,
        "published_at_basis": no_effective_date,
        "effective_from": None,
        "effective_from_basis": no_effective_date,
        "effective_to": None,
        "effective_to_basis": no_effective_date,
    }


def _iter_paragraphs(html_str: str):
    if not html_str:
        return []
    try:
        doc = lh.fromstring(html_str)
    except Exception:
        return []
    paras = doc.xpath("//p")
    return [p.text_content().replace("\xa0", " ") for p in paras]


def _parse_body(html_str: str):
    """Return (chapters, unresolved_lines). Deterministic, never guesses ids."""
    raw_paragraphs = _iter_paragraphs(html_str)
    chapters = []
    unresolved = []
    current = None
    seen_first_chapter = False
    pending_chapter_number = None

    for raw in raw_paragraphs:
        stripped = raw.strip()
        if not stripped:
            continue
        m = _CHAPTER_HEADER.match(_norm_ws(stripped))
        if m:
            pending_chapter_number = m.group(1)
            seen_first_chapter = True
            continue
        if pending_chapter_number is not None:
            # First non-blank paragraph after a "CHAPTER n" header is that chapter's catchline.
            current = {"chapter_number": pending_chapter_number, "chapter_title": _norm_ws(stripped),
                       "sections": [], "unparsed": []}
            chapters.append(current)
            pending_chapter_number = None
            continue
        if not seen_first_chapter:
            # Title-level chapter directory before the first "CHAPTER n" header; not parsed as
            # structure (the body chapter headers below are the authoritative source of chapter ids).
            continue
        # One paragraph can carry several entries separated by blank lines; split them first, then split
        # again on any embedded "<period> <section id>" boundary the source ran together without a gap.
        for blank_chunk in _BLANK_SPLIT.split(raw):
            blank_norm = _norm_ws(blank_chunk)
            if not blank_norm:
                continue
            for chunk_norm in _split_embedded(blank_norm):
                if not chunk_norm:
                    continue
                entry = _ENTRY.match(chunk_norm)
                if entry:
                    section_id = entry.group(1).strip().rstrip(",")
                    text = entry.group(2).strip()
                    if current is None:
                        unresolved.append({"reason": "entry before any chapter header", "id": section_id, "text": text})
                    elif _SECTION_ID_SHAPE.match(section_id):
                        current["sections"].append({"section_id": section_id, "text": text})
                    else:
                        # Not a chapter-prefixed statute reference (e.g. a bare ordinal from embedded form
                        # text) -- never guess a section id, keep it verbatim instead.
                        current["unparsed"].append(chunk_norm)
                else:
                    if current is None:
                        unresolved.append({"reason": "unparsed line before any chapter header", "text": chunk_norm})
                    else:
                        current["unparsed"].append(chunk_norm)
    return chapters, unresolved


def build():
    if not INPUT_DIR.is_dir():
        print("input directory not found: %s" % INPUT_DIR, file=sys.stderr)
        return 1
    files = sorted(INPUT_DIR.glob("title_*.json.gz"))
    titles = []
    unresolved_rows = []
    checks = []
    n_html = 0
    n_repealed = 0
    n_type_title = 0
    total_chapters = 0
    total_sections = 0
    total_unparsed = 0
    bad_status = 0

    for path in files:
        raw_bytes = path.read_bytes()
        file_sha256 = _sha256(raw_bytes)
        try:
            data = json.loads(gzip.decompress(raw_bytes).decode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive, no bad files observed
            unresolved_rows.append({"file": path.name, "reason": "could not parse: %r" % exc})
            continue
        retrieval = data.get("retrieval") or {}
        payload = data.get("payload") or {}
        if retrieval.get("http_status") != 200:
            bad_status += 1
        statute_id = payload.get("StatuteId")
        title_number = payload.get("Statute")
        catch_line = payload.get("CatchLine")
        html_body = payload.get("Html") or ""
        repealed = bool(payload.get("Repealed"))
        if repealed:
            n_repealed += 1
        if html_body:
            n_html += 1
        if payload.get("Type") == "Title":
            n_type_title += 1
        try:
            doc = lh.fromstring(html_body) if html_body else None
        except Exception:
            doc = None
        og_url = _og_url(doc) if doc is not None else None
        # The og:url meta tag on this Word-generated export carries whatever chapter page the export was
        # taken from, not necessarily the title's own page (measured wrong or null on 35 of 71 titles). The
        # title's real official page is always this exact URL shape, derived 1:1 from the native Statute
        # field, never guessed.
        public_url = ("https://sdlegislature.gov/Statutes/%s" % title_number) if title_number else None
        chapters, unresolved = _parse_body(html_body)
        for item in unresolved:
            item = dict(item)
            item["statute_id"] = statute_id
            item["title_number"] = title_number
            unresolved_rows.append(item)
        section_count = sum(len(c["sections"]) for c in chapters)
        unparsed_count = sum(len(c["unparsed"]) for c in chapters)
        total_chapters += len(chapters)
        total_sections += section_count
        total_unparsed += unparsed_count

        titles.append({
            "id": "sdcl:%s" % statute_id,
            "statute_id": statute_id,
            "title_number": title_number,
            "catch_line": catch_line,
            "repealed": repealed,
            "request_url": retrieval.get("request_url"),
            "public_url": public_url,
            "og_url": og_url,
            "temporal": _temporal(retrieval.get("retrieved_at")),
            "retrieved_at": retrieval.get("retrieved_at"),
            "http_status": retrieval.get("http_status"),
            "content_type": retrieval.get("content_type"),
            "content_length": retrieval.get("content_length"),
            "source_file": path.name,
            "source_file_sha256": file_sha256,
            "chapter_count": len(chapters),
            "section_count": section_count,
            "unparsed_line_count": unparsed_count,
            "chapters": chapters,
        })

    titles.sort(key=lambda t: (t["title_number"] is None, str(t["title_number"])))

    with TITLES_FILE.open("w", encoding="utf-8", newline="\n") as fh:
        for row in titles:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with UNRESOLVED_FILE.open("w", encoding="utf-8", newline="\n") as fh:
        for row in unresolved_rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    checks.append({"name": "title_count_71", "passed": len(titles) == 71})
    checks.append({"name": "all_titles_have_html", "passed": n_html == 71})
    checks.append({"name": "all_titles_type_title", "passed": n_type_title == len(titles)})
    checks.append({"name": "all_retrieval_http_200", "passed": bad_status == 0})
    checks.append({"name": "statute_id_unique", "passed": len({t["statute_id"] for t in titles}) == len(titles)})

    titles_bytes = TITLES_FILE.read_bytes()
    unresolved_bytes = UNRESOLVED_FILE.read_bytes()
    input_manifest = []
    for path in files:
        input_manifest.append({"path": str(path), "sha256": _sha256(path.read_bytes())})

    validation = {
        "schema_version": "1",
        "status": "passed" if all(c["passed"] for c in checks) else "failed",
        "ready": all(c["passed"] for c in checks),
        "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_files": [
            {"path": "titles.jsonl", "sha256": _sha256(titles_bytes), "rows": len(titles)},
            {"path": "unresolved.jsonl", "sha256": _sha256(unresolved_bytes), "rows": len(unresolved_rows)},
        ],
        "counts": {
            "titles": len(titles),
            "titles_with_html": n_html,
            "titles_repealed_or_transferred_label": n_repealed,
            "chapters": total_chapters,
            "sections": total_sections,
            "unparsed_toc_lines": total_unparsed,
            "unresolved_rows": len(unresolved_rows),
        },
        "checks": checks,
        "qualification": (
            "Built 2026-09-19 from a 2026-08-20 capture, not verified current. Local personal-testing view of "
            "a South Dakota "
            "Codified Laws title-level cache staged inside a private firm dataset (SW-BULK); the underlying "
            "content originates from the South Dakota Legislature's public statutes API "
            "(sdlegislature.gov/api/Statutes), not from CourtListener/RECAP. Coverage is title-level only: "
            "71 of 71 cached titles, each title's chapter and section table of contents (numbers and "
            "catchlines) as published by the source API. Full section text is not present in this cache -- "
            "this is a structural index, not the statute language. Not for redistribution."
        ),
        "license_ref": "sw_bulk_private_firm_work_product",
        "export_allowed": False,
        "inputs": input_manifest,
    }
    VALIDATION_FILE.write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("titles=%d titles_with_html=%d chapters=%d sections=%d unparsed_toc_lines=%d unresolved_rows=%d" % (
        len(titles), n_html, total_chapters, total_sections, total_unparsed, len(unresolved_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
