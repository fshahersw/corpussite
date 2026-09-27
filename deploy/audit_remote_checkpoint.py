"""Read-only import inventory and bounded original-byte readback. No activation."""
import hashlib,json,os,sqlite3,subprocess
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
from supabase_client import Client,BUCKET
from private_supabase import secret

ROOT=Path(__file__).resolve().parents[1]

def main():
    client=Client();datasets=client.json('GET','/rest/v1/corpus_datasets?select=id,ready,expected_records,imported_records')
    counts={}
    for dataset in datasets:
        response=client.call('GET','/rest/v1/corpus_records?select=id&limit=1&dataset=eq.'+quote(dataset['id']),headers={'Prefer':'count=exact'})
        dataset['actual_records']=int(response.headers['Content-Range'].split('/')[-1])
    for table in ['corpus_context','corpus_artifacts','corpus_display_groups','corpus_law_collections','corpus_law_nodes','corpus_law_segments']:
        response=client.call('GET','/rest/v1/'+table+'?select=*&limit=1',headers={'Prefer':'count=exact'})
        counts[table]=int(response.headers['Content-Range'].split('/')[-1])
    samples=client.json('GET','/rest/v1/corpus_artifacts?select=route,object_key,sha256,bytes&bytes=lt.2000000&order=sha256&limit=5')
    checked=[]
    for sample in samples:
        response=client.call('GET','/storage/v1/object/authenticated/'+BUCKET+'/'+sample['object_key'])
        checked.append({'sha256':sample['sha256'],'bytes':len(response.content),'hash_matches':hashlib.sha256(response.content).hexdigest()==sample['sha256'],'size_matches':len(response.content)==sample['bytes']})
    journal=ROOT/'_transfer_scratch/supabase_export/artifact_progress.sqlite3'
    with sqlite3.connect(f'file:{journal.as_posix()}?mode=ro',uri=True) as db:
        count,size=db.execute('select count(*),sum(bytes) from objects').fetchone()
    # The subprocess receives the credential in its environment, never argv/output.
    code="""import {createContext} from './deploy/cloud-context.mjs';
const ctx=createContext(); const assets=await ctx.asset(process.env.CORPUS_AUDIT_ROUTE);
const target=assets.headers.get('location');
const response=await fetch(target,{redirect:'error'});
const bytes=Buffer.from(await response.arrayBuffer());
const hash=(await import('node:crypto')).createHash('sha256').update(bytes).digest('hex');
console.log(JSON.stringify({status:assets.status,download_status:response.status,hash_matches:hash===process.env.CORPUS_AUDIT_SHA}));"""
    env={**os.environ,'CORPUS_SUPABASE_SECRET_KEY':secret(),'NODE_OPTIONS':'--use-system-ca','CORPUS_AUDIT_ROUTE':samples[0]['route'],'CORPUS_AUDIT_SHA':samples[0]['sha256']}
    process=subprocess.run(['node','--input-type=module','-e',code],cwd=ROOT,env=env,capture_output=True,text=True)
    if process.returncode:raise RuntimeError('Native cloud signed-download audit failed; credentials and signed URLs withheld')
    result={'at':datetime.now(timezone.utc).isoformat(),'project':'xosqzzsnhxcyehcnirpa','remote_datasets':datasets,'remote_tables':counts,'local_uploaded_object_checkpoint':{'objects':count,'bytes':size},'sample_readbacks':checked,'cloud_signed_download':json.loads(process.stdout),'publication':'held','remote_mutations':0}
    target=ROOT/'reports/supabase_migration_20260927/remote_checkpoint.json'
    target.write_text(json.dumps(result,indent=2)+'\n','utf-8')
    print(json.dumps(result,indent=2))
    if not all(x['hash_matches'] and x['size_matches'] for x in checked) or not result['cloud_signed_download']['hash_matches']:raise RuntimeError('Original readback mismatch')

if __name__=='__main__':main()
