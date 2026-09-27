"""Atomic title-only refresh over already indexed, validated pending observations.

New observations, evidence changes, and shared display groups require a full build.
This never publishes captures, fetches a URL, or modifies source evidence.
"""
from __future__ import annotations
import hashlib
from contextlib import closing, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FOLDER = 'sources/directory_pending_20260918'
DATASET = 'pending_publication'


class FullBuildRequired(ValueError):
    pass


@contextmanager
def writer_lock(database):
    """Serialize full builds and small updates; readers keep using the current DB."""
    path = Path(database).with_suffix('.writer.lock')
    stream = path.open('a+b')
    acquired = False
    try:
        if path.stat().st_size == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise FullBuildRequired('Another directory writer is active; retry after its checkpoint') from exc
        acquired = True
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record_id(value):
    return digest((DATASET + ':' + value).encode())[:32]


def load_projection(root):
    folder = root / FOLDER
    raw = (folder / 'resources.jsonl').read_bytes()
    summary = json.loads((folder / 'summary.json').read_bytes())
    receipt = json.loads((folder / 'validation.json').read_bytes())
    rows = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
    sha = digest(raw)
    required = ('valid', 'downloaded_status_verified_against_source_dbs',
                'raw_text_metadata_binding_and_hashes_verified', 'published_database_unchanged')
    if (any(receipt.get(k) is not True for k in required)
            or sha != summary.get('resources_sha256') or sha != receipt.get('resources_sha256')
            or len(rows) != summary.get('resources') or len(rows) != receipt.get('resource_records')
            or any(not isinstance(p, dict) or not isinstance(p.get('id'), str) for p in rows)
            or len({p['id'] for p in rows}) != len(rows)):
        raise FullBuildRequired('Pending projection is not bound to a valid validation receipt')
    return {record_id(p['id']): p for p in rows}, sha


def evidence_identity(payload):
    # Source snapshot timestamps may advance without changing capture evidence.
    value = dict(payload)
    value.pop('title', None)
    value['metadata'] = dict(value.get('metadata') or {})
    for field in ('title_basis', 'captured_title', 'source_database_snapshot_at'):
        value['metadata'].pop(field, None)
    return value


def title_fields(payload):
    meta = payload.get('metadata') or {}
    return payload.get('title'), meta.get('title_basis'), meta.get('captured_title')


def indexed_rows(db):
    # Use the compact dataset index, not a scan of the 1.33 GB records payload table.
    return {r['id']: dict(r) for r in db.execute(
        "SELECT rowid AS search_rowid,* FROM records WHERE id IN (SELECT id FROM browse WHERE dataset=?)", (DATASET,))}


def plan(db, projection):
    rows = indexed_rows(db)
    if rows.keys() != projection.keys():
        raise FullBuildRequired('Pending observation membership changed; run the full directory build')
    changes = []
    for key, p in projection.items():
        old = rows[key]
        prior = json.loads(old['payload'])
        if old['dataset'] != DATASET or evidence_identity(prior) != evidence_identity(p):
            raise FullBuildRequired('Pending body, identity, geography or evidence changed; run the full directory build')
        if title_fields(prior) == title_fields(p):
            continue
        group = db.execute('''SELECT g.id,g.preferred_id,
            (SELECT count(*) FROM display_members m2 WHERE m2.display_id=g.id) members
            FROM display_members m JOIN display_groups g ON g.id=m.display_id
            WHERE m.record_id=?''', (key,)).fetchone()
        if not group or group['members'] != 1 or group['preferred_id'] != key:
            raise FullBuildRequired('A changed title belongs to a shared display group; run the full directory build')
        # Full builds insert records and FTS rows together. Verify that binding,
        # then use rowid: filtering an UNINDEXED FTS id would scan every body.
        search = db.execute('SELECT id FROM search WHERE rowid=?', (old['search_rowid'],)).fetchone()
        if not search or search['id'] != key:
            raise FullBuildRequired('Pending search membership is inconsistent; run the full directory build')
        if not isinstance(p.get('title'), str) or not p['title'].strip():
            raise FullBuildRequired('A display title is empty')
        changes.append((key, old, p, group['id']))
    return changes


def title_builder():
    path = ROOT / FOLDER / 'build_pending.py'
    spec = importlib.util.spec_from_file_location('pending_title_derivation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_changed_evidence(root, changes):
    """Freshly bind each changed title to its preserved capture and seed evidence."""
    builder = title_builder()
    verified = set()
    for _, _, p, _ in changes:
        evidence = p['metadata']
        collection = evidence['collection']
        if collection not in builder.TITLE_CLEANUP_COLLECTIONS:
            raise FullBuildRequired('Title-only derivation is not enabled for this collection')
        base = (root / collection).resolve()
        if not base.is_relative_to(root):
            raise FullBuildRequired('Capture root is outside the archive')
        source = evidence['resource_row']
        captured_title = source.get('title') or evidence['source_metadata'].get('title')
        title, basis = builder.source_link_display_title(captured_title, p['source_url'], evidence['source_context_records'])
        if (title, basis, captured_title) != title_fields(p):
            raise FullBuildRequired('Display title is not reproduced from its preserved source evidence')
        if evidence['resource_row_sha256'] != digest(builder.canonical(source)):
            raise FullBuildRequired('Capture row hash mismatch')
        with closing(sqlite3.connect((base / 'corpus.sqlite3').as_uri() + '?mode=ro', uri=True)) as source_db:
            source_db.row_factory = sqlite3.Row
            current = source_db.execute('SELECT * FROM resources WHERE id=?', (evidence['resource_id'],)).fetchone()
            if not current or dict(current) != source:
                raise FullBuildRequired('Capture changed since the validated projection')
        for field in ('raw_evidence', 'text_evidence', 'metadata_evidence'):
            proof = evidence.get(field)
            if proof is None:
                if field != 'text_evidence':
                    raise FullBuildRequired('Missing capture evidence')
                continue
            path = (root / proof['path']).resolve()
            if not path.is_relative_to(base):
                raise FullBuildRequired('Capture artifact is outside its collection')
            data = path.read_bytes()
            if len(data) != proof['bytes'] or digest(data) != proof['sha256']:
                raise FullBuildRequired('Capture artifact differs from validated evidence')
            verified.add(proof['path'])
            if field == 'metadata_evidence' and json.loads(data) != evidence['source_metadata']:
                raise FullBuildRequired('Capture metadata differs from validated evidence')
    return len(verified)


def refresh(database=None, root=None, dry_run=False):
    started = time.perf_counter()
    root = Path(root or ROOT).resolve()
    database = Path(database or HERE / 'directory.sqlite3').resolve()
    projection, manifest_sha = load_projection(root)
    # No-op and dry-run checks cannot write even SQLite journal files.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        changes = plan(db, projection)
    count = verify_changed_evidence(root, changes) if changes else 0
    result = {'status': 'ready' if dry_run and changes else ('updated' if changes else 'unchanged'),
              'pending_observations': len(projection), 'changed_titles': len(changes),
              'resources_sha256': manifest_sha, 'fresh_artifact_hashes_verified': count,
              'dry_run': dry_run, 'full_rebuild': False, 'source_bytes_modified': False,
              'published_counts_modified': False}
    if dry_run or not changes:
        result['elapsed_seconds'] = round(time.perf_counter() - started, 6)
        return result
    with writer_lock(database), closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        current = plan(db, projection)
        if [(key, old, group) for key, old, _, group in current] != [(key, old, group) for key, old, _, group in changes]:
            raise FullBuildRequired('Directory changed during title verification; retry at a stable checkpoint')
        for key, old, p, group in changes:
            title = p['title']
            db.execute('UPDATE records SET title=?,payload=? WHERE id=?',
                       (title, json.dumps(p, ensure_ascii=False, separators=(',', ':')), key))
            db.execute('UPDATE browse SET title=? WHERE id=?', (title, key))
            db.execute('UPDATE display_groups SET title=? WHERE id=?', (' '.join(title.split()), group))
            db.execute('DELETE FROM search WHERE rowid=?', (old['search_rowid'],))
            text = ' '.join(str(v or '') for v in (title, old['state'], old['county'], old['kind'], old['source_url'], old['inline_text']))
            db.execute('INSERT INTO search(rowid,id,text) VALUES(?,?,?)', (old['search_rowid'], key, text))
        # Fail and roll back if another publisher rewrote the manifest/receipt in flight.
        _, current_sha = load_projection(root)
        if current_sha != manifest_sha:
            raise FullBuildRequired('Pending projection changed during title refresh')
        result['elapsed_seconds'] = round(time.perf_counter() - started, 6)
        row = db.execute("SELECT payload FROM settings WHERE key='summary'").fetchone()
        if row:
            summary = json.loads(row[0]); summary['pending_title_refresh'] = result
            db.execute("UPDATE settings SET payload=? WHERE key='summary'", (json.dumps(summary, ensure_ascii=False),))
        db.commit()
    result['elapsed_seconds'] = round(time.perf_counter() - started, 6)
    return result
