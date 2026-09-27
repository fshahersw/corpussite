"""Tests for the county_seed_packets status adapter (fail-closed hash gate, summary() only)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import county_seed_packets  # noqa: E402

REAL = county_seed_packets.DATA


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='csp_gate_'))
        for name in ('validation.json',) + county_seed_packets.REQUIRED:
            shutil.copy2(REAL / name, self.tmp / name)
        county_seed_packets.DATA = self.tmp
        county_seed_packets._CACHE.clear()

    def tearDown(self):
        county_seed_packets.DATA = REAL
        county_seed_packets._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertClosed(self):
        result = county_seed_packets.summary()
        self.assertEqual(result['available'], False)
        self.assertTrue(result['reason'])
        self.assertNotIn('county_seeds', result)

    def test_copy_without_tamper_opens(self):
        result = county_seed_packets.summary()
        self.assertTrue(result['available'])

    def test_tampered_data_file_closes_gate(self):
        with open(self.tmp / 'seeds_county.jsonl', 'ab') as handle:
            handle.write(b'{"id":"county-seed-packet:injected"}\n')
        self.assertClosed()

    def test_not_ready_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['ready'] = False
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_status_not_passed_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['status'] = 'pending'
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_validation_closes_gate(self):
        (self.tmp / 'validation.json').unlink()
        self.assertClosed()

    def test_missing_data_file_closes_gate(self):
        (self.tmp / 'coverage_gain.json').unlink()
        self.assertClosed()

    def test_unregistered_data_file_closes_gate(self):
        gate = json.loads((self.tmp / 'validation.json').read_text(encoding='utf-8'))
        gate['data_files'] = [e for e in gate['data_files'] if not e['path'].endswith('rejected.jsonl')]
        (self.tmp / 'validation.json').write_text(json.dumps(gate), encoding='utf-8')
        self.assertClosed()

    def test_missing_supplement_folder_closes_gate(self):
        shutil.rmtree(self.tmp)
        self.assertClosed()


class SummaryShapeTests(unittest.TestCase):
    def test_real_data_summary_shape_and_no_listing_function(self):
        result = county_seed_packets.summary()
        self.assertTrue(result['available'])
        for key in ('county_seeds', 'statewide_seeds', 'rejected', 'distinct_counties_seeded',
                    'zero_coverage_counties_reached', 'states_with_new_county_seeds', 'validated_at'):
            self.assertIn(key, result)
        self.assertIsInstance(result['county_seeds'], int)
        self.assertGreater(result['county_seeds'], 0)
        self.assertGreater(result['statewide_seeds'], 0)
        # This is a status module only: no listing/detail surface is exposed.
        self.assertFalse(hasattr(county_seed_packets, 'listing'))
        self.assertFalse(hasattr(county_seed_packets, 'detail'))

    def test_summary_never_raises_on_garbage_folder(self):
        county_seed_packets.DATA = Path(tempfile.mkdtemp(prefix='csp_missing_'))
        county_seed_packets._CACHE.clear()
        try:
            result = county_seed_packets.summary()
            self.assertEqual(result['available'], False)
        finally:
            shutil.rmtree(county_seed_packets.DATA, ignore_errors=True)
            county_seed_packets.DATA = REAL
            county_seed_packets._CACHE.clear()


if __name__ == '__main__':
    unittest.main()
