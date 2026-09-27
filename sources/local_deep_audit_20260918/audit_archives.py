"""Inspect ZIP central directories only; never extract or execute contents."""
from pathlib import Path
import json,re,zipfile
from datetime import datetime,timezone
OUT=Path(__file__).resolve().parent
files=json.loads((OUT/'downloads_top_files.json').read_text(encoding='utf-8-sig'))
pattern=re.compile(r'judge|judicial|portrait|courtlistener|people.db|fjc|county|counties',re.I)
rows=[]
for item in files:
    path=Path(item['FullName'])
    if path.suffix.lower()!='.zip':continue
    if re.search(r'credential|token|secret|auth|password|browser',path.name,re.I):continue
    try:
        with zipfile.ZipFile(path) as z:
            members=z.infolist()
            hits=[{'name':m.filename,'bytes':m.file_size,'compressed_bytes':m.compress_size} for m in members
                  if pattern.search(m.filename) and not any(x in m.filename for x in ['node_modules/','.git/','vendor/','target/'])]
            rows.append({'path':str(path),'members':len(members),'matching_members':len(hits),'hits':hits})
    except (OSError,zipfile.BadZipFile) as exc:rows.append({'path':str(path),'error':type(exc).__name__})
result={'generated_at':datetime.now(timezone.utc).isoformat(),'zip_files_inspected':len(rows),
        'matching_archives':sum(bool(r.get('hits')) for r in rows),'method':'ZIP directory only; no payload read, extraction or script execution','archives':rows}
(OUT/'archive_filename_inventory.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps({'zip_files_inspected':len(rows),'matching_archives':result['matching_archives'],
 'leading_matches':[{'path':r['path'],'matching_members':r['matching_members']} for r in sorted(rows,key=lambda r:r.get('matching_members',0),reverse=True)[:12]]},indent=2))
