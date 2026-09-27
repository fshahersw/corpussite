"""Offline, incremental page screening for saved law and court PDF archives.

The collector database is opened read-only and is never modified. This command
does not fetch documents or language models. Run it while the OCR worker is idle.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

import fitz

WORKSPACE = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"
MIN_TEXT_ALNUM = 120
MIN_SCAN_AREA = 0.10
RENDER_DPI = 200
MAX_RENDER_PIXELS = 12_000_000
INK_TABLE = bytes(int(i < 245) for i in range(256))
SCREENING_NOTE = (
    "OCR is queued only for pages with fewer than 120 embedded alphanumeric "
    "characters, visible nonwhite content, and a raster image covering at least "
    "10% of the page. This heuristic is not proof of complete or correct text "
    "extraction; sparse vector-only pages and unreadable text may require review."
)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()] if Path(path).exists() else []


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def atomic_lines(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
    tmp.replace(path)


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact path escapes the corpus root")
    return path


def screen_page(page):
    """Inspect embedded text and raster imagery, conservatively skipping blanks."""
    text = page.get_text("text")
    chars = sum(character.isalnum() for character in text)
    area = max(page.rect.get_area(), 1)
    images = page.get_image_info(hashes=False, xrefs=False)
    ratios = [max((fitz.Rect(item["bbox"]) & page.rect).get_area(), 0) / area for item in images]
    largest_image = max(ratios, default=0)
    result = {
        "embedded_text": text,
        "embedded_text_characters": len(text),
        "embedded_alphanumeric_characters": chars,
        "raster_image_count": len(images),
        "largest_raster_image_page_fraction": round(largest_image, 6),
        "summed_raster_image_page_fraction_upper_bound": round(min(sum(ratios), 1), 6),
        "page_width_points": page.rect.width,
        "page_height_points": page.rect.height,
        "needs_ocr": False,
        "manual_review_recommended": False,
    }
    if chars >= MIN_TEXT_ALNUM:
        result["screening_status"] = "embedded_text_present"
        return result
    thumbnail = page.get_pixmap(dpi=36, colorspace=fitz.csGRAY, alpha=False)
    dark_pixels = sum(thumbnail.samples.translate(INK_TABLE))
    result.update(blank_screen_dpi=36, pixels_below_245=dark_pixels,
                  blank_screen_pixels=thumbnail.width * thumbnail.height)
    if chars == 0 and dark_pixels == 0:
        result["screening_status"] = "blank_page_skipped"
    elif largest_image >= MIN_SCAN_AREA:
        result.update(screening_status="sparse_embedded_text_with_scan_image", needs_ocr=True)
    else:
        result.update(screening_status="sparse_text_without_scan_image", manual_review_recommended=True)
    return result


def copy_local_model(model_dir, output):
    source = model_dir / "eng.traineddata"
    provenance_file = model_dir / "eng.provenance.json"
    if not source.is_file() or not provenance_file.is_file():
        raise FileNotFoundError("Existing local English OCR model and provenance are required; nothing will be downloaded")
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    actual = digest(source)
    if actual != provenance["sha256"]:
        raise ValueError("Existing language model SHA-256 differs from its provenance")
    target_dir = output / "models"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "eng.traineddata"
    if not target.exists() or digest(target) != actual:
        shutil.copyfile(source, target)
    copied_provenance = {**provenance, "local_copy_source": str(source.resolve()),
                         "local_copy_source_provenance": str(provenance_file.resolve()),
                         "local_copy_verified_at_utc": now(), "network_used_for_this_copy": False}
    atomic_json(target_dir / "eng.provenance.json", copied_provenance)
    return actual


def snapshot_resources(root):
    database = root / "corpus.sqlite3"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return [dict(row) for row in db.execute(
            "SELECT id,url,raw_path,text_path,metadata_path,sha256,byte_count,"
            "last_fetch_id,extraction_status FROM resources "
            "WHERE status='downloaded' AND raw_complete=1 AND raw_path IS NOT NULL ORDER BY id"
        ).fetchall()]


def prepare(root, output, model_dir, *, resource_snapshot=None, snapshot_kind="collector_database"):
    root, output, model_dir = root.resolve(), output.resolve(), model_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    snapshot_at = now()
    resources = snapshot_resources(root) if resource_snapshot is None else resource_snapshot
    model_sha = copy_local_model(model_dir, output)
    grouped = {}
    errors = []
    for resource in resources:
        try:
            source = inside(root, resource["raw_path"])
            with source.open("rb") as handle:
                if b"%PDF-" not in handle.read(1024):
                    continue
            sha = resource["sha256"]
            if not sha or len(sha) != 64:
                raise ValueError("Downloaded PDF lacks a valid stored SHA-256")
            if sha not in grouped:
                actual = digest(source)
                if actual != sha:
                    raise ValueError("Source PDF SHA-256 mismatch")
                grouped[sha] = {"path": source, "resources": []}
            elif source != grouped[sha]["path"] and digest(source) != sha:
                raise ValueError("Duplicate PDF SHA-256 mismatch")
            grouped[sha]["resources"].append(resource)
        except Exception as exc:
            errors.append({"resource_id": resource["id"], "source_url": resource["url"],
                           "raw_path": resource["raw_path"], "error": type(exc).__name__ + ": " + str(exc)})

    # Retain earlier verified versions even if a URL's current PDF has changed.
    documents = {record["source_id"]: record for record in read_lines(output / "documents.jsonl")}
    queued = {(record["source_id"], record["page_number"]): record for record in read_lines(output / "pages.jsonl")}
    refreshed_ids = set()
    newly_screened = 0
    rendered = 0
    for sha, group in grouped.items():
        source_id = "pdf-" + sha
        refreshed_ids.add(source_id)
        source = group["path"]
        aliases = group["resources"]
        doc_dir = output / source_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        cache_file = doc_dir / "screening.json"
        cached = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None
        use_cache = bool(cached and cached.get("screening_version") == VERSION and cached.get("source_pdf_sha256") == sha and cached.get("screening_complete"))
        document = None
        try:
            document = fitz.open(source)
            if document.needs_pass:
                raise ValueError("PDF requires a password")
            coverage = cached["page_coverage"] if use_cache else []
            if not use_cache:
                newly_screened += 1
                for number, page in enumerate(document, 1):
                    try:
                        coverage.append({"source_id": source_id, "source_pdf_sha256": sha,
                                         "page_number": number, **screen_page(page)})
                    except Exception as exc:
                        coverage.append({"source_id": source_id, "source_pdf_sha256": sha,
                                         "page_number": number, "screening_status": "screening_error",
                                         "needs_ocr": False, "manual_review_recommended": True,
                                         "error": type(exc).__name__ + ": " + str(exc)})
            screening_complete = len(coverage) == len(document) and not any(page["screening_status"] == "screening_error" for page in coverage)
            needed = [page for page in coverage if page.get("needs_ocr")]
            prepared_tasks = {}
            for page in needed:
                number = page["page_number"]
                image_path = doc_dir / "rendered" / f"page-{number:04d}.png"
                prior = queued.get((source_id, number))
                render_reusable = bool(prior and prior.get("source_pdf_sha256") == sha and image_path.exists() and digest(image_path) == prior.get("image_sha256"))
                if not render_reusable:
                    pdf_page = document[number - 1]
                    projected = pdf_page.rect.width * pdf_page.rect.height * (RENDER_DPI / 72) ** 2
                    dpi = min(RENDER_DPI, max(24, math.floor(RENDER_DPI * math.sqrt(MAX_RENDER_PIXELS / max(projected, 1)))))
                    image_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False).save(image_path)
                    rendered += 1
                else:
                    dpi = prior["dpi"]
                prepared_tasks[(source_id, number)] = {"source_id": source_id, "page_number": number,
                    "source_pdf_sha256": sha, "source_url": aliases[0]["url"],
                    "image_path": str(image_path), "image_sha256": digest(image_path), "dpi": dpi,
                    "screening_status": page["screening_status"], "prepared_at_utc": prior.get("prepared_at_utc", now()) if render_reusable else now()}
            allowed_numbers = {page["page_number"] for page in needed}
            for task_key in [key for key in queued if key[0] == source_id and key[1] not in allowed_numbers]:
                del queued[task_key]
            atomic_lines(doc_dir / "page_coverage.jsonl", coverage)
            embedded_path = doc_dir / "embedded_text.txt"
            embedded_path.write_text("\n\n".join(f"===== EMBEDDED PAGE {page['page_number']} =====\n{page.get('embedded_text', '')}" for page in coverage), encoding="utf-8")
            record = {"source_id": source_id, "source_url": aliases[0]["url"],
                      "source_urls": [alias["url"] for alias in aliases], "source_resources": aliases,
                      "source_pdf_path": str(source), "source_pdf_relative_path": aliases[0]["raw_path"],
                      "source_pdf_sha256": sha, "source_pdf_bytes": source.stat().st_size,
                      "source_verified_at_utc": now(), "source_in_latest_snapshot": True,
                      "embedded_text_path": str(embedded_path), "embedded_text_sha256": digest(embedded_path),
                      "collector_embedded_text_paths": [str(inside(root, alias["text_path"])) for alias in aliases if alias.get("text_path")],
                      "pdf_pages": len(document), "ocr_pages_needed": len(needed),
                      "ocr_page_numbers": sorted(allowed_numbers), "screening_complete": screening_complete,
                      "screening_status_counts": dict(Counter(page["screening_status"] for page in coverage)),
                      "manual_review_page_numbers": [page["page_number"] for page in coverage if page.get("manual_review_recommended")],
                      "page_coverage_path": str(doc_dir / "page_coverage.jsonl"),
                      "screening_version": VERSION, "screening_note": SCREENING_NOTE}
            documents[source_id] = record
            queued.update(prepared_tasks)
            atomic_json(cache_file, {"source_pdf_sha256": sha, "screening_version": VERSION,
                                    "screening_complete": screening_complete, "page_coverage": coverage})
        except Exception as exc:
            errors.append({"source_id": source_id, "source_url": aliases[0]["url"], "raw_path": aliases[0]["raw_path"],
                           "error": type(exc).__name__ + ": " + str(exc)})
        finally:
            if document is not None:
                document.close()
    for source_id, record in documents.items():
        record["source_in_latest_snapshot"] = source_id in refreshed_ids
    records = sorted(documents.values(), key=lambda record: record["source_id"])
    pages = sorted(queued.values(), key=lambda page: (page["source_id"], page["page_number"]))
    atomic_lines(output / "documents.jsonl", records)
    atomic_lines(output / "pages.jsonl", pages)
    atomic_lines(output / "preparation_errors.jsonl", errors)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    atomic_lines(output / "preparation_runs" / f"{run_id}.errors.jsonl", errors)
    statuses = Counter()
    for record in records:
        statuses.update(record["screening_status_counts"])
    summary = {"generated_at_utc": now(), "source_snapshot_at_utc": snapshot_at,
               "snapshot_kind": snapshot_kind,
               "database_snapshot_at_utc": snapshot_at if snapshot_kind == "collector_database" else None,
               "database_read_only": True if snapshot_kind == "collector_database" else None,
               "downloaded_resources_in_snapshot": len(resources),
               "verified_unique_pdfs_in_snapshot": len(grouped), "documents": len(records),
               "documents_needing_ocr": sum(record["ocr_pages_needed"] > 0 for record in records),
               "documents_with_incomplete_screening": sum(not record["screening_complete"] for record in records),
               "pdf_pages_in_documents": sum(record["pdf_pages"] for record in records),
               "pages": len(pages), "page_screening_status_counts": dict(statuses),
               "newly_screened_documents_this_run": newly_screened, "rendered_pages_this_run": rendered,
               "preparation_errors": len(errors), "original_files_modified": False, "embedded_text_preserved": True,
               "network_requests": 0, "model_path": str(output / "models" / "eng.traineddata"),
               "model_sha256": model_sha, "render_engine": "PyMuPDF " + fitz.VersionBind,
               "render_dpi_target": RENDER_DPI, "max_render_pixels": MAX_RENDER_PIXELS,
               "screening_version": VERSION, "screening_note": SCREENING_NOTE,
               "elapsed_seconds": round(time.monotonic() - started, 3)}
    atomic_json(output / "queue_summary.json", summary)
    atomic_json(output / "preparation_runs" / f"{run_id}.json", summary)
    print(json.dumps(summary), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=WORKSPACE / "corpus/official_courts")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model-dir", type=Path, default=WORKSPACE / "sources/official_courts/ocr/models")
    parser.add_argument("--run-ocr", action="store_true")
    options = parser.parse_args()
    output = (options.output or options.root / "ocr").resolve()
    prepare(options.root, output, options.model_dir)
    if options.run_ocr:
        environment = {**os.environ, "OFFICIAL_OCR_ROOT": str(output),
                       "OFFICIAL_OCR_RUN_SECONDS": "7200", "OFFICIAL_OCR_WORKERS": "2"}
        subprocess.run(["C:/Program Files/nodejs/node.exe", str(WORKSPACE / "sources/official_courts/ocr_worker.cjs")],
                       env=environment, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
