"""Screen saved official-law PDFs and optionally OCR their selected scan pages.

Only successful PDF metadata records are inputs. Original documents, collector
text and failure records remain unchanged. No network requests are made.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

try:
    from . import prepare_court_ocr as prep
except ImportError:
    import prepare_court_ocr as prep


def snapshot(root):
    resources, errors = [], []
    for metadata in sorted((root / "metadata").glob("*.json")):
        try:
            item = json.loads(metadata.read_text(encoding="utf-8-sig"))
            if item.get("verification_status") != "retrieved" or item.get("format") != "pdf":
                continue
            if item.get("http_status") != 200:
                raise ValueError("Retrieved PDF metadata does not record HTTP 200")
            raw = prep.inside(root, item["evidence_path"])
            if not raw.is_relative_to((root / "documents").resolve()):
                raise ValueError("PDF is outside the official-law documents directory")
            text = item.get("text_path")
            if text and not prep.inside(root, text).is_relative_to((root / "text").resolve()):
                raise ValueError("Embedded text is outside the official-law text directory")
            resources.append({"id": metadata.stem, "url": item["source_url"],
                              "raw_path": item["evidence_path"], "text_path": text,
                              "metadata_path": str(metadata.relative_to(root)),
                              "sha256": item.get("sha256"), "byte_count": item.get("bytes"),
                              "last_fetch_id": metadata.stem, "extraction_status": "embedded_text_saved",
                              "retrieved_at_utc": item.get("retrieved_at_utc"),
                              "source_metadata_sha256": prep.digest(metadata)})
        except Exception as exc:
            errors.append({"metadata_path": str(metadata), "error": type(exc).__name__ + ": " + str(exc)})
    return resources, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=prep.WORKSPACE / "sources/official_laws")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model-dir", type=Path, default=prep.WORKSPACE / "sources/official_courts/ocr/models")
    parser.add_argument("--run-ocr", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = (args.output or root / "ocr").resolve()
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < 10 * 1024 ** 3:
        raise RuntimeError("Official-law OCR disk guard: less than 10 GiB free")
    resources, errors = snapshot(root)
    prep.atomic_lines(output / "source_snapshot_errors.jsonl", errors)
    prep.atomic_lines(output / "source_snapshot.jsonl", resources)
    summary = prep.prepare(root, output, args.model_dir, resource_snapshot=resources,
                           snapshot_kind="official_law_success_metadata")
    summary["source_snapshot_errors"] = len(errors)
    prep.atomic_json(output / "queue_summary.json", summary)
    if args.run_ocr:
        environment = {**os.environ, "OFFICIAL_OCR_ROOT": str(output),
                       "OFFICIAL_OCR_RUN_SECONDS": "7200", "OFFICIAL_OCR_WORKERS": "2"}
        subprocess.run(["C:/Program Files/nodejs/node.exe", str(prep.WORKSPACE / "sources/official_courts/ocr_worker.cjs")],
                       env=environment, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    main()
