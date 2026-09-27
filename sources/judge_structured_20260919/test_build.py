"""Unit tests for build.py rules plus an envelope/hash check of the built files (no network, read-only)."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402


def _appointment(**over):
    base = {'sequence': 1, 'court_type': 'U.S. District Court', 'court': 'U.S. District Court for the District of X',
            'commission_date': '2001-01-01', 'recess_appointment_date': None, 'senior_status_date': None,
            'termination_reason': None, 'termination_date': None}
    base.update(over)
    return base


class RuleTests(unittest.TestCase):
    def test_status_vocabulary_and_label(self):
        cases = [
            ({}, [_appointment()], 'active'),
            ({}, [_appointment(senior_status_date='2015-05-01')], 'senior'),
            ({}, [_appointment(termination_reason='Retirement', termination_date='2019-04-01')], 'terminated'),
            ({'Death Year': '1999'}, [_appointment(termination_reason='Death', termination_date='1999-02-02')], 'deceased'),
            ({}, [_appointment(termination_reason='Appointment to Another Judicial Position', termination_date='2010-01-01'),
                  _appointment(sequence=2, court_type='U.S. Court of Appeals', commission_date='2010-01-02')], 'active'),
        ]
        for row, appointments, expected in cases:
            status = build.fjc_status(row, appointments, '2026-09-14')
            self.assertEqual(status['value'], expected)
            self.assertEqual(status['label'], 'FJC-reported status as of 2026-09-14')
            self.assertNotIn('current', json.dumps(status).lower())

    def test_role_uses_open_appointment(self):
        role = build.fjc_role([_appointment(termination_reason='Appointment to Another Judicial Position', termination_date='2010-01-01'),
                               _appointment(sequence=2, court_type='U.S. Court of Appeals', court='U.S. Court of Appeals for the Third Circuit',
                                            commission_date='2010-01-02')])
        self.assertEqual((role['value'], role['basis'], role['all_values']),
                         ('circuit', 'fjc_court_type_of_open_appointment', ['circuit', 'district']))

    def test_court_patterns(self):
        self.assertEqual(build.classify_court('NY - Kings County Supreme Court', 'NY'), 'state_trial')
        self.assertEqual(build.classify_court('Supreme Court of Ohio', 'OH'), 'state_appellate')
        self.assertEqual(build.classify_court('Superior Court of Pennsylvania', 'PA'), 'state_appellate')
        self.assertEqual(build.classify_court('CA - Los Angeles County Superior Court', 'CA'), 'state_trial')
        self.assertEqual(build.classify_court('CO - U.S. Bankruptcy Court for the District of Colorado', 'CO'), 'bankruptcy')
        self.assertEqual(build.classify_court('U.S. District Court for the District of Nevada', None), 'us_district')
        self.assertIsNone(build.classify_court('Yurok Tribal Court', 'CA'))

    def test_us_district_without_fjc_record_is_not_called_a_district_or_magistrate_judge(self):
        entity = {'courts': ['U.S. District Court for the District of Nevada'], 'state_codes': ['NV'], 'biography': '', 'service': []}
        self.assertEqual(build.pattern_role(entity)['value'], 'other')
        entity['biography'] = 'Jane Roe is a United States magistrate judge for the District of Nevada.'
        self.assertEqual(build.pattern_role(entity)['value'], 'magistrate')
        entity['biography'] += ' She was later appointed by President X as a district judge.'
        self.assertEqual(build.pattern_role(entity)['value'], 'other')

    def test_completeness_is_deterministic(self):
        flags = {'fjc_appointments': True, 'fjc_status': True, 'role_established': True}
        first, second = build.completeness(flags), build.completeness(dict(flags))
        self.assertEqual(first, second)
        self.assertEqual(first['score'], 50)
        self.assertEqual(sum(build.WEIGHTS[i][1] for i in range(len(build.WEIGHTS))), 100)
        self.assertEqual(set(first['present']) | set(first['missing']), {name for name, _ in build.WEIGHTS})


class BuiltFilesTests(unittest.TestCase):
    def test_envelope_and_hashes(self):
        gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        for key in ('schema_version', 'status', 'ready', 'validated_at', 'data_files', 'counts', 'checks', 'qualification',
                    'license_ref', 'inputs'):
            self.assertIn(key, gate)
        self.assertEqual((gate['status'], gate['ready']), ('passed', True))
        for entry in gate['data_files']:
            raw = (HERE / entry['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
            self.assertEqual(raw.count(b'\n'), entry['rows'])
        self.assertEqual(gate['counts']['bridged'] + gate['counts']['bridge_withheld'], 3710)


if __name__ == '__main__':
    unittest.main()
