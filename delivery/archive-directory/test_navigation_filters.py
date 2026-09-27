"""Regression checks for evidence-based directory filtering."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import judges
import server


class FilterChecks(unittest.TestCase):
    def test_court_filter_matches_saved_affiliations_and_preserves_unknown_selection(self):
        court='U.S. District Court for the Northern District of California'
        result=judges.listing({'state':'California','court':court,'limit':60})
        self.assertGreater(result['total'],0)
        self.assertTrue(all(court in row['courts'] for row in result['items']))
        self.assertTrue(all('California' in row['states'] for row in result['items']))
        option=next(row for row in result['courts'] if row['value']==court)
        self.assertEqual(option['count'],result['total'])
        empty=judges.listing({'state':'California','court':court,'q':'never-a-real-judge-xyz'})
        self.assertEqual(empty['total'],0)
        self.assertIn({'value':court,'label':court,'count':0},empty['courts'])
        unknown=judges.listing({'court':"%' OR 1=1 --"})
        self.assertEqual(unknown['total'],0)
        self.assertEqual(unknown['courts'][-1]['count'],0)

    def test_name_search_and_content_filter_intersect_with_court(self):
        result=judges.listing({'court':'U.S. District Court for the Northern District of California','q':'Breyer','has':'photo'})
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['name'],'Charles R. Breyer')
        self.assertEqual(next(row['count'] for row in result['courts'] if row['value']=='U.S. District Court for the Northern District of California'),1)

    def test_county_availability_is_evidence_not_completeness(self):
        all_counties=server.query_counties({'state':'Washington'})
        saved=server.query_counties({'state':'Washington','availability':'local_resources','limit':100})
        missing=server.query_counties({'state':'Washington','availability':'no_local_resources','limit':100})
        self.assertEqual(saved['total']+missing['total'],all_counties['total'])
        self.assertTrue(all(row['local_resources']>0 and not row['complete'] for row in saved['items']))
        self.assertTrue(all(row['local_resources']==0 for row in missing['items']))
        facets={row['value']:row['count'] for row in saved['availabilities']}
        self.assertEqual(facets['local_resources'],saved['total'])
        self.assertEqual(facets['no_local_resources'],missing['total'])
        self.assertEqual(server.query_counties({'availability':'invented'})['total'],0)

    def test_county_query_treats_wildcards_as_literal_and_keeps_geoid(self):
        self.assertEqual(server.query_counties({'q':'06001'})['items'][0]['geoid'],'06001')
        self.assertEqual(server.query_counties({'q':'%'})['total'],0)
        self.assertEqual(server.query_counties({'q':"%' OR 1=1 --"})['total'],0)

    def test_changed_portrait_bytes_are_not_served_under_verified_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);images=root/'images';images.mkdir()
            self.assertTrue(root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
            image=images/'portrait.webp';image.write_bytes(b'verified portrait fixture')
            database=root/'test.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE images(id TEXT,path TEXT,sha256 TEXT,mime TEXT)')
                connection.execute('INSERT INTO images VALUES(?,?,?,?)',('test','images/portrait.webp',hashlib.sha256(image.read_bytes()).hexdigest(),'image/webp'))
            connection.close()
            with patch.object(judges,'ROOT',root),patch.object(judges,'FOLDER',root),patch.object(judges,'DB',database):
                self.assertIsNotNone(judges.image_file('test'))
                image.write_bytes(b'unrelated replacement bytes')
                self.assertIsNone(judges.image_file('test'))


if __name__=='__main__':unittest.main()
