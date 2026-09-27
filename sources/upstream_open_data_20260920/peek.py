"""Small structural look at a JSON file: python peek.py <path> [max items]"""
import json
import sys
from collections import Counter
from pathlib import Path

path = Path(sys.argv[1])
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 3
data = json.loads(path.read_text(encoding='utf-8'))
print(path.name, type(data).__name__, len(data))
items = list(data.items())[:limit] if isinstance(data, dict) else data[:limit]
for item in items:
    print(json.dumps(item, ensure_ascii=False)[:1200])
rows = list(data.values()) if isinstance(data, dict) else data
if rows and isinstance(rows[0], dict):
    keys = Counter(k for row in rows if isinstance(row, dict) for k in row)
    print('keys:', dict(keys))
