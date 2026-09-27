"""Source-directory documents (2026-09-19): index of the files downloaded by corpus/source_directory_documents_20260919.

Offline snapshot of a growing download collection; re-run to pick up new files. Each row is one distinct file (by SHA-256)
fetched from an open, official or association address listed in the source directory, with the project's robots-respecting crawler. Original bytes and extracted
text stay in the collection; this index records their hashes so the adapter can verify them again when serving.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COLLECTION = ROOT / 'corpus' / 'source_directory_documents_20260919'
DB = HERE / 'documents.sqlite3'
FTS_CHARS = 120_000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main():
    source = sqlite3.connect(f'file:{(COLLECTION / "corpus.sqlite3").as_posix()}?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    families = {}
    titles = {}
    for row in source.execute('SELECT rc.resource_id, c.source_family, c.seed_json FROM resource_contexts rc JOIN contexts c ON c.id=rc.context_id'):
        families.setdefault(row['resource_id'], row['source_family'])
        try:
            titles.setdefault(row['resource_id'], (json.loads(row['seed_json']) or {}).get('source_title'))
        except ValueError:
            pass
    if DB.exists():
        DB.unlink()
    out = sqlite3.connect(DB)
    out.executescript('''
        CREATE TABLE docs(id INTEGER PRIMARY KEY, url TEXT NOT NULL, host TEXT NOT NULL, family TEXT, title TEXT, title_basis TEXT, filename TEXT, ext TEXT, sha256 TEXT NOT NULL UNIQUE,
            bytes INTEGER, raw_path TEXT NOT NULL, text_path TEXT, text_sha256 TEXT, text_chars INTEGER, extraction_status TEXT, fetched_at TEXT, http_status INTEGER, other_urls TEXT);
        CREATE VIRTUAL TABLE docs_fts USING fts5(title, url, body, tokenize='unicode61');
    ''')
    seen = {}
    skipped = Counter()
    for row in source.execute("SELECT * FROM resources WHERE status='downloaded' AND raw_complete=1 AND sha256 IS NOT NULL AND duplicate_of IS NULL ORDER BY id"):
        raw = COLLECTION / row['raw_path']
        if not raw.is_file():
            skipped['raw file missing'] += 1
            continue
        if row['sha256'] in seen:
            seen[row['sha256']].append(row['url'])
            continue
        text, text_sha, text_chars = '', None, 0
        if row['text_path'] and (COLLECTION / row['text_path']).is_file():
            data = (COLLECTION / row['text_path']).read_bytes()
            text_sha, text = sha256_bytes(data), data.decode('utf-8', 'replace')
            text_chars = len(text)
        path = urllib.parse.unquote(urllib.parse.urlsplit(row['url']).path)
        filename = path.rsplit('/', 1)[-1] or row['host']
        extracted = (row['title'] or '').strip()
        pattern = re.match(r'^\d+-(\d{4})\.(\d{2})\.(\d{2})-([A-Z]{2}|Multistate|Multi-State)-(.+?)(?:\.pdf)?$', filename)
        listed = (titles.get(row['id']) or '').strip()
        # PDF metadata titles are often the authoring tool's file name ("2005Cover.cdr", "Microsoft Word - draft3.doc"): not a title.
        tool_name = bool(re.search(r'\.(cdr|docx?|indd|qxd|pdf|pmd|pptx?|wpd|xlsx?|p65|fm)$', extracted, re.I)) or extracted.lower().startswith('microsoft word -') or (' ' not in extracted and len(extracted) < 40 and bool(re.search(r'[\d_]', extracted)))
        if pattern:
            title, basis = '%s (%s, %s-%s-%s)' % (' '.join(pattern.group(5).replace('-', ' ').replace('_', ' ').split()), pattern.group(4), pattern.group(1), pattern.group(2), pattern.group(3)), 'file name as published (party, document, state, date)'
        elif extracted and not tool_name and extracted.lower() not in ('untitled', filename.lower()):
            title, basis = extracted[:300], 'title recorded in the file or page'
        elif listed:
            title, basis = listed[:300], 'link text from the discovery list'
        else:
            title, basis = filename, 'file name'
        seen[row['sha256']] = []
        out.execute('INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            row['id'], row['url'], row['host'], families.get(row['id']), title, basis, filename, (filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''), row['sha256'],
            row['byte_count'], row['raw_path'], row['text_path'] if text_sha else None, text_sha, text_chars, row['extraction_status'], row['updated_at'], row['last_http_status'], None))
        out.execute('INSERT INTO docs_fts(rowid, title, url, body) VALUES(?,?,?,?)', (row['id'], title, row['url'], text[:FTS_CHARS]))
    for digest, others in seen.items():
        if others:
            out.execute('UPDATE docs SET other_urls=? WHERE sha256=?', (json.dumps(others[:20]), digest))
    out.commit()
    statuses = dict(source.execute('SELECT status, count(*) FROM resources GROUP BY status').fetchall())
    paused = [dict(host=h, reason=r) for h, r in source.execute('SELECT host, pause_reason FROM hosts WHERE pause_reason IS NOT NULL')]
    counts = {
        'documents': out.execute('SELECT count(*) FROM docs').fetchone()[0], 'with_extracted_text': out.execute('SELECT count(*) FROM docs WHERE text_chars>0').fetchone()[0],
        'bytes': out.execute('SELECT coalesce(sum(bytes),0) FROM docs').fetchone()[0], 'by_family': dict(out.execute('SELECT family, count(*) FROM docs GROUP BY family ORDER BY 2 DESC')),
        'by_extension': dict(out.execute('SELECT ext, count(*) FROM docs GROUP BY ext ORDER BY 2 DESC')), 'skipped': dict(skipped),
        'collection_queue_at_build': statuses, 'hosts_paused_by_the_crawler': paused,
    }
    out.execute('VACUUM')
    out.close()
    source.close()
    validation = {
        'schema_version': '1', 'status': 'passed' if counts['documents'] else 'failed', 'ready': bool(counts['documents']), 'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': DB.name, 'sha256': sha256_bytes(DB.read_bytes()), 'rows': counts['documents']}], 'counts': counts,
        'checks': {'one_row_per_distinct_file_hash': True, 'raw_file_present_for_every_row': True, 'no_network_used_by_this_build': True},
        'qualification': 'Files downloaded on 2026-09-19 from official agency and science hosts (robots.txt respected, one request per host at a time). The list of addresses came from '
                         'locally saved discovery lists, so this is a partial and still-growing collection, not an agency\'s complete publications. %s. A file is whatever the '
                         'address returned on that date; it may since have been revised or withdrawn.' % (
                             'Hosts that refused the crawler and were not retried: ' + ', '.join('%s (%s)' % (p['host'], p['reason']) for p in paused) if paused else 'No host refused the crawler'),
        'license_ref': 'official_and_association_public_documents_saved_copies', 'inputs': [{'path': (COLLECTION / 'corpus.sqlite3').as_posix(), 'note': 'live download collection; snapshot at build time'}],
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1), encoding='utf-8')
    print(json.dumps({k: counts[k] for k in ('documents', 'with_extracted_text', 'bytes', 'by_family', 'by_extension', 'skipped')}, indent=1))


if __name__ == '__main__':
    main()
