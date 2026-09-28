"""Offline loss-prevention and resumability tests; never instantiate a real client."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import import_catalog as subject
import supabase_client


class LargeTextFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.assets=self.root/'text_assets';(self.assets/'files').mkdir(parents=True)
        self.text='Court résumé — 🏛\n' * 8
        self.row={'id':'record:1','dataset':'seeger','category':'rules','title':'Rules','item':{'has_text':True},'detail':{},'text':self.text}
        self.file=self.assets/'files'/'body.txt';self.file.write_bytes(self.text.encode())
        self.manifest=self.assets/'large_text_assets.jsonl'
        sha=hashlib.sha256(self.file.read_bytes()).hexdigest()
        route='/api/text?'+urlencode({'id':self.row['id']})
        self.entry={'id':'large-text:record:1','dataset':'large_text_assets',
            'detail':{'source_text_characters':len(self.text),'metadata':{'source_dataset':'seeger','source_record_id':self.row['id'],
                'display_text_sha256':sha,'utf8_bytes':len(self.file.read_bytes()),'reader_aliases':[self.row['id']]}},
            'artifacts':[{'url':route,'local_path':str(self.file),'sha256':sha,'bytes':len(self.file.read_bytes()),'mime':'text/plain; charset=utf-8','role':'cleaned_full_text'}]}
        self.pin_manifest()

    def pin_manifest(self):
        self.manifest.write_text(json.dumps(self.entry,ensure_ascii=False)+'\n',encoding='utf-8')
        self.manifest.with_suffix('.dataset.json').write_text(json.dumps({'id':'large_text_assets','rows':1,'sha256':hashlib.sha256(self.manifest.read_bytes()).hexdigest()}),encoding='utf-8')
        (self.assets/'validation.json').write_text(json.dumps({'status':'passed','exact_utf8_text':True,'source_sha256':{'seeger':'reviewed-source-hash'}}),encoding='utf-8')

    def offloader(self):return subject.VerifiedLargeText(self.manifest,preview_characters=20)

    def test_exact_full_utf8_retained_and_preview_is_explicit(self):
        before=copy.deepcopy(self.row);result=subject.normalize(self.row,large_text=self.offloader())
        self.assertEqual(result['text'],self.text[:20]);self.assertEqual(self.row,before)
        self.assertEqual(self.file.read_text(encoding='utf-8'),self.text)
        self.assertEqual(result['detail']['full_text_sha256'],hashlib.sha256(self.text.encode()).hexdigest())
        self.assertEqual(result['detail']['full_text_characters'],len(self.text))
        self.assertEqual(result['detail']['full_text_utf8_bytes'],len(self.text.encode()))
        self.assertTrue(result['detail']['text_truncated']);self.assertTrue(result['detail']['full_text_offloaded'])
        self.assertNotIn('local_path',json.dumps(result))

    def test_unmapped_text_is_never_silently_truncated(self):
        row=copy.deepcopy(self.row);row['id']='unmapped'
        self.assertEqual(subject.normalize(row,large_text=self.offloader())['text'],self.text)

    def test_changed_source_or_complete_file_fails_closed(self):
        row=copy.deepcopy(self.row);row['text']+='changed'
        with self.assertRaisesRegex(ValueError,'source differs'):subject.normalize(row,large_text=self.offloader())
        self.file.write_text('corrupt',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'corrupt'):subject.normalize(self.row,large_text=self.offloader())

    def test_missing_file_or_pinned_route_never_offloads(self):
        self.file.unlink()
        with self.assertRaises(FileNotFoundError):subject.normalize(self.row,large_text=self.offloader())
        self.entry['artifacts'][0]['url']='/api/text?id=wrong';self.pin_manifest()
        with self.assertRaisesRegex(ValueError,'route'):self.offloader()

    def test_manifest_corruption_and_source_version_mismatch_fail_closed(self):
        offloader=self.offloader()
        with self.assertRaisesRegex(ValueError,'different source export'):offloader.validate_source('seeger','wrong')
        self.manifest.write_text(self.manifest.read_text(encoding='utf-8')+' ',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'hash-bound'):self.offloader()

    def test_alias_cannot_point_to_another_file_or_external_route(self):
        bad=copy.deepcopy(self.entry['artifacts'][0]);bad['url']='https://example.org/api/text?id=record%3A1'
        self.entry['artifacts'].append(bad);self.pin_manifest()
        with self.assertRaisesRegex(ValueError,'alias'):self.offloader()

    def test_checkpointed_offloaded_batch_replays_once_and_source_is_unchanged(self):
        small=dict(self.row,id='small',text='short');records=[small,self.row]
        source=self.root/'seeger.jsonl';source.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
        original=source.read_bytes();sha=hashlib.sha256(original).hexdigest()
        source.with_suffix('.dataset.json').write_text(json.dumps({'id':'seeger','expected_records':2,'sha256':sha}),encoding='utf-8')
        validation=json.loads((self.assets/'validation.json').read_text());validation['source_sha256']['seeger']=sha
        (self.assets/'validation.json').write_text(json.dumps(validation))
        journal=sqlite3.connect(self.root/'import_progress.sqlite3')
        journal.execute('create table batches(file text,sha text,offset integer,records integer,primary key(file,sha,offset))')
        journal_key=str(source.resolve())+'#1:1500000'
        journal.executemany('insert into batches values(?,?,?,?)',[(journal_key,sha,0,1),(journal_key,sha,1,1)]);journal.commit();journal.close()
        class FakeClient:
            def __init__(self):self.catalog=[]
            def upsert(self,table,rows):
                if table=='corpus_records':self.catalog.extend(copy.deepcopy(rows))
            def call(self,*args,**kwargs):return type('Reply',(),{'headers':{'Content-Range':'0-0/2'}})()
            def json(self,*args,**kwargs):return None
        fake=FakeClient();real_class=subject.VerifiedLargeText
        factory=lambda:real_class(self.manifest,preview_characters=20)
        with patch.object(subject,'LOCAL',self.root),patch.object(subject,'Client',return_value=fake),patch.object(subject,'VerifiedLargeText',side_effect=factory),patch.object(subject,'_THREAD',threading.local()),patch('sys.argv',['import_catalog.py',str(source),'--batch-rows','1']):
            subject.main.__wrapped__();self.assertEqual([r['id'] for r in fake.catalog],['record:1'])
            subject.main.__wrapped__();self.assertEqual(len(fake.catalog),1)
        self.assertEqual(source.read_bytes(),original)
        receipt=json.loads(source.with_suffix('.import.json').read_text())
        self.assertEqual(receipt['offloaded_records'],1);self.assertEqual(receipt['transform_version'],subject.TRANSFORM_VERSION)
        self.assertFalse(receipt['ready'])


class TimeoutTests(unittest.TestCase):
    def client_with(self,replies):
        client=supabase_client.Client.__new__(supabase_client.Client)
        from unittest.mock import Mock
        client.session=Mock();client.session.request.side_effect=replies
        return client

    def reply(self,status,code,message):
        from unittest.mock import Mock
        r=Mock(status_code=status,text=json.dumps({'code':code,'message':message}));r.json.return_value={'code':code,'message':message};return r

    def test_confirmed_statement_timeout_does_not_retry_same_payload(self):
        c=self.client_with([self.reply(500,'57014','canceling statement due to statement timeout')])
        with patch.object(supabase_client.time,'sleep') as sleep:
            with self.assertRaises(supabase_client.StatementTimeout):c.call('POST','/rest/v1/corpus_records')
        self.assertEqual(c.session.request.call_count,1);sleep.assert_not_called()

    def test_transient_503_still_retries_and_returns_success(self):
        c=self.client_with([self.reply(503,'temporary','unavailable'),self.reply(200,'','')])
        with patch.object(supabase_client.time,'sleep') as sleep:r=c.call('POST','/rest/v1/corpus_records')
        self.assertEqual(r.status_code,200);self.assertEqual(c.session.request.call_count,2);sleep.assert_called_once()

    def test_atomic_timeout_splits_and_single_record_failure_is_retained(self):
        class Fake:
            def __init__(self):self.calls=[]
            def upsert(self,table,rows):
                self.calls.append([r['id'] for r in rows])
                if len(rows)>1:raise supabase_client.StatementTimeout('statement timeout')
        local=threading.local();local.client=Fake()
        with patch.object(subject,'_THREAD',local):self.assertEqual(subject.send([{'id':'a'},{'id':'b'}]),2)
        self.assertEqual(local.client.calls,[['a','b'],['a'],['b']])
        with patch.object(local.client,'upsert',side_effect=supabase_client.StatementTimeout('statement timeout')),patch.object(subject,'_THREAD',local):
            with self.assertRaises(supabase_client.StatementTimeout):subject.send([{'id':'a'}])


if __name__=='__main__':unittest.main()
