"""Read-only follow-up: saved court captures, evidence receipts, and code references.

Does not execute discovered scripts, hydrate placeholders, contact APIs, or read
credential files. Outputs source paths and counts, never code or secrets.
"""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit
import hashlib, json, re, shutil, subprocess, time

OUT = Path(__file__).resolve().parent
RETURNED = Path('C:/Users/firas/Downloads/returnedfiles')

def write(name, obj):
    (OUT/name).write_text(json.dumps(obj, indent=2, ensure_ascii=False)+'\n', encoding='utf8')

def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()

def unwrap(v):
    return v.get('value') if isinstance(v, dict) and 'value' in v else v

def main():
    start=time.monotonic()
    inventory=[json.loads(line) for line in (OUT/'additional_file_inventory.jsonl').open(encoding='utf8')]
    returned=[r for r in inventory if Path(r['root'])==RETURNED and not r['cloud_placeholder']]
    groups=defaultdict(list)
    for r in returned:
        parts=Path(r['relative_path']).parts
        groups[parts[0] if len(parts)>1 else '(root)'].append(r)
    group_stats=[{'folder':k,'files':len(rows),'bytes':sum(r['bytes'] for r in rows),
                  'extensions':dict(Counter(r['extension'] for r in rows))} for k,rows in groups.items()]

    # Treat JSON page captures separately from maps, manifests and data exports.
    pages=[]; portraits=[]; invalid=[]; types=Counter(); hashes=defaultdict(list)
    for r in returned:
        p=Path(r['path'])
        if p.suffix!='.json' or r['bytes']>4_000_000:continue
        if any(x in p.name.lower() for x in ['signin','login','authenticate','token','secret']):continue
        try:obj=json.loads(p.read_text(encoding='utf-8-sig'))
        except (ValueError,OSError):invalid.append(str(p));continue
        if not isinstance(obj,dict):types[type(obj).__name__]+=1;continue
        data=obj.get('data') if isinstance(obj.get('data'),dict) else obj
        metadata=data.get('metadata',{})
        if not isinstance(metadata,dict):metadata={}
        md=data.get('markdown')
        url=metadata.get('sourceURL') or metadata.get('url') or metadata.get('og:url') or metadata.get('twitter:url') or data.get('url')
        if not isinstance(md,str) or not md.strip() or not isinstance(url,str) or not url.startswith(('http://','https://')):
            types['other_json']+=1;continue
        title=metadata.get('title') or ''
        status=metadata.get('statusCode')
        digest=sha(p)
        bodysha=hashlib.sha256(md.encode()).hexdigest()
        row={'path':str(p),'source_url':url,'title':title,'source_sha256':digest,'text_sha256':bodysha,
             'markdown_chars':len(md),'status_code':status,'host':urlsplit(url).hostname}
        pages.append(row);hashes[(url,bodysha)].append(str(p))
        # Keep only image references on pages explicitly about an individual judge.
        headings=re.findall(r'^#{1,3}\s+(.+)$',md,re.M)
        person_header=next((h for h in headings if re.match(r'(?:Judge|Justice|Chief (?:Judge|Justice)|Senior Judge|President Judge)\s+\S',h,re.I)),None)
        if not person_header:continue
        for alt,image_url in re.findall(r'!\[([^\]]*)\]\((https?://[^\s)]+)(?:\s+"[^"]*")?\)',md):
            if not re.search(r'\.(?:png|jpe?g|webp)(?:[?#]|$)',image_url,re.I):continue
            if re.search(r'logo|icon|seal|banner|translate',image_url,re.I):continue
            portraits.append({'profile_title':person_header,'source_url':url,'image_url':image_url,
                'image_alt':alt,'capture_path':str(p),'capture_sha256':digest,
                'status':'observed_image_reference_on_judge_page','local_image_verified':False,
                'identity_join_status':'not_joined_to_current_entities'})
    unique_portraits={ (r['source_url'],r['image_url']):r for r in portraits}
    write('returned_page_inventory.json',pages)
    write('judge_portrait_references.json',list(unique_portraits.values()))

    reg=RETURNED/'court_access_registry_2026-08-21'
    rows=[json.loads(line) for line in (reg/'registry.jsonl').open(encoding='utf8')]
    evidence=[json.loads(line) for line in (reg/'evidence_manifest.jsonl').open(encoding='utf8')]
    checked=[]
    for item in evidence:
        rel=item.get('local_path'); expected=item.get('sha256')
        p=(reg/rel).resolve() if rel else None
        result={'evidence_id':item.get('evidence_id'),'local_path':rel,'has_expected_hash':bool(expected)}
        if p and p.is_relative_to(reg.resolve()) and p.is_file():
            result.update(exists=True,hash_matches=sha(p)==expected if expected else None)
        else:result.update(exists=False,hash_matches=None)
        checked.append(result)
    counties=defaultdict(list)
    for row in rows:counties[unwrap(row.get('state_code'))].append(row)
    state_counts={}
    for state,rs in counties.items():
        stats={'county_rows':len(rs),'unique_fips':len({unwrap(r.get('fips')) for r in rs})}
        for field in ['judges_seated','court_entities','clerk_offices','judicial_personnel','additional_judicial_officers']:
            vals=[unwrap(r.get(field)) for r in rs]
            stats[field+'_rows']=sum(len(x) for x in vals if isinstance(x,list))
        state_counts[state]=stats
    registry={'source':str(reg),'snapshot_generated_at':'2026-08-22T03:12:17.609152Z',
        'states':state_counts,'registry_sha256':sha(reg/'registry.jsonl'),
        'evidence_manifest_sha256':sha(reg/'evidence_manifest.jsonl'),
        'evidence_count':len(evidence),'evidence_existing':sum(x['exists'] for x in checked),
        'hash_matches':sum(x['hash_matches'] is True for x in checked),
        'hash_mismatches':[x for x in checked if x['hash_matches'] is False],
        'missing_evidence':[x for x in checked if not x['exists']],
        'missing_expected_hashes':[x for x in checked if not x['has_expected_hash']],
        'limitations':['Historical snapshot, not current-service verification.',
         'County rows do not mean complete local rules, filings, or documents.',
         'Texas individual county website and case-search fanout was not completed.',
         'Judge seat associations can repeat a person across counties; not a unique judge count.']}
    write('county_registry_summary.json',registry)

    # Read small first-party code/docs from the previously enumerated roots, and
    # returnedfiles root scripts. rg returns filenames only; no snippets are saved.
    extensions={'.py','.ts','.tsx','.js','.mjs','.cjs','.md','.sh','.ps1','.sql','.yaml','.yml','.toml'}
    files=[]
    for r in inventory:
        p=Path(r['path'])
        if r['cloud_placeholder'] or r['bytes']>2_000_000 or p.suffix not in extensions:continue
        if any(x in {'target','vendor','node_modules','.git','.auth','.cache'} for x in p.parts):continue
        if any(x in p.name.lower() for x in ['credential','token','secret','.env']):continue
        if Path(r['root'])==RETURNED and p.parent!=RETURNED:continue
        files.append(str(p))
    patterns={
      'courtlistener':r'courtlistener|people-db-people|people_db_person',
      'trellis':r'trellis\.law|TRELLIS_(?:API|KEY|TOKEN)',
      'fjc':r'fjc\.gov|Federal Judicial Center',
      'judge_images':r'(?:judge|judicial|portrait).{0,80}(?:image|photo|avatar|portrait)|(?:image|photo|avatar).{0,80}judge',
      'scraping_integrations':r'firecrawl|tavily',
      'commercial_analytics':r'lexmachina|lexis.{0,16}context|lexisnexis|vlex|bloomberglaw|westlaw',
    }
    references={k:[] for k in patterns};errors=[]
    rg=shutil.which('rg')
    # A single file-only content search followed by classification of just hits.
    # This avoids launching six nearly identical passes over the same files.
    hits=set()
    combined='|'.join('(?:'+pattern+')' for pattern in patterns.values())
    for startidx in range(0,len(files),65):
        args=[rg,'-l','-i','--no-messages','--',combined]+files[startidx:startidx+65]
        run=subprocess.run(args,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=40)
        if run.returncode not in (0,1):errors.append({'batch':startidx,'exit':run.returncode})
        hits.update(run.stdout.splitlines())
    for filename in sorted(hits):
        try:content=Path(filename).read_text(encoding='utf8',errors='replace')
        except OSError:continue
        for kind,pattern in patterns.items():
            if re.search(pattern,content,re.I):references[kind].append(filename)
    write('additional_code_references.json',{'eligible_files':len(files),'references':references,'errors':errors,
      'meaning':'Static references only; scripts were not executed and account access was not tested.'})
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.monotonic()-start,2),
       'returned_groups':group_stats,'readable_page_capture_files':len(pages),
       'unique_page_source_urls':len({r['source_url'] for r in pages}),
       'unique_source_and_text_pairs':len(hashes),'hosts':dict(Counter(r['host'] for r in pages)),
       'portrait_references':len(unique_portraits),'portrait_pages':len({r['source_url'] for r in portraits}),
       'portraits_downloaded':0,'json_parse_failures':len(invalid),'code_reference_counts':{k:len(v) for k,v in references.items()},
       'external_files_modified':False,'network_calls':0}
    write('additional_analysis_summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in {'returned_groups','hosts'}},indent=2))

if __name__=='__main__':main()
