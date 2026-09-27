"""Offline checks of the build helpers and of the published files (run: python -m unittest discover -s <this folder> -p test_build.py)."""
import hashlib
import json
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')
import build  # noqa: E402

HERE = Path(__file__).resolve().parent


class PeriodParsing(unittest.TestCase):
    def test_single_twelve_month_period(self):
        label, end, twelve = build.parse_period('Table C-3. Civil Cases Commenced During the 12-Month Period Ending June 30, 2026')
        self.assertEqual((label, end, twelve), ('12-Month Period Ending June 30, 2026', '2026-06-30', True))
        self.assertEqual(build.twelve_month_start('2026-06-30'), '2025-07-01')

    def test_year_lists_and_ranges_take_the_latest_named_date(self):
        self.assertEqual(build.parse_period('12-Month Periods Ending March 31, 2025 and 2026')[1:], ('2026-03-31', False))
        self.assertEqual(build.parse_period('Periods Ending June 30, 1990, and September 30, 1995 Through 2025')[1], '2025-09-30')
        self.assertEqual(build.parse_period('12-MONTH PERIODS ENDING JUNE 30, 2017, 2022, 2025, AND 2026')[1], '2026-06-30')
        self.assertEqual(build.parse_period('As of September 30, 2025, and March 31, 2026')[1], '2026-03-31')

    def test_no_date_means_unknown(self):
        self.assertEqual(build.parse_period('Folder date in URL (2025-01 / 2025-03) is an upload path, not an effective date'), (None, None, False))
        self.assertEqual(build.parse_period(''), (None, None, False))

    def test_cjra_total_line(self):
        m = build.TOTAL_RE.match('Total Motions for District Judge : Moss, Randolph D.                  19')
        self.assertEqual((m.group(1), m.group(2), m.group(3), m.group(4)), ('Motions', 'District Judge', 'Moss, Randolph D.', '19'))
        m = build.TOTAL_RE.match('Total All Cases for Magistrate Judge  HARVEY, G. MICHAEL 0')
        self.assertEqual((m.group(2), m.group(3), m.group(4)), ('Magistrate Judge', 'HARVEY, G. MICHAEL', '0'))


class PublishedFiles(unittest.TestCase):
    def test_validation_hashes_and_originals(self):
        gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        self.assertEqual((gate['status'], gate['ready']), ('passed', True))
        for entry in gate['data_files']:
            self.assertEqual(hashlib.sha256((HERE / entry['path']).read_bytes()).hexdigest(), entry['sha256'])
        tables = [json.loads(l) for l in (HERE / 'tables.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        self.assertEqual(len(tables), gate['counts']['tables'])
        for t in tables:
            raw = (HERE / t['raw_path']).resolve()
            self.assertTrue(raw.is_relative_to((HERE / 'raw').resolve()))
            self.assertEqual(raw.stat().st_size, t['bytes'])
            self.assertLessEqual(len(t['preview']), build.MAX_SHEETS)
            for p in t['preview']:
                self.assertLessEqual(len(p['data_rows']), build.MAX_DATA_ROWS)
            if t['period_end'] is None:
                self.assertIsNone(t['period_label'])
            else:
                self.assertTrue(t['period_basis'])

    def test_cjra_rows_reconcile_with_table_2(self):
        gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))
        rows = [json.loads(l) for l in (HERE / 'cjra_rows.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        for number, stats in gate['counts']['cjra'].items():
            mine = [r for r in rows if r['table_number'] == number]
            self.assertEqual(len(mine), stats['judge_rows'])
            self.assertEqual(sum(r['count'] for r in mine), stats['sum_of_counts'])
            total = next(x for x in stats['reconciliation_vs_cjra_table_2'] if x['circuit'] == 'Total')
            self.assertEqual(total['cjra_table_2_printed'], stats['sum_of_counts'])
            self.assertTrue(all(r['period_end'] == '2026-03-31' and r['judge_match']['status'] == 'not_attempted' for r in mine))


if __name__ == '__main__':
    unittest.main()
