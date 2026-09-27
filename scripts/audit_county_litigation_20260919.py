"""Independent read-only integrity and association audit of county litigation publication.

This does not certify legal currency. It checks saved evidence, identifiers, source
separation, classification support and downloadable bytes before live publication.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
KINDS = {'local_rule','court_form','standing_order','filing_guidance','fee_schedule',
         'court_information','court_contact','court_staff','source_directory','unknown'}
HEX = re.compile(r'[a-f0-9]{64}')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as handle:
        for data in iter(lambda:handle.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def confined(folder, value):
    if not isinstance(value,str) or not value or value.startswith(('/', '\\')) or ':' in value:
        raise ValueError('Artifact path must be relative')
    if '..' in value.replace('\\','/').split('/'):
        raise ValueError('Artifact traversal')
    path=(folder/value).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError('Artifact missing or outside publication')
    return path


def public_url(value):
    try:
        u=urlsplit(value)
        return u.scheme in {'http','https'} and bool(u.hostname) and not u.username and not u.password
    except (TypeError,ValueError):return False


def audit(folder):
    folder=Path(folder).resolve()
    errors=[]; warnings=[]; counts=Counter(); kinds=Counter(); counties=set(); ids=set()
    def fail(code, row=None, detail=None):
        errors.append({'code':code,'id':row.get('id') if isinstance(row,dict) else None,'detail':detail})
    try:
        gate=json.loads((folder/'validation.json').read_text(encoding='utf-8-sig'))
        if gate.get('status')!='passed' or gate.get('ready') is not True:fail('publication_not_ready')
        bound={}
        for entry in gate.get('data_files',[]):
            name=entry.get('path'); expected=entry.get('sha256')
            if name in bound:fail('duplicate_gate_path',detail=name)
            try:
                path=confined(folder,name)
                if not isinstance(expected,str) or not HEX.fullmatch(expected) or digest(path)!=expected:
                    fail('gate_hash_mismatch',detail=name)
                bound[name]=expected
            except (ValueError,OSError) as exc:fail('invalid_gate_artifact',detail={'path':name,'error':str(exc)})
        if 'resources.jsonl' not in bound:fail('resource_manifest_not_bound')
        asset_index={}
        if 'artifacts.jsonl' in bound:
            for line in (folder/'artifacts.jsonl').read_text(encoding='utf-8-sig').splitlines():
                if not line.strip():continue
                item=json.loads(line);name=item.get('path');expected=item.get('sha256')
                if name in asset_index:fail('duplicate_artifact_index_path',detail=name);continue
                try:
                    path=confined(folder,name)
                    if not isinstance(expected,str) or not HEX.fullmatch(expected) or digest(path)!=expected:
                        fail('indexed_artifact_hash_mismatch',detail=name);continue
                    if item.get('bytes') is not None and item['bytes']!=path.stat().st_size:
                        fail('indexed_artifact_size_mismatch',detail=name);continue
                    asset_index[name]=item;bound[name]=expected
                except (ValueError,OSError) as exc:fail('invalid_indexed_artifact',detail=str(exc))
        rows=[json.loads(line) for line in (folder/'resources.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        baseline={r['geoid']:r for r in json.loads((ROOT/'sources/official_courts/datasets/counties_50_plus_dc.json').read_text())}
        for row in rows:
            counts['resources']+=1
            rid=row.get('id')
            if not isinstance(rid,str) or not rid or rid in ids:fail('missing_or_duplicate_id',row)
            ids.add(rid)
            if not row.get('title'):fail('missing_title',row)
            if not public_url(row.get('source_url')):fail('invalid_source_url',row)
            meta=row.get('metadata') or {}
            kind=meta.get('resource_type',row.get('resource_kind'))
            kinds[kind or 'missing']+=1
            if kind not in KINDS:fail('unknown_resource_type',row,kind)
            geoids=row.get('county_geoids') or ([row['county_fips']] if row.get('county_fips') else [])
            for geoid in geoids:
                if geoid not in baseline:fail('unknown_county_fips',row,geoid);continue
                counties.add(geoid)
                if row.get('state') not in {baseline[geoid]['state'],baseline[geoid]['usps']}:
                    fail('county_state_mismatch',row,geoid)
            applicability=meta.get('applicability') or {}
            if applicability.get('status') in {'verified','confirmed','explicit'} and not applicability.get('evidence'):
                fail('unsupported_verified_applicability',row)
            if applicability.get('level')=='statewide' and kind=='local_rule':
                warnings.append({'id':rid,'code':'statewide_rule_in_local_rule_category','county_geoids':geoids})
            authority=meta.get('source_authority') or meta.get('authority') or {}
            if authority.get('verified') is True and not authority.get('evidence'):
                fail('unsupported_verified_authority',row)
            temporal=meta.get('temporal') or {}
            for name in ('published_at','effective_at'):
                if temporal.get(name) and not temporal.get('bases'):
                    fail('date_without_source_basis',row,name)
            classification=meta.get('classification') or {}
            if kind not in {'unknown','source_directory'} and not classification.get('evidence'):
                fail('classification_without_evidence',row)
            artifacts=list(meta.get('artifacts') or [])
            if not artifacts:
                for key,hashkey,role in [('raw_path','sha256','original'),('text_path','text_sha256','text')]:
                    name=row.get(key)
                    if not name:continue
                    item=asset_index.get(name)
                    if not item or item.get('sha256')!=row.get(hashkey):
                        fail('record_asset_not_bound',row,name);continue
                    artifacts.append({**item,'role':role})
            if not artifacts:fail('no_saved_artifact_evidence',row)
            text=None
            for artifact in artifacts:
                name=artifact.get('path'); expected=artifact.get('sha256')
                if name not in bound or bound.get(name)!=expected:
                    fail('artifact_not_bound_to_gate',row,name);continue
                try:
                    path=confined(folder,name)
                    if (artifact.get('mime') or artifact.get('mime_type'))=='application/pdf':
                        with path.open('rb') as handle:head=handle.read(1024)
                        if b'%PDF-' not in head:fail('pdf_mime_bytes_mismatch',row,name)
                    if artifact.get('role') in {'text','clean_text','extracted_text'}:
                        text=path.read_text(encoding='utf-8-sig')
                    counts['artifacts']+=1
                except (OSError,ValueError,UnicodeError) as exc:fail('artifact_decode_failed',row,str(exc))
            if text is None and row.get('text_path'):
                try:text=confined(folder,row['text_path']).read_text(encoding='utf-8-sig')
                except (OSError,ValueError,UnicodeError):pass
            if text is not None and not text.strip():
                counts['empty_text_gaps']+=1
                warnings.append({'id':rid,'code':'empty_reading_copy_original_may_be_available'})
            if text:
                if re.search(r'(?i)(verify you are human|checking your browser|access denied|just a moment)',text[:400]) and len(text)<2000:
                    warnings.append({'id':rid,'code':'possible_access_error_text'})
                if kind in {'court_form','local_rule','standing_order'} and len(text.strip())<80:
                    warnings.append({'id':rid,'code':'short_document_or_ocr_gap'})
                for fact in meta.get('facts') or []:
                    excerpt=fact.get('source_excerpt')
                    if not isinstance(excerpt,str) or not excerpt.strip() or excerpt not in text:
                        fail('fact_excerpt_not_in_reading_copy',row,fact.get('field'))
                structure=meta.get('document_structure') or {}
                if structure:
                    if structure.get('text_sha256')!=hashlib.sha256(text.encode('utf-8')).hexdigest():
                        fail('structure_text_hash_mismatch',row)
                    for category in ('outline','date_observations','status_observations'):
                        for observation in structure.get(category) or []:
                            evidence=observation.get('evidence') or {}
                            start,end=evidence.get('start'),evidence.get('end')
                            if not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=len(text) or text[start:end]!=evidence.get('excerpt'):
                                fail('structure_evidence_slice_mismatch',row,category)
            counts['facts']+=len(meta.get('facts') or [])
        counts['distinct_county_fips']=len(counties)
        counts['gate_files']=len(bound)
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
        fail('audit_input_failure',detail=str(exc))
    return {'status':'passed' if not errors else 'failed','checked_at':datetime.now(timezone.utc).isoformat(),
            'counts':dict(counts),'by_resource_type':dict(kinds),'errors':errors,'warnings':warnings,
            'qualification':'Artifact/identifier checks and structural review; no certification of legal currency, completeness or governing jurisdiction.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',default=str(ROOT/'sources/county_litigation_20260919'))
    parser.add_argument('--report',default=str(ROOT/'reports/county_litigation_20260919/independent_audit.json'))
    args=parser.parse_args(); result=audit(args.folder)
    target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'counts':result['counts'],'errors':len(result['errors']),'warnings':len(result['warnings'])}))
    raise SystemExit(0 if result['status']=='passed' else 1)


if __name__=='__main__':main()
