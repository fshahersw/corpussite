"""Offline Seeger import; external library is read-only, outputs are isolated."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources/seeger_import_20260918'
STAGING = Path('C:/Users/firas/Downloads/Seeger-Corpus-Enrichment-2026-09-13/staging')
LIBRARY = Path('C:/Users/firas/Downloads/Court-Document-Library')
MAX_TEXT_CHARS = 5_000_000
RULE = re.compile(r'\blocal rules?\b|\brules of (?:civil|criminal|appellate|evidence|procedure|court)|\bstanding orders?\b|\bgeneral orders?\b|\badministrative orders?\b', re.I)
ADMIN = re.compile(r'\b(?:job application|application for employment|employment application form|application for judicial vacancy|recruitment notice|employee benefits|vendor registration|procurement solicitation|request for proposals)\b', re.I)


def stamp(): return datetime.now(timezone.utc).isoformat()
def sha(data): return hashlib.sha256(data).hexdigest()
def stable(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
def relative(path): return path.resolve().relative_to(ROOT).as_posix()
def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
def save_jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as f:
        for value in values: f.write(json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n')
def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))
def read_jsonl(path):
    with path.open(encoding='utf-8-sig') as f: return [json.loads(line) for line in f if line.strip()]
def source_file(name):
    p = (LIBRARY / name).resolve(); p.relative_to(LIBRARY.resolve()); return p


class Embedded(HTMLParser):
    def __init__(self): super().__init__(); self.current = None; self.parts = []; self.data = {}
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type') == 'application/json': self.current = attrs.get('id'); self.parts = []
    def handle_data(self, data):
        if self.current is not None: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None: self.data[self.current] = json.loads(''.join(self.parts)); self.current = None


def body_text(path, fmt):
    """Native extraction only; no OCR, executable macros, passwords or network."""
    info = {'method': None, 'status': 'unsupported_format', 'complete_native_pass': False}
    text = ''
    try:
        if fmt == 'pdf':
            import fitz
            fitz.TOOLS.mupdf_display_errors(False)
            fitz.TOOLS.mupdf_display_warnings(False)
            info.update(method='PyMuPDF page.get_text(text), original page order', parser_version=fitz.VersionBind)
            with fitz.open(path) as doc:
                if doc.needs_pass: return '', {**info, 'status': 'password_required'}
                info['page_count'] = doc.page_count
                parts, chars, blank = [], 0, 0
                for i, page in enumerate(doc):
                    value = page.get_text('text')
                    blank += not value.strip()
                    parts.append(value[:max(0, MAX_TEXT_CHARS - chars)])
                    chars += len(value) + 2
                    if chars > MAX_TEXT_CHARS: break
                text = '\n\f\n'.join(parts)
                info.update(pages_processed=len(parts), pages_without_native_text=blank,
                    complete_native_pass=len(parts) == doc.page_count and chars <= MAX_TEXT_CHARS,
                    status='text_truncated' if chars > MAX_TEXT_CHARS else 'extracted' if text.strip() else 'no_native_text_ocr_needed')
        elif fmt == 'docx':
            info.update(method='DOCX OOXML paragraph text including tables, headers and footers; no macros executed')
            with zipfile.ZipFile(path) as z:
                members = ['word/document.xml'] + sorted(n for n in z.namelist() if re.fullmatch(r'word/(header\d+|footer\d+|footnotes|endnotes)\.xml', n))
                paragraphs = []
                for name in members:
                    tree = ET.fromstring(z.read(name))
                    for p in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
                        pieces = []
                        for node in p.iter():
                            tag = node.tag.split('}')[-1]
                            if tag in ('t', 'delText') and node.text: pieces.append(node.text)
                            elif tag == 'tab': pieces.append('\t')
                            elif tag in ('br', 'cr'): pieces.append('\n')
                        if pieces: paragraphs.append(''.join(pieces))
                text = '\n'.join(paragraphs)
                info.update(parts_processed=members, complete_native_pass=len(text) <= MAX_TEXT_CHARS,
                    status='text_truncated' if len(text) > MAX_TEXT_CHARS else 'extracted' if text.strip() else 'no_native_text',
                    tracked_changes_not_resolved=True, visual_layout_preserved=False)
        elif fmt == 'xml':
            tree = ET.parse(path)
            text = '\n'.join(value.strip() for value in tree.getroot().itertext() if value.strip())
            info.update(method='XML itertext in source order, structured provision derivatives separate', complete_native_pass=len(text) <= MAX_TEXT_CHARS,
                status='text_truncated' if len(text) > MAX_TEXT_CHARS else 'extracted')
        elif fmt == 'txt':
            data = path.read_bytes()
            try: text = data.decode('utf-8-sig'); encoding = 'utf-8-sig'
            except UnicodeDecodeError: text = data.decode('cp1252'); encoding = 'cp1252'
            # This publisher TXT is already the body; preserve it completely in UTF-8.
            info.update(method='publisher_plaintext_decode', encoding=encoding, complete_native_pass=True, status='extracted')
            return text, info
        elif fmt in ('doc', 'rtf'):
            info.update(method=None, status='legacy_format_native_parser_not_available')
        return text[:MAX_TEXT_CHARS], info
    except Exception as exc:
        return '', {**info, 'status': 'extraction_error', 'error': type(exc).__name__ + ': ' + str(exc)[:500]}


def import_original(task):
    source = source_file(task['relative_path'])
    data = source.read_bytes(); digest = sha(data)
    assert digest == task['sha256'] and len(data) == int(task['bytes']), task['relative_path']
    reused = False
    raw_path = None
    for existing in task.get('existing', []):
        p = (ROOT / existing['raw_path']).resolve()
        if p.is_relative_to(ROOT) and p.is_file() and sha(p.read_bytes()) == digest:
            raw_path = p; reused = True; break
    if raw_path is None:
        raw_path = OUT / 'raw' / digest[:2] / (digest + '.' + task['format'].lower())
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists(): raw_path.write_bytes(data)
        assert sha(raw_path.read_bytes()) == digest
    text_path, text_hash, extract = None, None, None
    for existing in task.get('existing', []):
        if not existing.get('text_path') or not existing.get('text_file_sha256'): continue
        p = (ROOT / existing['text_path']).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file(): continue
        value = p.read_bytes()
        if sha(value) != existing['text_file_sha256']: continue
        try: decoded = value.decode('utf-8')
        except UnicodeError: continue
        if not decoded.strip(): continue
        text_path, text_hash = relative(p), sha(value)
        extract = {'method': 'reuse_existing_hash_verified_text', 'status': existing['extraction_status'],
            'characters': len(decoded), 'source_version_id': existing['version_id'], 'source_collection': existing['collection'],
            'complete_native_pass': None, 'prior_extraction_status': existing['extraction_status']}
        break
    if extract is None:
        text, extract = body_text(raw_path, task['format'].lower())
        extract['characters'] = len(text)
        if text.strip():
            value = text.encode('utf-8'); text_hash = sha(value)
            p = OUT / 'text' / text_hash[:2] / (text_hash + '.txt'); p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists(): p.write_bytes(value)
            text_path = relative(p)
    result = {'original_sha256': digest, 'raw_path': relative(raw_path), 'text_path': text_path,
        'text_sha256': text_hash, 'raw_reused_from_existing_corpus': reused, 'bytes': len(data),
        'extraction': extract, 'external_source_path': source.as_posix(), 'external_source_stat': {'size': source.stat().st_size, 'mtime_ns': source.stat().st_mtime_ns}}
    save_json(OUT / 'extraction' / (digest + '.json'), result)
    return digest, result


def tier(title, reference=False):
    if reference: return 'reference_original'
    if ADMIN.search(title): return 'administrative_document'
    if RULE.search(title): return 'court_rule_or_order'
    return 'court_form_or_other_document'


def write_body(text):
    data = text.encode('utf-8'); digest = sha(data)
    path = OUT / 'text' / digest[:2] / (digest + '.txt'); path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_bytes(data)
    return relative(path), digest


def main():
    start = stamp(); OUT.mkdir(parents=True, exist_ok=True)
    input_names = ['00-Catalog/DOCUMENTS.csv', '00-Catalog/DOCUMENT-SOURCES.csv', '00-Catalog/CLEANUP-AUDIT.jsonl',
        '00-Catalog/document_metadata.jsonl', '00-Catalog/REFERENCE-DOCUMENTS.json', '00-Catalog/RULES-REFERENCES.json',
        '06-Primary-Law/PRIMARY-LAW.jsonl', '06-Primary-Law/MANIFEST.json', '06-Primary-Law/EXTRACTION-VALIDATION.json',
        '08-Extended-Primary-Law/Federal-Regulations/REGULATIONS.jsonl', '08-Extended-Primary-Law/Federal-Regulations/MANIFEST.json',
        '08-Extended-Primary-Law/New-Jersey/curated/selected-sections.jsonl',
        '08-Extended-Primary-Law/New-Jersey/curated/source-manifest.json', '08-Extended-Primary-Law/New-Jersey/curated/source-quality.json']
    inputs = []
    for name in input_names:
        source = source_file(name); value = source.read_bytes(); dest = OUT / 'provenance' / name
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(value)
        inputs.append({'source_path': source.as_posix(), 'path': relative(dest), 'sha256': sha(value), 'bytes': len(value)})
    for source in [STAGING / 'INDEX.html', STAGING / 'COVERAGE.html', STAGING.parent / 'FINAL-DELIVERY-RECEIPT.json', STAGING.parent / 'PUBLISH-RECEIPT.json']:
        data = source.read_bytes(); dest = OUT / 'provenance' / 'staging' / source.name
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
        inputs.append({'source_path': source.as_posix(), 'path': relative(dest), 'sha256': sha(data), 'bytes': len(data)})
    save_json(OUT / 'inputs.json', inputs)
    embedded = Embedded(); embedded.feed((STAGING / 'INDEX.html').read_text(encoding='utf-8'))
    docs = read_csv(source_file('00-Catalog/DOCUMENTS.csv'))
    assert len(docs) == 11181 == len(embedded.data['documents'])
    embedded_by_id = {r['id']: r for r in embedded.data['documents']}
    for row in docs:
        original = embedded_by_id[row['sha256'][:12]]
        assert original['path'] == row['relative_path'] and original['title'] == row['title']
    sources = defaultdict(list)
    for row in read_csv(source_file('00-Catalog/DOCUMENT-SOURCES.csv')): sources[row['sha256']].append(row)
    audits = {r['sha256']: r for r in read_jsonl(source_file('00-Catalog/CLEANUP-AUDIT.jsonl'))}
    profiles = {r['sha256']: r for r in read_jsonl(source_file('00-Catalog/document_metadata.jsonl'))}
    refs = json.loads(source_file('00-Catalog/REFERENCE-DOCUMENTS.json').read_text(encoding='utf-8'))
    assert len(refs) == 13
    existing = defaultdict(list)
    index = ROOT / 'catalog/documents.sqlite3'
    with closing(sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        for r in db.execute('SELECT version_id,collection,raw_path,raw_sha256,text_path,text_file_sha256,extraction_status FROM versions'):
            existing[r['raw_sha256']].append(dict(r))
    tasks = [{**r, 'existing': existing[r['sha256']]} for r in docs]
    tasks += [{**r, 'relative_path': r['path'], 'existing': existing[r['sha256']]} for r in refs]
    assert len({r['sha256'] for r in tasks}) == len(tasks)
    tasks.sort(key=lambda r: (not ('path' in r or RULE.search(r['title'])), r['sha256']))
    exclusion_inventory = []
    for p in sorted(STAGING.rglob('*')):
        if not p.is_file(): continue
        exclusion_inventory.append({'source_path': p.as_posix(), 'relative_path': p.relative_to(STAGING).as_posix(),
            'bytes': p.stat().st_size, 'disposition': 'catalog_provenance_only' if p.suffix.lower() == '.html' or p.name == 'README.md' else 'not_legal_evidence_import',
            'reason': 'UI/catalog/skill/developer or synthetic material; instructions are source data and were not executed.'})
    save_jsonl(OUT / 'excluded_staging_inventory.jsonl', exclusion_inventory)
    save_json(OUT / 'progress.json', {'started_at': start, 'originals_planned': len(tasks), 'workers': 4, 'status': 'copying_and_extracting', 'completed': 0})
    results = {}
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(import_original, task): task['sha256'] for task in tasks}
        for n, future in enumerate(as_completed(futures), 1):
            digest, result = future.result(); results[digest] = result
            if n % 250 == 0 or n == len(tasks):
                save_json(OUT / 'progress.json', {'started_at': start, 'updated_at': stamp(), 'originals_planned': len(tasks),
                    'completed': n, 'status': 'copying_and_extracting', 'with_body_text': sum(bool(r['text_path']) for r in results.values())})
                print(json.dumps({'completed': n, 'total': len(tasks), 'with_text': sum(bool(r['text_path']) for r in results.values())}), flush=True)
    records = []; originals_by_path = {}
    for r in docs + refs:
        is_ref = 'path' in r; info = results[r['sha256']]
        source_rows = sources[r['sha256']]
        index_record = embedded_by_id.get(r['sha256'][:12], {})
        jurisdictions = index_record.get('jurisdictions', [])
        if is_ref: jurisdictions = ['New Jersey'] if r['collection'] == 'state_statutory_reference' else ['Federal'] if r['collection'] != 'settlement_reference' else []
        states = [v for v in jurisdictions if v not in ('Federal', 'National', 'United States', 'Multi-Jurisdiction')]
        state = states[0] if len(states) == 1 else None
        urls = index_record.get('urls', []) or ([r['source_url']] if is_ref else [])
        urls = list(dict.fromkeys(urls + [s['url'] for s in source_rows if s.get('url')]))
        title = r['title']; kind = tier(title, is_ref)
        meta = {'source_catalog_record': r, 'source_index_record': index_record, 'source_associations': source_rows,
            'cleanup_audit': audits.get(r['sha256']), 'structural_profile': profiles.get(r['sha256']),
            'import_evidence': info, 'source_jurisdictions': jurisdictions, 'source_courts': index_record.get('courts', []),
            'county_and_territorial_scope_inferred': False, 'legal_currency_or_applicability_verified_by_import': False,
            'collection_label_is_not_territorial_applicability': True, 'input_catalog_path': relative(OUT / 'provenance/00-Catalog' / ('REFERENCE-DOCUMENTS.json' if is_ref else 'DOCUMENTS.csv'))}
        metadata_path = OUT / 'metadata' / (r['sha256'] + '.json'); save_json(metadata_path, meta)
        record = {'id': 'seeger_original_' + r['sha256'], 'title': title, 'source_url': urls[0] if urls else None,
            'state': state, 'county': None, 'county_geoids': [], 'group': 'reference' if is_ref and r['collection'] == 'settlement_reference' else 'federal' if jurisdictions == ['Federal'] else 'laws',
            'kind': kind, 'resource_kind': kind, 'raw_path': info['raw_path'], 'text_path': info['text_path'],
            'sha256': r['sha256'], 'text_sha256': info['text_sha256'], 'metadata_path': relative(metadata_path),
            'metadata_sha256': sha(metadata_path.read_bytes()), 'captured_at': r.get('retrieved_at') or next((s['retrieved_date'] for s in source_rows if s.get('retrieved_date')), None),
            'quality': 'Imported local original; source review flags retained; legal applicability unverified',
            'metadata': {'record_type': 'original_document', 'format': r['format'].lower(), 'bytes': info['bytes'], 'source_urls': urls,
                'source_jurisdictions': jurisdictions, 'source_courts': index_record.get('courts', []), 'source_collections': index_record.get('collections', []),
                'language': r.get('language'), 'technical_flags': r.get('technical_flags'), 'review_status': r.get('review_status'),
                'publisher_scope_note': r.get('publisher_scope_note'), 'source_date': r.get('source_date'), 'source_date_kind': r.get('source_date_kind'),
                'extraction': info['extraction'], 'raw_reused_from_existing_corpus': info['raw_reused_from_existing_corpus'],
                'kind_basis': 'source catalog/title organizing hint, not an independent content review',
                'source_evidence_path': relative(metadata_path), 'source_evidence_sha256': sha(metadata_path.read_bytes()),
                'county_territorial_assignment_verified': False, 'source_legal_currency_verified': False, 'retrieval_eligible': kind != 'administrative_document'}}
        records.append(record); originals_by_path[r.get('relative_path') or r['path']] = record
    # Reuse supplied, publisher-bound body text as readable derivatives, never JSON display.
    manifest_paths = {
        'usc': '06-Primary-Law/PRIMARY-LAW.jsonl',
        'fda': '08-Extended-Primary-Law/Federal-Regulations/REGULATIONS.jsonl',
        'nj': '08-Extended-Primary-Law/New-Jersey/curated/selected-sections.jsonl'}
    nj_manifest = json.loads(source_file('08-Extended-Primary-Law/New-Jersey/curated/source-manifest.json').read_text(encoding='utf-8'))
    nj_path = next(r['path'] for r in refs if r['id'] == 'nj-statutes-2025-405-txt')
    nj_bytes = source_file(nj_path).read_bytes()
    for family, manifest in manifest_paths.items():
        manifest_hash = sha(source_file(manifest).read_bytes())
        for line_no, r in enumerate(read_jsonl(source_file(manifest)), 1):
            text = r.get('body_text', r.get('text', ''))
            parent_path = r.get('xml_path') or r.get('original_path') or nj_path
            parent = originals_by_path[parent_path]
            if family == 'nj':
                literal = nj_bytes[r['source_byte_start']:r['source_byte_end_exclusive']]
                assert sha(literal) == r['source_slice_sha256'] and literal.decode('cp1252') == text
                method = 'exact publisher TXT byte slice, CP1252 decoded; boundary and RTF heading lineage retained'
                url, captured = nj_manifest['source_url'], nj_manifest['retrieved_at']
            else:
                assert parent['sha256'] == r.get('source_xml_sha256', r.get('source_sha256'))
                method = 'reuse provided structured body_text bound to verified publisher XML; semantic/parser validation not rerun'
                url, captured = r['source_url'], r.get('retrieved_at') or parent['captured_at']
            title = (r.get('citation') or '') + ' — ' + (r.get('title') or r.get('heading') or r['id'])
            # Editorial material remains visibly distinguished from the principal body.
            sections = [title, text]
            if r.get('editorial_notes'): sections += ['Editorial notes', r['editorial_notes']]
            if r.get('source_credit'): sections += ['Source credit', r['source_credit']]
            readable = '\n\n'.join(s for s in sections if s)
            text_path, text_hash = write_body(readable)
            rid = 'seeger_provision_' + sha(stable([family, manifest_hash, line_no, r['id']]))[:32]
            meta = {'source_record': r, 'source_manifest': {'path': relative(OUT / 'provenance' / manifest), 'sha256': manifest_hash, 'line_1based': line_no},
                'parent_original_id': parent['id'], 'parent_original_raw_sha256': parent['sha256'], 'method': method,
                'body_text_present': bool(text.strip()), 'legal_currency_verified': False,
                'nj_source_version_flags': nj_manifest if family == 'nj' else None}
            # The common NJ manifest is separately preserved rather than repeated 5,856 times.
            if family == 'nj': meta['nj_source_version_flags'] = {'source_manifest_path': relative(OUT / 'provenance/08-Extended-Primary-Law/New-Jersey/curated/source-manifest.json'), 'currentness_assessment': nj_manifest['currentness_assessment'], 'version_discrepancy': nj_manifest['version_discrepancy']}
            p = OUT / 'metadata' / (rid + '.json'); save_json(p, meta)
            provision_kind = 'regulatory_provision' if family == 'fda' else 'rule_provision' if family == 'usc' and r.get('family') not in ('USC9', 'USC28') else 'statutory_provision'
            records.append({'id': rid, 'title': title, 'source_url': url, 'state': 'New Jersey' if family == 'nj' else None,
                'county': None, 'county_geoids': [], 'group': 'laws' if family == 'nj' else 'federal', 'kind': provision_kind,
                'resource_kind': provision_kind, 'raw_path': parent['raw_path'],
                'text_path': text_path, 'sha256': parent['sha256'], 'text_sha256': text_hash,
                'metadata_path': relative(p), 'metadata_sha256': sha(p.read_bytes()), 'captured_at': captured,
                'quality': 'Imported dated source derivative; current legal effect unverified',
                'metadata': {'record_type': 'structured_provision', 'family': family, 'citation': r.get('citation'), 'source_status': r.get('source_status'),
                    'parent_original_id': parent['id'], 'source_record_id': r['id'], 'body_text_present': bool(text.strip()), 'method': method,
                    'source_legal_currency_verified': False, 'source_evidence_path': relative(p), 'source_evidence_sha256': sha(p.read_bytes()),
                    'nj_currentness_discrepancy': nj_manifest['currentness_assessment'] if family == 'nj' else None}})
    save_jsonl(OUT / 'resources.jsonl', records)
    save_jsonl(OUT / 'originals.jsonl', [r for r in records if r['metadata']['record_type'] == 'original_document'])
    gaps = [r for r in records if r['metadata']['record_type'] == 'original_document' and (not r['text_path'] or r['metadata']['extraction'].get('status') == 'text_truncated' or r['metadata']['extraction'].get('pages_without_native_text', 0))]
    save_jsonl(OUT / 'extraction_gaps.jsonl', [{'id': r['id'], 'raw_path': r['raw_path'], 'sha256': r['sha256'], 'title': r['title'], 'extraction': r['metadata']['extraction']} for r in gaps])
    summary = {'started_at': start, 'completed_at': stamp(), 'resources': len(records), 'retained_court_originals': len(docs),
        'reference_originals': len(refs), 'structured_provisions': len(records) - len(tasks), 'originals_with_nonempty_text': sum(bool(r['text_path']) for r in results.values()),
        'originals_without_native_text': sum(not r['text_path'] for r in results.values()), 'originals_with_partial_or_missing_text_evidence': len(gaps),
        'raw_files_reused_from_existing_corpus': sum(r['raw_reused_from_existing_corpus'] for r in results.values()),
        'original_bytes': sum(r['bytes'] for r in results.values()), 'bytes_copied': sum(r['bytes'] for r in results.values() if not r['raw_reused_from_existing_corpus']),
        'by_extraction_status': dict(Counter(r['extraction']['status'] for r in results.values())),
        'by_kind': dict(Counter(r['kind'] for r in records)), 'by_group': dict(Counter(r['group'] for r in records)),
        'staging_only_material_inventory': len(exclusion_inventory), 'source_staging': STAGING.as_posix(), 'actual_library': LIBRARY.as_posix(),
        'external_sources_modified': False, 'network_requests': 0, 'published_snapshots_modified': False,
        'resources_sha256': sha((OUT / 'resources.jsonl').read_bytes()), 'originals_sha256': sha((OUT / 'originals.jsonl').read_bytes()),
        'full_county_state_or_legal_content_complete': False, 'source_legal_currency_verified': False}
    save_json(OUT / 'summary.json', summary)
    save_json(OUT / 'progress.json', {'status': 'import_finished_pending_validation', **summary})
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__': main()
