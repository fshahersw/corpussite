"""Restores the data that git does not carry, from the release assets written by tools/push_data.py. Standard library only.

    python bootstrap.py list                      # units, sizes, licences, what is already installed
    python bootstrap.py pull                      # everything
    python bootstrap.py pull --only sources/ --only catalog --only delivery/
    python bootstrap.py pull --skip-optional      # leave out the 43 GB of court-document originals
    python bootstrap.py verify                    # re-hash installed files against the unit listings

Downloads use the GitHub CLI when it is installed and signed in (`gh auth login`); otherwise a token in GH_TOKEN or
GITHUB_TOKEN; otherwise anonymous access, which works only while the repository is public.
Every part is checked against its SHA-256 before a byte of it is unpacked; every file is hashed while it is written and
compared with the listing inside the archive; modification times are restored to the nanosecond because some start-up
checks compare them. One part is on disk at a time, so scratch space stays below one part size.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = 'fshahersw/externalcorpus'
TAG = 'data-20260920'
SCRATCH = ROOT / '_transfer_scratch'
INSTALLED = SCRATCH / 'installed.json'
LISTINGS = SCRATCH / 'listings'
BLOCK = 4 * 1024 * 1024
MB = 1024 * 1024


def long_path(path):
    text = os.path.abspath(path)
    return '\\\\?\\' + text if os.name == 'nt' and len(text) > 240 and not text.startswith('\\\\?\\') else text


def have_gh():
    return shutil.which('gh') is not None and subprocess.run(['gh', 'auth', 'status'], capture_output=True).returncode == 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(name, target, tag=TAG):
    """Downloads one release asset to `target`."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if have_gh():
        result = subprocess.run(['gh', 'release', 'download', tag, '--repo', REPO, '--pattern', name, '--dir', str(target.parent), '--clobber'], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError('gh could not download %s: %s' % (name, result.stderr.strip()[-300:]))
        return
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        with urllib.request.urlopen('https://github.com/%s/releases/download/%s/%s' % (REPO, tag, name)) as response, open(target, 'wb') as handle:
            shutil.copyfileobj(response, handle, BLOCK)
        return
    headers = {'Authorization': 'Bearer ' + token, 'X-GitHub-Api-Version': '2022-11-28'}
    request = urllib.request.Request('https://api.github.com/repos/%s/releases/tags/%s' % (REPO, tag), headers=headers)
    with urllib.request.urlopen(request) as response:
        assets = {asset['name']: asset['url'] for asset in json.load(response)['assets']}
    if name not in assets:
        raise RuntimeError('asset not found in the release: ' + name)
    # The API answers with a redirect to signed storage; the token must not be forwarded to that host.
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(urllib.request.Request(assets[name], headers=dict(headers, Accept='application/octet-stream')))
        raise RuntimeError('expected a redirect for ' + name)
    except urllib.error.HTTPError as error:
        if error.code not in (301, 302, 303, 307, 308):
            raise
        location = error.headers['Location']
    with urllib.request.urlopen(location) as response, open(target, 'wb') as handle:
        shutil.copyfileobj(response, handle, BLOCK)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(BLOCK), b''):
            digest.update(block)
    return digest.hexdigest()


class PartChain:
    """Reads a unit's parts as one stream, fetching and verifying each part only when the previous one is used up."""

    def __init__(self, parts, keep, tag=TAG):
        self.parts, self.keep, self.tag, self.position, self.handle, self.path = list(parts), keep, tag, 0, None, None

    def _next(self):
        if self.handle is not None:
            self.handle.close()
            if not self.keep:
                self.path.unlink()
        if self.position >= len(self.parts):
            self.handle = None
            return False
        part = self.parts[self.position]
        self.position += 1
        self.path = SCRATCH / 'parts' / part['name']
        if not (self.path.is_file() and self.path.stat().st_size == part['bytes'] and sha256_of(self.path) == part['sha256']):
            fetch(part['name'], self.path, self.tag)
        if self.path.stat().st_size != part['bytes'] or sha256_of(self.path) != part['sha256']:
            raise RuntimeError('part does not match its recorded SHA-256: ' + part['name'])
        print('   part %d/%d verified  %s' % (self.position, len(self.parts), part['name']), flush=True)
        self.handle = open(self.path, 'rb')
        return True

    def read(self, size=-1):
        chunks, wanted = [], size
        while wanted != 0:
            if self.handle is None and not self._next():
                break
            data = self.handle.read(wanted if wanted > 0 else BLOCK)
            if not data:
                if not self._next():
                    break
                continue
            chunks.append(data)
            if wanted > 0:
                wanted -= len(data)
        return b''.join(chunks)

    def close(self):
        if self.handle is not None:
            self.handle.close()
            if not self.keep:
                self.path.unlink()
            self.handle = None


def safe_target(name):
    parts = name.split('/')
    if name.startswith('/') or '..' in parts or ':' in parts[0] or '\\' in name:
        raise RuntimeError('unsafe path inside archive: ' + name)
    return ROOT.joinpath(*parts)


def install(unit, keep):
    chain = PartChain(unit['parts'], keep, unit.get('release_tag') or TAG)
    stream = gzip.GzipFile(fileobj=chain, mode='rb') if unit['mode'] == 'tar.gz' else chain
    written, listing = {}, None
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            if not member.isfile():
                continue
            source = archive.extractfile(member)
            if member.name.startswith('__manifest__/'):
                listing = source.read()
                continue
            target = safe_target(member.name)
            os.makedirs(long_path(target.parent), exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with open(long_path(target), 'wb') as handle:
                for block in iter(lambda: source.read(BLOCK), b''):
                    handle.write(block)
                    digest.update(block)
                    size += len(block)
            written[member.name] = (size, digest.hexdigest())
    chain.close()
    if listing is None:
        raise RuntimeError('archive carries no file listing: ' + unit['key'])
    if unit.get('file_listing_sha256') and hashlib.sha256(listing).hexdigest() != unit['file_listing_sha256']:
        raise RuntimeError('file listing does not match the manifest: ' + unit['key'])
    problems = 0
    for line in listing.decode('utf-8').splitlines():
        row = json.loads(line)
        got = written.pop(row['p'], None)
        if got != (row['s'], row['h']):
            problems += 1
            print('   MISMATCH', row['p'], flush=True)
            continue
        os.utime(long_path(safe_target(row['p'])), ns=(row['m'], row['m']))
    problems += len(written)
    LISTINGS.mkdir(parents=True, exist_ok=True)
    (LISTINGS / (unit['name'] + '.jsonl')).write_bytes(listing)
    return problems


def load_manifest(refresh=True):
    target = SCRATCH / 'data_manifest.json'
    if refresh or not target.is_file():
        fetch('data_manifest.json', target)
    return json.loads(target.read_text(encoding='utf-8'))


def load_installed():
    try:
        return json.loads(INSTALLED.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def selected(units, arguments):
    for unit in units:
        if arguments.only and not any(pattern in unit['key'] for pattern in arguments.only):
            continue
        if arguments.skip_optional and unit.get('optional'):
            continue
        if arguments.skip_restricted and unit.get('restricted'):
            continue
        yield unit


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=('list', 'pull', 'verify'))
    parser.add_argument('--only', action='append', help='units whose key contains this text (repeatable)')
    parser.add_argument('--skip-optional', action='store_true')
    parser.add_argument('--skip-restricted', action='store_true', help='leave out units whose licence forbids redistribution')
    parser.add_argument('--keep-parts', action='store_true')
    arguments = parser.parse_args()
    SCRATCH.mkdir(exist_ok=True)
    installed = load_installed()
    if arguments.command == 'verify':
        bad = 0
        for path in sorted(LISTINGS.glob('*.jsonl')):
            for line in path.read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                target = long_path(safe_target(row['p']))
                if not os.path.isfile(target) or os.path.getsize(target) != row['s'] or sha256_of(target) != row['h']:
                    bad += 1
                    print('MISMATCH', row['p'])
        print('verify finished; files that differ or are missing:', bad)
        return 1 if bad else 0
    manifest = load_manifest()
    units = list(selected(manifest['units'], arguments))
    if arguments.command == 'list':
        for unit in units:
            state = 'installed' if installed.get(unit['name']) == unit.get('file_listing_sha256') and unit.get('uploaded') else ('not uploaded yet' if not unit.get('uploaded') else 'available')
            print('%9.1f MB raw %8.1f MB packed %7d files  %-17s %s%s' % (unit['bytes'] / MB, unit.get('packed_bytes', 0) / MB, unit['files'], state, unit['key'], '  [restricted licence]' if unit.get('restricted') else ''))
        print('release %s of %s, written %s; %d of %d units uploaded' % (manifest['release_tag'], manifest['repository'], manifest['written_at'], manifest['units_uploaded'], manifest['units_total']))
        return 0
    failures = 0
    for unit in units:
        if not unit.get('uploaded'):
            print('skipping (not uploaded yet):', unit['key'])
            continue
        if installed.get(unit['name']) == unit.get('file_listing_sha256'):
            continue
        print('installing %s  (%d files, %.0f MB)' % (unit['key'], unit['files'], unit['bytes'] / MB), flush=True)
        try:
            problems = install(unit, arguments.keep_parts)
        except (RuntimeError, OSError, tarfile.TarError, EOFError) as error:
            print('   FAILED:', error)
            failures += 1
            continue
        if problems:
            print('   %d files did not match their listing' % problems)
            failures += 1
            continue
        installed[unit['name']] = unit.get('file_listing_sha256')
        INSTALLED.write_text(json.dumps(installed, indent=1) + '\n', encoding='utf-8')
    print('done; units with problems:', failures)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
