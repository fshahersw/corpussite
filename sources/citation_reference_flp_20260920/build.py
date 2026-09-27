"""Citation guide: case reporters, statute / regulation citation forms, law journals and standard abbreviations (2026-09-20).

Source: Free Law Project reporters-db (BSD-2-Clause), read from the pinned copy in sources/upstream_open_data_20260920
(commit and per-file SHA-256 in that folder's manifest, re-checked here). Offline; no third-party packages.

One row per published entry. A reporter keeps every edition with its publication years, every recorded variant spelling and the
courts it reports; a law entry keeps its citation examples and jurisdiction. Nothing is inferred: an abbreviation listed twice
upstream (two different publications) stays two rows.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UPSTREAM = ROOT / 'sources/upstream_open_data_20260920/repos/freelawproject__reporters-db'
DATA = UPSTREAM / 'files/reporters_db/data'
OUT = HERE / 'citation_reference.sqlite3'
LICENSE = 'free_law_project_reporters_db_bsd_2_clause'
QUALIFICATION = ('Reporter, statute and journal citation forms as published in the Free Law Project reporters database. Edition years are the '
                 'publisher\'s; an open end year means the series is recorded as still published. A finding aid for reading citations, not a '
                 'statement that any cited authority is saved in this library.')
USPS = {'al': 'Alabama', 'ak': 'Alaska', 'az': 'Arizona', 'ar': 'Arkansas', 'ca': 'California', 'co': 'Colorado', 'ct': 'Connecticut', 'de': 'Delaware',
        'dc': 'District of Columbia', 'fl': 'Florida', 'ga': 'Georgia', 'hi': 'Hawaii', 'id': 'Idaho', 'il': 'Illinois', 'in': 'Indiana', 'ia': 'Iowa', 'ks': 'Kansas',
        'ky': 'Kentucky', 'la': 'Louisiana', 'me': 'Maine', 'md': 'Maryland', 'ma': 'Massachusetts', 'mi': 'Michigan', 'mn': 'Minnesota', 'ms': 'Mississippi',
        'mo': 'Missouri', 'mt': 'Montana', 'ne': 'Nebraska', 'nv': 'Nevada', 'nh': 'New Hampshire', 'nj': 'New Jersey', 'nm': 'New Mexico', 'ny': 'New York',
        'nc': 'North Carolina', 'nd': 'North Dakota', 'oh': 'Ohio', 'ok': 'Oklahoma', 'or': 'Oregon', 'pa': 'Pennsylvania', 'ri': 'Rhode Island', 'sc': 'South Carolina',
        'sd': 'South Dakota', 'tn': 'Tennessee', 'tx': 'Texas', 'ut': 'Utah', 'vt': 'Vermont', 'va': 'Virginia', 'wa': 'Washington', 'wv': 'West Virginia',
        'wi': 'Wisconsin', 'wy': 'Wyoming', 'pr': 'Puerto Rico', 'gu': 'Guam', 'vi': 'Virgin Islands', 'as': 'American Samoa', 'mp': 'Northern Mariana Islands'}
CITE_TYPES = {'federal': 'Federal reporter', 'state': 'State reporter', 'state_regional': 'Regional reporter', 'specialty': 'Specialty reporter',
              'specialty_lexis': 'Lexis specialty series', 'specialty_west': 'West specialty series', 'neutral': 'Neutral (court-assigned) citation',
              'scotus_early': 'Early Supreme Court reporter', 'leg_statute': 'Statutory code', 'leg_session': 'Session laws', 'leg_act': 'Named act',
              'admin_compilation': 'Administrative code', 'admin_register': 'Administrative register', 'admin_docket': 'Agency docket', 'admin_filing': 'Agency filing',
              'municipal': 'Municipal code', 'journal': 'Law journal'}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def year(value):
    return str(value)[:4] if value else None


def places(codes):
    """States and scope named by the publisher's court codes (us:ca;supreme.court, us:c9:ca.nd;district.court, us;federal ...)."""
    states, scopes = set(), set()
    for code in codes or []:
        head = code.split(';')[0]
        parts = head.split(':')[1:]
        if not parts:
            scopes.add('United States')
            continue
        for part in parts:
            token = part.split('.')[0]
            if token in USPS:
                states.add(USPS[token])
            elif re.fullmatch(r'c\d{1,2}', token):
                scopes.add('Federal circuit %s' % token[1:].lstrip('0') if token != 'c0' else 'D.C. Circuit')
            elif token == 'fed':
                scopes.add('Federal Circuit')
            elif token == 'tribal':
                scopes.add('Tribal courts')
            elif token == 'c':
                scopes.add('United States')
    return sorted(states), sorted(scopes)


def main():
    manifest = json.loads((UPSTREAM / 'manifest.json').read_text(encoding='utf-8'))
    inputs = []
    for name in ('reporters.json', 'laws.json', 'journals.json', 'case_name_abbreviations.json', 'state_abbreviations.json'):
        recorded = manifest['files']['reporters_db/data/' + name]
        actual = sha256(DATA / name)
        if actual != recorded['sha256']:
            raise SystemExit('upstream file changed since it was fetched: ' + name)
        inputs.append({'path': 'sources/upstream_open_data_20260920/repos/freelawproject__reporters-db/files/reporters_db/data/' + name, 'sha256': actual,
                       'commit': manifest['commit'], 'url': recorded['url']})
    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript('''
        CREATE TABLE entries(id TEXT PRIMARY KEY, kind TEXT, abbreviation TEXT, name TEXT, cite_type TEXT, cite_type_label TEXT, jurisdiction TEXT, states TEXT, scopes TEXT,
                             first_year TEXT, last_year TEXT, still_published INTEGER, editions TEXT, variations TEXT, examples TEXT, courts TEXT, notes TEXT, href TEXT, publisher TEXT);
        CREATE TABLE variants(variant TEXT, entry_id TEXT, edition TEXT);
        CREATE TABLE abbreviations(kind TEXT, word TEXT, abbreviation TEXT);
        CREATE VIRTUAL TABLE entries_fts USING fts5(abbreviation, name, variations, examples, jurisdiction, content='');
    ''')
    rows, seen = [], Counter()

    listed = Counter()

    def identifier(kind, key):
        listed[(kind, key)] += 1
        slug = re.sub(r'[^A-Za-z0-9]+', '-', key).strip('-') or 'x'  # two spellings can share a slug ("A.2d" / "A. 2d"): number the slug, not the key
        seen[(kind, slug)] += 1
        return '%s:%s%s' % (kind, slug, '' if seen[(kind, slug)] == 1 else '~%d' % seen[(kind, slug)])

    reporters = json.loads((DATA / 'reporters.json').read_text(encoding='utf-8'))
    for key, entries in reporters.items():
        for entry in entries:
            editions = [{'edition': name, 'start': year(span.get('start')), 'end': year(span.get('end'))} for name, span in (entry.get('editions') or {}).items()]
            starts = [e['start'] for e in editions if e['start']]
            open_ended = any(e['end'] is None for e in editions)
            ends = [e['end'] for e in editions if e['end']]
            states, scopes = places(entry.get('mlz_jurisdiction'))
            variations = entry.get('variations') or {}
            rows.append({'id': identifier('reporter', key), 'kind': 'reporter', 'abbreviation': key, 'name': entry.get('name') or key, 'cite_type': entry.get('cite_type') or '',
                         'jurisdiction': ('United States' if len(states) > 10 else ', '.join(states[:4]) + (' and %d more' % (len(states) - 4) if len(states) > 4 else '')) if states else ', '.join(scopes[:2]),
                         'states': states, 'scopes': scopes, 'first_year': min(starts) if starts else None, 'last_year': None if open_ended else (max(ends) if ends else None),
                         'still_published': 1 if open_ended else 0, 'editions': editions, 'variations': [{'variant': v, 'edition': target} for v, target in variations.items()],
                         'examples': entry.get('examples') or [], 'courts': entry.get('mlz_jurisdiction') or [], 'notes': entry.get('notes') or '', 'href': entry.get('href') or '',
                         'publisher': entry.get('publisher') or ''})
    for source, kind in (('laws.json', 'law'), ('journals.json', 'journal')):
        for key, entries in json.loads((DATA / source).read_text(encoding='utf-8')).items():
            for entry in entries:
                place = entry.get('jurisdiction') or ''
                rows.append({'id': identifier(kind, key), 'kind': kind, 'abbreviation': key, 'name': entry.get('name') or key, 'cite_type': entry.get('cite_type') or kind,
                             'jurisdiction': place, 'states': [place] if place in USPS.values() else [], 'scopes': ['United States'] if place == 'United States' else [],
                             'first_year': year(entry.get('start')), 'last_year': year(entry.get('end')), 'still_published': 0 if entry.get('end') else 1, 'editions': [],
                             'variations': [{'variant': v, 'edition': key} for v in (entry.get('variations') or [])], 'examples': entry.get('examples') or [], 'courts': [],
                             'notes': entry.get('notes') if entry.get('notes') and entry.get('notes') != 'Automatically generated.' else '', 'href': entry.get('href') or '', 'publisher': ''})
    for number, row in enumerate(rows, 1):
        db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (row['id'], row['kind'], row['abbreviation'], row['name'], row['cite_type'], CITE_TYPES.get(row['cite_type'], row['cite_type']), row['jurisdiction'],
                    json.dumps(row['states']), json.dumps(row['scopes']), row['first_year'], row['last_year'], row['still_published'], json.dumps(row['editions']),
                    json.dumps(row['variations'], ensure_ascii=False), json.dumps(row['examples'], ensure_ascii=False), json.dumps(row['courts']), row['notes'], row['href'], row['publisher']))
        names = [row['abbreviation']] + [e['edition'] for e in row['editions']] + [v['variant'] for v in row['variations']]
        db.execute('INSERT INTO entries_fts(rowid, abbreviation, name, variations, examples, jurisdiction) VALUES(?,?,?,?,?,?)',
                   (number, row['abbreviation'], row['name'], ' | '.join(names), ' | '.join(row['examples']), row['jurisdiction']))
        for edition in row['editions']:
            db.execute('INSERT INTO variants VALUES(?,?,?)', (edition['edition'], row['id'], edition['edition']))
        for variant in row['variations']:
            db.execute('INSERT INTO variants VALUES(?,?,?)', (variant['variant'], row['id'], variant['edition']))
        if not row['editions']:
            db.execute('INSERT INTO variants VALUES(?,?,?)', (row['abbreviation'], row['id'], row['abbreviation']))
    db.execute('CREATE TABLE fts_map AS SELECT rowid AS number, id FROM entries')
    for word, short in json.loads((DATA / 'case_name_abbreviations.json').read_text(encoding='utf-8')).items():
        for value in (short if isinstance(short, list) else [short]):
            db.execute('INSERT INTO abbreviations VALUES(?,?,?)', ('case_name', word, value))
    for word, short in json.loads((DATA / 'state_abbreviations.json').read_text(encoding='utf-8')).items():
        db.execute('INSERT INTO abbreviations VALUES(?,?,?)', ('state', word, short))
    db.executescript('CREATE INDEX variants_lookup ON variants(variant COLLATE NOCASE); CREATE INDEX entries_kind ON entries(kind, cite_type);')
    db.commit()
    counts = {'entries': len(rows), **{kind + 's': sum(1 for r in rows if r['kind'] == kind) for kind in ('reporter', 'law', 'journal')},
              'editions_and_variants': db.execute('SELECT count(*) FROM variants').fetchone()[0], 'abbreviations': db.execute('SELECT count(*) FROM abbreviations').fetchone()[0],
              'abbreviations_listed_more_than_once': sum(1 for n in listed.values() if n > 1)}
    checks = {'every_upstream_entry_is_a_row': counts['entries'] == sum(len(v) for v in reporters.values()) + counts['laws'] + counts['journals'],
              'ids_are_unique': db.execute('SELECT count(DISTINCT id) FROM entries').fetchone()[0] == len(rows),
              'every_entry_is_searchable': db.execute('SELECT count(*) FROM fts_map').fetchone()[0] == len(rows),
              'known_reporter_present': db.execute("SELECT count(*) FROM variants WHERE variant='F. Supp. 3d'").fetchone()[0] >= 1}
    db.execute('VACUUM')
    db.close()
    validation = {'schema_version': 1, 'status': 'passed' if all(checks.values()) else 'failed', 'ready': all(checks.values()), 'validated_at': datetime.now(timezone.utc).isoformat(),
                  'data_files': [{'path': OUT.name, 'sha256': sha256(OUT), 'rows': len(rows)}], 'counts': counts, 'checks': checks, 'qualification': QUALIFICATION,
                  'license_ref': LICENSE, 'inputs': inputs}
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'checks': checks}, indent=1))


if __name__ == '__main__':
    main()
