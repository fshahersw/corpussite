"""Create individual readers from already-validated native rule boundaries."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'sources/gap_fill_20260927/laws'
OUT=ROOT/'sources/gap_fill_20260927/rule_sections'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    if (OUT/'manifest.jsonl').exists():raise ValueError('Rule-section snapshot already exists')
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'text').mkdir(exist_ok=True)
    parents={r['id']:r for r in map(json.loads,(SOURCE/'manifest.jsonl').read_text(encoding='utf-8').splitlines())}
    records=[];edges=[]
    for p in map(json.loads,(SOURCE/'provisions.jsonl').read_text(encoding='utf-8').splitlines()):
        parent=parents[p['record_id']]
        original=(ROOT/parent['raw_path']).resolve()
        if not original.is_relative_to(ROOT) or sha(original)!=p['source_sha256'] or parent['status']!='validated':raise ValueError('Rule parent has not passed source checks')
        if not p.get('text') or not p.get('native_id') or p.get('review_status')!='source_structure_validated':raise ValueError('Unvalidated rule boundary')
        body=p['text'].encode('utf-8');text_sha=hashlib.sha256(body).hexdigest();reader=OUT/'text'/f'{text_sha}.txt';reader.write_bytes(body)
        ident=p['id'];evidence={'source_url':p['source_url'],'source_path':parent['raw_path'],'source_sha256':p['source_sha256'],'quote':p['title'],'page':p['page_start'],'basis':p['derivation']}
        r={k:parent.get(k) for k in ('state','raw_path','raw_sha256','raw_bytes','mime_type','captured_at','source_as_of')}
        r.update(id=ident,title=p['title'],native_id=p['native_id'],source_url=p['source_url'],final_url=parent['final_url'],resource_type='rules',category='rules',document_shape='section',
                 text_path=reader.relative_to(ROOT).as_posix(),text_sha256=text_sha,text_characters=len(p['text']),source_page=p['page_start'],parent_compilation_id=p['record_id'],
                 published_at=None,effective_from=None,status='validated',review_status=p['review_status'],source_evidence=evidence,
                 date_evidence={'capture':'Parent official PDF retrieval time','effective_from':'Not inferred for individual rules from compilation update date'},
                 qualification=p['derivation'],extracted_at=datetime.now(timezone.utc).isoformat())
        records.append(r)
        edges.append({'source':ident,'target':parent['source_url'],'relation':'excerpt_of','evidence':evidence,'status':'source_evidenced'})
        native='rule:'+p['native_id'].replace(' ','_')
        edges.append({'source':ident,'target':native,'relation':'has_native_identifier','evidence':evidence,'status':'source_evidenced'})
    for name,data in [('manifest',records),('edges',edges)]:
        (OUT/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in data),encoding='utf-8')
    summary={'records':len(records),'new_network_requests':0,'source_provisions_sha256':sha(SOURCE/'provisions.jsonl'),'originals_reused':len({r['raw_sha256'] for r in records}),'legal_currency_verified':False}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary))
if __name__=='__main__':main()
