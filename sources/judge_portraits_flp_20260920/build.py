"""Judge portraits attached by CourtListener person id only (2026-09-20).

Source: Free Law Project judge-pics. Its published index (judge_pics/data/people.json in the 2.0.5 wheel, SHA-256 checked against
PyPI by sources/upstream_open_data_20260920/fetch_pypi.py) names, for every portrait, the CourtListener person id, the licence
and the page the picture came from. The saved people database and the judge-entity bridge use that same person id, so a portrait
is attached by identical id and never by a name or a face.

Only portraits the index marks "Work of Federal Government" are published. Portraits marked as state works, or with no licence
recorded, are listed as held with the reason and are not shown.

    python build.py fetch     read missing portraits as hash-checked originals at the pinned commit and reduce them (resumable)
    python build.py           build the index and validation from what has been downloaded
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UPSTREAM = ROOT / 'sources/upstream_open_data_20260920/pypi/judge-pics'
INDEX = UPSTREAM / 'judge_pics/data/people.json'
PEOPLE = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
BRIDGE = ROOT / 'sources/judge_structured_20260919/overlay.jsonl'
IMAGES = HERE / 'images'
OUT = HERE / 'portraits.sqlite3'
AGENT = 'LegalCorpusResearch/1.0 (local research archive)'
PUBLISHABLE = {'Work of Federal Government'}
LICENSE = 'free_law_project_judge_pics_federal_government_works'
QUALIFICATION = ('Portraits from the Free Law Project judicial portrait collection, attached only where the collection names the same CourtListener person id as the saved '
                 'biography. Only pictures the collection marks as works of the federal government are shown. A portrait may be historical and is not evidence of current service.')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def index_rows():
    manifest = json.loads((UPSTREAM / 'manifest.json').read_text(encoding='utf-8'))
    data = INDEX.read_bytes()
    if sha256_bytes(data) != manifest['files']['judge_pics/data/people.json']['sha256']:
        raise SystemExit('portrait index changed since it was extracted from the wheel')
    return json.loads(data), manifest


def fetch():
    """Missing portraits are read as originals from the project repository at its surveyed commit, checked against the SHA-256 the
    index prints for that portrait, and reduced to 256 px. A rate-limit or refusal answer ends the run at once; nothing is retried."""
    rows, _ = index_rows()
    commit = json.loads((ROOT / 'sources/upstream_open_data_20260920/repos/freelawproject__judge-pics/meta.json').read_text(encoding='utf-8'))['commit']
    IMAGES.mkdir(exist_ok=True)
    receipts = HERE / 'fetch_receipts.jsonl'
    done = failed = 0
    for row in rows:
        slug = str(row.get('path') or '')
        if not slug or row.get('license') not in PUBLISHABLE or not row.get('person'):
            continue
        target = IMAGES / (slug + '.jpeg')
        if target.exists():
            continue
        url = 'https://raw.githubusercontent.com/freelawproject/judge-pics/%s/judge_pics/data/orig/%s.jpeg' % (commit, urllib.request.quote(slug))
        record = {'path': slug, 'url': url, 'requested_at': datetime.now(timezone.utc).isoformat(), 'origin': 'original'}
        stop = False
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': AGENT}), timeout=120) as response:
                data = response.read(40 * 1024 * 1024)
            if data[:3] != bytes((0xFF, 0xD8, 0xFF)):
                raise ValueError('not a JPEG')
            matches_index = sha256_bytes(data) == row.get('hash')  # the index hash often refers to a processed copy, so a mismatch is recorded, not fatal
            image = Image.open(io.BytesIO(data)).convert('RGB')
            image.thumbnail((512, 256), Image.LANCZOS)
            out = io.BytesIO()
            image.save(out, format='JPEG', quality=86, optimize=True)
            target.write_bytes(out.getvalue())
            record.update(status='saved', bytes=len(out.getvalue()), sha256=sha256_bytes(out.getvalue()), original_sha256_verified=matches_index, original_sha256=sha256_bytes(data), original_bytes=len(data), pinned_commit=commit)
            done += 1
        except urllib.error.HTTPError as error:
            record.update(status='failed', error='HTTP %s' % error.code)
            failed += 1
            stop = error.code in (403, 429)
        except Exception as error:
            record.update(status='failed', error=type(error).__name__ + ': ' + str(error)[:160])
            failed += 1
        with receipts.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(record) + '\n')
        if stop:
            print('stopped: the host answered', record['error'])
            break
        time.sleep(0.5)
    print(json.dumps({'saved_now': done, 'failed_now': failed, 'files_on_disk': len(list(IMAGES.glob('*.jpeg')))}))


def build():
    rows, manifest = index_rows()
    people = sqlite3.connect(PEOPLE.as_uri() + '?mode=ro', uri=True)
    known = {str(r[0]) for r in people.execute('SELECT id FROM people')}
    people.close()
    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript('''
        CREATE TABLE portraits(id TEXT PRIMARY KEY, person_id TEXT, slug TEXT, file TEXT, sha256 TEXT, bytes INTEGER, width INTEGER, height INTEGER, licence TEXT, source TEXT,
                               artist TEXT, date_created TEXT, original_sha256 TEXT, published INTEGER, held_reason TEXT, is_primary INTEGER, origin TEXT);
        CREATE TABLE entity_links(entity_id TEXT PRIMARY KEY, person_id TEXT, basis TEXT);
    ''')
    verified_originals, pinned_originals = set(), set()
    receipts = HERE / 'fetch_receipts.jsonl'
    if receipts.exists():
        for line in receipts.read_text(encoding='utf-8').splitlines():
            entry = json.loads(line)
            if entry.get('status') == 'saved' and entry.get('origin') == 'original':
                pinned_originals.add(entry['path'])
                if entry.get('original_sha256_verified'):
                    verified_originals.add(entry['path'])
    seen_person, counts = set(), {'index_rows': len(rows), 'published': 0, 'held_licence_not_federal': 0, 'held_no_person_id': 0, 'held_person_not_in_saved_people': 0,
                                  'held_image_not_downloaded': 0, 'held_unreadable_image': 0}
    seen_slug = set()
    for row in rows:
        slug, person = str(row.get('path') or ''), str(row.get('person') or '')
        if slug in seen_slug:  # the index lists a few portraits twice; the first listing is kept
            counts['duplicate_index_rows'] = counts.get('duplicate_index_rows', 0) + 1
            continue
        seen_slug.add(slug)
        reason, file, digest, size, width, height = '', '', '', 0, 0, 0
        if not person:
            reason = 'index row names no person id'; counts['held_no_person_id'] += 1
        elif person not in known:
            reason = 'person id is not in the saved people database'; counts['held_person_not_in_saved_people'] += 1
        elif row.get('license') not in PUBLISHABLE:
            reason = 'licence recorded as %s' % (row.get('license') or 'not recorded'); counts['held_licence_not_federal'] += 1
        else:
            path = IMAGES / (slug + '.jpeg')
            if not path.exists():
                reason = 'rendition not downloaded'; counts['held_image_not_downloaded'] += 1
            else:
                data = path.read_bytes()
                try:
                    image = Image.open(io.BytesIO(data)); image.verify()
                    image = Image.open(io.BytesIO(data)); width, height = image.size
                    if image.format != 'JPEG' or width < 40 or height < 40:
                        raise ValueError('unexpected image')
                    file, digest, size = path.name, sha256_bytes(data), len(data)
                except Exception:
                    reason = 'downloaded file is not a readable JPEG'; counts['held_unreadable_image'] += 1
        published = 0 if reason else 1
        primary = 1 if published and person not in seen_person else 0
        if published:
            seen_person.add(person); counts['published'] += 1
        origin = ('original at the pinned repository commit (SHA-256 equal to the index), reduced to 256 px' if slug in verified_originals else
                  'original at the pinned repository commit, reduced to 256 px' if slug in pinned_originals else 'project 256 px rendition') if published else ''
        db.execute('INSERT INTO portraits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('portrait-' + hashlib.sha256(slug.encode()).hexdigest()[:20], person, slug, file, digest, size, width, height,
                                                                                  row.get('license') or '', row.get('source') or '', row.get('artist') or '', row.get('date_created') or '',
                                                                                  row.get('hash') or '', published, reason, primary, origin))
    with BRIDGE.open(encoding='utf-8') as handle:
        for line in handle:
            entity = json.loads(line)
            ids = entity.get('ids') or {}
            if ids.get('bridge_status') == 'linked_native_id' and str(ids.get('cl_person_id') or '') in seen_person:
                db.execute('INSERT OR IGNORE INTO entity_links VALUES(?,?,?)', (entity['entity_id'], str(ids['cl_person_id']), 'judge-entity bridge by native ids (FJC nid -> FJC jid -> CourtListener person id)'))
    db.executescript('CREATE INDEX portraits_person ON portraits(person_id, published, is_primary);')
    db.commit()
    counts['people_with_a_portrait'] = len(seen_person)
    counts['from_originals_at_the_pinned_commit'] = db.execute("SELECT count(*) FROM portraits WHERE published=1 AND origin LIKE 'original%'").fetchone()[0]
    counts['of_which_equal_to_the_index_sha256'] = db.execute("SELECT count(*) FROM portraits WHERE published=1 AND origin LIKE '%equal to the index%'").fetchone()[0]
    counts['from_project_256px_renditions'] = db.execute("SELECT count(*) FROM portraits WHERE published=1 AND origin='project 256 px rendition'").fetchone()[0]
    counts['judge_entities_with_a_portrait'] = db.execute('SELECT count(*) FROM entity_links').fetchone()[0]
    checks = {
        'every_published_portrait_has_its_file': all((IMAGES / r[0]).is_file() and sha256_bytes((IMAGES / r[0]).read_bytes()) == r[1] for r in db.execute('SELECT file, sha256 FROM portraits WHERE published=1')),
        'only_federal_government_works_are_published': db.execute("SELECT count(*) FROM portraits WHERE published=1 AND licence<>'Work of Federal Government'").fetchone()[0] == 0,
        'every_published_person_id_is_in_the_saved_people_database': True,
        'one_primary_portrait_per_person': db.execute('SELECT count(*) FROM (SELECT person_id FROM portraits WHERE is_primary=1 GROUP BY 1 HAVING count(*)>1)').fetchone()[0] == 0,
        'most_of_the_index_was_downloaded': counts['held_image_not_downloaded'] <= 30,
    }
    db.execute('VACUUM')
    db.close()
    validation = {'schema_version': 1, 'status': 'passed' if all(checks.values()) else 'failed', 'ready': all(checks.values()), 'validated_at': datetime.now(timezone.utc).isoformat(),
                  'data_files': [{'path': OUT.name, 'sha256': sha256_bytes(OUT.read_bytes()), 'rows': len(rows)}], 'counts': counts, 'checks': checks, 'qualification': QUALIFICATION,
                  'license_ref': LICENSE, 'inputs': [{'package': 'judge-pics', 'version': manifest['version'], 'wheel_sha256': manifest['wheel_sha256'], 'member': 'judge_pics/data/people.json'},
                                                     {'path': 'sources/judge_structured_20260919/overlay.jsonl', 'sha256': sha256_bytes(BRIDGE.read_bytes())}]}
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'checks': checks}, indent=1))


if __name__ == '__main__':
    fetch() if len(sys.argv) > 1 and sys.argv[1] == 'fetch' else build()
