"""Build conservative display entities from sealed judge source observations.

No network; no source mutation; no inferred current service. Existing verified
groups survive. New links require source identity or corroborating professional
evidence in compatible court/jurisdiction context. Possible matches stay apart.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, hashlib, itertools, json, re, shutil, sqlite3, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources/judge_entities_20260918'
VERSION = 'judge-display-entities-1.0.1'
STATE_NAMES = dict(zip('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split(),
    ['Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware','District of Columbia','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi','Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York','North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington','West Virginia','Wisconsin','Wyoming']))

def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(x):return hashlib.sha256(canonical(x).encode('utf-8')).hexdigest()
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def rows(path):
    with Path(path).open(encoding='utf-8-sig') as f:
        for line in f:
            if line.strip():yield json.loads(line)
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def write_rows(path,values):
    with Path(path).open('w',encoding='utf-8') as f:
        for value in values:f.write(canonical(value)+'\n')
def normal(value):return ' '.join(re.sub(r'[^\w\s-]',' ',unicodedata.normalize('NFKC',str(value or '')).casefold()).split())
def text(value):return str(value or '').replace('\\n','\n').strip()
def listing(value):return value if isinstance(value,list) else ([value] if value not in (None,'') else [])

def name_parts(value):
    value=re.sub(r'\b(?:the\s+hon\.?|honorable|hon\.?|judge|justice)\s+', '',str(value),flags=re.I)
    value=re.sub(r'\(\s*(?:ret\.?|retired)\s*\)','',value,flags=re.I)
    # Bracket-expanded given names in the FJC source (H[ezekiah]) retain letters.
    value=value.replace('[','').replace(']','')
    tokens=normal(value).split();suffix=''
    if tokens and tokens[-1] in {'jr','sr','ii','iii','iv','v'}:suffix=tokens.pop()
    return tokens,suffix

def compatible_names(a,b):
    x,sx=name_parts(a);y,sy=name_parts(b)
    if len(x)<2 or len(y)<2 or sx!=sy or x[-1]!=y[-1]:return False
    if len(x)!=len(y):return False  # Missing middle names remain candidates.
    return all(p==q or (min(len(p),len(q))==1 and p[0]==q[0]) for p,q in zip(x[:-1],y[:-1]))

def court_key(value):
    value=re.sub(r'^[A-Z]{2}\s*[-–]\s*','',text(value))
    value=normal(value).replace('united states','us')
    value=re.sub(r'\bu\s+s\b','us',value)
    # Exact structural alias, retaining the named district and state.
    m=re.fullmatch(r'us district court (.+?) (southern|northern|eastern|western|middle|central)',value)
    if m and m[1] in {normal(x) for x in STATE_NAMES.values()}:
        value='us district court for the '+m[2]+' district of '+m[1]
    return value

def specific_court(key,counties):
    if not key:return False
    if key in {'court','district court','superior court','circuit court','county court','county court at law'}:
        return bool(counties)
    return len(key.split())>=3 and ('court' in key or 'tribunal' in key)

def readable_item(value):
    if isinstance(value,str):return text(value)
    if not isinstance(value,dict):return ''
    if value.get('literal_text'):return text(value['literal_text'])
    v=value.get('as_reported',value)
    if not isinstance(v,dict):return text(v)
    if v.get('institution'):
        return ', '.join(str(v[k]) for k in ['institution','degree','credential','degree_year','year_as_reported','honors'] if v.get(k))
    if value.get('fields') and value.get('court'):
        f=value['fields'];parts=[str(value.get('appointment_title') or 'Judicial service')+' — '+value['court']]
        for k in ['Appointing President','Nomination Date','Confirmation Date','Commission Date','Service as Chief Judge, Begin','Service as Chief Judge, End','Senior Status Date','Termination','Termination Date']:
            if f.get(k):parts.append(k+': '+str(f[k]))
        return '; '.join(parts)
    if any(v.get(k) for k in ('role','court','employer','time_label')):
        return ' · '.join(str(v[k]) for k in ('role','court','employer','time_label','appointment_as_reported') if v.get(k))
    if v.get('value') and isinstance(v['value'],str):return text(v['value'])
    if v.get('text'):return text(v['text'])
    return ''

def feature(p):
    n=p.get('native_record') or {};native=n.get('native_record') or {};professional=p.get('professional_fields') or n.get('professional_fields') or {}
    courts=listing(p.get('courts') or n.get('courts'));counties=listing(p.get('counties') or n.get('counties'))
    keys={court_key(x) for x in courts if specific_court(court_key(x),counties)}
    states={str(p['state_code'])} if p.get('state_code') else set()
    state_basis='source_explicit' if states else 'not_recorded'
    if not states:
        for key in keys:
            for code,name in STATE_NAMES.items():
                if key.endswith(' district of '+normal(name)) or key.endswith(' district court for the district of '+normal(name)):
                    states.add(code)
        if states:state_basis='literal_federal_court_geographic_label_not_current_service'
    profile=n.get('profile_url') or (p.get('source_url') if str(p.get('source_url','')).startswith('https://trellis.law/judge/') else None)
    stable_ids=set()
    if profile:stable_ids.add('trellis-profile:'+profile.rstrip('/'))
    if n.get('fjc_nid'):stable_ids.add('fjc-nid:'+str(n['fjc_nid']))
    biography='\n\n'.join(readable_item(x) for x in listing(n.get('biography') or p.get('biography')) if readable_item(x))
    evaluation=n.get('native',{}).get('evaluation_narrative')
    if not biography and evaluation:biography=text(evaluation)
    edu=listing(n.get('education') or professional.get('education'))
    appointments=listing(n.get('appointments'))
    service=listing(n.get('service'))+listing(professional.get('judicial_experience'))
    education_keys=set()
    for value in edu:
        if isinstance(value,dict):
            item=value.get('as_reported',value);year=item.get('degree_year') or item.get('year_as_reported');institution=item.get('institution')
        else:
            m=re.match(r'^[^,]+,\s*(.+?)\s+-\s*((?:18|19|20)\d{2})$',str(value));institution,year=(m[1],m[2]) if m else (None,None)
        if institution and year and re.fullmatch(r'(?:18|19|20)\d{2}',str(year)):
            education_keys.add((normal(institution),str(year)))
    contacts=collections.defaultdict(set)
    for item in listing(n.get('professional_contacts')):
        if isinstance(item,dict) and isinstance(item.get('value'),str):
            if item.get('field')=='court_email':contacts['email'].add(item['value'].strip().casefold())
            if item.get('field')=='professional_phone':contacts['phone'].add(re.sub(r'\D','',item['value']))
    dates=set();service_years=set()
    for item in appointments:
        if not isinstance(item,dict):continue
        f=item.get('fields',{})
        for k in ['Nomination Date','Confirmation Date','Commission Date','Senior Status Date']:
            if f.get(k) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(f[k])):dates.add((court_key(item.get('court','')),str(f[k])))
        if f.get('Commission Date'):service_years.add((court_key(item.get('court','')),str(f['Commission Date'])[:4]))
    for item in listing(professional.get('judicial_experience')):
        a=item.get('as_reported',{});m=re.match(r'((?:18|19|20)\d{2})\s*-',a.get('time_label') or '')
        if m and a.get('role')=='Judge':service_years.add((court_key(a.get('court','')),m[1]))
    # Exact dates as printed in a biographical passage; no year-only narrative
    # event inference. The other side must supply structured dated service.
    narrative_dates=set()
    for m in re.finditer(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b',biography):
        context=biography[max(0,m.start()-150):min(len(biography),m.end()+70)]
        if re.search(r'nominat|appoint|confirm|senior status|chief judge',context,re.I):
            try:narrative_dates.add(dt.datetime.strptime(' '.join(m.groups()),'%B %d %Y').date().isoformat())
            except ValueError:pass
    births={str(v) for data in (p,n,native) for k,v in data.items() if k.casefold() in {'date of birth','birth date','dob','birth_date'} and v}
    return {'states':states,'state_basis':state_basis,'court_keys':keys,'courts':courts,'counties':counties,'stable_ids':stable_ids,'profile':profile,
        'fjc_nid':str(n.get('fjc_nid') or ''),'births':births,'biography':biography,'education':edu,'education_keys':education_keys,
        'appointments':appointments,'service':service,'professional_career':n.get('professional_career') or '',
        'contacts':contacts,'dated_service':dates,'service_years':service_years,'narrative_dates':narrative_dates,
        'source_status':n.get('status') or p.get('current_status')}

def conflicts(a,b):
    x,y=a['features'],b['features'];reasons=[]
    if not compatible_names(a['name'],b['name']):reasons.append('name_components_or_suffix_conflict')
    if x['states'] and y['states'] and not x['states']&y['states']:reasons.append('jurisdiction_conflict')
    if a['judge_system'] and b['judge_system'] and a['judge_system']!=b['judge_system']:reasons.append('court_system_conflict')
    if x['births'] and y['births'] and not x['births']&y['births']:reasons.append('birth_date_conflict')
    if x['fjc_nid'] and y['fjc_nid'] and x['fjc_nid']!=y['fjc_nid']:reasons.append('different_fjc_person_ids')
    if x['profile'] and y['profile'] and x['profile'].rstrip('/')!=y['profile'].rstrip('/'):reasons.append('competing_publisher_profile_ids')
    if x['court_keys'] and y['court_keys'] and not x['court_keys']&y['court_keys']:reasons.append('different_reported_courts_no_history_bridge')
    return reasons

def decide(a,b):
    x,y=a['features'],b['features'];why=conflicts(a,b)
    if why:return False,why,{}
    shared_courts=x['court_keys']&y['court_keys'];shared_states=x['states']&y['states']
    evidence={'shared_court_keys':sorted(shared_courts),'shared_state_codes':sorted(shared_states)}
    if not shared_courts:return False,['missing_specific_shared_court'],evidence
    if not shared_states:return False,['missing_shared_jurisdiction'],evidence
    # Generic local court labels need an identical county too.
    generic={'court','district court','superior court','circuit court','county court','county court at law'}
    if shared_courts<=generic and not set(map(normal,x['counties']))&set(map(normal,y['counties'])):
        return False,['generic_court_without_same_county'],evidence
    ids=x['stable_ids']&y['stable_ids']
    if ids:return True,['same_publisher_or_native_person_id'],{**evidence,'shared_person_ids':sorted(ids)}
    exact=normal(a['name'])==normal(b['name'])
    common_edu=x['education_keys']&y['education_keys']
    exact_dates={date for court,date in x['dated_service'] if court in shared_courts and date in y['narrative_dates']} | {date for court,date in y['dated_service'] if court in shared_courts and date in x['narrative_dates']}
    years=x['service_years']&y['service_years']
    if exact_dates or (len(common_edu)>=2) or (common_edu and (exact or years)):
        return True,['compatible_name_court_jurisdiction_and_professional_evidence'],{**evidence,'shared_education_institution_year':sorted(common_edu),'shared_exact_service_dates':sorted(exact_dates),'shared_structured_service_years':sorted(years)}
    if exact and x['contacts']['email']&y['contacts']['email'] and x['contacts']['phone']&y['contacts']['phone']:
        return True,['same_full_name_court_and_two_professional_contact_fields'],{**evidence,'shared_professional_email':sorted(x['contacts']['email']&y['contacts']['email']),'shared_professional_phone':sorted(x['contacts']['phone']&y['contacts']['phone'])}
    if x['births']&y['births']:
        return True,['compatible_name_court_jurisdiction_and_birth_date'],{**evidence,'shared_birth_dates':sorted(x['births']&y['births'])}
    return False,['insufficient_independent_identity_evidence'],evidence

def member_key(dataset,observation_id):return dataset+'|'+observation_id

def resolve(observations):
    bykey={o['member_key']:o for o in observations};parent={k:k for k in bykey};groups={k:{k} for k in bykey}
    def find(k):
        while parent[k]!=k:k=parent[k]
        return k
    def union(a,b):
        a,b=find(a),find(b)
        if a==b:return
        a,b=sorted((a,b));parent[b]=a;groups[a]|=groups.pop(b)
    prior=collections.defaultdict(list)
    for o in observations:
        if o.get('existing_judge_id'):prior[o['dataset']+'|'+o['existing_judge_id']].append(o['member_key'])
    decisions=[]
    for key,members in sorted(prior.items()):
        for other in sorted(members)[1:]:
            first=sorted(members)[0];union(first,other)
            decisions.append({'decision':'linked_existing_validated_group','members':[first,other],'basis':['existing_sealed_identity_group'],'evidence':{'source_identity_group':key}})
    blocks=collections.defaultdict(list)
    for o in observations:
        tokens,suffix=name_parts(o['name'])
        if len(tokens)>=2:blocks[(tokens[-1],tokens[0][0])].append(o['member_key'])
    for block,keys in sorted(blocks.items()):
        for akey,bkey in itertools.combinations(sorted(keys),2):
            if find(akey)==find(bkey):continue
            a,b=bykey[akey],bykey[bkey]
            # Broad block permits suffix/middle-name conflicts to remain visible,
            # but unrelated given names are not useful possible-match suggestions.
            an,_=name_parts(a['name']);bn,_=name_parts(b['name'])
            if an[0]!=bn[0] and min(len(an[0]),len(bn[0]))>1:continue
            accepted,why,evidence=decide(a,b)
            if accepted:
                incompatibilities=[{'members':[x,y],'reasons':conflicts(bykey[x],bykey[y])} for x in sorted(groups[find(akey)]) for y in sorted(groups[find(bkey)]) if conflicts(bykey[x],bykey[y])]
                if incompatibilities:accepted=False;why=['would_bridge_conflicting_group_members'];evidence['group_conflicts']=incompatibilities
            decisions.append({'decision':'linked_new_evidence' if accepted else 'possible_match_not_linked','members':[akey,bkey],'basis':why,'evidence':evidence})
            if accepted:union(akey,bkey)
    result=[sorted(v) for _,v in sorted(groups.items())]
    mapping={k:'judge-entity-'+digest(keys)[:24] for keys in result for k in keys}
    for decision in decisions:
        decision['decision_id']='identity-decision-'+digest(decision)[:24]
        decision['member_evidence']=[{'member_key':key,**bykey[key]['source_anchor']} for key in decision['members']]
        decision['final_entity_ids']=[mapping[key] for key in decision['members']]
    return result,mapping,decisions

def validated_snapshot(folder,required,inputs):
    pointer=ROOT/folder/'latest.json';p=read(pointer);base=(ROOT/p['snapshot_path']).resolve();base.relative_to(ROOT/folder/'snapshots')
    manifest=base/'files.sha256.json'
    if sha(manifest)!=p['manifest_sha256']:raise ValueError('Snapshot manifest hash mismatch')
    inventory=read(manifest)
    for path in [pointer,manifest,*[base/name for name in required]]:
        rel=path.relative_to(ROOT).as_posix();actual=sha(path)
        if path not in (pointer,manifest) and inventory.get(rel,{}).get('sha256')!=actual:raise ValueError('Consumed sealed file hash mismatch: '+rel)
        inputs[rel]={'sha256':actual,'bytes':path.stat().st_size}
    return base

def load_sources():
    inputs={};observations=[];facts=[];analyses=[]
    base=validated_snapshot('delivery/judge_enrichment_20260914',['judge_corpus.sqlite3','components/identity/match_decisions.jsonl'],inputs)
    vendor=validated_snapshot('delivery/judge_vendor_enrichment_20260914',['observations.jsonl','facts.jsonl','analyses.jsonl'],inputs)
    database=base/'judge_corpus.sqlite3';rel=database.relative_to(ROOT).as_posix()
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    for row in c.execute('SELECT * FROM observations ORDER BY observation_id'):
        p=json.loads(row['payload']);key=member_key('judge_enrichment',row['observation_id'])
        observations.append({'member_key':key,'dataset':'judge_enrichment','source_observation_id':row['observation_id'],'existing_judge_id':row['judge_id'],'name':row['name'],'source_class':row['source_class'],'judge_system':row['judge_system'],'payload':p,
            'source_anchor':{'database':rel,'database_sha256':inputs[rel]['sha256'],'table':'observations','row_id':row['observation_id'],'payload_sha256':hashlib.sha256(row['payload'].encode()).hexdigest(),'source_url':p.get('source_url'),'captured_at':p.get('captured_at'),'source_evidence':p.get('evidence')}})
    for table,target,idfield in [('facts',facts,'fact_id'),('analyses',analyses,'analysis_id')]:
        for row in c.execute('SELECT * FROM '+table+' ORDER BY '+idfield):
            target.append({'dataset':'judge_enrichment','member_key':member_key('judge_enrichment',row['observation_id']),'original_id':row[idfield],'payload':json.loads(row['payload']),
                'input_evidence':{'database':rel,'database_sha256':inputs[rel]['sha256'],'table':table,'row_id':row[idfield],'payload_sha256':hashlib.sha256(row['payload'].encode()).hexdigest()}})
    c.close()
    for number,p in enumerate(rows(vendor/'observations.jsonl'),1):
        key=member_key('judge_vendor',p['source_observation_id']);path=(vendor/'observations.jsonl').relative_to(ROOT).as_posix()
        observations.append({'member_key':key,'dataset':'judge_vendor','source_observation_id':p['source_observation_id'],'existing_judge_id':None,'name':p['name'],'source_class':p['source_class'],'judge_system':p.get('judge_system'),'payload':p,
            'source_anchor':{'file':path,'file_sha256':inputs[path]['sha256'],'line':number,'payload_canonical_sha256':digest(p),'source_url':p.get('source_url'),'captured_at':p.get('captured_at'),'source_evidence':{'source_path':p.get('source_path'),'source_sha256':p.get('source_sha256')}}})
    for filename,target,idfield in [('facts.jsonl',facts,'fact_id'),('analyses.jsonl',analyses,'analysis_id')]:
        path=(vendor/filename).relative_to(ROOT).as_posix()
        for number,p in enumerate(rows(vendor/filename),1):
            target.append({'dataset':'judge_vendor','member_key':member_key('judge_vendor',p['source_observation_id']),'original_id':p[idfield],'payload':p,'input_evidence':{'file':path,'file_sha256':inputs[path]['sha256'],'line':number,'payload_canonical_sha256':digest(p)}})
    for o in observations:o['features']=feature(o['payload'])
    return observations,facts,analyses,inputs

def profile_score(o):
    f=o['features'];rich=bool(f['biography'])
    return (rich,min(len(f['biography']),30000),len(f['education'])+len(f['appointments'])+len(f['service']),o['source_class'] in {'official_observation','federal_biographies'},o['member_key'])

def create_entities(observations,groups,mapping,decisions,facts,analyses):
    bykey={o['member_key']:o for o in observations};byfact=collections.defaultdict(list);byanalysis=collections.defaultdict(list);bydecision=collections.defaultdict(list)
    for record in facts:byfact[mapping[record['member_key']]].append(record)
    for record in analyses:byanalysis[mapping[record['member_key']]].append(record)
    for decision in decisions:
        if decision['decision'].startswith('linked'):
            for entity in set(decision['final_entity_ids']):bydecision[entity].append(decision)
    entities=[];members=[]
    for keys in groups:
        entity_id=mapping[keys[0]];obs=[bykey[k] for k in keys];ordered=sorted(obs,key=profile_score,reverse=True);best=ordered[0]
        state_codes=sorted(set().union(*(o['features']['states'] for o in obs)))
        fields=collections.defaultdict(list)
        for o in ordered:
            f=o['features']
            for field,values in [('biography',listing(f['biography'])),('education',f['education']),('appointments',f['appointments']),('service',f['service']),('professional_career',listing(f['professional_career']))]:
                for value in values:
                    rendered=readable_item(value)
                    if not rendered:continue
                    fields[field].append({'value':rendered,'member_key':o['member_key'],'source_observation_id':o['source_observation_id'],'dataset':o['dataset'],'source_url':o['payload'].get('source_url'),'captured_at':o['payload'].get('captured_at'),'source_field':field,'value_as_reported':value,'source_anchor':o['source_anchor']})
        def unique(field):return list(dict.fromkeys(v['value'] for v in fields[field]))
        biography=fields['biography'][0]['value'] if fields['biography'] else ''
        aliases=[{'name':name,'member_keys':[o['member_key'] for o in obs if o['name']==name]} for name in sorted({o['name'] for o in obs})]
        # A structured FJC full name is preferred to an initial variant only
        # within a proven group; the richer narrative remains best_profile.
        name=next((o['name'] for o in ordered if o['source_class']=='federal_biographies'),best['name'])
        courts=list(dict.fromkeys(c for o in ordered for c in o['features']['courts']));counties=list(dict.fromkeys(c for o in ordered for c in o['features']['counties']))
        member_rows=[]
        for o in obs:
            m={'entity_id':entity_id,'member_key':o['member_key'],'dataset':o['dataset'],'source_observation_id':o['source_observation_id'],'original_judge_id':o['existing_judge_id'],'name':o['name'],'source_class':o['source_class'],'source_url':o['payload'].get('source_url'),'captured_at':o['payload'].get('captured_at'),'source_anchor':o['source_anchor'],'is_best_profile':o is best}
            members.append(m);member_rows.append({k:m[k] for k in ['member_key','dataset','source_observation_id','name','source_class','source_url','captured_at']})
        display_analyses=[]
        for a in byanalysis[entity_id]:
            p=a['payload'];source=bykey[a['member_key']]
            # Retain every reported measure/context field. In particular,
            # outcomes and limitations distinguish total/granted/denied counts.
            # Only the redundant nested source copy is omitted here; it remains
            # unchanged in analyses.jsonl's complete original payload.
            display_analyses.append({k:v for k,v in p.items() if k!='native_record'}|{'dataset':a['dataset'],'source_observation_id':source['source_observation_id'],'member_key':a['member_key'],'captured_at':source['payload'].get('captured_at'),'source_url':source['payload'].get('source_url'),'source_anchor':a['input_evidence']})
        confidence='source_identity_only' if len(keys)==1 else ('professional_evidence_linked' if any(d['decision']=='linked_new_evidence' and d['basis']!=['same_publisher_or_native_person_id'] for d in bydecision[entity_id]) else 'publisher_or_prior_confirmed_identity')
        lines=[name]
        if courts:lines+=['Reported courts: '+ '; '.join(courts)]
        if state_codes:lines+=['Jurisdiction labels: '+ '; '.join(STATE_NAMES.get(s,s) for s in state_codes)]
        lines+=['Current judicial service has not been independently verified.']
        if biography:lines+=['',biography]
        for field,label in [('education','Education'),('appointments','Appointments and historical service'),('service','Other reported service'),('professional_career','Professional career')]:
            values=unique(field)
            if values:lines+=['',label,*['• '+x for x in values]]
        lines+=['',str(len(keys))+' source observation'+('s' if len(keys)!=1 else '')+' retained; '+str(len(byfact[entity_id]))+' source fact claims; '+str(len(display_analyses))+' source analysis records.']
        if display_analyses:lines+=['Analysis describes its source program/corpus and period; it is not a national performance score.']
        entities.append({'entity_id':entity_id,'name':name,'aliases':aliases,'state_codes':state_codes,'state_labels':[STATE_NAMES.get(s,s) for s in state_codes],'courts':courts,'counties':counties,
            'judge_systems':sorted({o['judge_system'] for o in obs if o['judge_system']}),'identity_status':confidence,'confidence_basis':[d['decision_id'] for d in bydecision[entity_id]],'member_count':len(keys),'members':member_rows,'source_observation_ids':[o['source_observation_id'] for o in obs],
            'best_profile_member':best['member_key'],'best_profile_source_url':best['payload'].get('source_url'),'biography':biography,'education':unique('education'),'appointments':unique('appointments'),'service':unique('service'),'professional_career':unique('professional_career'),'profile_text':'\n'.join(lines),
            'field_provenance':dict(fields)|{'name':[{'value':o['name'],'member_key':o['member_key'],'source_anchor':o['source_anchor']} for o in obs],'jurisdiction':[{'states':sorted(o['features']['states']),'basis':o['features']['state_basis'],'courts_as_reported':o['features']['courts'],'member_key':o['member_key'],'source_anchor':o['source_anchor']} for o in obs]},
            'fact_ids':[a['dataset']+'|'+a['original_id'] for a in byfact[entity_id]],'analysis_ids':[a['dataset']+'|'+a['original_id'] for a in byanalysis[entity_id]],'analyses':display_analyses,'current_service_verified':False,
            'source_status_observations':[{'status':o['features']['source_status'],'member_key':o['member_key']} for o in obs if o['features']['source_status']],
            'limitations':['Internal source-evidence grouping, not a government person identifier or census.','All source observations and historical/conflicting claims remain separate; current office is not inferred.','Display selects a rich source representation; selection does not establish superior legal authority.']})
    return sorted(entities,key=lambda e:(normal(e['name']),e['entity_id'])),sorted(members,key=lambda m:m['member_key'])

def build():
    OUT.mkdir(parents=True,exist_ok=True);stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');folder=OUT/'snapshots'/stamp;folder.mkdir(parents=True)
    observations,facts,analyses,inputs=load_sources();groups,mapping,decisions=resolve(observations)
    entities,members=create_entities(observations,groups,mapping,decisions,facts,analyses)
    for records in (facts,analyses):
        for record in records:record['entity_id']=mapping[record['member_key']]
    possible=[d for d in decisions if d['decision']=='possible_match_not_linked' and len(set(d['final_entity_ids']))>1]
    conflicts_out=[d for d in possible if any('conflict' in reason or 'different_' in reason or 'competing_' in reason for reason in d['basis'])]
    exports={'entities.jsonl':entities,'members.jsonl':members,'facts.jsonl':facts,'analyses.jsonl':analyses,'match_decisions.jsonl':decisions,'possible_matches.jsonl':possible,'conflicts.jsonl':conflicts_out}
    for name,records in exports.items():write_rows(folder/name,records)
    ids={o['member_key'] for o in observations};errors=[]
    if len(ids)!=len(observations) or {m['member_key'] for m in members}!=ids or len(members)!=len(ids):errors.append('membership_not_exactly_once')
    if any(r['member_key'] not in ids for r in facts+analyses):errors.append('orphan_source_claim')
    if sum(len(e['analysis_ids']) for e in entities)!=len(analyses):errors.append('analysis_loss_or_duplication')
    if sum(len(e['fact_ids']) for e in entities)!=len(facts):errors.append('fact_loss_or_duplication')
    if any(e['current_service_verified'] for e in entities):errors.append('unsupported_current_service_claim')
    for name,evidence in inputs.items():
        if sha(ROOT/name)!=evidence['sha256']:errors.append('source_changed_during_build:'+name)
    for decision in decisions:
        if decision['decision']=='linked_new_evidence':
            index={o['member_key']:o for o in observations};a,b=(index[k] for k in decision['members'])
            if not decide(a,b)[0]:errors.append('new_link_not_reproducible:'+decision['decision_id'])
    validation={'validated':not errors,'errors':errors,'source_observations_preserved':len(members),'facts_preserved':len(facts),'analyses_preserved':len(analyses),'source_files_hash_verified_before_and_after':len(inputs),'new_links_replayed':sum(d['decision']=='linked_new_evidence' for d in decisions),'network_requests':0,'originals_modified':False}
    write(folder/'validation.json',validation)
    if errors:raise RuntimeError('Judge entity validation failed: '+repr(errors))
    prior_groups=len({o['existing_judge_id'] for o in observations if o['dataset']=='judge_enrichment'})+sum(o['dataset']=='judge_vendor' for o in observations)
    summary={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'version':VERSION,'source_observations':len(observations),'prior_identity_groups_plus_vendor_observations':prior_groups,'entities':len(entities),'display_rows_reduced_from_source_observations':len(observations)-len(entities),'additional_groups_consolidated':prior_groups-len(entities),'multi_observation_entities':sum(e['member_count']>1 for e in entities),'entities_with_biography':sum(bool(e['biography']) for e in entities),'source_fact_claims':len(facts),'source_analysis_records':len(analyses),'possible_match_pairs_unmerged':len(possible),'conflicting_match_pairs_unmerged':len(conflicts_out),'decisions_by_basis':dict(collections.Counter(';'.join(d['basis']) for d in decisions)),'unique_current_judges':None,'national_identity_resolution_complete':False,'source_observations_by_class':dict(collections.Counter(o['source_class'] for o in observations)),'snapshot_path':folder.relative_to(ROOT).as_posix()}
    write(folder/'summary.json',summary)
    method={'version':VERSION,'rules':['Preserve sealed confirmed identity groups.','New identity links require compatible name components/suffix, specific court and shared jurisdiction; retain explicit court label evidence for normalization.','Same publisher profile/native person ID can link compatible observations, including initial variants.','Other new links require matching dated judicial service, education institution/year or two professional contact fields in the same full-name/court context.','Exact surname/given-initial blocks generate candidates only; name-only matches never merge.','Check all prospective group members for conflicting names, suffixes, DOB/native IDs, competing publisher profile IDs and incompatible courts before joining.','Missing middle names, ambiguous jurisdictions, historical court changes and unsupported current service are not inferred.'],'field_selection':'Rich source biography first; remaining structured fields retain every source claim. FJC full names are preferred only within an already linked entity.','date_currency':'Capture, service and evaluation dates remain source-specific. Present is a publisher literal; no current-office claim is derived.','input_files':inputs,'script_sha256':sha(Path(__file__))}
    write(folder/'provenance.json',method)
    (folder/'README.md').write_text('# Judge display entities\n\n'+f"{len(entities):,} conservative display groups from {len(observations):,} preserved observations. This is not a count of unique current judges.\n\n"+'Use entities.jsonl for human-readable profiles and structured display fields; members.jsonl maps every original observation exactly once. facts.jsonl and analyses.jsonl preserve source claims with input anchors. match_decisions.jsonl records every accepted link; possible_matches.jsonl and conflicts.jsonl preserve unresolved candidates. Original snapshots are unchanged.\n',encoding='utf-8')
    manifest={p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()};write(folder/'files.sha256.json',manifest)
    # Convenience copies are published only after validation; immutable snapshots
    # retain each earlier derivation. No raw source or upstream package is edited.
    for path in folder.iterdir():
        if path.is_file():
            temporary=OUT/(path.name+'.ready');shutil.copyfile(path,temporary);temporary.replace(OUT/path.name)
    write(OUT/'latest.json',{'snapshot_path':folder.relative_to(ROOT).as_posix(),'manifest_sha256':sha(folder/'files.sha256.json')})
    print(json.dumps(summary,indent=2));return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.parse_args();build()
