"""Correct bounded title-hint errors from saved document evidence; preserve prior release."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
def sha(data): return hashlib.sha256(data).hexdigest()
def write(path, value): path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
def jsonl(path, rows): path.write_bytes(b''.join(json.dumps(r,ensure_ascii=False).encode('utf-8')+b'\n' for r in rows))

rows = [json.loads(s) for s in (OUT/'resources.jsonl').read_text(encoding='utf-8').splitlines()]
targets = [r for r in rows if r['metadata'].get('retrieval_eligible') is False]
assert len(targets) == 11, 'Expected the original, unreviewed eleven-record classification packet'
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
snapshot = OUT/'snapshots'/('before_administrative_review_'+stamp); snapshot.mkdir(parents=True)
frozen = {}
for name in ['resources.jsonl','originals.jsonl','summary.json','validation.json','progress.json']:
    data = (OUT/name).read_bytes(); (snapshot/name).write_bytes(data)
    frozen[name] = {'sha256':sha(data),'bytes':len(data),'snapshot_path':str((snapshot/name).relative_to(ROOT)).replace('\\','/')}
for row in targets:
    original = ROOT/row['metadata_path']; copy = snapshot/'metadata'/original.name; copy.parent.mkdir(exist_ok=True)
    data = original.read_bytes(); copy.write_bytes(data)
    frozen[row['metadata_path']] = {'sha256':sha(data),'bytes':len(data),'snapshot_path':str(copy.relative_to(ROOT)).replace('\\','/')}
write(snapshot/'receipt.json', {'frozen_at':stamp,'purpose':'Preserve released manifest and eleven pre-review metadata sidecars; original evidence files are unchanged.','files':frozen})

reviews = []
for row in targets:
    title = row['title']; meta = row['metadata']; text = (ROOT/row['text_path']).read_bytes().decode('utf-8')
    assert sha(text.encode('utf-8')) == row['text_sha256']
    assert sha((ROOT/row['raw_path']).read_bytes()) == row['sha256']
    if 'Transcript Purchase Order' in title:
        actual = 'transcript_order_form'; eligible = True
        assert 'TRANSCRIPT PURCHASE ORDER' in text and ('appeal' in text.lower() or 'appell' in text.lower())
        reason = 'Appellate transcript ordering form tied to court procedure, not a procurement or personnel form.'
    elif title == 'Motion for Recruitment of Counsel':
        actual = 'motion_for_recruitment_of_counsel'; eligible = True
        assert 'MOTION FOR RECRUITMENT OF COUNSEL' in text
        reason = 'Litigant motion asking the court to recruit counsel in the pending case.'
    elif title.startswith('1114 Verified Statement in Support of Employment Application'):
        actual = 'bankruptcy_professional_employment_statement'; eligible = True
        assert '11 U.S.C.' in text and 'chapter 11' in text.lower()
        reason = 'Bankruptcy filing concerning approval of professional employment under the Bankruptcy Code, not a job application.'
    elif title == 'Form 8 Protective Order in Procurement Protest Cases':
        actual = 'protective_order_form'; eligible = True
        assert 'PROTECTIVE ORDER' in text and 'litigation' in text.lower()
        reason = 'Court protective-order form for procurement-protest litigation; procurement is the case subject.'
    elif title == 'Employment Application Form':
        actual = 'court_employment_application'; eligible = False
        assert 'APPLICATION FOR EMPLOYMENT' in text and 'examination process' in text
        reason = 'Judiciary of Guam personnel application evaluated as part of an employment examination.'
    elif title == 'Application for Judicial Vacancy':
        actual = 'judicial_vacancy_application'; eligible = False
        assert 'APPLICATION FOR JUDICIAL VACANCY' in text
        reason = 'Application to fill a judicial vacancy, preserved in the administrative/review tier.'
    elif title.startswith('Recruitment Notice for Criminal Justice Act'):
        actual = 'court_panel_recruitment_notice'; eligible = False
        assert 'RECRUITMENT NOTICE FOR CRIMINAL JUSTICE ACT' in text
        reason = 'Notice recruiting attorneys to a court appointment/mentorship panel, preserved as an administrative reference.'
    else: raise AssertionError(title)
    kind = 'court_form_or_other_document' if eligible else 'administrative_document'
    review = {'reviewed_at':datetime.now(timezone.utc).isoformat(),'id':row['id'],'sha256':row['sha256'],
        'text_sha256':row['text_sha256'],'raw_path':row['raw_path'],'text_path':row['text_path'],
        'title':title,'inferred_document_type':actual,'resource_kind':kind,'retrieval_eligible':eligible,
        'basis':'Reviewed inferred document type from preserved original/extracted text; not an authority or applicability determination.',
        'reason':reason,'evidence':{'text_start_char':0,'text_end_char':min(700,len(text)),'literal':text[:700]},
        'prior_kind':row['kind'],'prior_retrieval_eligible':False,'legal_currency_verified':False}
    reviews.append(review)
    sidecar_path = ROOT/row['metadata_path']; sidecar = json.loads(sidecar_path.read_bytes())
    sidecar['classification_review'] = review; write(sidecar_path,sidecar)
    row['kind'] = row['resource_kind'] = kind
    row['metadata_sha256'] = sha(sidecar_path.read_bytes())
    meta.update(retrieval_eligible=eligible,kind_basis=review['basis'],classification_review=review,
        source_evidence_sha256=row['metadata_sha256'])

jsonl(OUT/'classification_review.jsonl',reviews)
jsonl(OUT/'resources.jsonl',rows)
jsonl(OUT/'originals.jsonl',[r for r in rows if r['metadata']['record_type']=='original_document'])
summary = json.loads((OUT/'summary.json').read_bytes())
summary.update(by_kind=dict(Counter(r['kind'] for r in rows)),
    resources_sha256=sha((OUT/'resources.jsonl').read_bytes()), originals_sha256=sha((OUT/'originals.jsonl').read_bytes()),
    classification_review={'reviewed':len(reviews),'corrected_false_positive_administrative_labels':sum(r['retrieval_eligible'] for r in reviews),
        'remaining_administrative_references':sum(not r['retrieval_eligible'] for r in reviews),'prior_release_snapshot':str(snapshot.relative_to(ROOT)).replace('\\','/'),
        'path':'sources/seeger_import_20260918/classification_review.jsonl','sha256':sha((OUT/'classification_review.jsonl').read_bytes()),
        'classification_is_inferred_not_legal_authority':True})
write(OUT/'summary.json',summary)
write(OUT/'progress.json',{'status':'classification_corrected_pending_revalidation',**summary})
print(json.dumps(summary['classification_review'],indent=2))
