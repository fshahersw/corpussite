"""Apply a validated recovery pass without rebuilding the entire local directory."""
from pathlib import Path
import json,sqlite3,sys,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'delivery/archive-directory'))
import server,recovery

def main():
    recovered=recovery.load()
    if not recovered:raise SystemExit('No validated recovered text is ready')
    c=sqlite3.connect(server.DB,timeout=60);c.row_factory=sqlite3.Row
    search_rows={row['id']:row['rowid'] for row in c.execute('SELECT rowid,id FROM search')}
    updated=0
    for original in server.jsonl(ROOT/'sources/seeger_import_20260918/resources.jsonl'):
        if original['id'] not in recovered:continue
        p=recovery.overlay(original,recovered);key=server.sid('seeger:'+p['id'])
        row=c.execute('SELECT * FROM records WHERE id=?',(key,)).fetchone()
        if row is None:raise ValueError('Recovery source record missing from directory')
        text=server.safe_path(p['text_path']).read_text(encoding='utf-8',errors='replace')
        if not text.strip():raise ValueError('Recovered body is empty')
        text_id=server.sid(Path(p['text_path']).as_posix())
        c.execute('INSERT OR IGNORE INTO files VALUES(?,?)',(text_id,Path(p['text_path']).as_posix()))
        for path in server.paths_in(p['metadata']['text_recovery']):
            rel=path.relative_to(ROOT).as_posix();c.execute('INSERT OR IGNORE INTO files VALUES(?,?)',(server.sid(rel),rel))
        c.execute('UPDATE records SET payload=?,inline_text=?,text_id=?,quality=? WHERE id=?',(server.dumps(p),text,text_id,p['quality'],key))
        if key in search_rows:c.execute('DELETE FROM search WHERE rowid=?',(search_rows[key],))
        c.execute('INSERT INTO search VALUES(?,?)',(key,' '.join([row['title'],row['state'],row['county'],row['kind'],row['source_url'],text])))
        c.execute('UPDATE browse SET inline_text=1,text_id=?,quality=? WHERE id=?',(text_id,p['quality'],key));updated+=1
    if updated!=len(recovered):raise ValueError('Not every recovery was bound to its original')
    c.commit();c.close()
    receipt={'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'updated_records':updated,'duplicate_records_added':0,'originals_modified':False,'recovery_manifest_sha256':hashlib.sha256((recovery.FOLDER/'resources.jsonl').read_bytes()).hexdigest()}
    (recovery.FOLDER/'directory_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))

if __name__=='__main__':main()
