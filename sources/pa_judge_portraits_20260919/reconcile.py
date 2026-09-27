"""Offline PA profile reconciliation; does not download images or merge entities."""
from pathlib import Path
import collections
import datetime
import hashlib
import json
import re
import sqlite3
import unicodedata

OUT=Path(__file__).resolve().parent
ROOT=OUT.parent.parent
REFS=ROOT/'sources/local_deep_audit_20260918/portraits/prioritized_portrait_references.jsonl'
ENTITIES=ROOT/'sources/judge_entities_20260918/entities.jsonl'
PEOPLE=ROOT/'sources/courtlistener_people_20260918/catalog.sqlite3'

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def normalized(s):
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
    s=re.sub(r'[^a-z0-9]+',' ',s).strip()
    words=s.split()
    while words and words[0] in {'the','hon','honorable','president','senior','magistrate','chief','judge','justice','emerita','emeritus'}:words.pop(0)
    return ' '.join(words)
def write_jsonl(p,rows):p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
def raw_row(connection,table,row):
    cols=json.loads(connection.execute('SELECT columns_json FROM source_files WHERE table_name=?',(table,)).fetchone()[0])
    return {k:row[k] for k in cols}

refs=[json.loads(l) for l in REFS.open(encoding='utf-8')]
entities=[json.loads(l) for l in ENTITIES.open(encoding='utf-8')]
entity_url_index=collections.defaultdict(list)
entity_name_index=collections.defaultdict(list)
for e in entities:
    urls={str(e.get('best_profile_source_url','')).rstrip('/')}|{str(m.get('source_url','')).rstrip('/') for m in e.get('members',[])}
    for url in urls:
        if url:entity_url_index[url].append(e)
    names={e['name']}|{a['name'] for a in e.get('aliases',[])}
    for name in names:entity_name_index[normalized(name)].append(e)

con=sqlite3.connect(PEOPLE.as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
accepted=[];unresolved=[];profiles=[];intersections=[];errors=[]
(OUT/'evidence').mkdir(exist_ok=True)
try:
    people=[dict(r) for r in con.execute('SELECT p.id,p.name_first,p.name_middle,p.name_last,p.name_suffix,p.is_alias_of_id,s.display_name FROM people p JOIN people_search s ON s.person_id=p.id')]
    source_files={r['table_name']:dict(r) for r in con.execute('SELECT * FROM source_files')}
    for ref in refs:
        name=ref['name_as_published'];url=ref['source_url'];key='pa-official-profile:'+hashlib.sha256(url.encode()).hexdigest()[:24]
        capture=Path(ref['capture_path']);capture_hash=sha(capture)
        payload=json.loads(capture.read_text(encoding='utf-8-sig'));markdown=payload.get('markdown','');metadata=payload.get('metadata',{})
        verify={
          'capture_hash_matches_reference':capture_hash==ref['capture_sha256'],
          'image_url_in_saved_markdown':ref['image_url'] in markdown,
          'profile_heading_in_saved_markdown':ref['profile_heading_as_published'] in markdown,
          'official_profile_url_in_metadata':url in json.dumps(metadata,ensure_ascii=False),
          'prior_profile_section_association_verified':ref['evidence'].get('image_immediately_in_named_profile_section_before_first_bio_heading') is True,
        }
        if not all(verify.values()):errors.append({'source_profile_id':key,'checks':verify})
        exact=entity_url_index[url.rstrip('/')]
        named=entity_name_index[normalized(name)]
        unique_exact={e['entity_id']:e for e in exact}
        namesakes=[e for e in entities if normalized(e['name']).split() and normalized(e['name']).split()[-1]==normalized(name).split()[-1] and normalized(e['name'])[0]==normalized(name)[0]]
        matches=[]
        # A literal official individual-profile URL is sufficient to identify a
        # source member, only if one entity owns it. There are none in this batch.
        if len(unique_exact)==1 and all(verify.values()):
            target=next(iter(unique_exact.values()))
            matches.append({'entity_id':target['entity_id'],'basis':'exact_unique_official_profile_url'})
        candidate_people=[]
        first=normalized(name).split()[0];last=normalized(name).split()[-1]
        for p in people:
            if normalized(p['name_last']).split()[-1:]!=[last] or normalized(p['name_first']).split()[:1]!=[first]:continue
            positions=[raw_row(con,'positions',r) for r in con.execute('SELECT * FROM positions WHERE person_id=?',(p['id'],))]
            educations=[raw_row(con,'educations',r) for r in con.execute('SELECT * FROM educations WHERE person_id=?',(p['id'],))]
            courts=[raw_row(con,'courts',r) for r in con.execute('SELECT DISTINCT c.* FROM courts c JOIN positions p ON p.court_id=c.id WHERE p.person_id=?',(p['id'],))]
            school_ids={r['school_id'] for r in educations if r['school_id']}
            schools=[raw_row(con,'schools',r) for sid in sorted(school_ids) for r in con.execute('SELECT * FROM schools WHERE id=?',(sid,))]
            school_claims=[]
            for school in schools:
                for official in ref['education_as_published']:
                    # Record exact normalized institution containment only; no
                    # inferred degree/year or automatic cross-source identity merge.
                    if normalized(school['name']) in normalized(official):
                        school_claims.append({'school_id':school['id'],'courtlistener_school':school['name'],'official_claim':official,'basis':'institution_name_containment_only','degree_year_independently_matched':False})
            court_match=any(normalized(c['full_name'])==normalized(ref['court']) for c in courts)
            cp={**p,'matching_court':court_match,'schools':schools,'educations':educations,'positions':positions,'courts':courts,
                'exact_normalized_name':normalized(p['display_name'])==normalized(name),'school_correspondences':school_claims,
                'status':'candidate_cross_reference_only_not_an_accepted_entity_link',
                'limits':['Different service dates may describe different terms; no date was silently reconciled.','School correspondence does not supply a missing degree or year.','This native source person is not an existing main-entity ID.']}
            candidate_people.append(cp)
        evidence={
            'source_profile_id':key,'name_as_published':name,'official_profile_url':url,'image_url':ref['image_url'],
            'capture_path':capture.as_posix(),'capture_sha256':capture_hash,'capture_checks':verify,
            'reference_queue_path':REFS.as_posix(),'reference_queue_sha256':sha(REFS),'reference_index':ref['reference_index'],
            'entities_path':ENTITIES.as_posix(),'entities_sha256':sha(ENTITIES),
            'exact_official_url_entity_matches':[{'entity_id':e['entity_id'],'name':e['name']} for e in exact],
            'exact_name_entity_candidates':[{'entity_id':e['entity_id'],'name':e['name']} for e in named],
            'rejected_main_entity_namesakes':[{'entity_id':e['entity_id'],'name':e['name'],'state_codes':e.get('state_codes',[]),'courts':e.get('courts',[]),'reason':'No exact official URL, compatible full name, or corroborating biography bridge to the PA appellate profile.'} for e in namesakes],
            'courtlistener_candidate_records':candidate_people,
            'courtlistener_source_files':{k:source_files[k] for k in ['people','positions','educations','schools','courts']},
            'official_role_heading':ref['profile_heading_as_published'],'official_term':ref['term_as_published'],
            'education_as_published':ref['education_as_published'],'career_as_published':ref['career_as_published'],
            'accepted_main_entity_links':matches,'facial_identification_performed':False,'image_bytes_acquired':False,
        }
        evidence_path=OUT/'evidence'/(key.split(':')[1]+'.json')
        evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for match in matches:accepted.append({**match,'source_profile_id':key,'source_url':url,'image_url':ref['image_url'],'evidence_path':evidence_path.as_posix(),'evidence_sha256':sha(evidence_path),'image_bytes_verified':False})
        if not matches:unresolved.append({'source_profile_id':key,'name':name,'source_url':url,'reason':'No supported identity link to an existing main entity; do not attach to a namesake.','exact_official_url_matches':len(unique_exact),'exact_name_candidates':len(named),'courtlistener_native_candidates':[p['id'] for p in candidate_people],'evidence_path':evidence_path.as_posix(),'evidence_sha256':sha(evidence_path)})
        intersections.append({'source_profile_id':key,'name':name,'courtlistener_candidate_ids':[p['id'] for p in candidate_people],'candidates_with_matching_court':sum(p['matching_court'] for p in candidate_people),'candidates_with_institution_correspondence':sum(bool(p['school_correspondences']) for p in candidate_people),'accepted_entity_links':0})
        profile={
          'schema_version':'official-pa-judge-profile-source.v1','source_profile_id':key,'source_name':'Pennsylvania Unified Judicial System',
          'source_url':url,'canonical_identity_basis':'exact_official_individual_profile_url','entity_id':None,
          'name':name,'profile_heading_as_published':ref['profile_heading_as_published'],
          'state':'Pennsylvania','state_code':'PA','court':ref['court'],
          'term_as_published':ref['term_as_published'],'education_as_published':ref['education_as_published'],
          'career_as_published':ref['career_as_published'],'judicial_service_identity_anchors':ref['judicial_service_identity_anchors'],
          'emeritus_or_emerita_in_source_heading':bool(re.search(r'emerit[au]s?|emerita',ref['profile_heading_as_published'],re.I)),
          'current_service_verified':False,'image_reference':{'url':ref['image_url'],'alt_as_published':ref['image_alt'],'association_basis':'Adjacent image in single named official profile section','local_path':None,'image_bytes_verified':False},
          'capture':{'path':capture.as_posix(),'sha256':capture_hash,'representation':'Saved provider response JSON, not original HTTP bytes','metadata_source_url':metadata.get('sourceURL',metadata.get('url'))},
          'field_provenance':{f:{'source_url':url,'capture_path':capture.as_posix(),'capture_sha256':capture_hash,'source_reference_index':ref['reference_index']} for f in ['name','profile_heading_as_published','court','term_as_published','education_as_published','career_as_published','image_reference']},
          'identity_review_evidence_path':evidence_path.as_posix(),'identity_review_evidence_sha256':sha(evidence_path),
          'priority_order':ref['priority_order'],'priority_tier':ref['priority_tier'],
          'eligible_as_separate_source_profile':all(verify.values()),'existing_entity_merge_performed':False,
          'limitations':['Historical or emeritus publisher claims preserved as captured; no current office certification.','Portrait bytes must be acquired and validated before displaying the image.','No attachment to the existing entity corpus without a supported identity bridge.']}
        profiles.append(profile)
finally:con.close()

write_jsonl(OUT/'accepted_links.jsonl',accepted)
write_jsonl(OUT/'unresolved.jsonl',unresolved)
write_jsonl(OUT/'official_profiles.jsonl',profiles)
write_jsonl(OUT/'courtlistener_candidate_intersections.jsonl',intersections)
summary={'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'references_reviewed':len(refs),'existing_entities_reviewed':len(entities),
 'accepted_existing_entity_links':len(accepted),'unresolved_existing_entity_links':len(unresolved),
 'eligible_separate_official_source_profiles':sum(p['eligible_as_separate_source_profile'] for p in profiles),
 'profiles_with_courtlistener_native_candidates':sum(bool(r['courtlistener_candidate_ids']) for r in intersections),
 'profiles_with_education_institution_correspondence':sum(bool(r['candidates_with_institution_correspondence']) for r in intersections),
 'courtlistener_native_candidates_automatically_merged':0,'emeritus_or_emerita_source_profiles':sum(p['emeritus_or_emerita_in_source_heading'] for p in profiles),
 'new_images_downloaded':0,'network_requests':0,'source_files_modified':0,'capture_verification_errors':errors,
 'recommended_fast_path':'Acquire verified image references and publish as separate official PA source profiles; do not attach photos to existing namesakes.',
 'source_queue_sha256':sha(REFS),'entity_source_sha256':sha(ENTITIES),
 'files':{p.name:{'path':p.as_posix(),'sha256':sha(p),'bytes':p.stat().st_size} for p in [OUT/'accepted_links.jsonl',OUT/'unresolved.jsonl',OUT/'official_profiles.jsonl',OUT/'courtlistener_candidate_intersections.jsonl']}}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='files'},ensure_ascii=False,indent=2))
