"""Upload reviewed finite metadata/context exports, without modifying source datasets."""
import argparse,hashlib,json
from pathlib import Path
from supabase_client import Client
from import_catalog import normalize,LOCAL
from context_transfer import write_context
from import_lock import import_writer
@import_writer
def main():
    p=argparse.ArgumentParser();p.add_argument('file',type=Path);a=p.parse_args();path=a.file.resolve()
    if not path.is_relative_to(LOCAL.resolve()):p.error('Only reviewed migration exports may be imported')
    client=Client();batch=[];size=0;total=0
    for line in path.open(encoding='utf-8'):
        row=json.loads(line)
        value=normalize({'id':'context','dataset':'context','category':'directories','item':row['data']})['item']
        if len(line)>400_000:
            if batch:client.upsert('corpus_context',batch);batch=[];size=0
            write_context(client,row['key'],value,row.get('source_sha256'));total+=1
            continue
        batch.append({'key':row['key'],'data':value,'source_sha256':row.get('source_sha256') or hashlib.sha256(line.encode()).hexdigest(),'ready':False});size+=len(line);total+=1
        if len(batch)>=100 or size>=1_500_000:client.upsert('corpus_context',batch);batch=[];size=0
    if batch:client.upsert('corpus_context',batch)
    print(json.dumps({'contexts_imported':total,'file':path.name}))
if __name__=='__main__':main()
