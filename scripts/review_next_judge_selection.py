"""Independently replay staged judge links and deduplicate live saved captures."""
import argparse
from collections import Counter
import datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / 'sources/judges/focus_20260913'
CAPTURES = ROOT / 'sources/trellis/judge_focus_20260913/judges'

class Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            href = dict(attrs).get('href')
            if href:
                self.hrefs.append(href)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--limit', type=int, default=1000)
    a = p.parse_args()
    if not 1 <= a.limit <= 1000:
        p.error('Review at most the frozen next1000 packet')
    captured = set()
    for path in CAPTURES.glob('*.firecrawl.json'):
        d = json.loads(path.read_bytes()); d = d.get('data', d)
        metadata = d.get('metadata') or {}
        captured.add(metadata.get('sourceURL') or metadata.get('url'))
    seed = PACKET / 'selection_next1000.jsonl'
    staged = [json.loads(line) for line in seed.read_text(encoding='utf-8').splitlines() if line]
    assert len(staged) == 1000 and len({r['url'] for r in staged}) == 1000
    db = sqlite3.connect((ROOT / 'sources/trellis/worker/frontier.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.execute('BEGIN')
    parents, checks, selected, skipped = {}, [], [], []
    for row in staged:
        url = row['url']; ev = row['parent_evidence']; u = urlsplit(url)
        assert u.scheme == 'https' and u.netloc == 'trellis.law' and u.path.startswith('/judge/') and len(u.path.split('/')) == 3 and not u.query and not u.fragment
        original = db.execute('SELECT category,status,attempts,in_scope FROM frontier WHERE url=?', (url,)).fetchone()
        assert original == ('judge_profile', 'pending', 0, 1)
        assert db.execute('SELECT count(*) FROM attempts WHERE url=?', (url,)).fetchone()[0] == 0
        path = ev['source_path']
        if path not in parents:
            raw = (ROOT / path).read_bytes(); data = json.loads(raw); data = data.get('data', data)
            parser = Anchors(); parser.feed(data.get('html') or '')
            parents[path] = (sha(raw), data, set(parser.hrefs))
        digest, data, hrefs = parents[path]
        assert digest == ev['source_sha256']
        assert ev['source_http_status'] == 200 and data['metadata']['statusCode'] == 200
        assert ev['observed_href'] in hrefs and urljoin(ev['source_url'], ev['observed_href']) == url
        assert url not in {r['url'] for r in selected}
        if url in captured:
            skipped.append({'url': url, 'reason': 'already_saved_in_live_judge_capture_base'})
            continue
        checks.append({'url': url, 'parent_path': path, 'parent_sha256': digest, 'literal_href_reobserved': ev['observed_href'], 'original_unattempted_frontier_verified': True})
        if len(selected) < a.limit:
            selected.append(row)
    db.rollback(); db.close()
    target = PACKET / 'root_selection_next.jsonl'
    state = ROOT / 'sources/trellis/judge_focus_20260913/worker'
    active_path = state / 'active_run.json'
    if active_path.exists():
        active = json.loads(active_path.read_text(encoding='utf-8'))
        if active.get('status') == 'running' and Path(active['selection_file']).resolve() == target.resolve():
            raise RuntimeError('Never regenerate a selector referenced by an active run')
    pointer = state / 'batch_active.json'
    if pointer.exists():
        local_id = json.loads(pointer.read_text(encoding='utf-8'))['local_id']
        manifest = json.loads((state / 'batch_jobs' / local_id / 'manifest.json').read_text(encoding='utf-8'))
        if Path((manifest.get('selection') or {}).get('path', '.')).resolve() == target.resolve():
            raise RuntimeError('Resolve the provider job before changing its frozen selector')
    # This root selector is separate from the frozen agent packet.
    target.write_text(''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in selected), encoding='utf-8')
    receipt = {'reviewed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'original_packet_path': seed.relative_to(ROOT).as_posix(), 'original_packet_sha256': sha(seed.read_bytes()),
        'staged_urls_checked': len(staged), 'literal_parent_files_replayed': len(parents),
        'selected_urls': len(selected), 'requested_limit': a.limit, 'skipped': skipped,
        'state_source_associations': dict(Counter(r['state_code'] for r in selected)),
        'selector_path': target.relative_to(ROOT).as_posix(), 'selector_file_sha256': sha(target.read_bytes()),
        'url_order_sha256': sha(json.dumps([r['url'] for r in selected], separators=(',', ':')).encode()),
        'source_checks': checks, 'originals_modified': False, 'worker_frontier_modified': False,
        'network_requests': 0, 'current_geography_verification': 'literal source associations only; current profile service remains unverified'}
    (PACKET / 'root_review_next.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in receipt.items() if key not in {'source_checks', 'skipped'}}))

if __name__ == '__main__':
    main()
