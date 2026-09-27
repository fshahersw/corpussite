"""Read data files out of a published PyPI wheel without installing or running it.

    python fetch_pypi.py <package> <member glob> [<member glob> ...]

The wheel (a zip archive) is downloaded from the address PyPI lists for the newest release, its SHA-256 is checked against the
digest PyPI publishes, and only the matching members are extracted into pypi/<package>/. Nothing is imported or executed.
"""
from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = 'LegalCorpusResearch/1.0 (local research archive)'


def read(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': AGENT}), timeout=180) as response:
        return response.read()


def main(package: str, patterns):
    facts = json.loads(read('https://pypi.org/pypi/%s/json' % package))
    version = facts['info']['version']
    wheels = [f for f in facts['urls'] if f['packagetype'] == 'bdist_wheel'] or [f for f in facts['urls'] if f['filename'].endswith('.zip')]
    if not wheels:
        raise SystemExit('no wheel published for %s %s' % (package, version))
    chosen = wheels[0]
    body = read(chosen['url'])
    digest = hashlib.sha256(body).hexdigest()
    if digest != chosen['digests']['sha256']:
        raise SystemExit('wheel digest does not match the digest published by PyPI')
    target = HERE / 'pypi' / package
    target.mkdir(parents=True, exist_ok=True)
    saved = {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = archive.namelist()
        for name in names:
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                data = archive.read(name)
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                saved[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    manifest = {'package': package, 'version': version, 'licence': facts['info'].get('license'), 'wheel': chosen['filename'], 'wheel_url': chosen['url'],
                'wheel_sha256': digest, 'wheel_bytes': len(body), 'upload_time': chosen.get('upload_time_iso_8601'), 'fetched_at': datetime.now(timezone.utc).isoformat(),
                'members_in_wheel': len(names), 'files': saved}
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    print(package, version, chosen['filename'], len(body), 'bytes;', len(names), 'members; extracted', len(saved))
    for name in names[:40]:
        print('   ', name)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2:])
