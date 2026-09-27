"""Build the exact, ordered migration inventory. Read-only unless writing its local plan.

Smoke directories and unclassified source exports are deliberately not inputs.
This utility never spends credits, resizes storage, activates data or starts imports.
"""
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXPORT=ROOT/'_transfer_scratch/supabase_export'
FOLDERS=['.','core','generic','federal','navigation','text_assets']
CONTEXTS=['core/contexts.jsonl','navigation/contexts.jsonl','federal/contexts.regulations.jsonl','federal/contexts.agency.jsonl','federal/contexts.hubs.jsonl','related/contexts.jsonl','contexts.coverage_order.jsonl']

def signature(path):
    digest=hashlib.sha256();rows=0
    with path.open('rb') as source:
        while block:=source.read(8*1024*1024):digest.update(block);rows+=block.count(b'\n')
    return digest.hexdigest(),rows

def build(folder=EXPORT,verify=False):
    datasets=[];seen=set()
    for name in FOLDERS:
        for descriptor in sorted((folder/name).glob('*.dataset.json')):
            meta=json.loads(descriptor.read_text('utf-8'));path=descriptor.with_name(descriptor.name.replace('.dataset.json','.jsonl'))
            identity=meta.get('id') or meta.get('dataset')
            if not identity or identity in seen:raise ValueError('Missing or duplicate dataset identity: '+str(identity))
            seen.add(identity)
            expected=next((meta[k] for k in ('expected_records','rows','records') if k in meta),None)
            sha=meta.get('export_jsonl_sha256') or meta.get('sha256')
            if expected is None or not sha or len(sha)!=64 or not path.is_file():raise ValueError('Unfinished export: '+descriptor.name)
            if verify and signature(path)!=(sha,expected):raise ValueError('Export hash/count mismatch: '+str(path))
            datasets.append({'id':identity,'path':path.relative_to(folder).as_posix(),'descriptor':descriptor.relative_to(folder).as_posix(),'records':expected,'bytes':path.stat().st_size,'sha256':sha})
    contexts=[]
    for name in CONTEXTS:
        path=folder/name;sha,rows=signature(path)
        contexts.append({'path':name,'rows':rows,'bytes':path.stat().st_size,'sha256':sha})
    outline=json.loads((folder/'law_outline/manifest.json').read_text('utf-8'))
    if outline['status']!='passed':raise ValueError('Outline export is not validated')
    for table in outline['tables']:
        if verify and signature(folder/'law_outline'/table['path'])!=(table['sha256'],table['rows']):raise ValueError('Outline mismatch')
    groups=folder/'core/groups.table.jsonl';sha,rows=signature(groups)
    return {'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'project':'xosqzzsnhxcyehcnirpa',
        'status':'local_hashes_verified' if verify else 'descriptors_only','publication':'held',
        'capacity':{'approved_database_disk_gb':64,'compute_change_authorized':False,'resize_verified':False},
        'datasets':sorted(datasets,key=lambda d:(d['bytes'],d['id'])),
        'totals':{'datasets':len(datasets),'catalog_records':sum(d['records'] for d in datasets),'jsonl_bytes':sum(d['bytes'] for d in datasets),'context_rows_before_ordered_overrides':sum(d['rows'] for d in contexts)},
        'contexts_in_import_order':contexts,'outline':outline['tables'],
        'groups':{'path':'core/groups.table.jsonl','table':'corpus_display_groups','rows':rows,'sha256':sha},
        'activation_requirements':['Verified 64 GB disk in the correct project','Every dataset remote count equals its pinned export','Original routes reconcile and byte readbacks pass','All context chunks and law outline validate','Hosted filters, readers, portraits, downloads and security checks pass','Publish publication:release with the complete dataset inventory only after acceptance']}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    plan=build(verify=args.verify)
    (EXPORT/'migration_plan.json').write_text(json.dumps(plan,indent=2)+'\n','utf-8')
    print(json.dumps({'status':plan['status'],**plan['totals'],'publication':plan['publication']}))

if __name__=='__main__':main()
