"""Read-only validation of the published bulk catalog against its retained Parquets."""
from __future__ import annotations
import datetime, hashlib, json, pathlib, sqlite3, time
import pyarrow.parquet as pq

HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def main():
    ready=json.loads((HERE/'ready.json').read_text(encoding='utf-8'))
    summary=json.loads((HERE/'import_summary.json').read_text(encoding='utf-8'))
    assert ready['ready'] is True and ready['sqlite_quick_check']=='ok'
    assert ready['fts_external_content_integrity_checked'] is True
    db=sqlite3.connect('file:'+str(HERE/'catalog.sqlite3').replace('\\','/')+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    total=db.execute('SELECT count(*) FROM records').fetchone()[0]
    files=db.execute('SELECT count(*),sum(rows) FROM files').fetchone()
    assert total==ready['indexed_rows']==summary['indexed_rows']==2978617
    assert tuple(files)==(229,total)
    expected_files=dict(db.execute('SELECT id,rows FROM files'))
    actual_files=dict(db.execute('SELECT file_id,count(*) FROM records GROUP BY file_id'))
    assert actual_files==expected_files
    metadata_gaps={'blank_source_id_rows':db.execute("SELECT count(*) FROM records WHERE source_id='' OR source_id IS NULL").fetchone()[0],
                   'blank_citation_rows':db.execute("SELECT count(*) FROM records WHERE citation='' OR citation IS NULL").fetchone()[0]}
    expected_ny={'statutes':40140,'constitutions':204,'court_rules':1088,'guidance':1199}
    actual_ny=dict(db.execute("SELECT kind,count(*) FROM records WHERE state='NY' GROUP BY kind"))
    assert actual_ny==expected_ny
    bindings=[]
    for filename in ['us_ny_constitutions.parquet','us_ny_court_rules.parquet','us_ny_statutes.parquet','us_federal_statutes.parquet','us_ca_statutes.parquet']:
        record=db.execute('SELECT r.*,f.path,f.sha256 AS file_sha256 FROM records r JOIN files f ON f.id=r.file_id WHERE r.file_id=? AND r.row_index=0',(filename,)).fetchone()
        assert record is not None
        path=(ROOT/record['path']).resolve()
        assert path.is_relative_to((HERE/'snapshot').resolve())
        original=next(pq.ParquetFile(path,memory_map=True,pre_buffer=False).iter_batches(batch_size=1,use_threads=False)).to_pylist()[0]
        assert record['text']==original['text']
        assert record['source_id']==(original['act_id'] or '')
        original_url=original['source_url'] or ''
        assert record['source_url']==(original_url if original_url.strip() else '')
        assert hashlib.sha256(record['text'].encode()).hexdigest()==record['content_hash']
        payload=json.loads(record['payload'])
        assert set(payload)==set(original)-{'text'}
        assert payload=={k:v for k,v in original.items() if k!='text'}
        bindings.append({'id':record['id'],'file_id':filename,'row_index':0,'source_url':record['source_url'],'content_hash':record['content_hash'],'text_characters':len(record['text']),'exact_parquet_text_and_metadata_match':True})
    queries=[]
    for query,state in [('"due process"','NY'),('discrimination','NY'),('"personal jurisdiction"','CA')]:
        start=time.monotonic()
        rows=db.execute('SELECT r.id,r.title,r.state,r.kind,r.source_url,r.file_id,length(r.text) AS text_characters FROM records_fts JOIN records r ON r.rowid=records_fts.rowid WHERE records_fts MATCH ? AND r.state=? LIMIT 3',(query,state)).fetchall()
        assert rows and all(r['state']==state and r['text_characters']>0 and r['id'].startswith('oul:') for r in rows)
        queries.append({'query':query,'state':state,'seconds':round(time.monotonic()-start,3),'sample_results':[dict(r) for r in rows]})
    barrier_screen=[]
    for phrase in ['"checking your browser"','"enable javascript"','"verify you are human"']:
        rows=db.execute('SELECT r.id,r.file_id,r.title,substr(r.text,1,800) AS text_preview FROM records_fts JOIN records r ON r.rowid=records_fts.rowid WHERE records_fts MATCH ? AND length(r.text)<2000 LIMIT 20',(phrase,)).fetchall()
        barrier_screen.append({'phrase':phrase,'short_text_sample_matches':[dict(r) for r in rows],'sample_limit':20,'match_requires_manual_review':True})
    report={'validated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':True,'indexed_rows':total,'verified_files':files[0],'all_file_row_counts_reconciled':True,'catalog_metadata_gaps':metadata_gaps,'ny_rows_by_kind':actual_ny,'parquet_binding_checks':bindings,'full_text_queries':queries,'short_text_barrier_phrase_screen':barrier_screen,'limitations':['Five original-row bindings are a sample, not a full semantic legal-text audit.','SQLite and full-text integrity were checked by the importer before publication.','Rows are publisher exports, not independently verified unique substantive sections.']}
    (HERE/'catalog_validation.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'success':True,'indexed_rows':total,'verified_files':files[0],'binding_checks':len(bindings),'query_checks':len(queries)}))
    db.close()

if __name__=='__main__':main()
