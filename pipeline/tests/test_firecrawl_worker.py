import json,pathlib,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from firecrawl_worker import Queue,allowed,retry_delay,save
from unittest.mock import patch

class CheckpointStorageTests(unittest.TestCase):
    def test_transient_windows_reader_preserves_then_replaces_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            target=pathlib.Path(td)/'status.json';target.write_text('{"prior":true}',encoding='utf-8')
            original=pathlib.Path.replace
            calls=[]
            def replace(source,destination):
                calls.append(1)
                if len(calls)==1:
                    self.assertEqual(json.loads(target.read_text()),{'prior':True})
                    raise PermissionError('simulated transient reader')
                return original(source,destination)
            with patch('firecrawl_worker.os.name','nt'),patch.object(pathlib.Path,'replace',replace):
                save(target,{'new':True})
            self.assertEqual(json.loads(target.read_text()),{'new':True})
            self.assertEqual(len(calls),2)

    def test_persistent_denial_keeps_old_checkpoint_and_pending_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            target=pathlib.Path(td)/'status.json';target.write_text('{"prior":true}',encoding='utf-8')
            with patch.object(pathlib.Path,'replace',side_effect=PermissionError('persistent denial')),patch('firecrawl_worker.time.monotonic',side_effect=[0,2]):
                with self.assertRaises(PermissionError):save(target,{'new':True})
            self.assertEqual(json.loads(target.read_text()),{'prior':True})
            self.assertEqual(json.loads(target.with_suffix('.json.tmp').read_text()),{'new':True})

class FirecrawlQueueTests(unittest.TestCase):
    def test_rate_limit_uses_latest_server_deadline(self):
        body={'error':'Please retry after 39s, resets at Sun Sep 13 2026 08:18:18 GMT+0000 (Coordinated Universal Time)',
              '_collector_transport':{'retry_after':'120'}}
        self.assertEqual(retry_delay(body,1789287431),121)
        body['_collector_transport']['retry_after']='Sun, 13 Sep 2026 08:20:00 GMT'
        self.assertEqual(retry_delay(body,1789287431),170)
        del body['_collector_transport']
        self.assertEqual(retry_delay(body,1789287431),68)
    def test_scope_excludes_credentials_actions_external_and_sort_duplicates(self):
        self.assertTrue(allowed('https://trellis.law/coverage/illinois/cook'))
        self.assertTrue(allowed('https://trellis.law/state-rules/az/constitution'))
        self.assertFalse(allowed('https://trellis.law/account/overview'))
        self.assertFalse(allowed('https://evil.example/case/1'))
        self.assertFalse(allowed('https://secret@trellis.law/coverage'))
        self.assertFalse(allowed('https://trellis.law/judges/il/1?sort=alpha'))
        self.assertFalse(allowed('https://trellis.law/case/1?output=pdf'))
    def test_import_retains_scoped_links_and_does_not_queue_cases(self):
        with tempfile.TemporaryDirectory() as td:
            q=Queue(pathlib.Path(td)/'queue.sqlite')
            county='https://trellis.law/coverage/illinois/cook';case='https://trellis.law/case/17031/a/b'
            judge='https://trellis.law/judge/jane.doe'
            q.import_data({'metadata':{'sourceURL':county,'statusCode':200},'markdown':'County','links':[case,'https://official.gov/court',judge]},pathlib.Path(td)/'page.json')
            self.assertEqual(q.db.execute('SELECT status FROM frontier WHERE url=?',(county,)).fetchone()[0],'downloaded')
            self.assertIsNone(q.db.execute('SELECT status FROM frontier WHERE url=?',(case,)).fetchone())
            self.assertEqual(q.db.execute('SELECT count(*) FROM edges').fetchone()[0],1)
            self.assertEqual(q.claim()['url'],judge)
            q.db.close()
    def test_phase_order_and_resumed_no_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            file=pathlib.Path(td)/'queue.sqlite';q=Queue(file)
            for _ in range(2):q.add('https://trellis.law/coverage/arizona/apache')
            q.add('https://trellis.law/judge/jane.doe')
            q.add('https://trellis.law/state-rules/az/constitution');q.db.commit();q.db.close()
            q=Queue(file);self.assertEqual(q.db.execute('SELECT count(*) FROM frontier').fetchone()[0],3)
            self.assertEqual(q.claim()['category'],'rules')
            self.assertIsNone(q.claim())
            q.db.execute("UPDATE frontier SET status='downloaded' WHERE status='fetching'")
            self.assertEqual(q.claim()['category'],'judge_profile')
            q.db.execute("UPDATE frontier SET status='downloaded' WHERE status='fetching'")
            self.assertEqual(q.claim()['category'],'county');q.db.close()
    def test_legacy_scope_change_preserves_attempts_but_excludes_case_and_date_crawls(self):
        with tempfile.TemporaryDirectory() as td:
            q=Queue(pathlib.Path(td)/'queue.sqlite')
            for url,category,priority in [('https://trellis.law/case/1/a','cases',80),('https://trellis.law/coverage/arizona/apache/2020/december','coverage',15),('https://trellis.law/coverage/arizona/apache?page=2','county',10),('https://trellis.law/state-rules/az/constitution','rules',20)]:
                q.db.execute('INSERT INTO frontier(url,category,priority,state,status,attempts) VALUES(?,?,?,?,?,?)',(url,category,priority,'az','pending',2))
            q.db.commit();q.reconcile_scope()
            self.assertEqual(q.db.execute('SELECT count(*) FROM frontier WHERE in_scope=0 AND status=? AND attempts=2',('deferred_scope',)).fetchone()[0],3)
            self.assertEqual(q.claim()['category'],'rules')
            self.assertIsNone(q.claim())
            q.db.close()
if __name__=='__main__':unittest.main()
