"""Reviewed identity bridges for new original CAND portraits; no live integration."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'sources/judge_portrait_refocus_20260919'))
from datetime import datetime, timezone
import io
import json
import re
import sqlite3
import unicodedata
from PIL import Image
from finalize import ROOT, checked, sha, save_json, save_rows, rows, rel, atomic
from cand_capture import profile_evidence
HERE=Path(__file__).resolve().parent

COURT='U.S. District Court for the Northern District of California'
ENTITY_PATH=ROOT/'sources/judge_entities_20260918/entities.jsonl'
BRIDGES=json.loads((HERE/'reviewed_bridges.json').read_text(encoding='utf-8'))


def norm(s): return re.sub(r'\s+',' ',str(s)).strip().casefold()
def tokens(s): return re.findall(r'[a-z]+',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower())


def candidate_inventory(profile, entities):
    """Find review leads, never identity joins, including retained member aliases."""
    pt=[t for t in tokens(profile['name']) if t not in ('jr','ii','iii')]
    found=[]
    for entity in entities:
        names=[entity['name']]+[a['name'] for a in entity.get('aliases',[]) if isinstance(a,dict) and isinstance(a.get('name'),str)]
        matching=[]
        for name in names:
            nt=[t for t in tokens(name) if t not in ('jr','ii','iii')]
            if len(nt)>=2 and len(pt)>=2 and nt[0]==pt[0] and nt[-1]==pt[-1]: matching.append(name)
        if matching: found.append({'entity_id':entity['entity_id'],'name':entity['name'],'matched_name_or_alias':matching,
            'courts':entity.get('courts',[]),'same_specific_court':COURT in entity.get('courts',[])})
    return {'candidate_search_scope':'All10669existing entity names and retained member aliases; accent/punctuation normalized first and last names, with suffixes ignored for discovery only.',
            'candidate_count':len(found),'same_court_candidate_count':sum(r['same_specific_court'] for r in found),'candidates':found,
            'resolution_status':'no_existing_entity_candidate_found_in_name_alias_review' if not found else 'candidate_exists_but_no_reviewed_biographical_bridge',
            'not_an_identity_merge':True}


def reviewed_match(profile, entities, bridge):
    expected_id, facts=bridge
    if len(facts)<2 or not any(field in ('education','professional_career','service') for _,_,field,_ in facts):
        raise ValueError('Identity bridge requires independent biographical facts')
    for _,literal,_,_ in facts:
        if norm(literal) not in norm(profile['biography']): raise ValueError('Reviewed literal absent from saved official biography')
    surname=[t for t in tokens(profile['name']) if t not in ('jr','ii','iii')][-1]
    candidates=[e for e in entities if surname in tokens(e['name']) and COURT in e.get('courts',[])]
    matched=[e for e in candidates if all(norm(literal) in norm(' '.join(e.get(field,[]))) for _,_,field,literal in facts)]
    if [e['entity_id'] for e in matched]!=[expected_id]: raise ValueError('No unique entity supported by all reviewed facts')
    e=matched[0]
    return e, {'identity_basis':'Named official judge-photo field and explicit court, with multiple reviewed literal education/service/appointment facts; unique candidate among same-court surname candidates.',
        'facts':[{'fact':label,'official_profile_literal':literal,'entity_field':field,'entity_literal':entity_literal,
                  'entity_field_provenance':e.get('field_provenance',{}).get(field,[])} for label,literal,field,entity_literal in facts],
        'candidates_considered':[{'entity_id':x['entity_id'],'name':x['name'],'matched':x['entity_id']==expected_id} for x in candidates],
        'facial_matching':False,'current_service_verified':False,'matter_assignment_asserted':False}


def validate_image(data, record, receipt):
    if record['status']!='downloaded' or receipt.get('status')!='downloaded' or receipt.get('http_status')!=200 or receipt.get('raw_complete') is not True:
        raise ValueError('Image not a complete successful original response')
    if receipt.get('requested_url')!=record['url'] or sha(data)!=record['sha256'] or sha(data)!=receipt.get('sha256') or len(data)!=receipt.get('byte_count'):
        raise ValueError('Image bytes or source URL mismatch')
    if not 0<len(data)<=20*1024*1024: raise ValueError('Image byte limit')
    try:
        with Image.open(io.BytesIO(data)) as im:
            format=im.format; size=im.size; im.verify()
        with Image.open(io.BytesIO(data)) as im: im.load()
    except Exception as error: raise ValueError('Undecodable image') from error
    if format not in ('JPEG','PNG','WEBP') or min(size)<64 or size[0]*size[1]>25_000_000: raise ValueError('Unsupported image format or dimensions')
    return {'mime':Image.MIME[format],'format':format,'width':size[0],'height':size[1],'bytes':len(data),
            'sha256':sha(data),'http_content_type':receipt.get('headers',{}).get('content-type')}


def main():
    save_json(HERE/'validation.json',{'passed':False,'status':'validation_in_progress'})
    profiles=rows(HERE/'image_candidates.jsonl');entities=rows(ENTITY_PATH);entity_sha=sha(ENTITY_PATH.read_bytes())
    db=sqlite3.connect((HERE/'image_corpus/corpus.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    accepted=[];appends=[];images=[];held=[];failures=[]
    (HERE/'images').mkdir(exist_ok=True);(HERE/'evidence').mkdir(exist_ok=True)
    for profile in profiles:
        _,raw=checked(ROOT,profile['raw_path'],profile['raw_sha256'])
        live=profile_evidence(raw,profile['source_url'])
        if any(live[k]!=profile[k] for k in ('name','image_source_url','biography')): raise ValueError('Profile evidence changed')
        r=dict(db.execute('SELECT * FROM resources WHERE url=?',(profile['image_source_url'],)).fetchone())
        if r['status']!='downloaded': failures.append({'source_url':profile['source_url'],'image_url':r['url'],'error':r['error']});continue
        receipt_path,receipt_data=checked(HERE/'image_corpus',r['metadata_path']);receipt=json.loads(receipt_data)
        original,image_data=checked(HERE/'image_corpus',r['raw_path'],r['sha256'])
        verification=validate_image(image_data,r,receipt)
        ext={'WEBP':'.webp','PNG':'.png','JPEG':'.jpg'}[verification['format']]
        destination=HERE/'images'/(verification['sha256']+ext);atomic(destination,image_data)
        image={'id':'cand-portrait-'+sha(profile['source_url'].encode())[:24],'source_profile_id':profile['source_profile_id'],
            'name':profile['name'],'source_url':profile['source_url'],'image_source_url':r['url'],
            'path':rel(destination),'sha256':verification['sha256'],'mime':verification['mime'],
            'bytes':len(image_data),'width':verification['width'],'height':verification['height'],
            'profile_capture':{'path':profile['raw_path'],'sha256':profile['raw_sha256']},
            'image_receipt':{'path':rel(receipt_path),'sha256':sha(receipt_data)},
            'captured_at':receipt['fetched_at'],'verification':verification}
        images.append(image)
        if profile['name'] not in BRIDGES:
            held.append({**image,'entity_id':None,**candidate_inventory(profile,entities),
                         'reason':'Source-bound portrait verified; no reviewed existing-entity biographical bridge in this batch. Do not assign by name or create a duplicate automatically.'});continue
        entity,evidence=reviewed_match(profile,entities,BRIDGES[profile['name']])
        proof={**evidence,'entity_id':entity['entity_id'],'entity_name':entity['name'],'source_profile':profile,
            'image':image,'entity_manifest':{'path':rel(ENTITY_PATH),'sha256':entity_sha},
            'entity_record':entity,'source_wording_differences':'Source and existing entity wording is retained independently; current service is not inferred.'}
        proof_path=HERE/'evidence'/(image['id']+'.json');save_json(proof_path,proof)
        item={**image,'entity_id':entity['entity_id'],'entity_name':entity['name'],
            'identity_evidence_path':rel(proof_path),'identity_evidence_sha256':sha(proof_path.read_bytes()),
            'proposed_presentation_path':'sources/judge_presentation_20260918/images/'+destination.name}
        accepted.append(item)
        appends.append({'entity_id':entity['entity_id'],'id':image['id'],'path':item['proposed_presentation_path'],
            'sha256':image['sha256'],'mime':image['mime'],'provenance':{
                'source_url':profile['source_url'],'image_source_url':image['image_source_url'],'captured_at':image['captured_at'],
                'identity_evidence_path':rel(proof_path),'identity_evidence_sha256':sha(proof_path.read_bytes()),
                'identity_basis':evidence['identity_basis'],'source_manifest':rel(HERE/'accepted_links.jsonl'),
                'current_service_verified':False,'matter_assignment_asserted':False}})
    existing=rows(ROOT/'sources/judge_presentation_20260918/portraits.jsonl')
    if {r['entity_id'] for r in accepted}&{r['entity_id'] for r in existing}: raise ValueError('Already installed portrait would be overwritten')
    if len({r['entity_id'] for r in accepted})!=len(accepted) or len({r['sha256'] for r in images})!=len(images): raise ValueError('Duplicate proposed identity/image')
    for name,data in [('images.jsonl',images),('accepted_links.jsonl',accepted),('portraits.append.jsonl',appends),('held_images.jsonl',held),('image_failures.jsonl',failures)]: save_rows(HERE/name,data)
    summary={'validated_at':datetime.now(timezone.utc).isoformat(),'official_roster_profile_links':36,'already_installed_from_roster':20,
        'additional_profile_links':16,'selected_profile_requests':2,'successful_profile_captures':2,'profile_request_failures':0,
        'decorative_gavel_excluded':0,'source_bound_portrait_references':len(profiles),'downloaded_valid_images':len(images),
        'accepted_existing_entity_links':len(accepted),'held_unlinked_images':len(held),'image_failures':len(failures),
        'held_with_no_name_or_alias_candidate':sum(r['candidate_count']==0 for r in held),
        'held_with_unresolved_candidate':sum(r['candidate_count']>0 for r in held),
        'image_http_attempts':db.execute('SELECT count(*) FROM fetches').fetchone()[0],
        'single_same_url_image_retry_after_remote_disconnect':0,'paid_calls':0,'shared_host_minimum_delay_seconds':2,
        'new_images_attached':0,'expected_verified_total_after_root_integrates':49+len(accepted),
        'browse_profiles_created':0,'projection_modified':False,'all_roster_portraits_complete':False,
        'national_portrait_completion_percent':None,'image_bytes':sum(r['bytes'] for r in images)}
    save_json(HERE/'summary.json',summary)
    files={name:{'sha256':sha((HERE/name).read_bytes()),'bytes':(HERE/name).stat().st_size}
        for name in ('images.jsonl','accepted_links.jsonl','portraits.append.jsonl','held_images.jsonl','image_failures.jsonl','summary.json','image_candidates.jsonl','profile_gaps.json')}
    save_json(HERE/'validation.json',{'status':'passed','passed':True,'validated_at':summary['validated_at'],'files':files,
        'counts':{'images':len(images),'accepted_entity_links':len(accepted),'held':len(held),'image_failures':len(failures)},
        'checks':['Original successful profile and image receipt hashes','Exact observed source and image URL binding',
                  'Individual judge-photo field and named heading','Image bytes/length/decode/dimensions',
                  'Unique reviewed court+biographical identity evidence','Existing portraits not overwritten','No live projection mutation']})
    db.close();print(json.dumps(summary))


if __name__=='__main__':main()
