"""Read-only source-specific vendor reports, with published/preview/example separation."""
import argparse
import json
from pathlib import Path
import sqlite3

def query(database, name=None, observation_id=None, limit=20):
    database=Path(database).resolve()
    con=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    con.execute('PRAGMA query_only=ON')
    if observation_id:
        selected=con.execute('SELECT id,record_json FROM observations WHERE id=?',(observation_id,)).fetchall()
    else:
        escaped=(name or '').replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        selected=con.execute("SELECT id,record_json FROM observations WHERE name LIKE ? ESCAPE '\\' ORDER BY name,id LIMIT ?",('%'+escaped+'%',max(1,min(limit,100)))).fetchall()
    reports=[]
    for oid,raw in selected:
        observation=json.loads(raw)
        facts=[json.loads(x[0]) for x in con.execute('SELECT record_json FROM facts WHERE observation_id=? ORDER BY id',(oid,))]
        analyses=[json.loads(x[0]) for x in con.execute('SELECT record_json FROM analyses WHERE observation_id=? ORDER BY id',(oid,))]
        candidates=[json.loads(x[0]) for x in con.execute('SELECT record_json FROM candidates WHERE observation_id=? ORDER BY id',(oid,))]
        reports.append({'observation':observation,'facts':facts,
            'analysis_groups':{kind:[a for a in analyses if a['record_class']==kind] for kind in ['vendor_published','public_ui_preview','historical_illustration']},
            'unresolved_candidates':candidates,'identity_merge_performed':False,
            'limitation':'Source-specific vendor assertions; preview/example measures are separate and do not establish current performance.'})
    con.close();return reports

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    choice=parser.add_mutually_exclusive_group(required=True);choice.add_argument('--name');choice.add_argument('--id')
    parser.add_argument('--limit',type=int,default=20)
    args=parser.parse_args()
    print(json.dumps(query(args.database,args.name,args.id,args.limit),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
