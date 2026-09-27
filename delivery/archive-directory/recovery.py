"""Validated, additive text recovery over preserved Seeger source records."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
FOLDER=ROOT/'sources/seeger_text_recovery_20260918'
METADATA_FILES=('README.md','summary.json','resources.jsonl','validation.json','unresolved.jsonl','directory_receipt.json')

def load():
    gate=FOLDER/'validation.json'
    if not gate.is_file():return {}
    validation=json.loads(gate.read_text(encoding='utf-8-sig'))
    if validation.get('status')!='passed' or validation.get('overlay_ready') is not True:return {}
    manifest=FOLDER/'resources.jsonl'
    if hashlib.sha256(manifest.read_bytes()).hexdigest()!=validation.get('resources_sha256'):raise ValueError('Recovery manifest differs from validated release')
    result={}
    with (FOLDER/'resources.jsonl').open(encoding='utf-8-sig') as source:
        for line in source:
            r=json.loads(line)
            if r.get('status')!='recovered':continue
            path=(ROOT/r['text_path']).resolve()
            try:path.relative_to(FOLDER)
            except ValueError:raise ValueError('Recovered text is outside its evidence pass')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=r['text_sha256']:raise ValueError('Recovered text hash mismatch')
            metadata=(ROOT/r['metadata_path']).resolve()
            try:metadata.relative_to(FOLDER)
            except ValueError:raise ValueError('Recovery metadata is outside its evidence pass')
            if hashlib.sha256(metadata.read_bytes()).hexdigest()!=r['metadata_sha256']:raise ValueError('Recovery metadata hash mismatch')
            if r['id'] in result:raise ValueError('Duplicate recovery identity')
            result[r['id']]=r
    return result

def overlay(record,recoveries):
    r=recoveries.get(record.get('id'))
    if not r:return record
    if r['raw_sha256']!=(record.get('sha256') or record.get('raw_sha256')):raise ValueError('Recovery parent does not match source')
    p=dict(record);p['metadata']=dict(p.get('metadata') or {})
    p['metadata']['prior_text_representation']={k:p.get(k) for k in ['text_path','text_sha256','quality']}
    p['metadata']['text_recovery']=r
    p.update(text_path=r['text_path'],text_sha256=r['text_sha256'],quality='Recovered offline text from preserved original; see extraction method and limitations.')
    return p
