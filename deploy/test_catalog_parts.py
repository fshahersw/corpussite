"""Crash/restart and isolation checks for durable split acknowledgments; no network."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import import_catalog as mod


class PartsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.sqlite3'
        with contextlib.closing(sqlite3.connect(self.path)) as db:db.execute(mod.PART_SCHEMA)
        self.rows=[{'id':str(i),'text':'retained original '+str(i)} for i in range(4)]

    def run_batch(self,client,rows=None,offset=0):
        local=threading.local();local.client=client
        with patch.object(mod,'_THREAD',local),contextlib.redirect_stdout(io.StringIO()):
            return mod.send_journaled(rows or self.rows,self.path,'frozen-file','source-sha',offset)

    def test_split_success_survives_failure_without_replaying_saved_half(self):
        calls=[]
        class FailSecond:
            def upsert(_,table,rows):
                ids=[r['id'] for r in rows];calls.append(ids)
                if ids==['0','1','2','3'] or ids[0]=='2':raise mod.StatementTimeout('statement timeout')
        with self.assertRaises(mod.StatementTimeout):self.run_batch(FailSecond())
        self.assertIn(['0','1'],calls)
        with contextlib.closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('select start_row,end_row from batch_parts').fetchall(),[(0,2)])
        resumed=[]
        class Success:
            def upsert(_,table,rows):resumed.append([r['id'] for r in rows])
        self.assertEqual(self.run_batch(Success()),4);self.assertEqual(resumed,[['2','3']])
        self.assertEqual(self.run_batch(Success()),4);self.assertEqual(len(resumed),1)

    def test_changed_payload_and_overlapping_parts_stop_before_request(self):
        p=mod.BatchParts(self.path,'frozen-file','source-sha',0,self.rows)
        p.acknowledge(0,self.rows[:2]);p.close()
        class NoCall:
            def upsert(*args):raise AssertionError('must not send altered batch')
        changed=[dict(r) for r in self.rows];changed[3]['text']='changed'
        with self.assertRaisesRegex(ValueError,'payload differs'):self.run_batch(NoCall(),changed)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            db.execute('insert into batch_parts values(?,?,?,?,?,?,?,?,?)',(mod.PROJECT_REF,'frozen-file','source-sha',0,1,3,4,mod.payload_hash(self.rows),mod.payload_hash(self.rows[1:3])))
            db.commit()
        with self.assertRaisesRegex(ValueError,'overlapping'):self.run_batch(NoCall())

    def test_project_pin_refuses_other_target(self):
        with patch.object(mod,'ORIGIN','https://other.supabase.co'):
            with self.assertRaisesRegex(ValueError,'project'):mod.BatchParts(self.path,'frozen-file','source-sha',0,self.rows)

    def test_parallel_local_acknowledgments_use_separate_connections(self):
        def write(offset):
            p=mod.BatchParts(self.path,'frozen-file','source-sha',offset,self.rows)
            try:p.acknowledge(offset,self.rows);self.assertEqual(p.remaining(),[])
            finally:p.close()
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(write,range(0,40,4)))
        with contextlib.closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('select count(*) from batch_parts').fetchone()[0],10)

    def test_telemetry_has_no_body_identity_or_error_message(self):
        local=threading.local()
        class Failure:
            def upsert(*args):raise mod.StatementTimeout('secret-sensitive-error')
        local.client=Failure();output=io.StringIO()
        with patch.object(mod,'_THREAD',local),contextlib.redirect_stdout(output):
            with self.assertRaises(mod.StatementTimeout):mod.send([{'id':'private-id','text':'private body'}],source_offset=52)
        event=json.loads(output.getvalue());self.assertEqual(event['batch_offset'],52)
        self.assertEqual(event['batch_outcome'],'singleton_timeout')
        self.assertNotIn('private',output.getvalue());self.assertNotIn('secret',output.getvalue())


if __name__=='__main__':unittest.main()
