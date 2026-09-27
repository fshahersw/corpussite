"""Court reference and court seals, joined to the court registry by CourtListener court id only (2026-09-20).

Sources (pinned copies in sources/upstream_open_data_20260920, SHA-256 re-checked here):
  * Free Law Project courts-db (BSD-2-Clause): citation abbreviation, system, level, type, place, parent court, the dates a
    court existed (with the publisher's notes) and the name forms seen in case captions.
  * Free Law Project seal-rookery: court seals. 232 PNG originals are verified against the SHA-256 printed in the project's own
    index and reduced to 256 px; 22 seals published only as SVG are taken as the project's 256 px PNG rendition (not hash-checked,
    and marked so). Federal seals are works of the United States Government; the project records no copyright facts for state seals.

The join is an exact id match: both projects use CourtListener court ids, the same ids the court registry uses. No names are
compared. A courts-db court that is not in the registry is kept and marked; nothing is attached by resemblance.

    python build.py             build from the pinned files (fetches the 22 PNG renditions once, then works offline)
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UP = ROOT / 'sources/upstream_open_data_20260920/repos'
COURTS = UP / 'freelawproject__courts-db'
SEALS = UP / 'freelawproject__seal-rookery'
SPINE = ROOT / 'sources/court_spine_20260919/courts.jsonl'
OUT = HERE / 'court_reference.sqlite3'
SEAL_DIR = HERE / 'seals'
RENDITIONS = HERE / 'renditions'
AGENT = 'LegalCorpusResearch/1.0 (local research archive)'
LICENSE = 'free_law_project_courts_db_bsd_2_clause_and_seal_rookery'
QUALIFICATION = ('Court facts and seals from the Free Law Project court and seal databases, attached to the registry only where the court id is identical. '
                 'Dates, levels and name forms are the publisher\'s; they are not a statement that a court sits today. A seal identifies a court; it is not an '
                 'endorsement and must not be reused to suggest one.')
LEVELS = {'colr': 'Court of last resort', 'iac': 'Intermediate appellate court', 'gjc': 'General jurisdiction trial court', 'ljc': 'Limited jurisdiction court',
          'trial': 'Trial court', 'gjc & iac': 'General jurisdiction and intermediate appellate'}
TYPES = {'trial': 'Trial', 'appellate': 'Appellate', 'special': 'Special', 'bankruptcy': 'Bankruptcy', 'ag': 'Attorney general', 'international': 'International',
         'trial & iac': 'Trial and intermediate appellate'}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked(folder: Path, relative: str) -> bytes:
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    data = (folder / 'files' / relative).read_bytes()
    if sha256_bytes(data) != manifest['files'][relative]['sha256']:
        raise SystemExit('pinned file changed since it was fetched: ' + relative)
    return data


def name_forms(row, variables):
    """Readable name forms: the publisher's examples plus any pattern that is already plain words."""
    forms = [str(v) for v in row.get('examples') or []]
    for pattern in row.get('regex') or []:
        if '$' not in pattern and not re.search(r'[\\()\[\]?*+|{}^]', pattern):
            forms.append(pattern)
    return list(dict.fromkeys(f.strip() for f in forms if f and f.strip()))[:40]


def thumbnail(data: bytes) -> tuple[bytes, int, int]:
    image = Image.open(io.BytesIO(data))
    image = image.convert('RGBA')
    image.thumbnail((256, 256), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format='PNG', optimize=True)
    return out.getvalue(), image.width, image.height


def rendition(court_id: str) -> bytes | None:
    RENDITIONS.mkdir(exist_ok=True)
    path = RENDITIONS / (court_id + '.png')
    if path.exists():
        return path.read_bytes()
    url = 'https://seals.free.law/v2/256/%s.png' % court_id
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': AGENT}), timeout=60) as response:
            data = response.read(4 * 1024 * 1024)
    except Exception:
        return None
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    path.write_bytes(data)
    time.sleep(0.4)
    return data


def main():
    courts = json.loads(checked(COURTS, 'courts_db/data/courts.json'))
    variables = json.loads(checked(COURTS, 'courts_db/data/variables.json'))
    seal_index = json.loads(checked(SEALS, 'seal_rookery/seals/seals.json'))
    seal_manifest = json.loads((SEALS / 'manifest.json').read_text(encoding='utf-8'))
    registry = {}
    with SPINE.open(encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                registry[row['id']] = row.get('name') or ''
    if OUT.exists():
        OUT.unlink()
    SEAL_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(OUT)
    db.executescript('''
        CREATE TABLE courts(id TEXT PRIMARY KEY, name TEXT, citation_string TEXT, system TEXT, level TEXT, level_label TEXT, type TEXT, type_label TEXT, place TEXT,
                            parent TEXT, parent_name TEXT, appeal_to TEXT, court_url TEXT, first_start TEXT, last_end TEXT, open_ended INTEGER, dates TEXT, notes TEXT,
                            name_forms TEXT, case_types TEXT, in_registry INTEGER, has_seal INTEGER);
        CREATE TABLE seals(court_id TEXT PRIMARY KEY, court_name TEXT, file TEXT, sha256 TEXT, bytes INTEGER, width INTEGER, height INTEGER, origin TEXT,
                           upstream_sha256 TEXT, upstream_hash_verified INTEGER, in_registry INTEGER);
    ''')
    names = {c['id']: c.get('name') or '' for c in courts}
    today = date.today().isoformat()
    sealed = {}
    held = []
    skipped = {'original_missing': 0, 'hash_mismatch': 0, 'rendition_unavailable': 0, 'unreadable_image': 0}
    for court_id, facts in seal_index.items():
        if not facts.get('has_seal'):
            continue
        relative = 'seal_rookery/seals/orig/%s.png' % court_id
        verified, origin, data = 0, '', None
        if relative in seal_manifest['files']:
            data = checked(SEALS, relative)
            if facts.get('hash') and sha256_bytes(data) == facts['hash']:
                verified, origin = 1, 'original file at the pinned commit, SHA-256 equal to the project index'
            else:
                skipped['hash_mismatch'] += 1
                held.append({'court_id': court_id, 'reason': 'original file does not match the SHA-256 printed in the project index; not shown'})
                continue
        else:
            data = rendition(court_id)
            origin = 'project 256 px PNG rendition of an SVG original (no published hash for the rendition)'
            if data is None:
                skipped['rendition_unavailable'] += 1
                continue
        try:
            small, width, height = thumbnail(data)
        except Exception:
            skipped['unreadable_image'] += 1
            continue
        (SEAL_DIR / (court_id + '.png')).write_bytes(small)
        sealed[court_id] = 1
        db.execute('INSERT INTO seals VALUES(?,?,?,?,?,?,?,?,?,?,?)', (court_id, facts.get('name') or names.get(court_id, ''), court_id + '.png', sha256_bytes(small), len(small), width, height,
                                                                     origin, facts.get('hash') or '', verified, 1 if court_id in registry else 0))
    for row in courts:
        spans = row.get('dates') or []
        starts = [s.get('start') for s in spans if s.get('start')]
        ends = [s.get('end') for s in spans if s.get('end')]
        open_ended = any(s.get('end') in (None, '') for s in spans) if spans else 0
        note_parts = [str(row.get('notes') or '').strip()] + [str(s.get('notes')).strip() for s in spans if s.get('notes')]
        db.execute('INSERT INTO courts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (row['id'], row.get('name') or '', row.get('citation_string') or '', row.get('system') or '', row.get('level') or '', LEVELS.get(row.get('level') or '', ''),
                    row.get('type') or '', TYPES.get(row.get('type') or '', ''), row.get('location') or '', row.get('parent') or '', names.get(row.get('parent') or '', ''),
                    json.dumps(row.get('appeal_to')) if row.get('appeal_to') else '', row.get('court_url') or '', min(starts) if starts else None,
                    None if open_ended else (max(ends) if ends else None), 1 if open_ended else 0, json.dumps(spans), ' '.join(p for p in note_parts if p),
                    json.dumps(name_forms(row, variables), ensure_ascii=False), json.dumps(row.get('case_types') or []), 1 if row['id'] in registry else 0, sealed.get(row['id'], 0)))
    db.executescript('CREATE INDEX courts_place ON courts(place); CREATE INDEX courts_system ON courts(system, level);')
    db.commit()
    counts = {
        'courts_db_courts': len(courts), 'courts_in_registry': db.execute('SELECT count(*) FROM courts WHERE in_registry=1').fetchone()[0],
        'courts_not_in_registry': db.execute('SELECT count(*) FROM courts WHERE in_registry=0').fetchone()[0], 'registry_courts': len(registry),
        'with_citation_abbreviation': db.execute("SELECT count(*) FROM courts WHERE citation_string<>''").fetchone()[0],
        'with_existence_dates': db.execute('SELECT count(*) FROM courts WHERE first_start IS NOT NULL').fetchone()[0],
        'with_name_forms': db.execute("SELECT count(*) FROM courts WHERE name_forms<>'[]'").fetchone()[0],
        'seals': db.execute('SELECT count(*) FROM seals').fetchone()[0], 'seals_hash_verified': db.execute('SELECT count(*) FROM seals WHERE upstream_hash_verified=1').fetchone()[0],
        'seals_in_registry': db.execute('SELECT count(*) FROM seals WHERE in_registry=1').fetchone()[0], 'seals_skipped': skipped, 'seals_held': held, 'built_on': today,
    }
    files = sorted(SEAL_DIR.glob('*.png'))
    checks = {
        'every_seal_row_has_its_file': all((SEAL_DIR / r[0]).is_file() and sha256_bytes((SEAL_DIR / r[0]).read_bytes()) == r[1] for r in db.execute('SELECT file, sha256 FROM seals')),
        'no_stray_seal_files': len(files) == counts['seals'],
        'no_original_is_shown_without_matching_its_published_hash': db.execute("SELECT count(*) FROM seals WHERE upstream_hash_verified=0 AND origin LIKE 'original%'").fetchone()[0] == 0,
        'ids_are_unique': db.execute('SELECT count(DISTINCT id) FROM courts').fetchone()[0] == len(courts),
    }
    db.execute('VACUUM')
    db.close()
    validation = {'schema_version': 1, 'status': 'passed' if all(checks.values()) else 'failed', 'ready': all(checks.values()), 'validated_at': datetime.now(timezone.utc).isoformat(),
                  'data_files': [{'path': OUT.name, 'sha256': sha256_bytes(OUT.read_bytes()), 'rows': len(courts)}], 'counts': counts, 'checks': checks,
                  'qualification': QUALIFICATION, 'license_ref': LICENSE,
                  'inputs': [{'repository': 'freelawproject/courts-db', 'commit': json.loads((COURTS / 'meta.json').read_text(encoding='utf-8'))['commit']},
                             {'repository': 'freelawproject/seal-rookery', 'commit': json.loads((SEALS / 'meta.json').read_text(encoding='utf-8'))['commit']},
                             {'path': 'sources/court_spine_20260919/courts.jsonl', 'sha256': sha256_bytes(SPINE.read_bytes())}]}
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'checks': checks}, indent=1))


if __name__ == '__main__':
    main()
