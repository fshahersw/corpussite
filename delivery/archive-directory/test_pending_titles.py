"""Offline title-refresh invariants; no network or production mutations."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import pending_titles as titles


class PendingTitleChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'directory.sqlite3'
        self.collection = 'corpus/county_local_backfill_20260918T2213'
        self.url = 'https://court.example/rules-Revised-2024-01-17.pdf'
        self.source = {'id': 1, 'title': self.url, 'url': self.url, 'status': 'downloaded'}
        base = self.root / self.collection; base.mkdir(parents=True)
        with closing(sqlite3.connect(base / 'corpus.sqlite3')) as c:
            c.execute('CREATE TABLE resources(id INTEGER,title TEXT,url TEXT,status TEXT)')
            c.execute('INSERT INTO resources VALUES(?,?,?,?)', tuple(self.source.values())); c.commit()
        meta = {'title': self.url, 'requested_url': self.url}
        proofs = {}
        for kind, name, body in [('raw_evidence','raw.pdf',b'%PDF-1.4 preserved source'),
                                 ('text_evidence','body.txt',b'Rule 12. Serve a copy within ten days.'),
                                 ('metadata_evidence','metadata.json',json.dumps(meta).encode())]:
            path = base / name; path.write_bytes(body)
            proofs[kind] = {'path':path.relative_to(self.root).as_posix(),'bytes':len(body),'sha256':titles.digest(body)}
        evidence = {'collection':self.collection,'resource_id':1,'resource_row':self.source,
                    'resource_row_sha256':titles.digest(titles.title_builder().canonical(self.source)),
                    'source_metadata':meta,'source_context_records':[{'seed':{'provenance':[{
                        'target_url':self.url,'link_reproduced_from_raw_html':True,'anchor_text':'Local Rules DRAFT #8'}]}}],
                    'title_basis':'capture_metadata','captured_title':self.url,
                    'source_database_snapshot_at':'old checkpoint',**proofs}
        self.old = {'id':'fixture','title':self.url,'state':'Iowa','county':'Example County',
                    'county_geoids':['19999'],'group':'counties','resource_kind':'rules',
                    'source_url':self.url,'raw_path':proofs['raw_evidence']['path'],
                    'text_path':proofs['text_evidence']['path'],'sha256':proofs['raw_evidence']['sha256'],
                    'quality':'Awaiting publication validation','metadata':evidence}
        self.new = json.loads(json.dumps(self.old))
        self.new['title'], self.new['metadata']['title_basis'] = titles.title_builder().source_link_display_title(
            self.url,self.url,evidence['source_context_records'])
        self.new['metadata']['source_database_snapshot_at'] = 'new checkpoint'
        self.key = titles.record_id('fixture')
        with closing(sqlite3.connect(self.database)) as c:
            c.executescript('''
                CREATE TABLE records(id TEXT PRIMARY KEY,title TEXT,group_name TEXT,dataset TEXT,state TEXT,county TEXT,kind TEXT,source_url TEXT,quality TEXT,content_id INTEGER,payload TEXT,inline_text TEXT,original_id TEXT,text_id TEXT);
                CREATE TABLE browse(id TEXT PRIMARY KEY,title TEXT,dataset TEXT);
                CREATE INDEX browse_dataset ON browse(dataset);
                CREATE TABLE display_groups(id TEXT PRIMARY KEY,preferred_id TEXT,title TEXT,state TEXT,county TEXT,source_count INTEGER,group_basis TEXT);
                CREATE TABLE display_members(record_id TEXT PRIMARY KEY,display_id TEXT);
                CREATE INDEX display_member_group ON display_members(display_id,record_id);
                CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,text);
                CREATE TABLE settings(key TEXT PRIMARY KEY,payload TEXT);
            ''')
            c.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                self.key,self.url,'counties',titles.DATASET,'Iowa','Example County','rules',self.url,
                'Awaiting publication validation',None,json.dumps(self.old),'Rule 12. Serve a copy within ten days.','original','text'))
            c.execute('INSERT INTO browse VALUES(?,?,?)',(self.key,self.url,titles.DATASET))
            c.execute('INSERT INTO display_groups VALUES(?,?,?,?,?,?,?)',('group',self.key,self.url,'Iowa','Example County',1,'Identical original SHA-256'))
            c.execute('INSERT INTO display_members VALUES(?,?)',(self.key,'group'))
            c.execute('INSERT INTO search VALUES(?,?)',(self.key,self.url+' Rule 12. Serve a copy within ten days.'))
            c.execute('INSERT INTO settings VALUES(?,?)',('summary',json.dumps({'published':{'capture_records':16454},'directory_built_at':'unchanged full-build time'})))
            c.commit()
        self.write_projection([self.new])

    def write_projection(self, rows):
        folder=self.root/titles.FOLDER;folder.mkdir(parents=True,exist_ok=True)
        raw=''.join(json.dumps(p)+'\n' for p in rows).encode()
        (folder/'resources.jsonl').write_bytes(raw)
        for name,p in [('summary.json',{'resources':len(rows),'resources_sha256':titles.digest(raw)}),
                       ('validation.json',{'valid':True,'downloaded_status_verified_against_source_dbs':True,
                                           'raw_text_metadata_binding_and_hashes_verified':True,'published_database_unchanged':True,
                                           'resource_records':len(rows),'resources_sha256':titles.digest(raw)})]:
            (folder/name).write_text(json.dumps(p))

    def rows(self, table):
        with closing(sqlite3.connect(self.database)) as c:return c.execute('SELECT * FROM '+table).fetchall()

    def refresh(self, **kwargs):return titles.refresh(self.database,self.root,**kwargs)

    def test_updates_all_title_surfaces_preserving_body_originals_status_and_versions(self):
        old=self.rows('records')[0];result=self.refresh();new=self.rows('records')[0]
        self.assertEqual(result['changed_titles'],1);self.assertEqual(result['fresh_artifact_hashes_verified'],3)
        for index in range(len(old)):
            if index not in (1,10):self.assertEqual(old[index],new[index])
        self.assertIn('DRAFT #8',new[1]);self.assertIn('Revised 2024-01-17',new[1])
        self.assertEqual(self.rows('browse')[0][1],new[1]);self.assertEqual(self.rows('display_groups')[0][2],new[1])
        with closing(sqlite3.connect(self.database)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM search WHERE search MATCH 'DRAFT'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM search WHERE search MATCH 'ten'").fetchone()[0],1)
        summary=json.loads(self.rows('settings')[0][1])
        self.assertEqual(summary['published']['capture_records'],16454)
        self.assertEqual(summary['directory_built_at'],'unchanged full-build time')

    def test_noop_and_dry_run_do_not_modify_database(self):
        before=self.database.read_bytes()
        result=self.refresh(dry_run=True);self.assertEqual(result['status'],'ready');self.assertEqual(self.database.read_bytes(),before)
        self.write_projection([self.old]);result=self.refresh()
        self.assertEqual(result['status'],'unchanged');self.assertEqual(self.database.read_bytes(),before)
        self.assertFalse(self.database.with_suffix('.writer.lock').exists())

    def test_membership_changes_require_full_build(self):
        self.write_projection([])
        with self.assertRaisesRegex(titles.FullBuildRequired,'membership changed'):self.refresh()

    def test_body_identity_geography_and_evidence_changes_rejected(self):
        for field,value in [('state','California'),('sha256','a'*64),('text_path','elsewhere.txt')]:
            with self.subTest(field=field):
                row=json.loads(json.dumps(self.new));row[field]=value;self.write_projection([row])
                with self.assertRaisesRegex(titles.FullBuildRequired,'body, identity'):self.refresh()

    def test_shared_group_rejected_without_partial_update(self):
        with closing(sqlite3.connect(self.database)) as c:
            c.execute('INSERT INTO display_members VALUES(?,?)',('other-observation','group'));c.commit()
        before=self.database.read_bytes()
        with self.assertRaisesRegex(titles.FullBuildRequired,'shared display group'):self.refresh()
        self.assertEqual(self.database.read_bytes(),before)

    def test_changed_raw_bytes_rejected(self):
        (self.root/self.new['raw_path']).write_bytes(b'%PDF changed')
        with self.assertRaisesRegex(titles.FullBuildRequired,'artifact differs'):self.refresh()

    def test_arbitrary_title_rejected(self):
        self.new['title']='Invented title';self.write_projection([self.new])
        with self.assertRaisesRegex(titles.FullBuildRequired,'not reproduced'):self.refresh()

    def test_capture_row_changed_since_validation_rejected(self):
        with closing(sqlite3.connect(self.root/self.collection/'corpus.sqlite3')) as c:
            c.execute("UPDATE resources SET status='network_error'");c.commit()
        with self.assertRaisesRegex(titles.FullBuildRequired,'Capture changed'):self.refresh()

    def test_invalid_receipt_rejected(self):
        path=self.root/titles.FOLDER/'validation.json';receipt=json.loads(path.read_text());receipt['resources_sha256']='0'*64;path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(titles.FullBuildRequired,'valid validation receipt'):self.refresh()

    def test_fts_row_identity_mismatch_requires_full_build(self):
        with closing(sqlite3.connect(self.database)) as c:
            c.execute('DELETE FROM search')
            c.execute('INSERT INTO search(rowid,id,text) VALUES(?,?,?)',(7,self.key,'Preserved source body'));c.commit()
        with self.assertRaisesRegex(titles.FullBuildRequired,'search membership'):self.refresh()

    def test_late_projection_change_rolls_back_every_surface(self):
        before={t:self.rows(t) for t in ['records','browse','display_groups','search','settings']}
        original=titles.load_projection;calls=0
        def changing(root):
            nonlocal calls
            calls+=1
            if calls>1:raise titles.FullBuildRequired('Projection changed during refresh')
            return original(root)
        with patch.object(titles,'load_projection',side_effect=changing):
            with self.assertRaisesRegex(titles.FullBuildRequired,'Projection changed'):self.refresh()
        self.assertEqual(before,{t:self.rows(t) for t in before})

    def test_writer_lock_rejects_concurrent_writer_and_releases(self):
        with titles.writer_lock(self.database):
            with self.assertRaisesRegex(titles.FullBuildRequired,'Another directory writer'):self.refresh()
        self.assertEqual(self.refresh()['status'],'updated')


if __name__=='__main__':unittest.main(verbosity=2)
