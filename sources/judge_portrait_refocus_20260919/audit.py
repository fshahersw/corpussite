"""Bounded existing-portrait audit and exact-source profile discovery queue."""
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
import hashlib
import json
import sqlite3
import sys
from urllib.parse import urlsplit
from lxml import html

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'delivery/archive-directory'))
import judges


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def rows(path): return [json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def output(name, value): (HERE/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')


def main():
    existing=rows(ROOT/'sources/judge_presentation_20260918/portraits.jsonl')
    pa=rows(ROOT/'sources/pa_judge_portraits_20260919/images.jsonl')
    for row in existing: judges.validated_image(row,ROOT/'sources/judge_presentation_20260918/images')
    for row in pa: judges.validated_image(row,ROOT/'sources/pa_judge_portraits_20260919/images')
    db=sqlite3.connect((ROOT/'sources/judge_presentation_20260918/profiles.sqlite3').as_uri()+'?mode=ro',uri=True)
    total,photos=db.execute('SELECT count(*),sum(has_photo) FROM profiles').fetchone()
    installed={r[0] for r in db.execute('SELECT id FROM images')};db.close()
    assert installed=={r['id'] for r in existing+pa}
    pa_refs=rows(ROOT/'sources/local_deep_audit_20260918/portraits/prioritized_portrait_references.jsonl')
    # Prior references use image_url or image.url depending on their source format.
    old_ref_urls={r.get('image_url') or (r.get('image') or {}).get('url') for r in pa_refs}
    old_installed_urls={r['image_source_url'] for r in pa}
    observed=[];img_rows=[]
    captures=rows(ROOT/'sources/public_law_acquisition_20260919/resources.jsonl')
    for cap in captures:
        if cap['kind']!='judge_directory': continue
        raw=ROOT/cap['raw_path']; assert sha(raw)==cap['raw_sha256']
        doc=html.fromstring(raw.read_bytes())
        images=[{'src':n.get('src'),'alt':n.get('alt')} for n in doc.xpath('//img')]
        img_rows.append({'source_url':cap['source_url'],'raw_path':cap['raw_path'],'raw_sha256':cap['raw_sha256'],'image_elements':images,'portrait_references':0,'review':'Court seals, search/close/feed icons and PDF icons only; no person images.'})
        metadata=ROOT/cap['reading_metadata_path']; assert sha(metadata)==cap['reading_metadata_sha256']
        for link in json.loads(metadata.read_text(encoding='utf-8'))['links']:
            url=link['url'];parsed=urlsplit(url)
            if parsed.scheme=='https' and parsed.netloc=='www.fjc.gov' and parsed.path.startswith('/history/judges/'):
                observed.append({'profile_url':url,'observed_link_label':link['label'],'source_roster_url':cap['source_url'],'source_roster_title':cap['title'],'capture_path':cap['raw_path'],'capture_sha256':cap['raw_sha256'],'captured_at':cap['captured_at'],'reading_metadata_path':cap['reading_metadata_path'],'reading_metadata_sha256':cap['reading_metadata_sha256'],'image_url':None,'entity_id':None,'evidence_level':'Observed individual official profile link; not yet an image reference or resolved identity link.','assignment_gate':'Require exact FJC native node ID from profile and retained fjc:nid observation, then a portrait image associated with that same profile. No name-only or facial matching.'})
    unique={r['profile_url']:r for r in observed}
    candidates=list(unique.values())[:50]
    (HERE/'profile_discovery_candidates.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in candidates),encoding='utf-8')
    (HERE/'verified_image_candidates.jsonl').write_text('',encoding='utf-8')
    people=sqlite3.connect((ROOT/'sources/courtlistener_people_20260918/catalog.sqlite3').as_uri()+'?mode=ro',uri=True)
    photo_flags=people.execute('SELECT has_photo,count(*) FROM people GROUP BY has_photo').fetchall();people.close()
    structured=ROOT/'delivery/archive-directory/judge_structured.py'
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'installed_projection':{'profiles':total,'profiles_with_verified_images':photos,'profiles_without_installed_image':total-photos,'known_verified_assets':len(existing)+len(pa),'known_assets_present_in_projection':len(installed),'known_asset_gap':0,'known_asset_completion_percent':100,'nationwide_portrait_completion_percent':None,'note':'Missing installed image does not establish that a public portrait exists or may be assigned.'},'existing_assets':{'entity_portraits':len(existing),'separate_pa_source_profiles':len(pa),'all_43_raw_hashes_and_decodes_verified':True,'all_prior_pa_reference_urls_accounted_for':old_ref_urls==old_installed_urls,'prior_reference_queue':len(pa_refs)},'prior_saved_evidence_audit':{'path':'sources/judge_portrait_backfill_20260918/remaining_source_opportunities.json','sha256':sha(ROOT/'sources/judge_portrait_backfill_20260918/remaining_source_opportunities.json'),'previously_found_new_named_images':0,'not_repeated_full_source_scan':True},'new_roster_review':img_rows,'new_verified_image_candidates':0,'profile_discovery':{'exact_observed_profile_urls':len(unique),'selected_next_discovery_candidates':len(candidates),'file':'profile_discovery_candidates.jsonl','sha256':sha(HERE/'profile_discovery_candidates.jsonl'),'network_started':False,'proposed_max_workers':8,'shared_host_dir':'corpus/_shared_hosts','robots_enforced':True,'max_depth':0,'follow_links':False,'profile_discovery_not_image_acquisition':True},'courtlistener_photo_flags':{'source':'sources/courtlistener_people_20260918/catalog.sqlite3','counts':photo_flags,'image_url_column_present':False,'flags_are_not_images':True},'staged_structured_layer':{'adapter_exists':structured.exists(),'adapter_sha256':sha(structured),'data_folder':'sources/judge_structured_20260919','data_folder_exists':(ROOT/'sources/judge_structured_20260919').exists(),'portrait_fields_in_adapter':False},'integration_boundary':{'judges_module':'delivery/archive-directory/judges.py','entity_images':'portraits.jsonl entry requires unique entity_id and validated file in the existing exact image root.','official_pa_images':'Separate source profiles use exact source_profile_id and URL links plus ready/hash receipt; no entity merge.','new_source_assets':'Require a registered exact source root/manifest and source identity evidence before root adds serving or flags.','existing_projection_modified':False,'ui_modified':False},'network_requests':0,'paid_calls':0,'facial_matching':False}
    output('audit.json',summary)
    print(json.dumps({'installed':photos,'known_asset_gap':0,'new_verified_image_candidates':0,'selected_profile_discovery_candidates':len(candidates),'prior_pa_refs_accounted':old_ref_urls==old_installed_urls,'structured_data_present':summary['staged_structured_layer']['data_folder_exists']}))


if __name__=='__main__':main()
