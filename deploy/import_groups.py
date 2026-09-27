"""Import reviewed display groups after core records; never change publication gates.

Only this table is written. A local, hash-bound journal checkpoints successful
atomic upserts; failed or uncertain batches are safe to replay by primary key.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from import_lock import import_writer
from supabase_client import Client

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / '_transfer_scratch/supabase_export'
PROJECT = 'xosqzzsnhxcyehcnirpa'
TABLE = 'corpus_display_groups'
MAX_BATCH_BYTES = 256_000


def signature(path):
    digest = hashlib.sha256()
    rows = 0
    with path.open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
            rows += block.count(b'\n')
    return digest.hexdigest(), rows


def read_pin(path, export_root, plan, receipt):
    descriptor_path = Path(receipt or plan).resolve()
    if not descriptor_path.is_relative_to(export_root) or not descriptor_path.is_file():
        raise ValueError('A reviewed migration plan or explicit pinned receipt is required inside the export folder')
    data = json.loads(descriptor_path.read_text(encoding='utf-8'))
    if 'project' in data and data['project'] != PROJECT:
        raise ValueError('Reviewed descriptor targets another project')
    if receipt is None and data.get('project') != PROJECT:
        raise ValueError('Migration plan must identify the pinned project')
    descriptor = data.get('groups', data) if receipt else data.get('groups')
    if not isinstance(descriptor, dict) or descriptor.get('table') != TABLE:
        raise ValueError('Reviewed descriptor must target corpus_display_groups')
    declared_path = export_root / descriptor.get('path', '')
    if declared_path.resolve() != path or not declared_path.resolve().is_relative_to(export_root):
        raise ValueError('Reviewed group export path differs from the selected file')
    if type(descriptor.get('rows')) is not int or descriptor['rows'] <= 0:
        raise ValueError('Reviewed group count must be a positive integer')
    if not re.fullmatch(r'[0-9a-f]{64}', descriptor.get('sha256', '')):
        raise ValueError('Reviewed group SHA-256 is required')
    return descriptor


def validate_row(row):
    if not isinstance(row, dict) or set(row) != {'id', 'preferred_id', 'metadata'}:
        raise ValueError('Group row must contain only id, preferred_id and metadata')
    if any(not isinstance(row[k], str) or not row[k].strip() for k in ('id', 'preferred_id')):
        raise ValueError('Group identity must be nonempty text')
    meta = row['metadata']
    if not isinstance(meta, dict) or meta.get('id') != row['id'] or meta.get('preferred_id') != row['preferred_id']:
        raise ValueError('Group metadata identity mismatch')
    members = meta.get('member_ids')
    if not isinstance(members, list) or not members or any(not isinstance(v, str) or not v for v in members):
        raise ValueError('Group member identities are required')
    if len(set(members)) != len(members) or row['preferred_id'] not in members:
        raise ValueError('Group preferred identity or duplicate member mismatch')
    # Identity-only judge entities can be group members without being counted
    # as source documents. Preserve this intentionally different source count.
    source_count = meta.get('source_count')
    if type(source_count) is not int or not 0 <= source_count <= len(members):
        raise ValueError('Group source count is outside the retained membership bounds')
    if 'retained_members' in meta and meta['retained_members'] != len(members):
        raise ValueError('Group retained member count differs from member identities')
    encoded = json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if b'\\u0000' in encoded:
        raise ValueError('Group JSON contains unsupported NUL text')
    if len(encoded) + 2 > MAX_BATCH_BYTES:
        raise ValueError('Single group exceeds the bounded upload size; review it separately')
    return encoded


def validate_export(path, descriptor):
    if signature(path) != (descriptor['sha256'], descriptor['rows']):
        raise ValueError('Group export hash/count differs from reviewed descriptor')
    seen = set()
    with path.open(encoding='utf-8') as source:
        for line in source:
            row = json.loads(line)
            validate_row(row)
            if row['id'] in seen:
                raise ValueError('Duplicate group identity in reviewed export')
            seen.add(row['id'])
    if len(seen) != descriptor['rows']:
        raise ValueError('Group export JSON rows differ from reviewed count')


def import_file(path, *, plan=None, receipt=None, export_root=EXPORT, client_factory=None, batch_rows=200):
    export_root = Path(export_root).resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(export_root) or not path.is_file():
        raise ValueError('Only reviewed group exports inside _transfer_scratch/supabase_export may be imported')
    if not 1 <= batch_rows <= 200:
        raise ValueError('Group batches must contain between 1 and 200 rows')
    descriptor = read_pin(path, export_root, plan or export_root / 'migration_plan.json', receipt)
    validate_export(path, descriptor)
    client = (client_factory or Client)()
    digest = descriptor['sha256']
    journal_key = f'{path}#{batch_rows}:{MAX_BATCH_BYTES}'
    sent = skipped = 0
    with closing(sqlite3.connect(export_root / 'groups_progress.sqlite3')) as state:
        state.execute('create table if not exists batches(file text,sha text,offset integer,records integer,payload_sha text,primary key(file,sha,offset))')
        done = {r[0]: (r[1], r[2]) for r in state.execute('select offset,records,payload_sha from batches where file=? and sha=?', (journal_key, digest))}

        def flush(rows, offset):
            nonlocal sent, skipped
            payload = json.dumps(rows, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
            batch_hash = hashlib.sha256(payload).hexdigest()
            if offset in done:
                if done[offset] != (len(rows), batch_hash):
                    raise RuntimeError('Group checkpoint payload differs from current export')
                skipped += len(rows)
                return
            client.upsert(TABLE, rows)
            state.execute('insert or replace into batches values(?,?,?,?,?)', (journal_key, digest, offset, len(rows), batch_hash))
            state.commit()
            sent += len(rows)

        rows, size, offset, count = [], 2, 0, 0
        with path.open(encoding='utf-8') as source:
            for line in source:
                row = json.loads(line)
                encoded = validate_row(row)
                if rows and (len(rows) >= batch_rows or size + len(encoded) + 1 > MAX_BATCH_BYTES):
                    flush(rows, offset)
                    rows, size, offset = [], 2, count
                rows.append(row)
                size += len(encoded) + 1
                count += 1
            if rows:
                flush(rows, offset)

    if signature(path) != (digest, descriptor['rows']) or count != descriptor['rows']:
        raise RuntimeError('Group export changed during import: hash/count mismatch')
    response = client.call('GET', '/rest/v1/' + TABLE + '?select=id&limit=1', headers={'Prefer': 'count=exact'})
    content_range = response.headers.get('Content-Range', '')
    if not re.fullmatch(r'(?:\d+-\d+|\*)/\d+', content_range):
        raise RuntimeError('Remote exact group count was not returned')
    remote_count = int(content_range.split('/')[-1])
    if remote_count != descriptor['rows']:
        raise RuntimeError(f'Remote count mismatch: expected {descriptor["rows"]}, received {remote_count}')
    result = {'table': TABLE, 'rows': count, 'remote_count': remote_count,
              'source_sha256': digest, 'rows_uploaded': sent, 'rows_checkpointed': skipped,
              'readiness': 'unchanged', 'remote_content_hash': 'not_checked',
              'completed_at': datetime.now(timezone.utc).isoformat()}
    receipt_path = path.with_suffix('.import.json')
    temporary = receipt_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    temporary.replace(receipt_path)
    return result


@import_writer
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?', type=Path, default=EXPORT / 'core/groups.table.jsonl')
    parser.add_argument('--plan', type=Path, default=EXPORT / 'migration_plan.json')
    parser.add_argument('--receipt', type=Path, help='Explicit reviewed path/table/rows/sha256 descriptor when no plan is available')
    parser.add_argument('--batch-rows', type=int, default=200)
    args = parser.parse_args()
    result = import_file(args.file, plan=args.plan, receipt=args.receipt, batch_rows=args.batch_rows)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
