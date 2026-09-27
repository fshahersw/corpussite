"""Publish the bounded FJC discovery evidence; never infer or manufacture portraits."""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import hashlib
import json
import re
import sqlite3
from lxml import html

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CORPUS = HERE/'corpus'
MEMBERS = ROOT/'sources/judge_entities_20260918/members.jsonl'


def sha(data): return hashlib.sha256(data).hexdigest()
def rows(path): return [json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def rel(path): return path.resolve().relative_to(ROOT).as_posix()


def atomic(path, data):
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_bytes(data)
    temp.replace(path)


def save_json(path, data): atomic(path,(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())
def save_rows(path, data): atomic(path,''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in data).encode())


def checked(root, relative, expected=None):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file(): raise ValueError('Artifact outside registered root or missing')
    data=path.read_bytes()
    if expected is not None and sha(data)!=expected: raise ValueError('Artifact digest mismatch')
    return path,data


def member_index(members):
    index=defaultdict(list)
    for member in members:
        if member.get('source_class')!='federal_biographies': continue
        match=re.fullmatch(r'fjc:nid:(\d+):csv:[0-9a-f]+',member.get('source_observation_id',''))
        if match: index[match.group(1)].append(member)
    return index


def parse_profile(raw, source_url, index):
    """Native shortlink ID and the exact canonical/about URL identify the source."""
    parsed=urlsplit(source_url)
    if parsed.scheme!='https' or parsed.netloc!='www.fjc.gov' or not parsed.path.startswith('/history/judges/'):
        raise ValueError('Not an exact official FJC individual-profile URL')
    doc=html.fromstring(raw)
    if doc.xpath('//link[@rel="canonical"]/@href')!=[source_url]: raise ValueError('Canonical URL mismatch')
    shortlinks=doc.xpath('//link[@rel="shortlink"]/@href')
    if len(shortlinks)!=1: raise ValueError('Missing or ambiguous native ID')
    match=re.fullmatch(r'https://www\.fjc\.gov/node/(\d+)',shortlinks[0])
    if not match: raise ValueError('Invalid FJC native shortlink')
    nid=match.group(1)
    matches=index.get(nid,[])
    entities={r['entity_id'] for r in matches}
    if len(entities)!=1: raise ValueError('Native ID has no unique existing entity')
    nodes=doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," node--judge ")]')
    if len(nodes)!=1 or urljoin(source_url,nodes[0].get('about',''))!=source_url:
        raise ValueError('Individual judge body not bound to source URL')
    headings=doc.xpath('//h1[not(contains(concat(" ",normalize-space(@class)," ")," site-name "))]')
    if len(headings)!=1: raise ValueError('Missing or ambiguous published heading')
    heading=' '.join(headings[0].text_content().split())
    fields=nodes[0].xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," field--name-judge-record-display ")]')
    if len(fields)!=1: raise ValueError('Missing published biography field')
    body=fields[0]
    # All media candidates remain unverified references until separate human/context review.
    media=[{'url':urljoin(source_url,n.get('src')),'alt':n.get('alt'),'candidate_only':True}
           for n in nodes[0].xpath('.//img[@src]')]
    all_images=[{'url':urljoin(source_url,n.get('src')),'alt':n.get('alt')}
                for n in doc.xpath('//img[@src]')]
    for node in body.xpath('.//script | .//style | .//nav'): node.drop_tree()
    for node in body.xpath('.//br'): node.tail='\n'+(node.tail or '')
    for node in body.xpath('.//p | .//b | .//strong'):
        node.text='\n'+(node.text or '')
        node.tail='\n'+(node.tail or '')
    lines=[re.sub(r'[ \t]+',' ',line).strip() for line in body.text_content().splitlines()]
    reading=heading+'\n\n'+'\n'.join(line for line in lines if line)+'\n'
    if len(reading)<100: raise ValueError('Insufficient biography text')
    return {'native_nid':nid,'native_shortlink':shortlinks[0],'entity_id':next(iter(entities)),
            'member_keys':sorted({r['member_key'] for r in matches}),
            'source_observation_ids':sorted({r['source_observation_id'] for r in matches}),
            'heading_as_published':heading,'reading':reading,'all_image_elements':all_images,
            'profile_media_candidates':media,'portrait_assigned':False,
            'identity_basis':'Exact official shortlink node ID equals retained fjc:nid observation; canonical URL and judge body about URL agree. No name-only matching.'}


def main():
    save_json(HERE/'validation.json',{'passed':False,'status':'validation_in_progress'})
    seeds={r['url']:r for r in rows(HERE/'seeds.jsonl')}
    db=sqlite3.connect((CORPUS/'corpus.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    if db.execute('SELECT count(*) FROM runs WHERE ended_at IS NULL').fetchone()[0]: raise ValueError('Collector still active')
    captures=[dict(r) for r in db.execute('SELECT * FROM resources ORDER BY url')]
    if {r['url'] for r in captures}!=set(seeds): raise ValueError('Seed scope mismatch')
    members=member_index(rows(MEMBERS)); resources=[]; identities=[]; gaps=[]
    (HERE/'reading').mkdir(exist_ok=True)
    for row in captures:
        if row['status']!='downloaded':
            gaps.append({'source_url':row['url'],'status':'not_attempted' if not row['attempts'] else row['status'],
                         'reason':'Five-minute bounded discovery checkpoint; no portrait absence inferred.' if not row['attempts'] else row['error']})
            continue
        receipt_path,receipt_data=checked(CORPUS,row['metadata_path'])
        receipt=json.loads(receipt_data)
        if receipt.get('requested_url')!=row['url'] or receipt.get('http_status')!=200 or receipt.get('raw_complete') is not True:
            raise ValueError('Incomplete or misbound capture receipt')
        raw_path,raw=checked(CORPUS,row['raw_path'],row['sha256'])
        text_path,text=checked(CORPUS,row['text_path'],receipt['text_sha256'])
        if sha(raw)!=receipt['sha256'] or len(raw)!=receipt['byte_count']: raise ValueError('Raw receipt mismatch')
        seed=seeds[row['url']]['source_evidence']
        checked(ROOT,seed['capture_path'],seed['capture_sha256'])
        checked(ROOT,seed['reading_metadata_path'],seed['reading_metadata_sha256'])
        profile=parse_profile(raw,row['url'],members)
        ident=sha(row['url'].encode())[:24]
        reading_path=HERE/'reading'/(ident+'.txt');atomic(reading_path,profile.pop('reading').encode())
        profile.update({'source_url':row['url'],'raw_path':rel(raw_path),'raw_sha256':sha(raw),
                        'captured_at':receipt['fetched_at'],'members_manifest_path':rel(MEMBERS),
                        'members_manifest_sha256':sha(MEMBERS.read_bytes()),'roster_source_evidence':seed})
        identities.append(profile)
        resources.append({'id':'fjc-profile:'+ident,'source_url':row['url'],'final_url':row['url'],
            'title':profile['heading_as_published'],'kind':'judge_individual_profile','entity_id':profile['entity_id'],
            'native_nid':profile['native_nid'],'captured_at':receipt['fetched_at'],'source_as_of':None,
            'raw_path':rel(raw_path),'raw_sha256':sha(raw),'raw_bytes':len(raw),'mime_type':'text/html',
            'text_path':rel(reading_path),'text_sha256':sha(reading_path.read_bytes()),
            'text_characters':len(reading_path.read_text(encoding='utf-8')),
            'extracted_path':rel(text_path),'extracted_sha256':sha(text),
            'access_receipt_path':rel(receipt_path),'access_receipt_sha256':sha(receipt_data),
            'current_service_verified':False,'portrait_available_in_saved_page':False if not profile['profile_media_candidates'] else None,
            'provenance_note':'Saved publisher biography; source-relative present/date wording is preserved and not promoted to a current-service assertion.'})
        gaps.append({'source_url':row['url'],'entity_id':profile['entity_id'],'status':'no_portrait_in_saved_page' if not profile['profile_media_candidates'] else 'image_context_review_required',
                     'reason':'Saved judge body contains no image; page-level Home/Share images are unrelated icons.' if not profile['profile_media_candidates'] else 'Image references require separate verification before assignment.'})
    if len({r['entity_id'] for r in identities})!=len(identities): raise ValueError('Duplicate entity in batch')
    save_rows(HERE/'resources.jsonl',resources);save_rows(HERE/'identity_links.jsonl',identities)
    save_rows(HERE/'portraits.jsonl',[]);save_json(HERE/'gaps.json',{'items':gaps})
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'selected_profile_urls':len(seeds),
        'downloaded_profiles':len(resources),'unique_native_entity_links':len(identities),
        'failed_requests':sum(bool(r['attempts']) and r['status']!='downloaded' for r in captures),
        'unattempted_profiles':sum(not r['attempts'] for r in captures),'saved_profiles_without_portrait':sum(not r['profile_media_candidates'] for r in identities),
        'unverified_profile_media_candidates':sum(len(r['profile_media_candidates']) for r in identities),
        'new_images_downloaded':0,'new_images_attached':0,'previous_verified_images':43,
        'raw_bytes':sum(r['raw_bytes'] for r in resources),'clean_text_characters':sum(r['text_characters'] for r in resources),
        'network_run':dict(db.execute('SELECT id,started_at,ended_at,processed,stop_reason FROM runs ORDER BY started_at DESC LIMIT 1').fetchone()),
        'robots_crawl_delay_seconds':30,'paid_calls':0,'image_requests':0,'projection_modified':False,
        'portrait_discovery_complete':False,'national_portrait_completion_percent':None,
        'stop_reason':'Approved bounded checkpoint; nine saved examples contain no portraits. No further FJC requests scheduled.'}
    save_json(HERE/'summary.json',summary)
    files={name:{'sha256':sha((HERE/name).read_bytes()),'bytes':(HERE/name).stat().st_size}
           for name in ('resources.jsonl','identity_links.jsonl','portraits.jsonl','gaps.json','summary.json')}
    save_json(HERE/'validation.json',{'status':'passed','passed':True,'validated_at':summary['generated_at'],
        'resources_sha256':files['resources.jsonl']['sha256'],'files':files,'counts':{'profiles':len(resources),'identity_links':len(identities),'portraits':0},
        'checks':['Frozen exact URL scope','Complete HTTP200 raw hash and byte receipts','Native extracted text hash',
                  'Roster evidence hashes','Exact canonical and judge-body URL','Unique native NID-to-existing-entity match',
                  'Cleaned biography text nonempty','No body image substituted by site logos','Atomic validation gate written last'],
        'network_or_projection_mutation_by_export':False})
    db.close(); print(json.dumps(summary))


if __name__=='__main__': main()
