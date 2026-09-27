"""Check the discovery handoff and local artifact references, without source scans."""
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime,timezone
import hashlib,json,re,tomllib
OUT=Path(__file__).resolve().parent
def read(p):return json.loads((OUT/p).read_text(encoding='utf8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
report=read('report.json'); county=read('county_registry_summary.json')
judge_validation=read('judges/validation.json');law_validation=read('counties/validation.json')
portrait=read('portraits/portrait_reference_review.json');performance=read('performance/report.json')
photo_flags=[json.loads(s) for s in (OUT/'judges/courtlistener_photo_flag_candidates.jsonl').read_text().splitlines() if s.strip()]
portraits=[json.loads(s) for s in (OUT/'portraits/prioritized_portrait_references.jsonl').read_text().splitlines() if s.strip()]
class Parser(HTMLParser):
 def __init__(self):super().__init__();self.hrefs=[];self.cards=0;self.ids=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='a':self.hrefs.append(a.get('href',''))
  if tag=='article':self.cards+=1
  if 'id' in a:self.ids.append(a['id'])
parser=Parser();parser.feed((OUT/'INDEX.html').read_text(encoding='utf8'))
missing=[x for x in parser.hrefs if not x.startswith(('http:','https:','#')) and not (OUT/x).is_file()]
automation=tomllib.loads(Path('C:/Users/firas/.codex/automations/continue-official-legal-corpus/automation.toml').read_text(encoding='utf8'))
checks={
 'judge_audit_valid':judge_validation['passed'],
 'county_audit_valid':law_validation['status']=='passed',
 'judge_report_hash_matches':all(sha(OUT.parents[1]/r['path'])==r['sha256'] for r in judge_validation['artifacts']),
 'county_report_hash_matches':sha(OUT/'counties/report.json')==law_validation['report_sha256'],
 'portrait_review_passed':portrait['status']=='passed' and portrait['all_capture_hashes_verified'],
 'portrait_queue_29':len(portraits)==29,
 'photo_flag_queue_1230':len(photo_flags)==1230,
 'county_registry_374':sum(s['county_rows'] for s in county['states'].values())==374,
 'registry_167_hash_matches_no_mismatches':county['hash_matches']==167 and not county['hash_mismatches'],
 'missing_evidence_explicit':len(county['missing_evidence'])==1,
 'performance_tests_passed':performance['status']=='passed' and performance['tests']['passed']==27,
 'discovery_cards_10':parser.cards==len(report['findings'])==10,
 'report_filter_controls_present':all(s in parser.ids for s in ['q','category','count']),
 'local_html_links_resolve':not missing,
 'no_new_import_claim':report['changes']['new_source_imports']==0 and report['changes']['new_installed_portraits']==0,
 'automation_preserved_schedule_and_thread':automation['status']=='ACTIVE' and automation['rrule']=='FREQ=MINUTELY;INTERVAL=30' and automation['target_thread_id']=='01a099a9-1253-75c2-b4f8-b128eb3ba7b3',
 'automation_reads_new_handoff':'sources/local_deep_audit_20260918/NEXT_RUN.md' in automation['prompt'],
 'larger_bounded_profile_trial':'up to 10 ordinary sequential' in automation['prompt'],
}
files=['report.json','README.md','INDEX.html','NEXT_RUN.md','county_registry_summary.json','connector_candidates.json','additional_analysis_summary.json','judges/report.json','counties/report.json','portraits/prioritized_portrait_references.jsonl','performance/report.json']
result={'verified_at':datetime.now(timezone.utc).isoformat(),'passed':all(checks.values()),'checks':checks,
 'scope':'Audit handoff integrity, reviewed queue counts, static HTML links, and persisted continuation configuration; not a new nationwide data or legal-currentness certification.',
 'root_code_verification':{'title_refresh_tests_passed':12,'live_cli_status':'unchanged','live_cli_seconds':0.37883,'initial_test_command_correction':'Two guessed nonexistent module names were removed; the confirmed title-refresh module passed independently.'},
 'artifacts':[{'path':str((OUT/p).relative_to(OUT.parents[1])),'sha256':sha(OUT/p),'bytes':(OUT/p).stat().st_size} for p in files],
 'automation':{'id':automation['id'],'status':automation['status'],'prompt_sha256':hashlib.sha256(automation['prompt'].encode()).hexdigest()},'missing_html_links':missing}
(OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps({'passed':result['passed'],'checks':checks},indent=2))
raise SystemExit(0 if result['passed'] else 1)
