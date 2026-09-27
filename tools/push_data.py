"""Packages every data file that git does not track into hashed archive parts and uploads them as assets of one GitHub release.

    python tools/push_data.py plan                  # list the units and sizes; uploads nothing
    python tools/push_data.py push                  # package and upload; resumable, safe to stop and run again
    python tools/push_data.py push --only sources/court_reference_flp_20260920
    python tools/push_data.py status

Why releases and not git: GitHub rejects any git file above 100 MB (35 files here, one of 28 GB) and a release asset may be
2 GiB, so each unit is a deterministic tar stream (gzip where it helps) cut into parts. A unit is one collection folder
(sources/<name>, corpus/<name>, delivery/<name>, reports/<name>), one other top-level folder, the loose top-level files, or a
bucket of the court-document originals that live outside the project. Small folders are bundled per top-level folder.

Accuracy: the last member of every archive is __manifest__/<unit>.jsonl with the path, size, modification time in nanoseconds
and SHA-256 of every file, hashed from the very bytes that went into the archive. bootstrap.py checks each part, each file,
and restores the modification times (some start-up checks compare them). data_manifest.json lists units, parts and licences.

Never packaged: .git, .auth (a credential that only this Windows account can decrypt), devvvv (a separate project with its
own repository), .tools, node_modules, __pycache__, logs, lock files, SQLite -shm files, server.json, .digest_cache.json.
If the repository is public, units whose licence forbids redistribution are held back and listed unless the owner passes
--include-restricted; nothing else changes.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = 'fshahersw/externalcorpus'
TAG = 'data-20260920'
SCRATCH = ROOT / '_transfer_scratch'
STATE_PATH = SCRATCH / 'state.json'
LOG_PATH = SCRATCH / 'push.log'
MANIFEST_PATH = ROOT / 'data_manifest.json'
MB = 1024 * 1024
SPLIT_TOPS = ('sources', 'corpus', 'delivery', 'reports')
TOP_SKIP = {'.git', '.auth', '_transfer_scratch', 'devvvv', '.tools', 'external'}
ANY_SKIP = {'node_modules', '__pycache__'}
SKIP_SUFFIXES = ('.log', '.lock', '.pyc', '.sqlite3-shm')
SKIP_NAMES = {'server.json', '.digest_cache.json', 'data_manifest.json'}
STORED = {'.pdf', '.parquet', '.zip', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.gz', '.tar', '.docx', '.xlsx', '.woff2', '.epub'}
SMALL_UNIT = 16 * MB
BLOCK = 4 * MB
# A unit that a collector is still writing is packaged last, once no process mentions the fragment on its command line.
DEFERRED = {'corpus/source_directory_documents_20260919': 'source_directory_documents_20260919'}
# Court-document originals are served from a folder outside the project; only the files the index names are packaged.
EXTERNAL_INDEX = ROOT / 'sources/court_document_library_20260919/index.sqlite3'
EXTERNAL_FROM = Path('C:/Users/firas/Downloads/SW-BULK/publiclaw_registry_v2/staging')
EXTERNAL_AS = 'external/publiclaw_registry_v2/staging'
RESTRICTED_WORDS = ('not for redistribution', 'prohibit', 'local_research_only', 'internal research use')


def log(message):
    line = '%s %s' % (datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), message)
    print(line, flush=True)
    SCRATCH.mkdir(exist_ok=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def long_path(path):
    text = os.path.abspath(path)
    return '\\\\?\\' + text if os.name == 'nt' and len(text) > 240 and not text.startswith('\\\\?\\') else text


def run(arguments, **options):
    return subprocess.run(arguments, capture_output=True, text=True, **options)


def tracked_files():
    listed = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True).stdout.split(b'\0')
    return {item.decode('utf-8') for item in listed if item}


def unit_name(key):
    slug = re.sub(r'[^A-Za-z0-9]+', '_', key).strip('_')[:70] or 'root'
    return '%s-%s' % (slug, hashlib.sha1(key.encode('utf-8')).hexdigest()[:6])


def licence_of(key):
    gate_path = ROOT / key / 'validation.json'
    if not gate_path.is_file():
        return None, None
    try:
        gate = json.loads(gate_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None, None
    licence = gate.get('license_ref') if isinstance(gate.get('license_ref'), str) else None
    return licence, gate.get('export_allowed')


def is_restricted(key, licence, files):
    """Restricted means a publisher's terms forbid redistribution (the commercial docket vendor's pages and reports, and
    layers whose licence line says so). The older export_allowed=false flag on SW-BULK-derived layers is NOT used here: the
    owner states those are their own collections from public sources."""
    text = (key + ' ' + (licence or '')).lower()
    return 'trellis' in text or any(word in text for word in RESTRICTED_WORDS) or any('trellis' in item[0].lower() for item in files)


def project_units():
    tracked = tracked_files()
    units = {}
    root = str(ROOT)
    stack = [root]
    while stack:
        current = stack.pop()
        at_top = current == root
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in ANY_SKIP or (at_top and entry.name in TOP_SKIP):
                        continue
                    stack.append(entry.path)
                    continue
                if entry.name in SKIP_NAMES or entry.name.lower().endswith(SKIP_SUFFIXES):
                    continue
                relative = entry.path[len(root) + 1:].replace(os.sep, '/')
                if relative in tracked:
                    continue
                parts = relative.split('/')
                if len(parts) == 1:
                    key = '(top-level files)'
                elif parts[0] in SPLIT_TOPS:
                    key = parts[0] + '/' + parts[1] if len(parts) > 2 else parts[0] + '/(files)'
                else:
                    key = parts[0]
                status = entry.stat(follow_symlinks=False)
                units.setdefault(key, []).append((relative, entry.path, status.st_size))
    merged = {}
    for key, files in units.items():
        size = sum(item[2] for item in files)
        top = key.split('/')[0]
        if top in SPLIT_TOPS and size < SMALL_UNIT and key not in DEFERRED:
            merged.setdefault(top + '/(small folders)', []).extend(files)
        else:
            merged[key] = files
    return merged


def external_units():
    if not EXTERNAL_INDEX.is_file() or not EXTERNAL_FROM.is_dir():
        return {}
    connection = sqlite3.connect(f"file:{EXTERNAL_INDEX.as_posix()}?mode=ro", uri=True)
    try:
        names = [row[0] for row in connection.execute("SELECT DISTINCT local_rel_path FROM documents WHERE local_rel_path IS NOT NULL AND local_rel_path != ''")]
    finally:
        connection.close()
    units = {}
    for name in names:
        relative = name.replace('\\', '/')
        if relative.startswith('/') or '..' in relative.split('/') or ':' in relative.split('/')[0]:
            continue
        source = EXTERNAL_FROM / relative
        try:
            size = os.stat(long_path(source)).st_size
        except OSError:
            continue
        bucket = hashlib.sha1(relative.encode('utf-8')).hexdigest()[0]
        units.setdefault('external/court_documents/bucket_' + bucket, []).append((EXTERNAL_AS + '/' + relative, str(source), size))
    return units


def build_plan():
    started = time.time()
    rows = []
    groups = project_units()
    groups.update(external_units())
    for key, files in groups.items():
        files.sort(key=lambda item: item[0])
        size = sum(item[2] for item in files)
        stored = sum(item[2] for item in files if os.path.splitext(item[0])[1].lower() in STORED)
        licence, export_allowed = licence_of(key) if not key.startswith('external/') else ('documents fetched from public court websites (owner collection); indexed by sources/court_document_library_20260919', None)
        rows.append({'key': key, 'name': unit_name(key), 'mode': 'tar' if size and stored / size > 0.6 else 'tar.gz', 'files': len(files), 'bytes': size,
                     'license_ref': licence, 'export_allowed': export_allowed, 'restricted': is_restricted(key, licence, files),
                     'optional': key.startswith('external/'), '_files': files})

    def order(row):
        key = row['key']
        if key.startswith('external/'):
            return (4, row['bytes'])
        if key in DEFERRED:
            return (3, 0)
        if key.startswith('corpus/') or key == 'sources/open_us_law_20260918':
            return (2, row['bytes'])
        return (1, row['bytes'])
    rows.sort(key=order)
    log('plan: %d units, %d files, %.2f GB (%.0f s)' % (len(rows), sum(r['files'] for r in rows), sum(r['bytes'] for r in rows) / MB / 1024, time.time() - started))
    return rows


class PartWriter(io.RawIOBase):
    """Cuts the archive stream into numbered parts, hashes each one and hands every finished part to on_part."""

    def __init__(self, stem, limit, on_part):
        super().__init__()
        self.stem, self.limit, self.on_part = stem, limit, on_part
        self.index, self.handle, self.size, self.digest, self.total = 0, None, 0, None, 0

    def writable(self):
        return True

    def _open(self):
        self.index += 1
        self.path = SCRATCH / 'parts' / ('%s.p%03d' % (self.stem, self.index))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle, self.size, self.digest = open(self.path, 'wb'), 0, hashlib.sha256()

    def _finish(self):
        self.handle.close()
        self.handle = None
        self.on_part(self.path, self.index, self.size, self.digest.hexdigest())

    def write(self, data):
        view = memoryview(data)
        written = len(view)
        while len(view):
            if self.handle is None:
                self._open()
            piece = view[:self.limit - self.size]
            self.handle.write(piece)
            self.digest.update(piece)
            self.size += len(piece)
            self.total += len(piece)
            view = view[len(piece):]
            if self.size >= self.limit:
                self._finish()
        return written

    def close(self):
        if self.handle is not None:
            self._finish()
        super().close()


class HashingReader:
    def __init__(self, handle):
        self.handle, self.digest, self.count = handle, hashlib.sha256(), 0

    def read(self, size=-1):
        data = self.handle.read(size)
        self.digest.update(data)
        self.count += len(data)
        return data


def signature_of(files):
    digest = hashlib.sha256()
    for relative, source, size in files:
        try:
            stamp = os.stat(long_path(source)).st_mtime_ns
        except OSError:
            stamp = -1
        digest.update(('%s\t%d\t%d\n' % (relative, size, stamp)).encode('utf-8'))
    return digest.hexdigest()


def package(row, part_bytes, on_part):
    """Streams one unit into parts. Returns (files written, bytes, files skipped because they changed or vanished)."""
    sink = PartWriter(row['name'] + '.' + row['mode'], part_bytes, on_part)
    compressed = gzip.GzipFile(filename='', mode='wb', compresslevel=1, fileobj=sink, mtime=0) if row['mode'] == 'tar.gz' else None
    archive = tarfile.open(fileobj=compressed or sink, mode='w|', format=tarfile.PAX_FORMAT, bufsize=BLOCK)
    lines, skipped, total = [], [], 0
    for relative, source, _size in row['_files']:
        path = long_path(source)
        try:
            before = os.stat(path)
            with open(path, 'rb') as handle:
                info = tarfile.TarInfo(relative)
                info.size, info.mtime, info.mode = before.st_size, int(before.st_mtime), 0o644
                reader = HashingReader(handle)
                archive.addfile(info, reader)
            after = os.stat(path)
        except FileNotFoundError:
            skipped.append({'path': relative, 'reason': 'vanished before it was read'})
            continue
        if reader.count != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            raise RuntimeError('file changed while it was being packaged: ' + relative)
        lines.append(json.dumps({'p': relative, 's': before.st_size, 'm': before.st_mtime_ns, 'h': reader.digest.hexdigest()}, ensure_ascii=False))
        total += before.st_size
    listing = ('\n'.join(lines) + '\n').encode('utf-8')
    info = tarfile.TarInfo('__manifest__/%s.jsonl' % row['name'])
    info.size, info.mtime, info.mode = len(listing), 0, 0o644
    archive.addfile(info, io.BytesIO(listing))
    archive.close()
    if compressed is not None:
        compressed.close()
    sink.close()
    return len(lines), total, skipped, hashlib.sha256(listing).hexdigest()


def tag_of(row):
    """A release holds at most 1,000 assets, so the court-document originals get a release of their own."""
    return TAG + '-courtdocs' if row['key'].startswith('external/') else TAG


def upload(path, tag=TAG):
    # The uplink of the collecting machine corrupts roughly one TLS record per 500 MB ("bad record MAC"): TLS refuses the
    # transfer, nothing damaged is ever accepted, and a retry succeeds. Parts are therefore small and retries quick.
    for attempt in range(1, 13):
        started = time.time()
        result = run(['gh', 'release', 'upload', tag, str(path), '--repo', REPO, '--clobber'])
        if result.returncode == 0:
            return time.time() - started
        log('upload attempt %d failed for %s: %s' % (attempt, path.name, (result.stderr or result.stdout).strip()[-300:]))
        time.sleep(min(300, 5 * 2 ** (attempt - 1)))
    raise RuntimeError('upload failed twelve times: ' + path.name)


def process_mentions(fragment):
    result = run(['powershell', '-NoProfile', '-Command', "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object -ExpandProperty CommandLine"])
    return any(fragment in line and 'push_data.py' not in line for line in result.stdout.splitlines())


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_json(path, value):
    temporary = Path(str(path) + '.tmp')
    temporary.write_text(json.dumps(value, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def write_manifest(plan, state, visibility, held):
    units = []
    for row in plan:
        done = state.get(row['name']) or {}
        units.append({key: row[key] for key in ('key', 'name', 'mode', 'files', 'bytes', 'license_ref', 'export_allowed', 'restricted', 'optional')}
                     | {'release_tag': tag_of(row), 'uploaded': bool(done.get('complete')), 'parts': done.get('parts') or [], 'packed_bytes': sum(part['bytes'] for part in done.get('parts') or []),
                        'file_listing_sha256': done.get('listing_sha256'), 'skipped_files': done.get('skipped') or [], 'held': held.get(row['name'])})
    commit = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip() or None
    manifest = {'schema_version': 1, 'repository': REPO, 'release_tag': TAG, 'repository_visibility_when_written': visibility, 'written_at': datetime.now(timezone.utc).isoformat(),
                'git_commit': commit, 'units_total': len(units), 'units_uploaded': sum(1 for unit in units if unit['uploaded']),
                'bytes_total': sum(unit['bytes'] for unit in units), 'bytes_uploaded_units': sum(unit['bytes'] for unit in units if unit['uploaded']),
                'never_packaged': ['.git', '.auth (machine-bound credential)', 'devvvv (separate project)', '.tools', 'node_modules', '__pycache__', '*.log', '*.lock', '*.sqlite3-shm', 'server.json', '.digest_cache.json'],
                'owner_note': 'The owner states that the folders labelled seeger / SW-BULK hold their own collections from public sources; the older "private firm work product" labels inside validation.json files are kept unchanged because start-up checks read them.',
                'units': units}
    save_json(MANIFEST_PATH, manifest)
    return manifest


def push(arguments):
    SCRATCH.mkdir(exist_ok=True)
    for leftover in (SCRATCH / 'parts').glob('*'):
        leftover.unlink()
    visibility = run(['gh', 'repo', 'view', REPO, '--json', 'visibility', '-q', '.visibility']).stdout.strip() or 'UNKNOWN'
    log('repository %s is %s' % (REPO, visibility))
    if visibility != 'PRIVATE' and arguments.include_restricted:
        log('OWNER OVERRIDE: restricted units are uploaded although the repository is %s' % visibility)
    for tag in (TAG, TAG + '-courtdocs'):
        if run(['gh', 'release', 'view', tag, '--repo', REPO]).returncode != 0:
            notes = 'Data archives for the legal archive. Restore with `python bootstrap.py pull`; see TRANSFER.md. Parts are verified by SHA-256.'
            created = run(['gh', 'release', 'create', tag, '--repo', REPO, '--title', 'Data archives ' + tag, '--notes', notes, '--latest=false'])
            if created.returncode != 0:
                raise SystemExit('could not create the release: ' + created.stderr.strip())
    plan = build_plan()
    state, held = load_state(), {}
    part_bytes = arguments.part_mb * MB
    failures = 0
    for row in plan:
        if arguments.only and not any(pattern in row['key'] for pattern in arguments.only):
            continue
        if arguments.skip_external and row['key'].startswith('external/'):
            held[row['name']] = 'external originals skipped on request'
            continue
        if visibility != 'PRIVATE' and row['restricted'] and not arguments.include_restricted:
            held[row['name']] = 'held: licence forbids redistribution and the repository is not private'
            log('HELD %s (%s)' % (row['key'], held[row['name']]))
            continue
        fragment = DEFERRED.get(row['key'])
        waited = 0
        while fragment and process_mentions(fragment) and waited < 5 * 3600:
            log('waiting for the collector writing %s to finish' % row['key'])
            time.sleep(300)
            waited += 300
        signature = signature_of(row['_files'])
        previous = state.get(row['name']) or {}
        if previous.get('complete') and previous.get('signature') == signature:
            continue
        known = {part['name']: part for part in previous.get('parts') or []}
        parts = []

        def on_part(path, index, size, digest, known=known, parts=parts, tag=tag_of(row)):
            old = known.get(path.name)
            if old and old.get('sha256') == digest and old.get('bytes') == size and old.get('uploaded'):
                log('part already uploaded, unchanged: %s' % path.name)
            else:
                seconds = upload(path, tag)
                log('uploaded %s  %.0f MB in %.0f s (%.1f MB/s)' % (path.name, size / MB, seconds, size / MB / max(seconds, 0.1)))
            parts.append({'name': path.name, 'bytes': size, 'sha256': digest, 'uploaded': True})
            state[row['name']] = {'key': row['key'], 'complete': False, 'parts': parts}
            save_json(STATE_PATH, state)
            path.unlink()

        log('packaging %s  (%d files, %.0f MB, %s)' % (row['key'], row['files'], row['bytes'] / MB, row['mode']))
        try:
            count, total, skipped, listing_digest = package(row, part_bytes, on_part)
        except (RuntimeError, OSError) as error:
            failures += 1
            log('FAILED %s: %s' % (row['key'], error))
            for leftover in (SCRATCH / 'parts').glob(row['name'] + '.*'):
                leftover.unlink()
            continue
        state[row['name']] = {'key': row['key'], 'complete': True, 'signature': signature, 'parts': parts, 'files': count, 'bytes': total, 'skipped': skipped, 'listing_sha256': listing_digest}
        save_json(STATE_PATH, state)
        write_manifest(plan, state, visibility, held)
        upload(MANIFEST_PATH)
    manifest = write_manifest(plan, state, visibility, held)
    upload(MANIFEST_PATH)
    log('finished: %d of %d units uploaded, %d held, %d failed' % (manifest['units_uploaded'], manifest['units_total'], len(held), failures))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=('plan', 'push', 'status'))
    parser.add_argument('--only', action='append', help='package only units whose key contains this text (repeatable)')
    parser.add_argument('--part-mb', type=int, default=128)
    parser.add_argument('--include-restricted', action='store_true', help='owner decision: upload units whose licence forbids redistribution even though the repository is not private')
    parser.add_argument('--skip-external', action='store_true', help='leave out the court-document originals that live outside the project')
    arguments = parser.parse_args()
    if arguments.command == 'plan':
        plan = build_plan()
        for row in plan:
            print('%9.1f MB %7d files  %-6s %s%s' % (row['bytes'] / MB, row['files'], row['mode'], row['key'], '   [restricted licence]' if row['restricted'] else ''))
        print('total: %.2f GB in %d units; restricted: %.2f GB' % (sum(r['bytes'] for r in plan) / MB / 1024, len(plan), sum(r['bytes'] for r in plan if r['restricted']) / MB / 1024))
        return 0
    if arguments.command == 'status':
        state = load_state()
        done = [value for value in state.values() if value.get('complete')]
        print('units complete: %d; packed bytes uploaded: %.2f GB' % (len(done), sum(part['bytes'] for value in state.values() for part in value.get('parts') or []) / MB / 1024))
        return 0
    return push(arguments)


if __name__ == '__main__':
    sys.exit(main())
