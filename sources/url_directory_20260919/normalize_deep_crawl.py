"""Normaliser for the deep_crawl_urls / deep_crawl_documents inputs (2026-09-19).

Inputs (read-only, in place, never modified):
  C:/Users/firas/Downloads/returnedfiles/deep_crawl_urls.jsonl        (178,961 lines)
  C:/Users/firas/Downloads/returnedfiles/deep_crawl_documents.csv     (63,684 rows)
  C:/Users/firas/Downloads/returnedfiles/deep_crawl_url_directory.md  (legend / job names, read-only reference)

Each row of deep_crawl_urls.jsonl carries a `sources` tag naming the parallel-crawl JOB that
discovered the URL (per the "Per-job yield" table in deep_crawl_url_directory.md) -- it is NOT a
claim about which organisation's domain the URL lives on (crawls follow outbound links, so e.g. a
`cpsc` job can discover a `bhgs.dca.ca.gov` PDF). TAG_LEGEND below maps each job tag to the layer
and org_name of the crawl job itself, exactly as instructed by the build task ("map each tag to a
layer + org_name using the directory md as the legend"). A row can carry more than one tag
(semicolon-joined, e.g. "fjc;supremecourt") when more than one job's crawl reached the same URL;
the first tag is used for layer/jurisdiction_level/state/org_name and every tag is preserved,
verbatim, in `topics`.

This module is deterministic and offline: no network, no filesystem writes except the three
declared output files, streaming line-by-line so memory use stays flat regardless of input size.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import urlnorm  # noqa: E402

SOURCE_LIST = 'deep_crawl_urls'
DEFAULT_URLS_PATH = 'C:/Users/firas/Downloads/returnedfiles/deep_crawl_urls.jsonl'
DEFAULT_DOCUMENTS_PATH = 'C:/Users/firas/Downloads/returnedfiles/deep_crawl_documents.csv'
DEFAULT_DIRECTORY_MD_PATH = 'C:/Users/firas/Downloads/returnedfiles/deep_crawl_url_directory.md'

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'normalized')
OUT_ROWS_PATH = os.path.join(OUT_DIR, 'deep_crawl.jsonl')
OUT_REJECTED_PATH = os.path.join(OUT_DIR, 'deep_crawl.rejected.jsonl')
OUT_REPORT_PATH = os.path.join(OUT_DIR, 'deep_crawl.report.json')

# tag (as it appears in the source's `sources` field) -> (layer, jurisdiction_level, state, org_name)
# Legend source: the "Per-job yield" table in deep_crawl_url_directory.md, cross-checked against the
# job's own home domain (e.g. www.jpml.uscourts.gov for "jpml"). org_name/state describe the crawl
# JOB, not necessarily the domain of any individual URL it turned up.
TAG_LEGEND = {
    'fjc': ('federal_agency', 'federal', None, 'Federal Judicial Center'),
    'ca_oal': ('state_agency', 'state', 'CA', 'California Office of Administrative Law'),
    'nhtsa': ('federal_agency', 'federal', None, 'National Highway Traffic Safety Administration'),
    'ny_dos': ('state_agency', 'state', 'NY', 'New York Department of State'),
    'ca_courts': ('state_court', 'state', 'CA', 'California Courts (Judicial Council of California)'),
    'ussc': ('federal_agency', 'federal', None, 'United States Sentencing Commission'),
    'osha': ('federal_agency', 'federal', None, 'Occupational Safety and Health Administration'),
    'supremecourt': ('federal_court', 'federal', None, 'Supreme Court of the United States'),
    'cpsc': ('federal_agency', 'federal', None, 'Consumer Product Safety Commission'),
    'uscourts_forms': ('federal_court', 'federal', None, 'United States Courts (Administrative Office of the U.S. Courts)'),
    'jpml': ('federal_court', 'federal', None, 'Judicial Panel on Multidistrict Litigation'),
    'nc_leg': ('legislature_law', 'state', 'NC', 'North Carolina General Assembly'),
    'nj_oal': ('state_agency', 'state', 'NJ', 'New Jersey Office of Administrative Law'),
    'phila_courts': ('county_court', 'municipal', 'PA', 'Philadelphia Courts'),
}


def split_tags(raw_sources):
    """Split a possibly-semicolon-joined `sources` value into a list of non-empty tag strings.
    Never invents a tag; an empty/None input yields []."""
    if not raw_sources:
        return []
    return [t.strip() for t in str(raw_sources).split(';') if t.strip()]


def tag_info(tag):
    """(layer, jurisdiction_level, state, org_name) for one tag; unmapped tags fall back to
    layer 'other' with everything else None, per the build task's instruction for unknown tags."""
    return TAG_LEGEND.get(tag, ('other', None, None, None))


def load_document_index(documents_path):
    """Stream deep_crawl_documents.csv into {url: {'kind':..., 'label':...}} for the join.
    The CSV carries the same per-row fields as the jsonl (kind, domain, refs, label, url, sources);
    joining lets a document row's own kind/label win when the two disagree, without emitting a
    second row for URLs that already appear in deep_crawl_urls.jsonl."""
    index = {}
    with open(documents_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get('url') or '').strip()
            if not url:
                continue
            index[url] = {'kind': (row.get('kind') or '').strip() or None, 'label': (row.get('label') or '').strip() or None}
    return index


def build_row(raw, source_file, doc_index):
    """raw: parsed JSON dict for one deep_crawl_urls.jsonl line. Returns a urlnorm-schema row.
    Raises ValueError (caught by the caller) for anything urlnorm.make_row rejects."""
    if not isinstance(raw, dict):
        raise ValueError('row is not a JSON object')
    url = (raw.get('url') or '').strip()
    if not url:
        raise ValueError('empty url')

    doc = doc_index.get(url)
    kind_hint = (doc['kind'] if doc and doc.get('kind') else raw.get('kind')) or None
    label = (doc['label'] if doc and doc.get('label') else raw.get('label')) or None
    title = label if label else None

    raw_sources = raw.get('sources')
    tags = split_tags(raw_sources)
    primary = tags[0] if tags else None
    layer, jurisdiction_level, state, org_name = tag_info(primary)

    domain = (raw.get('domain') or '').strip() or None

    return urlnorm.make_row(
        url=url,
        source_list=SOURCE_LIST,
        source_file=source_file,
        layer=layer,
        jurisdiction_level=jurisdiction_level,
        state=state,
        county_fips=None,
        org_name=org_name,
        host=domain,
        doc_kind=urlnorm.doc_kind(url, hinted=kind_hint),
        title=title,
        topics=tags,
        parent_url=None,
        source_date=None,
        source_status=None,
    )


def reject_reason(line, raw, exc):
    if raw is None:
        return 'malformed_json'
    if not isinstance(raw, dict):
        return 'malformed_row'
    url = (raw.get('url') or '').strip()
    if not url:
        return 'empty_url'
    if urlnorm.split(url) is None:
        return 'not_http_url'
    return 'malformed_row: ' + str(exc)


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def file_descriptor(path):
    size = os.path.getsize(path)
    if size > 200 * 1024 * 1024:
        return {'path': path, 'size': size, 'mtime': os.path.getmtime(path)}
    return {'path': path, 'sha256': file_hash(path)}


def run(urls_path=DEFAULT_URLS_PATH, documents_path=DEFAULT_DOCUMENTS_PATH, out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    rows_path = os.path.join(out_dir, 'deep_crawl.jsonl')
    rejected_path = os.path.join(out_dir, 'deep_crawl.rejected.jsonl')
    report_path = os.path.join(out_dir, 'deep_crawl.report.json')

    doc_index = load_document_index(documents_path)

    rows_in = 0
    rows_out = 0
    rejected = 0
    by_layer = {}
    by_doc_kind = {}
    by_state = {}
    host_counts = {}
    noise_counts = {}

    with open(urls_path, 'r', encoding='utf-8') as fin, \
            open(rows_path, 'w', encoding='utf-8') as fout, \
            open(rejected_path, 'w', encoding='utf-8') as frej:
        for line in fin:
            stripped = line.strip()
            if not stripped:
                rows_in += 1
                rejected += 1
                frej.write(json.dumps({'raw': stripped, 'reason': 'empty'}) + '\n')
                continue
            rows_in += 1
            raw = None
            try:
                raw = json.loads(stripped)
                row = build_row(raw, urls_path, doc_index)
            except Exception as exc:  # noqa: BLE001 - reject, never crash the stream
                rejected += 1
                frej.write(json.dumps({'raw': stripped[:2000], 'reason': reject_reason(stripped, raw, exc)}) + '\n')
                continue

            fout.write(json.dumps(row, ensure_ascii=False) + '\n')
            rows_out += 1
            by_layer[row['layer']] = by_layer.get(row['layer'], 0) + 1
            by_doc_kind[row['doc_kind']] = by_doc_kind.get(row['doc_kind'], 0) + 1
            state_key = row['state'] or 'unknown'
            by_state[state_key] = by_state.get(state_key, 0) + 1
            if row['host']:
                host_counts[row['host']] = host_counts.get(row['host'], 0) + 1
            for flag in row['noise']:
                noise_counts[flag] = noise_counts.get(flag, 0) + 1

    top_hosts = sorted(host_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]

    report = {
        'schema_version': '1',
        'rows_in': rows_in,
        'rows_out': rows_out,
        'rejected': rejected,
        'by_layer': by_layer,
        'by_doc_kind': by_doc_kind,
        'by_state': by_state,
        'top_25_hosts': [{'host': h, 'count': c} for h, c in top_hosts],
        'noise_flag_counts': noise_counts,
        'inputs': [file_descriptor(urls_path), file_descriptor(documents_path)],
    }
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)

    return report


if __name__ == '__main__':
    result = run()
    print(json.dumps({k: v for k, v in result.items() if k != 'inputs'}, indent=2))
