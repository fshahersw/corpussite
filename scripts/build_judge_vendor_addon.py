"""Stage a source-specific vendor add-on; publish only after exact independent review."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'delivery/judge_vendor_enrichment_20260914'
VERSION = 'vendor-addon-1.0.0'
COMPONENTS = ('context', 'lex_machina')
CLASSES = ('vendor_published', 'public_ui_preview', 'historical_illustration')
SUBJECTS = ('judge', 'court', 'vendor_product', 'report')
CODE = [Path(__file__), ROOT/'scripts/query_judge_vendor_addon.py']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def hash_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

def space(value):
    return ' '.join(value.split())

def safe(value, within=ROOT):
    if not isinstance(value, str) or not value:
        raise ValueError('Expected nonempty local path')
    path = (ROOT/value).resolve()
    path.relative_to(within.resolve())
    if not path.is_file():
        raise ValueError('Missing local file: '+value)
    return path

def rel(path):
    return path.resolve().relative_to(ROOT).as_posix()

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def rows(path):
    if not path.exists(): return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')

def write_rows(path, values):
    path.write_text(''.join(compact(v)+'\n' for v in values), encoding='utf-8', newline='\n')

def required(row, keys):
    for key in keys:
        if key not in row or row[key] is None or row[key] == '':
            raise ValueError('Required field missing: '+key)

def canonical(component, native, kind=None):
    if not isinstance(native, str) or not native.strip() or any(ord(c)<32 for c in native):
        raise ValueError('Invalid native identifier')
    prefix='vendor:'+component+(':'+kind if kind else '')+':'
    return prefix+quote(native, safe='-_.~')

def url(value):
    if not isinstance(value, str) or urlsplit(value).scheme not in ('https','http') or not urlsplit(value).netloc:
        raise ValueError('Expected public HTTP(S) source URL')

class InertText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','svg','noscript','template'): self.hidden+=1
        if tag in ('p','div','br','li','tr','td','th') and not self.hidden: self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag in ('script','style','svg','noscript','template'): self.hidden=max(0,self.hidden-1)
        if tag in ('p','div','li','tr','td','th') and not self.hidden: self.parts.append(' ')
    def handle_data(self, value):
        if not self.hidden: self.parts.append(value)

def plain(html):
    parser=InertText(); parser.feed(html); return space(' '.join(parser.parts))

def pointer(data, path):
    if path == '': return data
    if not isinstance(path,str) or not path.startswith('/'): raise ValueError('Invalid JSON pointer')
    for part in path[1:].split('/'):
        key=part.replace('~1','/').replace('~0','~')
        data=data[int(key)] if isinstance(data,list) else data[key]
    return data

def has_literal_value(value, text):
    """No arithmetic: normalized scalars must actually occur in cited text."""
    if value is None: return True
    if isinstance(value,bool): return True  # Source booleans are not converted into counts.
    if isinstance(value,(int,float)):
        if not math.isfinite(value): return False
        numbers=re.findall(r'(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?![\w.])',text)
        return any(float(token.replace(',',''))==value for token in numbers)
    if isinstance(value,str): return space(value).casefold() in space(text).casefold()
    if isinstance(value,list): return all(has_literal_value(v,text) for v in value)
    if isinstance(value,dict): return all(has_literal_value(v,text) for v in value.values())
    return False

class EvidenceValidator:
    def __init__(self, root=ROOT):
        self.originals={}; self.text_cache={}; self.checked=0
    def original(self, path, digest):
        if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):
            raise ValueError('Invalid SHA-256 receipt')
        actual=safe(path)
        if path in self.originals and self.originals[path] != digest: raise ValueError('Conflicting source digests')
        if sha(actual)!=digest: raise ValueError('Source SHA mismatch: '+path)
        self.originals[path]=digest
        return actual
    def scan_receipts(self, value):
        if isinstance(value,list):
            for row in value: self.scan_receipts(row)
        elif isinstance(value,dict):
            for pk,hk in [('source_path','source_sha256'),('raw_path','raw_sha256'),('text_path','text_sha256'),('metadata_path','metadata_sha256'),('artifact_path','artifact_sha256')]:
                if bool(value.get(pk)) != bool(value.get(hk)): raise ValueError('One-sided receipt: '+pk)
                if value.get(pk): self.original(value[pk],value[hk])
            for row in value.values():
                if isinstance(row,(dict,list)): self.scan_receipts(row)
    def validate(self, value, owned):
        evidence=value if isinstance(value,list) else [value]
        if not evidence or not all(isinstance(e,dict) for e in evidence): raise ValueError('Missing literal evidence')
        literals=[]
        for ev in evidence:
            required(ev,['source_path','source_sha256'])
            if owned.get(ev['source_path'])!=ev['source_sha256']: raise ValueError('Evidence original not owned by observation')
            path=self.original(ev['source_path'],ev['source_sha256'])
            self.scan_receipts(ev)
            key=(ev['source_path'],ev.get('json_pointer'),ev.get('embedded_json_pointer'),ev.get('pdf_page'))
            if key not in self.text_cache:
                if ev.get('pdf_page') is not None:
                    if 'json_pointer' in ev: raise ValueError('Conflicting text locators')
                    from pypdf import PdfReader
                    reader=PdfReader(path); page=ev['pdf_page']
                    if isinstance(page,bool) or not isinstance(page,int) or not 1<=page<=len(reader.pages): raise ValueError('Invalid PDF page')
                    text=reader.pages[page-1].extract_text()
                elif 'json_pointer' in ev:
                    text=pointer(read(path),ev['json_pointer'])
                    if 'embedded_json_pointer' in ev:
                        if not isinstance(text,str):raise ValueError('Embedded JSON parent is not a string')
                        text=pointer(json.loads(text),ev['embedded_json_pointer'])
                else:
                    if 'embedded_json_pointer' in ev:raise ValueError('Embedded JSON requires an outer pointer')
                    text=path.read_text(encoding='utf-8-sig')
                if not isinstance(text,str): raise ValueError('Evidence locator does not select a string')
                self.text_cache[key]=text
            text=self.text_cache[key]
            html_mode='html_character_start' in ev or 'html_character_end' in ev
            start=ev.get('html_character_start') if html_mode else ev.get('start')
            end=ev.get('html_character_end') if html_mode else ev.get('end')
            if isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=len(text):
                raise ValueError('Invalid literal evidence offsets')
            fragment=text[start:end]
            if html_mode:
                required(ev,['html_fragment_sha256','literal_text'])
                if hash_text(fragment)!=ev['html_fragment_sha256']: raise ValueError('HTML fragment hash mismatch')
                if plain(fragment)!=space(ev['literal_text']): raise ValueError('HTML literal text mismatch')
                if ev.get('literal_text_sha256') and hash_text(ev['literal_text'])!=ev['literal_text_sha256']: raise ValueError('Literal text hash mismatch')
                if ev.get('raw_byte_start') is not None or ev.get('raw_byte_end') is not None:
                    if 'json_pointer' in ev or ev.get('pdf_page') is not None: raise ValueError('Raw-byte spans require raw HTML')
                    bstart,bend=ev.get('raw_byte_start'),ev.get('raw_byte_end')
                    if isinstance(bstart,bool) or isinstance(bend,bool) or not isinstance(bstart,int) or not isinstance(bend,int) or not 0<=bstart<bend<=path.stat().st_size:
                        raise ValueError('Invalid raw HTML byte span')
                    raw_fragment=path.read_bytes()[bstart:bend]
                    normalized_raw=raw_fragment.decode('utf-8').replace('\r\n','\n').replace('\r','\n')
                    if normalized_raw!=fragment:
                        raise ValueError('Raw HTML byte span mismatch')
                    if ev.get('raw_fragment_sha256') and hashlib.sha256(raw_fragment).hexdigest()!=ev['raw_fragment_sha256']:
                        raise ValueError('Raw HTML byte fragment hash mismatch')
                literal=ev['literal_text']
            else:
                if not isinstance(ev.get('quote'),str) or fragment!=ev['quote']: raise ValueError('Literal source slice mismatch')
                literal=ev['quote']
                if ev.get('evidence_format')=='literal_html_fragment':
                    required(ev,['fragment_sha256','literal_text'])
                    if hash_text(fragment)!=ev['fragment_sha256']:raise ValueError('Declared HTML fragment hash mismatch')
                    if plain(fragment)!=space(ev['literal_text']):raise ValueError('Declared HTML literal text mismatch')
                    literal=ev['literal_text']
            literals.append(literal); self.checked+=1
        return '\n'.join(literals)

def owned_sources(observation, validator):
    sources=[observation]+(observation.get('additional_sources') or [])
    owned={}
    for row in sources:
        required(row,['source_path','source_sha256','source_url']);url(row['source_url'])
        validator.original(row['source_path'],row['source_sha256'])
        if row['source_path'] in owned and owned[row['source_path']]!=row['source_sha256']: raise ValueError('Conflicting owned source')
        owned[row['source_path']]=row['source_sha256']
    return owned

def validate_analysis(row, observation, validator, owned):
    required(row,['analysis_id','source_observation_id','analysis_type','metric','label','unit','metric_subject_type'])
    if 'value' not in row: raise ValueError('Missing analysis value')
    if row['metric_subject_type']!=observation['subject_type']: raise ValueError('Metric subject mismatch; no court metric on a judge')
    if row.get('record_class',observation['record_class'])!=observation['record_class']: raise ValueError('Analysis class promotion forbidden')
    if row.get('source_reported') is not True or row.get('independently_computed') not in (None,False): raise ValueError('Computed vendor metrics forbidden')
    if row.get('calculation') or row.get('formula'): raise ValueError('Computed outcome rates forbidden')
    text=validator.validate(row.get('evidence'),owned)
    if not has_literal_value(row['value'],text): raise ValueError('Analysis value absent from literal evidence')
    for key in ['numerator','denominator','scale_minimum','scale_maximum']:
        val=row.get(key)
        if val is not None and (isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or not has_literal_value(val,text)):
            raise ValueError('Unsupported '+key+'; keep unobserved context null')
    if row.get('scale_maximum') is not None and row.get('denominator') is not None:
        raise ValueError('Scale ratings use scale_maximum; respondent denominator must remain null')
    if row.get('methodology_url'): url(row['methodology_url'])
    return text

def export_csv(path, values, fields):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in values:
            record={}
            for key in fields:
                val=row.get(key)
                if isinstance(val,(list,dict)): val=compact(val)
                if isinstance(val,str) and val.startswith(('=','+','-','@')): val="'"+val
                record[key]=val
            writer.writerow(record)

def build(components):
    validator=EvidenceValidator(); observations=[];facts=[];analyses=[];candidates=[];inputs={};names_evidence={}
    code_hashes={rel(p):sha(p) for p in CODE+[BASE/'SCHEMA.md']}
    for component in components:
        if component not in COMPONENTS: raise ValueError('Unexpected component')
        folder=BASE/component; receipt=read(folder/'validation.json')
        if receipt.get('validated') is not True: raise ValueError('Component not validated: '+component)
        bindings=receipt.get('output_sha256') or {}
        required_files=['observations.jsonl','facts.jsonl','analyses.jsonl']
        if (folder/'candidates.jsonl').exists(): required_files.append('candidates.jsonl')
        for name in required_files:
            if not bindings.get(name) or sha(folder/name)!=bindings[name]: raise ValueError('Unbound or changed component file: '+component+'/'+name)
        for name,digest in bindings.items():
            original=safe(rel(folder/name),folder)
            if sha(original)!=digest: raise ValueError('Component digest mismatch: '+name)
            inputs[rel(original)]=digest
        inputs[rel(folder/'validation.json')]=sha(folder/'validation.json')
        validator.scan_receipts(receipt)
        for path,digest in (receipt.get('original_sha256') or {}).items(): validator.original(path,digest)
        local={};owners={};native_ids=set()
        for raw in rows(folder/'observations.jsonl'):
            required(raw,['source_observation_id','name','subject_type','record_class','source_url','source_path','source_sha256','captured_at'])
            if raw['subject_type'] not in SUBJECTS or raw['record_class'] not in CLASSES: raise ValueError('Invalid observation type/class')
            if raw.get('current_service_verified') is True or raw.get('identity_merge_performed') is True: raise ValueError('Vendor observation cannot certify identity/current service')
            if not isinstance(raw['captured_at'],str): raise ValueError('Capture time must be an ISO timestamp')
            dt.datetime.fromisoformat(raw['captured_at'].replace('Z','+00:00'))
            native=raw['source_observation_id']
            if native in local: raise ValueError('Duplicate component observation')
            oid=canonical(component,raw.get('native_id',native))
            if oid in native_ids: raise ValueError('Canonical observation collision')
            native_ids.add(oid);owned=owned_sources(raw,validator);validator.scan_receipts(raw)
            record={**raw,'source_observation_id':oid,'native_source_observation_id':native,'component':component,
                    'current_service_verified':False,'identity_resolution':'source_specific_no_cross_source_merge',
                    'identity_merge_performed':False,'native_record':raw}
            for field in ['state_code','courts','counties','judge_system','source_published_at']: record.setdefault(field,None)
            local[native]=record;owners[native]=owned;observations.append(record);names_evidence[oid]=[]
            if raw.get('evidence'):names_evidence[oid].append(validator.validate(raw['evidence'],owned))
            for match in raw.get('candidate_matches') or []:
                if match.get('match_status') not in (None,'candidate_only','unresolved'): raise ValueError('External identity match cannot be confirmed here')
                candidates.append({'candidate_id':canonical(component,native+':'+str(len(candidates)),'candidate'),
                    'source_observation_id':oid,'record_class':'candidate','match_status':'candidate_only',
                    'identity_merge_performed':False,'native_record':match})
        fact_ids=set();analysis_ids=set()
        for raw in rows(folder/'facts.jsonl'):
            required(raw,['fact_id','source_observation_id','field'])
            if raw['source_observation_id'] not in local: raise ValueError('Fact has no component-owned observation')
            if raw['fact_id'] in fact_ids: raise ValueError('Duplicate native fact ID')
            fact_ids.add(raw['fact_id']);obs=local[raw['source_observation_id']]
            literal=validator.validate(raw.get('evidence'),owners[raw['source_observation_id']]);validator.scan_receipts(raw)
            if not has_literal_value(raw.get('value'),literal): raise ValueError('Fact value absent from evidence: '+raw['field'])
            names_evidence[obs['source_observation_id']].append(literal)
            facts.append({**raw,'fact_id':canonical(component,raw['fact_id'],'fact'),'source_observation_id':obs['source_observation_id'],
                          'component':component,'record_class':obs['record_class'],'claim_scope':'source_reported','native_record':raw})
        for raw in rows(folder/'analyses.jsonl'):
            if raw.get('source_observation_id') not in local: raise ValueError('Analysis has no component-owned observation')
            obs=local[raw['source_observation_id']]
            literal=validate_analysis(raw,obs,validator,owners[raw['source_observation_id']]);validator.scan_receipts(raw)
            if raw['analysis_id'] in analysis_ids: raise ValueError('Duplicate native analysis ID')
            analysis_ids.add(raw['analysis_id']);names_evidence[obs['source_observation_id']].append(literal)
            record={**raw,'analysis_id':canonical(component,raw['analysis_id'],'analysis'),
                    'source_observation_id':obs['source_observation_id'],'component':component,
                    'record_class':obs['record_class'],'subject_type':obs['subject_type'],
                    'source_reported':True,'independently_computed':False,'native_record':raw}
            for field in ['period','cohort','numerator','denominator','methodology_url','scale_minimum','scale_maximum']:
                record.setdefault(field,None)
            analyses.append(record)
        for raw in rows(folder/'candidates.jsonl'):
            required(raw,['candidate_id','reason']);url(raw.get('url',raw.get('source_url')))
            if raw.get('match_status') not in (None,'candidate_only','unresolved'): raise ValueError('Confirmed candidate identity forbidden')
            if raw.get('source_observation_id') and raw['source_observation_id'] not in local: raise ValueError('Candidate claims foreign observation')
            validator.scan_receipts(raw)
            candidates.append({**raw,'candidate_id':canonical(component,raw['candidate_id'],'candidate'),
                'source_observation_id':local[raw['source_observation_id']]['source_observation_id'] if raw.get('source_observation_id') else None,
                'component':component,'record_class':'candidate','match_status':'candidate_only','identity_merge_performed':False,'native_record':raw})
    if not observations: raise ValueError('No validated source observations to stage')
    for obs in observations:
        if not has_literal_value(obs['name'],'\n'.join(names_evidence[obs['source_observation_id']])):
            raise ValueError('Observation name absent from its own verified evidence: '+obs['name'])
    ids=[o['source_observation_id'] for o in observations]
    if len(ids)!=len(set(ids)): raise ValueError('Cross-component canonical collision')
    snap=BASE/'snapshots'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snap.mkdir(parents=True,exist_ok=False);(snap/'originals').mkdir();(snap/'pipeline').mkdir()
    frozen={}
    for original,digest in validator.originals.items():
        source=safe(original);dest=snap/'originals'/(digest+source.suffix.lower())
        if not dest.exists():shutil.copyfile(source,dest)
        if sha(source)!=digest or sha(dest)!=digest: raise ValueError('Original changed while freezing')
        frozen[original]={'sha256':digest,'snapshot_path':rel(dest),'bytes':source.stat().st_size}
    for original,digest in inputs.items():
        dest=snap/'components'/Path(original).relative_to(BASE.relative_to(ROOT));dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(safe(original),dest)
        if sha(dest)!=digest or sha(safe(original))!=digest:raise ValueError('Component changed during freeze')
    for path in CODE:shutil.copyfile(path,snap/'pipeline'/path.name)
    shutil.copyfile(BASE/'SCHEMA.md',snap/'SCHEMA.md')
    fact_groups={oid:[] for oid in ids};analysis_groups={oid:[] for oid in ids}
    for f in facts:fact_groups[f['source_observation_id']].append(f)
    for a in analyses:analysis_groups[a['source_observation_id']].append(a)
    reports=[{'source_observation_id':o['source_observation_id'],'name':o['name'],'record_class':o['record_class'],
              'observation':o,'facts':fact_groups[o['source_observation_id']],
              'analysis_groups':{kind:[a for a in analysis_groups[o['source_observation_id']] if a['record_class']==kind] for kind in CLASSES},
              'identity_merge_performed':False} for o in observations]
    for name,data in [('observations',observations),('facts',facts),('analyses',analyses),('candidates',candidates),('report_rows',reports)]:
        write_rows(snap/(name+'.jsonl'),data)
    published=[a for a in analyses if a['record_class']=='vendor_published' and a['subject_type']=='judge']
    previews=[a for a in analyses if a['record_class']=='public_ui_preview']
    historical=[a for a in analyses if a['record_class']=='historical_illustration']
    context=[a for a in analyses if a['subject_type']!='judge']
    for name,data in [('vendor_published_judge_measures',published),('public_ui_preview_measures',previews),('historical_illustrations',historical),('non_judge_context_measures',context)]:write_rows(snap/(name+'.jsonl'),data)
    export_csv(snap/'observations.csv',observations,['source_observation_id','name','component','record_class','subject_type','state_code','courts','counties','judge_system','source_url','source_path','source_sha256','captured_at'])
    export_csv(snap/'facts.csv',facts,['fact_id','source_observation_id','component','record_class','field','value','evidence'])
    export_csv(snap/'analyses.csv',analyses,['analysis_id','source_observation_id','component','record_class','subject_type','analysis_type','metric','label','value','unit','period','cohort','numerator','denominator','scale_minimum','scale_maximum','methodology_url','source_reported','evidence'])
    con=sqlite3.connect(snap/'vendor_addon.sqlite');con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''
    CREATE TABLE observations(id TEXT PRIMARY KEY,name TEXT NOT NULL,component TEXT NOT NULL,record_class TEXT NOT NULL,subject_type TEXT NOT NULL,record_json TEXT NOT NULL);
    CREATE TABLE facts(id TEXT PRIMARY KEY,observation_id TEXT NOT NULL REFERENCES observations(id),field TEXT NOT NULL,record_json TEXT NOT NULL);
    CREATE TABLE analyses(id TEXT PRIMARY KEY,observation_id TEXT NOT NULL REFERENCES observations(id),record_class TEXT NOT NULL,subject_type TEXT NOT NULL,metric TEXT NOT NULL,value_json TEXT,record_json TEXT NOT NULL);
    CREATE TABLE candidates(id TEXT PRIMARY KEY,observation_id TEXT REFERENCES observations(id),record_json TEXT NOT NULL);
    CREATE VIEW vendor_published_judge_measures AS SELECT * FROM analyses WHERE record_class='vendor_published' AND subject_type='judge';
    CREATE VIEW public_ui_preview_measures AS SELECT * FROM analyses WHERE record_class='public_ui_preview';
    CREATE VIEW historical_illustrations AS SELECT * FROM analyses WHERE record_class='historical_illustration';
    CREATE VIEW non_judge_context_measures AS SELECT * FROM analyses WHERE subject_type!='judge';
    CREATE INDEX observations_name ON observations(name);
    CREATE INDEX fact_observation ON facts(observation_id);
    CREATE INDEX analysis_observation ON analyses(observation_id);
    ''')
    con.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?)',[(o['source_observation_id'],o['name'],o['component'],o['record_class'],o['subject_type'],compact(o)) for o in observations])
    con.executemany('INSERT INTO facts VALUES (?,?,?,?)',[(f['fact_id'],f['source_observation_id'],f['field'],compact(f)) for f in facts])
    con.executemany('INSERT INTO analyses VALUES (?,?,?,?,?,?,?)',[(a['analysis_id'],a['source_observation_id'],a['record_class'],a['subject_type'],a['metric'],compact(a['value']),compact(a)) for a in analyses])
    con.executemany('INSERT INTO candidates VALUES (?,?,?)',[(c['candidate_id'],c.get('source_observation_id'),compact(c)) for c in candidates])
    con.commit();assert not con.execute('PRAGMA foreign_key_check').fetchall();assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for table,data,key in [('observations',observations,'source_observation_id'),('facts',facts,'fact_id'),('analyses',analyses,'analysis_id'),('candidates',candidates,'candidate_id')]:
        got={r[0]:json.loads(r[1]) for r in con.execute('SELECT id,record_json FROM '+table)}
        if got!={row[key]:row for row in data}:raise ValueError('SQLite JSON projection mismatch')
    con.close()
    summary={'builder_version':VERSION,'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'snapshot_path':rel(snap),
        'components':list(components),'observations':len(observations),'facts':len(facts),'analyses':len(analyses),'candidates':len(candidates),
        'observation_record_classes':dict(Counter(o['record_class'] for o in observations)),
        'analysis_record_classes':dict(Counter(a['record_class'] for a in analyses)),
        'vendor_published_judge_measures':len(published),'public_ui_preview_measures':len(previews),'historical_illustrations':len(historical),
        'non_judge_context_measures':len(context),'identity_merges':0,'independently_computed_outcome_rates':0,
        'source_originals':len(frozen),'literal_evidence_checks':validator.checked,'network_requests':0,'published':False}
    write(snap/'summary.json',summary);write(snap/'verified_originals.json',frozen);write(snap/'component_inputs.json',inputs)
    validation={'validated':True,'snapshot_path':rel(snap),'literal_evidence_checks':validator.checked,'original_hash_checks':len(frozen),
        'observation_ownership_checked':True,'metric_subjects_checked':True,'record_classes_not_promoted':True,
        'missing_context_preserved_null':True,'identity_merges':0,'computed_outcome_rates':0,'sqlite_integrity':'ok','sqlite_projection_matches_jsonl':True,
        'errors':[],'pipeline_sha256':code_hashes,'output_sha256':{name:sha(snap/name) for name in ['observations.jsonl','facts.jsonl','analyses.jsonl','candidates.jsonl','vendor_addon.sqlite','summary.json']}}
    write(snap/'validation.json',validation)
    (snap/'README.md').write_text('# Source-specific judge vendor add-on\n\n'+f"This staged add-on contains {len(observations)} source observations, {len(facts)} facts and {len(analyses)} analyses. It has {len(published)} vendor-published judge measures, {len(previews)} public UI preview measures and {len(historical)} historical illustrations. These classes are separate files and SQLite views. No identities are merged with the existing corpus; external matches and undownloaded reports remain candidates.\n\n"+
        'All native records, source URLs, capture dates, literal evidence and original-byte hashes survive in JSONL and SQLite. Original citations are copied into originals/ with an original-to-copy map in verified_originals.json. Facts and analyses can cite only originals explicitly owned by their source observation. Unknown cohort, method, period and counts remain null; scale maxima are separate from respondent counts. No outcome rate is independently calculated. Historical and preview data do not establish current judge performance.\n\n'+
        'Query by name with the bundled Python runtime: `python pipeline/query_judge_vendor_addon.py --database vendor_addon.sqlite --name Sabraw`. The query emits one report per source observation and separates record classes. Use --id for an exact canonical observation. No updates or network requests occur.\n\n'+
        'This snapshot is reviewable but not published. The builder requires a separate independent-review receipt bound to its exact manifest before updating latest.json. The previous sealed judge corpus is untouched. See SCHEMA.md, validation.json, component inputs and files.sha256.json.\n',encoding='utf-8',newline='\n')
    for path,digest in code_hashes.items():
        if sha(safe(path))!=digest:raise ValueError('Pipeline changed during build')
    manifest={rel(path):{'sha256':sha(path),'bytes':path.stat().st_size} for path in sorted(snap.rglob('*')) if path.is_file()}
    write(snap/'files.sha256.json',manifest)
    return summary

def publish(snapshot, review_path):
    snap=snapshot.resolve();snap.relative_to((BASE/'snapshots').resolve())
    receipt=read(review_path);manifest=snap/'files.sha256.json'
    if receipt.get('validated') is not True or receipt.get('unresolved_material_findings')!=0:raise ValueError('Independent review has not passed')
    if receipt.get('snapshot_path')!=rel(snap) or receipt.get('manifest_sha256')!=sha(manifest):raise ValueError('Review does not bind this exact snapshot')
    reviewed_code=receipt.get('reviewed_code_sha256') or {}
    for path in CODE+[BASE/'SCHEMA.md']:
        if reviewed_code.get(rel(path))!=sha(path):raise ValueError('Independent review does not bind current code/schema')
    for name,item in read(manifest).items():
        path=safe(name,snap)
        if sha(path)!=item['sha256']:raise ValueError('Reviewed snapshot changed')
    for name,item in read(snap/'verified_originals.json').items():
        if sha(safe(name))!=item['sha256'] or sha(safe(item['snapshot_path'],snap))!=item['sha256']:raise ValueError('Reviewed original changed')
    for name,digest in read(snap/'component_inputs.json').items():
        if sha(safe(name))!=digest:raise ValueError('Reviewed component input changed')
    for path in CODE:
        if sha(path)!=sha(snap/'pipeline'/path.name):raise ValueError('Reviewed pipeline changed')
    if sha(BASE/'SCHEMA.md')!=sha(snap/'SCHEMA.md'):raise ValueError('Reviewed schema changed')
    reviews=BASE/'reviews';reviews.mkdir(exist_ok=True);sealed=reviews/(snap.name+'.json')
    if sealed.exists() and sha(sealed)!=sha(review_path):raise ValueError('Different immutable review already exists')
    if not sealed.exists():shutil.copyfile(review_path,sealed)
    pointer_value={'snapshot_path':rel(snap),'manifest_sha256':sha(manifest),'independent_review_path':rel(sealed),'independent_review_sha256':sha(sealed)}
    tmp=BASE/'latest.json.tmp';write(tmp,pointer_value);tmp.replace(BASE/'latest.json')
    return {'published':rel(snap),'independent_review_verified':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--components',nargs='+',choices=COMPONENTS,default=list(COMPONENTS))
    parser.add_argument('--publish-staged',type=Path)
    parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    if args.publish_staged:
        if not args.review:parser.error('--review is required for publication')
        result=publish(args.publish_staged,args.review)
    else:result=build(args.components)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
