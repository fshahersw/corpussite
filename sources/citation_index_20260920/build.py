"""Citation index: the authorities cited inside saved documents, found with eyecite (2026-09-20).

What it does
    scan    reads the saved text of four document layers and records every full citation eyecite finds (cases, statutes and
            regulations, law journals), with a short passage around the first mention in each document. Resumable: finished
            documents are skipped, so the scan can be stopped and continued.
    build   groups the mentions into one row per authority, and resolves citations to the full text saved in this library where
            that can be done exactly: "21 U.S.C. § 355" and "21 C.F.R. § 314.80" by their citation string in the law catalog
            (a section range resolves to its first section and says so). Case citations get the CourtListener citation address
            for their volume, reporter and page; nothing is fetched.

What it does not do
    It does not say an authority is good law, was relied on, or is important; counts are "documents in this library that cite it".
    Short forms (Id., supra, short case cites) are not counted. A case name is only the text eyecite found beside the citation.

Tools: eyecite 2.7.8, reporters-db 3.2.66, courts-db 0.10.27 (Free Law Project, BSD-2-Clause), already installed.

    python build.py scan [--workers 6] [--seconds 7200] [--layer agency-documents]
    python build.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCAN = HERE / 'scan.sqlite3'
OUT = HERE / 'citation_index.sqlite3'
CATALOG = ROOT / 'sources/open_us_law_20260918/catalog.sqlite3'
LICENSE = 'derived_index_of_saved_documents_eyecite_bsd_2_clause'
QUALIFICATION = ('Authorities found by pattern in the text of saved documents. A count is the number of saved documents that cite the authority, not a measure of importance or '
                 'validity. Statute and regulation citations open the saved text only where the citation string matches exactly; case citations link to the CourtListener '
                 'citation address and are not saved here. Names beside case citations are as found in the text and can be incomplete.')
LAYERS = {
    'agency-documents': {'label': 'Agency & science documents', 'db': 'sources/agency_science_documents_20260919/documents.sqlite3', 'sql': 'SELECT id, title, url, text_path FROM docs WHERE text_chars>200',
                         'files': 'corpus/agency_science_documents_20260919', 'route': 'agency-documents'},
    'source-documents': {'label': 'Downloaded source files', 'db': 'sources/source_directory_documents_20260919/documents.sqlite3', 'sql': 'SELECT id, title, url, text_path FROM docs WHERE text_chars>200',
                         'files': 'corpus/source_directory_documents_20260919', 'route': 'source-documents'},
    'uscourts': {'label': 'U.S. Courts publications', 'db': 'sources/uscourts_pages_20260919/uscourts_pages.sqlite3', 'sql': 'SELECT id, title, url, text FROM pages WHERE text_chars>200', 'files': None, 'route': 'uscourts'},
    'saved-pages': {'label': 'Saved web pages', 'db': 'sources/saved_web_pages_20260919/pages.sqlite3', 'sql': 'SELECT id, title, url, text FROM pages WHERE text_chars>200', 'files': None, 'route': 'saved-pages'},
}
SIGNAL = re.compile(r'U\.\s?S\.\s?C|C\.\s?F\.\s?R|\bCFR\b|\bUSC\b|§|\bF\.\s?(?:2d|3d|4th|Supp|App)|S\.\s?Ct\.|\bU\.S\.\s\d|\bv\.\s[A-Z]|\bL\.\s?(?:Rev|J)\.|Pub\.\s?L\.|\bStat\.\s')
MAX_CHARS = 3_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def clean_name(value):
    text = ' '.join(str(value or '').replace('*', ' ').split()).strip(' ,;:.')
    return text if 2 <= len(text) <= 120 and not re.search(r'\b(Id|See|Cf|supra)\b', text) else ''


def extract(job):
    """One document: (layer, doc_id, title, url, text) -> rows. Runs in a worker process."""
    layer, doc_id, title, url, text = job
    logging.disable(logging.CRITICAL)
    from eyecite import clean_text, get_citations
    from eyecite.models import FullCaseCitation, FullJournalCitation, FullLawCitation
    text = (text or '')[:MAX_CHARS]
    if not SIGNAL.search(text):
        return layer, doc_id, title, url, len(text), 'no_citation_signal', []
    try:
        cleaned = clean_text(text, ['all_whitespace'])
        found = get_citations(cleaned)
    except Exception as error:  # a document the tokenizer cannot handle is recorded and skipped
        return layer, doc_id, title, url, len(text), 'failed: ' + type(error).__name__, []
    grouped = {}
    for cite in found:
        if isinstance(cite, FullCaseCitation):
            kind = 'case'
        elif isinstance(cite, FullLawCitation):
            kind = 'law'
        elif isinstance(cite, FullJournalCitation):
            kind = 'journal'
        else:
            continue
        groups = dict(cite.groups)
        try:
            normal = cite.corrected_citation()
        except Exception:
            normal = cite.matched_text()
        normal = ' '.join(str(normal).split())
        if kind == 'law':
            normal = re.sub(r'§§', '§', normal)
        key = (kind, normal)
        meta = cite.metadata
        if key not in grouped:
            start, end = cite.span()
            passage = ' '.join(cleaned[max(0, start - 130):end + 130].split())
            name = ''
            if kind == 'case':
                plaintiff, defendant = clean_name(getattr(meta, 'plaintiff', '')), clean_name(getattr(meta, 'defendant', ''))
                name = '%s v. %s' % (plaintiff, defendant) if plaintiff and defendant else ''
            grouped[key] = {'kind': kind, 'normal': normal, 'reporter': ' '.join(str(groups.get('reporter') or '').split()), 'volume': str(groups.get('volume') or ''),
                            'page': str(groups.get('page') or ''), 'title_num': str(groups.get('title') or '') if kind == 'law' else '', 'section': str(groups.get('section') or '') if kind == 'law' else '',
                            'year': str(getattr(meta, 'year', '') or ''), 'court': str(getattr(meta, 'court', '') or ''), 'name': name, 'count': 0, 'offset': start, 'passage': passage[:420]}
        grouped[key]['count'] += 1
    return layer, doc_id, title, url, len(text), 'scanned', list(grouped.values())


def jobs(layer, done):
    spec = LAYERS[layer]
    db = sqlite3.connect((ROOT / spec['db']).as_uri() + '?mode=ro', uri=True)
    base = ROOT / spec['files'] if spec['files'] else None
    for doc_id, title, url, body in db.execute(spec['sql']):
        if (layer, str(doc_id)) in done:
            continue
        if base is not None:
            try:
                body = (base / body).read_text(encoding='utf-8', errors='replace')
            except OSError:
                body = ''
        yield layer, str(doc_id), title or '', url or '', body
    db.close()


def scan(workers, seconds, only):
    db = sqlite3.connect(SCAN)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS docs(layer TEXT, doc_id TEXT, title TEXT, url TEXT, chars INTEGER, status TEXT, citations INTEGER, PRIMARY KEY(layer, doc_id));
        CREATE TABLE IF NOT EXISTS cites(layer TEXT, doc_id TEXT, kind TEXT, normal TEXT, reporter TEXT, volume TEXT, page TEXT, title_num TEXT, section TEXT, year TEXT, court TEXT, name TEXT,
                                         mentions INTEGER, first_offset INTEGER, passage TEXT);
    ''')
    done = {(r[0], r[1]) for r in db.execute('SELECT layer, doc_id FROM docs')}
    started, handled = time.time(), 0
    with multiprocessing.Pool(workers, maxtasksperchild=40) as pool:
        for layer in LAYERS:
            if only and layer != only:
                continue
            for result in pool.imap_unordered(extract, jobs(layer, done), chunksize=2):
                layer_name, doc_id, title, url, chars, status, rows = result
                db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?,?)', (layer_name, doc_id, title, url, chars, status, len(rows)))
                db.executemany('INSERT INTO cites VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [(layer_name, doc_id, r['kind'], r['normal'], r['reporter'], r['volume'], r['page'], r['title_num'], r['section'],
                                                                                       r['year'], r['court'], r['name'], r['count'], r['offset'], r['passage']) for r in rows])
                handled += 1
                if handled % 50 == 0:
                    db.commit()
                    print('%d documents this run, %.0f s, layer %s' % (handled, time.time() - started, layer_name), flush=True)
                if seconds and time.time() - started > seconds:
                    db.commit()
                    pool.terminate()
                    print('time budget reached; run scan again to continue', flush=True)
                    return
    db.commit()
    print('scan complete: %d documents this run' % handled, flush=True)


def first_section(section):
    section = re.sub(r'\(.*$', '', str(section or '').strip()).strip(' .,;')
    return section


def resolve_law(catalog, reporter, title_num, section, citation=''):
    """(record id, citation, basis) for a federal statute or regulation section saved in the law catalog; None otherwise."""
    code = re.sub(r'[\s.,]', '', reporter or '').upper()
    if code in ('USC', 'USCA', 'USCS', 'USCODE', 'UNITEDSTATESCODE') and title_num.isdigit():
        pattern, edition = '%s U.S.C. § %s (2024)', 'statutes'
    elif code == 'CFR' and not title_num.isdigit() and re.match(r'^(\d{1,2})\s*C\.?\s?F\.?\s?R', citation or ''):
        title_num = re.match(r'^(\d{1,2})', citation).group(1)  # eyecite keeps the C.F.R. title outside its "title" group
        pattern, edition = '%s C.F.R. § %s (2026)', 'regulations'
    elif code == 'CFR' and title_num.isdigit():
        pattern, edition = '%s C.F.R. § %s (2026)', 'regulations'
    else:
        return None
    base = first_section(section)
    if not base:
        return None
    candidates = [(base, 'exact citation')]
    ranged = re.fullmatch(r'([0-9]+[A-Za-z]*(?:\.[0-9]+)?)\s*[-–]\s*([0-9]+[A-Za-z]*(?:\.[0-9]+)?)', base)
    if ranged:
        candidates.append((ranged.group(1), 'first section of the cited range'))
    for value, basis in candidates:
        row = catalog.execute("SELECT id, citation FROM records WHERE citation=? AND state='FEDERAL' AND kind=? LIMIT 1", (pattern % (title_num, value), edition)).fetchone()
        if row:
            return row[0], row[1], basis
    return None


SAFE_REPORTERS = {'U.S.', 'S. Ct.', 'L. Ed.', 'L. Ed. 2d', 'F.', 'F.2d', 'F.3d', 'F.4th', 'F. Supp.', 'F. Supp. 2d', 'F. Supp. 3d', 'F.R.D.', 'B.R.', 'Fed. Cl.', "F. App'x", 'A.2d', 'A.3d',
                  'P.2d', 'P.3d', 'N.E.2d', 'N.E.3d', 'N.W.2d', 'N.W.3d', 'S.E.2d', 'S.W.2d', 'S.W.3d', 'So. 2d', 'So. 3d', 'Cal. Rptr.', 'Cal. Rptr. 2d', 'Cal. Rptr. 3d',
                  'N.Y.S.2d', 'N.Y.S.3d', 'Cal. 4th', 'Cal. 5th', 'Cal. App. 4th', 'Cal. App. 5th', 'WL', 'LEXIS'}


def other_layers(authorities):
    """Exact links into two other saved layers: Federal Register documents by volume and first page, public laws by number or Statutes at Large page."""
    links = {}
    wanted_fr = {}
    for key, entry in authorities.items():
        code = re.sub(r'[\s.]', '', entry['reporter'] or '').upper()
        if entry['kind'] == 'law' and code in ('FR', 'FEDREG') and entry['volume'].isdigit():
            page = entry['page'].replace(',', '')
            if page.isdigit():
                wanted_fr['%s FR %s' % (entry['volume'], page)] = key
    register = ROOT / 'sources/federal_register_history_20260919/federal_register.sqlite3'
    if wanted_fr and register.exists():
        db = sqlite3.connect(register.as_uri() + '?mode=ro', uri=True)
        for _doc_id, number, citation in db.execute('SELECT id, number, citation FROM docs WHERE citation IS NOT NULL'):
            key = wanted_fr.get(citation)
            if key and key not in links:
                links[key] = ('federal_register', '#federal-register?q=' + number, 'Federal Register document %s (its first page is the cited page)' % number)
        db.close()
    laws = ROOT / 'sources/public_law_uscode_20260919/public_law_uscode.sqlite3'
    if laws.exists():
        db = sqlite3.connect(laws.as_uri() + '?mode=ro', uri=True)
        by_number = {'%s-%s' % (r[1], r[2]): r for r in db.execute("SELECT id, congress, law_number, statutes_citation FROM laws WHERE public_private='public'")}
        by_stat = {r[3]: r for r in by_number.values() if r[3]}
        db.close()
        for key, entry in authorities.items():
            if entry['kind'] != 'law' or key in links:
                continue
            code = re.sub(r'[\s.]', '', entry['reporter'] or '').upper()
            row = None
            if code in ('PUBL', 'PUBLICLAW', 'PUBLNO') and re.fullmatch(r'\d{2,3}-\d{1,3}', entry['title_num'] or ''):
                row = by_number.get(entry['title_num'])
            elif code == 'STAT' and entry['volume'].isdigit() and entry['page'].isdigit():
                row = by_stat.get('%s Stat. %s' % (entry['volume'], entry['page']))
            if row:
                links[key] = ('public_law', '#public-laws?q=%s-%s' % (row[1], row[2]), 'Public Law %s-%s in the saved public-law layer' % (row[1], row[2]))
    return links


def build():
    if not SCAN.exists():
        raise SystemExit('run "python build.py scan" first')
    scan_db = sqlite3.connect(SCAN.as_uri() + '?mode=ro', uri=True)
    catalog = sqlite3.connect(CATALOG.as_uri() + '?mode=ro', uri=True)
    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript('''
        CREATE TABLE authorities(id INTEGER PRIMARY KEY, kind TEXT, citation TEXT, reporter TEXT, volume TEXT, page TEXT, title_num TEXT, section TEXT, name TEXT, year TEXT, court TEXT,
                                 documents INTEGER, mentions INTEGER, local_record_id TEXT, local_citation TEXT, local_basis TEXT, link_out TEXT, local_kind TEXT, local_link TEXT);
        CREATE TABLE mentions(authority_id INTEGER, layer TEXT, doc_id TEXT, mentions INTEGER, passage TEXT);
        CREATE TABLE documents(layer TEXT, doc_id TEXT, title TEXT, url TEXT, chars INTEGER, status TEXT, citations INTEGER, PRIMARY KEY(layer, doc_id));
        CREATE TABLE layers(layer TEXT PRIMARY KEY, label TEXT, route TEXT, documents INTEGER, scanned INTEGER, with_citations INTEGER);
    ''')
    db.executemany('INSERT INTO documents VALUES(?,?,?,?,?,?,?)', scan_db.execute('SELECT layer, doc_id, title, url, chars, status, citations FROM docs'))
    authorities = {}
    for layer, doc_id, kind, normal, reporter, volume, page, title_num, section, year, court, name, count, passage in scan_db.execute(
            'SELECT layer, doc_id, kind, normal, reporter, volume, page, title_num, section, year, court, name, mentions, passage FROM cites'):
        key = (kind, normal)
        entry = authorities.get(key)
        if entry is None:
            entry = authorities[key] = {'kind': kind, 'citation': normal, 'reporter': reporter, 'volume': volume, 'page': page, 'title_num': title_num, 'section': section, 'names': {}, 'years': {},
                                        'courts': {}, 'rows': []}
        if name:
            entry['names'][name] = entry['names'].get(name, 0) + 1
        if year:
            entry['years'][year] = entry['years'].get(year, 0) + 1
        if court:
            entry['courts'][court] = entry['courts'].get(court, 0) + 1
        entry['rows'].append((layer, doc_id, count, passage))
    dropped = [key for key, entry in authorities.items() if entry['kind'] == 'case' and entry['reporter'] not in SAFE_REPORTERS and not entry['names']]
    for key in dropped:  # "on Day 23", "14CO2": a reporter-shaped string with no case name and no year beside it is not counted as a case
        del authorities[key]
    merged = {}
    for key in list(authorities):
        entry = authorities[key]
        if entry['kind'] != 'law':
            continue
        local = resolve_law(catalog, entry['reporter'], entry['title_num'], entry['section'], entry['citation'])
        if not local or local[2] != 'exact citation':
            continue
        canonical = re.sub(r'\s*\(\d{4}\)$', '', local[1])
        target = merged.get(local[0])
        if target is None:
            merged[local[0]] = key
            entry['display'] = canonical
        else:
            keep = authorities[target]
            combined = {}
            for layer, doc_id, count, passage in keep['rows'] + entry['rows']:
                if (layer, doc_id) in combined:
                    combined[(layer, doc_id)][2] += count
                else:
                    combined[(layer, doc_id)] = [layer, doc_id, count, passage]
            keep['rows'] = [tuple(v) for v in combined.values()]
            del authorities[key]
    elsewhere = other_layers(authorities)
    resolved = 0
    for number, entry in enumerate(sorted(authorities.values(), key=lambda e: (-len(e['rows']), e['citation'])), 1):
        local = resolve_law(catalog, entry['reporter'], entry['title_num'], entry['section'], entry['citation']) if entry['kind'] == 'law' else None
        other = elsewhere.get((entry['kind'], entry['citation']))
        link = ''
        if entry['kind'] == 'case' and entry['volume'].isdigit() and entry['page'].isdigit() and entry['reporter'] and entry['reporter'] not in ('WL', 'LEXIS'):
            link = 'https://www.courtlistener.com/c/%s/%s/%s/' % (entry['reporter'].replace(' ', '%20'), entry['volume'], entry['page'])
        best = lambda table: max(table.items(), key=lambda kv: kv[1])[0] if table else ''
        if local:
            resolved += 1
        db.execute('INSERT INTO authorities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (number, entry['kind'], entry.get('display') or entry['citation'], entry['reporter'], entry['volume'], entry['page'], entry['title_num'],
                                                                                      entry['section'], best(entry['names']), best(entry['years']), best(entry['courts']), len(entry['rows']),
                                                                                      sum(r[2] for r in entry['rows']), local[0] if local else '', local[1] if local else '', local[2] if local else (other[2] if other else ''), link,
                                                                                      'law_text' if local else (other[0] if other else ''), ('#record/' + local[0]) if local else (other[1] if other else '')))
        db.executemany('INSERT INTO mentions VALUES(?,?,?,?,?)', [(number, layer, doc_id, count, passage) for layer, doc_id, count, passage in entry['rows']])
    # a document's count is what survived the precision filter, not what the scan first saw
    db.execute('CREATE INDEX mentions_doc ON mentions(layer, doc_id)')
    db.execute('UPDATE documents SET citations=(SELECT count(*) FROM mentions m WHERE m.layer=documents.layer AND m.doc_id=documents.doc_id)')
    for layer, spec in LAYERS.items():
        source = sqlite3.connect((ROOT / spec['db']).as_uri() + '?mode=ro', uri=True)
        total = source.execute('SELECT count(*) FROM (%s)' % spec['sql']).fetchone()[0]
        source.close()
        scanned = db.execute('SELECT count(*), sum(citations>0) FROM documents WHERE layer=?', (layer,)).fetchone()
        db.execute('INSERT INTO layers VALUES(?,?,?,?,?,?)', (layer, spec['label'], spec['route'], total, scanned[0] or 0, scanned[1] or 0))
    db.executescript('''
        CREATE INDEX mentions_authority ON mentions(authority_id);
        CREATE INDEX authorities_local ON authorities(local_record_id); CREATE INDEX authorities_kind ON authorities(kind, documents DESC);
        CREATE VIRTUAL TABLE authorities_fts USING fts5(citation, name, content='authorities', content_rowid='id');
        INSERT INTO authorities_fts(rowid, citation, name) SELECT id, citation, name FROM authorities;
    ''')
    db.commit()
    counts = {'authorities': len(authorities), **{kind: sum(1 for e in authorities.values() if e['kind'] == kind) for kind in ('case', 'law', 'journal')},
              'mentions_rows': db.execute('SELECT count(*) FROM mentions').fetchone()[0], 'law_citations_opening_saved_text': resolved,
              'citations_opening_a_federal_register_document': sum(1 for v in elsewhere.values() if v[0] == 'federal_register'),
              'citations_opening_a_saved_public_law': sum(1 for v in elsewhere.values() if v[0] == 'public_law'), 'case_shaped_strings_not_counted': len(dropped),
              'documents_scanned': db.execute('SELECT count(*) FROM documents').fetchone()[0], 'documents_with_citations': db.execute('SELECT count(*) FROM documents WHERE citations>0').fetchone()[0],
              'documents_failed': db.execute("SELECT count(*) FROM documents WHERE status LIKE 'failed%'").fetchone()[0],
              'layers': {r[0]: {'documents': r[1], 'scanned': r[2], 'with_citations': r[3]} for r in db.execute('SELECT layer, documents, scanned, with_citations FROM layers')}}
    checks = {'every_mention_points_to_an_authority': db.execute('SELECT count(*) FROM mentions m LEFT JOIN authorities a ON a.id=m.authority_id WHERE a.id IS NULL').fetchone()[0] == 0,
              'every_resolved_citation_exists_in_the_law_catalog': all(catalog.execute('SELECT 1 FROM records WHERE id=?', (r[0],)).fetchone() for r in db.execute("SELECT local_record_id FROM authorities WHERE local_record_id<>'' LIMIT 400")),
              'some_documents_were_scanned': counts['documents_scanned'] > 0}
    db.execute('VACUUM')
    db.close()
    stat = CATALOG.stat()
    validation = {'schema_version': 1, 'status': 'passed' if all(checks.values()) else 'failed', 'ready': all(checks.values()), 'validated_at': datetime.now(timezone.utc).isoformat(),
                  'data_files': [{'path': OUT.name, 'sha256': sha256(OUT), 'rows': counts['authorities']}], 'counts': counts, 'checks': checks, 'qualification': QUALIFICATION, 'license_ref': LICENSE,
                  'inputs': [{'path': spec['db'], 'sha256': sha256(ROOT / spec['db'])} for spec in LAYERS.values()] + [{'path': 'sources/open_us_law_20260918/catalog.sqlite3', 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}],
                  'tools': {'eyecite': '2.7.8', 'reporters-db': '3.2.66', 'courts-db': '0.10.27'}}
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'checks': checks}, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', nargs='?', default='build', choices=['scan', 'build'])
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--seconds', type=int, default=0)
    parser.add_argument('--layer', default='')
    arguments = parser.parse_args()
    if arguments.action == 'scan':
        scan(arguments.workers, arguments.seconds, arguments.layer)
    else:
        build()
