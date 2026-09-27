"""Promote the reviewed portrait links without changing source identities."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'sources/judge_portrait_backfill_20260918'
TARGET=ROOT/'sources/judge_presentation_20260918'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def rows(path):return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def main():
    validation=read(SOURCE/'validation.json')
    assert validation.get('ready_for_root_integration') is True
    summary=read(SOURCE/'summary.json')
    accepted_path=SOURCE/'accepted_links.jsonl';projected_path=SOURCE/'portraits.append.jsonl'
    assert sha(accepted_path)==summary['accepted_manifest_sha256']
    assert sha(projected_path)==summary['integration_manifest_sha256']
    accepted={row['entity_id']:row for row in rows(accepted_path)}
    projected=rows(projected_path)
    assert len(projected)==len(accepted)==summary['new_evidence_supported_links']
    manifest=TARGET/'portraits.jsonl';original=manifest.read_bytes()
    prior=rows(manifest);existing={row['entity_id']:row for row in prior}
    staged=[]
    for row in projected:
        evidence=accepted[row['entity_id']]
        source=(ROOT/evidence['verified_copy_path']).resolve()
        destination=(ROOT/row['path']).resolve()
        assert source.is_relative_to((SOURCE/'images').resolve())
        assert destination.is_relative_to((TARGET/'images').resolve())
        assert row['path']==evidence['proposed_presentation_path']
        assert row['sha256']==evidence['sha256']==sha(source)
        identity=(ROOT/evidence['identity_evidence_path']).resolve()
        assert identity.is_relative_to((SOURCE/'evidence').resolve())
        assert sha(identity)==evidence['identity_evidence_sha256']==row['provenance']['identity_evidence_sha256']
        if row['entity_id'] in existing:
            assert existing[row['entity_id']]==row
            continue
        if destination.exists():assert sha(destination)==row['sha256']
        staged.append((source,destination,row))
    backup=SOURCE/'prior_presentation_portraits.jsonl'
    if not backup.exists():backup.write_bytes(original)
    for source,destination,row in staged:
        destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists():shutil.copyfile(source,destination)
        assert sha(destination)==row['sha256']
        existing[row['entity_id']]=row
    if staged:
        temporary=TARGET/'portraits.building.jsonl'
        temporary.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in existing.values()),encoding='utf-8')
        temporary.replace(manifest)
    receipt={'integrated_at':datetime.now(timezone.utc).isoformat(),'new_links':len(staged),'total_portraits':len(existing),
             'manifest_sha256':sha(manifest),'prior_manifest_sha256':sha(backup),'entity_records_modified':False,
             'external_originals_modified':False,'accepted_links_sha256':sha(accepted_path)}
    (SOURCE/'integration_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
