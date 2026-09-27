"""Resumable first-page-text title extraction for the court document library.

Reads sha256/local_rel_path/extension straight from index.sqlite3 (built by build.py), opens each
PDF's first page with pypdf (never PDF metadata / Author fields), and writes one line per attempted
file to titles.jsonl. A checkpoint.json records the last sha256 processed so a re-run continues
instead of restarting. Run it for at most CLI --seconds (default 600 = 10 minutes); it always exits
cleanly and leaves both files valid for build.py to re-read (title stays null for anything not yet
processed — the index works with or without titles).

Never fabricates a title: if no usable first-page text line is found, text_extracted is false and
title is null. Never touches PDF metadata (Author, /Title dictionary, etc.) — only extracted page text.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
DB_PATH = DATA_DIR / 'index.sqlite3'
TITLES_PATH = DATA_DIR / 'titles.jsonl'
CHECKPOINT_PATH = DATA_DIR / 'checkpoint.json'

PYTHON_CANDIDATES = (
    'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe',
    'C:/Users/firas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',
)

_WS = re.compile(r'\s+')
_JUNK_LINE = re.compile(r'^[\W_0-9]*$')

# Reject list applied before a candidate line is ever accepted as a title (repair review finding:
# letterhead, pagination/table headers, street addresses and Acrobat placeholder text were being
# accepted as document titles). Applied in addition to the length/word floor below.
_PAGE_OF = re.compile(r'^page\s+\d', re.I)
# "Total" followed by more than one numeric token (a table row total line, not a title).
_TOTAL_NUMERIC_ROW = re.compile(r'^total\b(?:.*?\d[\d,]*(?:\.\d+)?){2,}', re.I)
_STREET_ADDRESS = re.compile(
    r'\d+\s+[^,]*\b(street|st\.?|avenue|ave\.?|road|rd\.?|suite|ste\.?|building|bldg\.?|p\.?o\.?\s*box)\b'
    r'|\(\d{3}\)\s?\d{3}[-.]\d{4}'
    r'|\b\d{5}(-\d{4})?\s*$',
    re.I,
)
_PLEASE_WAIT = re.compile(r'please\s+wait', re.I)
# Bare court-name letterhead, federal or state ("IN THE ... COURT", "... DISTRICT/BANKRUPTCY/... COURT").
_COURT_LETTERHEAD = re.compile(
    r'^(in the )?'
    r'(united states|u\.s\.|commonwealth of [a-z]+(?: [a-z]+){0,2}|state of [a-z]+(?: [a-z]+){0,2}'
    r'|[a-z]+(?: [a-z]+){0,2})\s*'
    r'(district|bankruptcy|appeals?|circuit|superior|supreme|county|probate|civil|criminal|magistrate)\s+'
    r'court(\s+of\s+[a-z]+(?: [a-z]+){0,2})?$',
    re.I,
)


def _accept_title_line(line):
    """True if `line` is shaped like a real document title, not letterhead / pagination / an address /
    a table-header row / Acrobat placeholder text. Pure, deterministic, never fabricates."""
    if len(line) < 15 or len(line) > 200:
        return False
    if len(line.split()) < 3:
        return False
    if _JUNK_LINE.match(line):
        return False
    digits_commas = sum(1 for ch in line if ch.isdigit() or ch == ',')
    if digits_commas / len(line) > 0.5:
        return False
    if _PAGE_OF.match(line):
        return False
    if _TOTAL_NUMERIC_ROW.match(line):
        return False
    if _STREET_ADDRESS.search(line):
        return False
    if _PLEASE_WAIT.search(line):
        return False
    if _COURT_LETTERHEAD.match(line):
        return False
    return True


def _import_pypdf():
    try:
        import pypdf  # noqa: F401
        return pypdf
    except ImportError:
        return None


def find_working_pypdf():
    """Report which of the two known interpreters has pypdf importable (informational; this script
    runs under whichever interpreter invoked it, per BRIEF's package-check-first rule)."""
    import subprocess
    found = []
    for candidate in PYTHON_CANDIDATES:
        if not Path(candidate).exists():
            continue
        try:
            out = subprocess.run([candidate, '-c', 'import pypdf; print(pypdf.__version__)'],
                                  capture_output=True, text=True, timeout=20)
            if out.returncode == 0:
                found.append((candidate, out.stdout.strip()))
        except Exception:
            continue
    return found


def first_page_title(pdf_path, pypdf):
    """Return (title_or_None, text_extracted_bool). Uses only rendered page text, first non-junk line."""
    try:
        reader = pypdf.PdfReader(str(pdf_path), strict=False)
        if not reader.pages:
            return None, False
        text = reader.pages[0].extract_text() or ''
    except Exception:
        return None, False
    if not text.strip():
        return None, False
    for raw_line in text.splitlines():
        line = _WS.sub(' ', raw_line).strip()
        if not _accept_title_line(line):
            continue
        return line, True
    return None, True  # text was extracted but no usable title-shaped line


def load_checkpoint():
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    return {'done_sha256': []}


def load_done_shas():
    """Union of the checkpoint list and every sha256 already present in titles.jsonl (belt and braces:
    a prior run's checkpoint write and its last titles.jsonl line can be out of step after a cutoff)."""
    done = set(load_checkpoint().get('done_sha256', []))
    if TITLES_PATH.exists():
        with TITLES_PATH.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                sha = row.get('sha256')
                if sha:
                    done.add(sha)
    return done


def save_checkpoint(done_shas):
    CHECKPOINT_PATH.write_text(json.dumps({'done_sha256': sorted(done_shas)}), encoding='utf-8')


def candidate_rows(staging_root):
    con = sqlite3.connect(str(DB_PATH))
    try:
        rows = con.execute(
            "SELECT sha256, local_rel_path FROM documents "
            "WHERE extension='pdf' AND local_rel_path IS NOT NULL "
            "GROUP BY sha256 ORDER BY sha256"
        ).fetchall()
    finally:
        con.close()
    return rows


def run(seconds=600, staging_root=None):
    pypdf = _import_pypdf()
    if pypdf is None:
        print('pypdf not importable under this interpreter; nothing extracted this run.', file=sys.stderr)
        return {'attempted': 0, 'extracted': 0, 'skipped_already_done': 0, 'errors': 0}
    from build import STAGING  # local import: reuses the same confined staging root as build.py
    staging_root = Path(staging_root or STAGING)
    if not DB_PATH.exists():
        print('index.sqlite3 does not exist yet; run build.py first.', file=sys.stderr)
        return {'attempted': 0, 'extracted': 0, 'skipped_already_done': 0, 'errors': 0}

    done = load_done_shas()
    rows = candidate_rows(staging_root)
    started = time.monotonic()
    attempted = extracted = errors = skipped = 0
    with TITLES_PATH.open('a', encoding='utf-8') as out:
        for sha256, rel_path in rows:
            if sha256 in done:
                skipped += 1
                continue
            if time.monotonic() - started > seconds:
                break
            full_path = (staging_root / rel_path).resolve()
            try:
                full_path.relative_to(staging_root.resolve())
            except ValueError:
                errors += 1
                done.add(sha256)
                continue
            attempted += 1
            title, text_extracted = (None, False)
            if full_path.is_file():
                title, text_extracted = first_page_title(full_path, pypdf)
            record = {'sha256': sha256, 'local_rel_path': rel_path, 'title': title,
                      'text_extracted': bool(text_extracted), 'extracted_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
            out.write(json.dumps(record) + '\n')
            out.flush()
            if title:
                extracted += 1
            done.add(sha256)
            if attempted % 200 == 0:
                save_checkpoint(done)
    save_checkpoint(done)
    return {'attempted': attempted, 'extracted': extracted, 'skipped_already_done': skipped, 'errors': errors,
            'remaining': max(0, len(rows) - len(done))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=600, help='wall-clock budget for this run (default 600 = 10 minutes)')
    args = parser.parse_args()
    print(json.dumps(run(seconds=args.seconds), indent=2))
