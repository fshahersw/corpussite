"""Read-only peek: how FJC appointments / federal courts appear on judge entities."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path('C:/Users/firas/Downloads/SCRAPE')
systems = Counter()
with_app = 0
shown = 0
member_prefixes = Counter()
with (ROOT / 'sources/judge_entities_20260918/entities.jsonl').open(encoding='utf-8') as fh:
    for line in fh:
        e = json.loads(line)
        for s in e.get('judge_systems') or []:
            systems[s] += 1
        for m in e.get('members') or []:
            key = m if isinstance(m, str) else m.get('member_key', '')
            member_prefixes[str(key).split(':')[0]] += 1
        if e.get('appointments'):
            with_app += 1
            if shown < 2 and any('district' in json.dumps(a).lower() for a in e['appointments']):
                print(json.dumps({k: e.get(k) for k in ('entity_id', 'name', 'aliases', 'appointments', 'courts', 'judge_systems', 'service', 'members', 'identity_status')}, ensure_ascii=False)[:3000])
                print('---')
                shown += 1
print('entities with appointments', with_app)
print('judge_systems', systems.most_common(20))
print('member prefixes', member_prefixes.most_common(20))
