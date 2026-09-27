"""Small semantic fixtures for the source-only importer. No external connections."""
import bz2
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('people_importer',ROOT/'import_people.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AUDIT=json.loads(module.DEFAULT_AUDIT.read_text(encoding='utf-8'))
SCHEMAS={table:next(r['columns'] for r in AUDIT['tables'] if Path(r['path']).name==filename) for table,filename in module.TABLE_FILES.items()}


class ImportFixtures(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='test-',dir=ROOT)
        self.base=Path(self.temp.name)
        self.rows={
            'people':[
                {'id':'1','name_first':'Ada','name_last':'Judge','has_photo':'t','date_dob':'1900-01-01','date_granularity_dob':'%Y'},
                {'id':'2','name_first':'A.','name_last':'Judge','is_alias_of_id':'1','has_photo':'f'},
                {'id':'3','name_first':'Appointing','name_last':'Official','has_photo':'f'},
            ],
            'positions':[
                {'id':'10','person_id':'1','appointer_id':'11','court_id':'ct','date_start':'1950-01-01','date_granularity_start':'%Y','position_type':'jud','has_inferred_values':'t'},
                {'id':'11','person_id':'3','organization_name':'An "Office"\nwith two lines','position_type':'pres'},
            ],
            'schools':[{'id':'school1','name':'A "Quoted" School'}],
            'educations':[{'id':'ed1','person_id':'1','school_id':'school1','degree_year':'1925'}],
            'political_affiliations':[{'id':'party1','person_id':'3','date_start':'1940-01-01','date_granularity_start':'%Y'}],
            'courts':[{'id':'ct','full_name':'Example Court','jurisdiction':'FD'}],
        }

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self):
        tables={}
        for table,rows in self.rows.items():
            path=self.base/(table+'.csv.bz2')
            columns=SCHEMAS[table]
            with bz2.open(path,'wt',encoding='utf-8',newline='') as f:
                writer=csv.writer(f,escapechar='\\',doublequote=False)
                writer.writerow(columns)
                writer.writerows([[r.get(c,'') for c in columns] for r in rows])
            tables[table]={'path':path.as_posix(),'sha256':module.digest(path),'compressed_bytes':path.stat().st_size,'expected_rows':len(rows),'columns':columns}
        return {'tables':tables,'source_snapshot':'fixture'}

    def test_raw_strings_dates_aliases_search_and_appointer_namespace(self):
        manifest=self.manifest();db=self.base/'built.sqlite3'
        summary,validation=module.build_database(db,manifest)
        self.assertTrue(validation['passed'])
        self.assertEqual(summary['counts']['people'],3)
        self.assertEqual(summary['people_with_photo_flags'],1)
        self.assertEqual(summary['people_with_alias_reference'],1)
        c=sqlite3.connect(db)
        try:
            self.assertEqual(c.execute("SELECT date_dob,date_granularity_dob,is_alias_of_id FROM people WHERE id='1'").fetchone(),('1900-01-01','%Y',''))
            self.assertEqual(c.execute("SELECT organization_name FROM positions WHERE id='11'").fetchone()[0],'An "Office"\nwith two lines')
            self.assertEqual(c.execute("SELECT appointer_id,appointer_person_id,appointer_display_name FROM position_profiles WHERE id='10'").fetchone(),('11','3','Appointing Official'))
            self.assertEqual(c.execute("SELECT count(*) FROM people_fts WHERE people_fts MATCH 'Ada'").fetchone()[0],1)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:c.close()

    def test_wrong_hash_fails_closed(self):
        manifest=self.manifest();manifest['tables']['people']['sha256']='0'*64
        with self.assertRaisesRegex(module.ImportErrorChecked,'hash mismatch'):
            module.build_database(self.base/'built.sqlite3',manifest)

    def test_second_writer_fails_before_publication(self):
        with module.writer_lock(self.base/'writer.lock'):
            with self.assertRaisesRegex(module.ImportErrorChecked,'writer lock'):
                with module.writer_lock(self.base/'writer.lock'):
                    self.fail('Second writer unexpectedly acquired the lock')

    def test_tampered_receipt_rejected_on_reuse(self):
        manifest=self.manifest();db=self.base/'catalog.sqlite3'
        summary,validation=module.build_database(db,manifest)
        for name,value in [('source_manifest.json',manifest),('summary.json',summary),('validation.json',validation)]:
            module.atomic_json(self.base/name,value)
        ready={'ready':True,'schema_version':'courtlistener-people-catalog.v1','database_bytes':db.stat().st_size,
               'database_sha256':module.digest(db),'source_manifest_sha256':module.digest(self.base/'source_manifest.json'),
               'summary_path':(self.base/'summary.json').as_posix(),'summary_sha256':module.digest(self.base/'summary.json'),
               'validation_path':(self.base/'validation.json').as_posix(),'validation_sha256':module.digest(self.base/'validation.json'),
               'summary':{k:v for k,v in summary.items() if k!='court_position_counts'}}
        self.assertTrue(module.existing_publication_valid(self.base,manifest,ready))
        validation['passed']=False
        module.atomic_json(self.base/'validation.json',validation)
        self.assertFalse(module.existing_publication_valid(self.base,manifest,ready))

    def test_dangling_alias_rejected(self):
        self.rows['people'][1]['is_alias_of_id']='missing'
        with self.assertRaisesRegex(module.ImportErrorChecked,'Foreign-key errors'):
            module.build_database(self.base/'built.sqlite3',self.manifest())

    def test_dangling_appointer_position_rejected(self):
        # Person3 exists; it must not accidentally validate as appointer position3.
        self.rows['positions'][0]['appointer_id']='3'
        with self.assertRaisesRegex(module.ImportErrorChecked,'Foreign-key errors'):
            module.build_database(self.base/'built.sqlite3',self.manifest())

    def test_wrong_count_rejected(self):
        manifest=self.manifest();manifest['tables']['schools']['expected_rows']=5
        with self.assertRaisesRegex(module.ImportErrorChecked,'Row count mismatch'):
            module.build_database(self.base/'built.sqlite3',manifest)

    def test_malformed_row_width_rejected(self):
        manifest=self.manifest();p=Path(manifest['tables']['people']['path'])
        with bz2.open(p,'at',encoding='utf-8',newline='') as f:f.write('bad,row\n')
        manifest['tables']['people']['sha256']=module.digest(p)
        with self.assertRaisesRegex(module.ImportErrorChecked,'row width mismatch'):
            module.build_database(self.base/'built.sqlite3',manifest)


if __name__=='__main__':unittest.main(verbosity=2)
