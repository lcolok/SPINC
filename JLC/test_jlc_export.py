#!/usr/bin/env python3
"""Adversarial round-trip tests using copies of the frozen production CSVs.

These exercise acceptance logic, not JLCEDA, electronics, or a physical board.
Never mutate the repository's production inputs.
"""
from __future__ import annotations

import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import verify_jlc_export as verifier


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bom = verifier.read_csv(verifier.BASE_BOM)
        self.cpl = verifier.read_csv(verifier.BASE_CPL)
        self.report = self.root / 'report.json'

    def change(self, rows, ref, column, value):
        next(row for row in rows if row['Designator'] == ref)[column] = value

    def run_gate(self, *, bom=None, cpl=None, extra=()):
        for name, rows in [('bom.csv', self.bom if bom is None else bom),
                           ('cpl.csv', self.cpl if cpl is None else cpl)]:
            with (self.root / name).open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        args = ['verify_jlc_export.py', '--bom', str(self.root / 'bom.csv'),
                '--cpl', str(self.root / 'cpl.csv'), '--report', str(self.report), *extra]
        # Seed an old PASS: every failed invocation must invalidate it.
        self.report.write_text('{"result":"pass","stale":true}\n')
        with patch('sys.argv', args), contextlib.redirect_stdout(io.StringIO()):
            code = verifier.main()
        data = json.loads(self.report.read_text(), parse_constant=lambda x: self.fail(f'nonstandard JSON {x}'))
        self.assertNotIn('stale', data)
        self.assertEqual(data['result'], 'pass' if code == 0 else 'fail')
        if code:
            self.assertTrue(data['failures'])
        return code, data

    def test_unchanged_frozen_source_passes(self):
        code, data = self.run_gate()
        self.assertEqual(code, 0)
        self.assertEqual(data['assembled_reference_count'], 91)
        self.assertEqual(data['compared_cpl_reference_count'], 91)

    def test_global_translation_passes(self):
        for row in self.cpl:
            row['Mid X'] = str(float(row['Mid X']) + 100)
            row['Mid Y'] = str(float(row['Mid Y']) - 20)
        self.assertEqual(self.run_gate()[0], 0)

    def test_mirror_requires_consistent_angles_and_remains_warning(self):
        for row in self.cpl:
            row['Mid Y'] = str(-float(row['Mid Y']))
            row['Rotation'] = str(-float(row['Rotation']))
        code, data = self.run_gate()
        self.assertEqual(code, 0)
        self.assertEqual(data['geometry_fit']['mode'], 'mirror-y-coordinate-frame')
        self.assertTrue(data['warnings'])

    def test_mirror_without_angle_conversion_fails(self):
        for row in self.cpl:
            row['Mid Y'] = str(-float(row['Mid Y']))
        self.assertNotEqual(self.run_gate()[0], 0)

    def test_real_mil_suffix_conversion_passes(self):
        for row in self.cpl:
            for axis in ['Mid X', 'Mid Y']:
                row[axis] = f"{float(row[axis]) / 0.0254:.10f} mil"
        self.assertEqual(self.run_gate()[0], 0)

    def test_explicit_mil_headers_pass(self):
        for row in self.cpl:
            for axis in ['Mid X', 'Mid Y']:
                row[f'{axis} (mil)'] = str(float(row.pop(axis)) / 0.0254)
        self.assertEqual(self.run_gate()[0], 0)

    def test_conflicting_unit_suffix_fails(self):
        for row in self.cpl:
            row['Mid X (mm)'] = row.pop('Mid X') + ' mil'
        self.assertNotEqual(self.run_gate()[0], 0)

    def test_mislabeled_mil_is_not_silently_mm(self):
        for row in self.cpl:
            for axis in ['Mid X', 'Mid Y']:
                row[axis] += ' mil'
        self.assertNotEqual(self.run_gate()[0], 0)

    def test_aliases_and_omitted_quantity_still_pass(self):
        for row in self.bom:
            row['Supplier Part'] = row.pop('LCSC Part #')
            row['References'] = row.pop('Designator').replace(',', ';')
            del row['Quantity']
        for row in self.cpl:
            row['X (mm)'] = row.pop('Mid X')
            row['Y (mm)'] = row.pop('Mid Y')
            row['Side'] = row.pop('Layer')
        self.assertEqual(self.run_gate()[0], 0)

    def test_dnp_in_bom_fails(self):
        self.bom.append({'Designator':'TH3','Footprint':'0603','Quantity':'1',
                         'Value':'Thermistor_NTC','LCSC Part #':'C13564'})
        self.assertNotEqual(self.run_gate()[0], 0)

    def test_cpl_only_dnp_and_fiducials_are_optional(self):
        cpl = [r for r in self.cpl if r['Designator'] not in verifier.CPL_ONLY_ALLOWED]
        self.assertEqual(self.run_gate(cpl=cpl)[0], 0)

    def test_missing_populated_reference_fails(self):
        self.assertNotEqual(self.run_gate(cpl=[r for r in self.cpl if r['Designator'] != 'Q5'])[0], 0)

    def test_duplicate_reference_fails(self):
        self.cpl.append(copy.deepcopy(self.cpl[0]))
        self.assertNotEqual(self.run_gate()[0], 0)

    def test_extra_column_alias_is_ambiguous(self):
        for row in self.cpl:
            row['X'] = row['Mid X']
        self.assertNotEqual(self.run_gate()[0], 0)

    def test_duplicate_csv_headers_fail(self):
        p = self.root/'duplicate.csv'
        p.write_text('Designator,Mid X,Mid X\nQ5,1,2\n')
        with self.assertRaises(ValueError):
            verifier.read_csv(p)

    def test_csv_width_mismatch_fails(self):
        for text in ['A,B\n1,2,3\n','A,B\n1\n']:
            with self.subTest(text=text):
                p=self.root/'bad.csv';p.write_text(text)
                with self.assertRaises(ValueError):
                    verifier.read_csv(p)

    def test_large_finite_inputs_cannot_overflow_to_pass(self):
        for i, row in enumerate(self.cpl):
            row['Mid X'] = '1.7e308' if i % 2 else '-1.7e308'
        self.assertNotEqual(self.run_gate()[0], 0)

    def test_nonfinite_or_negative_tolerances_fail(self):
        for flag in ['--xy-tolerance-mm', '--rotation-tolerance-deg']:
            for val in ['nan','inf','-1','1e999','']:
                with self.subTest(flag=flag, val=val):
                    self.assertNotEqual(self.run_gate(extra=[flag,val])[0], 0)

    def test_nan_tolerance_does_not_hide_placement_drift(self):
        self.change(self.cpl, 'Q5', 'Mid X', '999')
        self.assertNotEqual(self.run_gate(extra=['--xy-tolerance-mm','nan'])[0], 0)

    def test_missing_input_invalidates_previous_pass(self):
        self.report.write_text('{"result":"pass","stale":true}')
        argv=['verify_jlc_export.py','--bom',str(self.root/'missing.csv'),
              '--cpl',str(verifier.BASE_CPL),'--report',str(self.report)]
        with patch('sys.argv',argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(verifier.main(),0)
        self.assertEqual(json.loads(self.report.read_text())['result'],'fail')

    def test_report_must_not_clobber_input(self):
        source = self.root / 'source.csv'
        source.write_text('original input')
        argv = ['verify_jlc_export.py', '--bom', str(source), '--cpl', str(verifier.BASE_CPL),
                '--report', str(source)]
        with patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(verifier.main(), 0)
        self.assertEqual(source.read_text(), 'original input')

    def test_missing_arguments_invalidate_old_report(self):
        self.report.write_text('{"result":"pass","stale":true}')
        with patch('sys.argv', ['verify_jlc_export.py','--report',str(self.report)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(verifier.main(), 0)
        self.assertEqual(json.loads(self.report.read_text())['result'], 'fail')


def make_rejection_test(dataset, ref, column, value):
    def test(self):
        self.change(getattr(self, dataset), ref, column, value)
        self.assertNotEqual(self.run_gate()[0], 0)
    return test


# Every case below used to be insufficiently exercised by --self-test. These
# are independent tests, not many assertions against the same happy path.
for name, dataset, ref, column, value in [
    ('nan_x','cpl','Q5','Mid X','nan'),
    ('nan_y','cpl','Q5','Mid Y','NaN'),
    ('inf_x','cpl','Q5','Mid X','inf'),
    ('overflow_x','cpl','Q5','Mid X','1e999'),
    ('nan_angle','cpl','Q5','Rotation','nan'),
    ('inf_angle','cpl','Q5','Rotation','Infinity'),
    ('blank_zero_angle','cpl','Q5','Rotation',''),
    ('unknown_unit','cpl','Q5','Mid X','126.45in'),
    ('placement_drift','cpl','Q5','Mid X','126.55'),
    ('rotation_drift','cpl','Q5','Rotation','1'),
    ('wrong_side','cpl','Q5','Layer','bottom'),
    ('unknown_side','cpl','Q5','Layer','some-layer'),
    ('blank_side','cpl','Q5','Layer',''),
    ('wrong_quantity','bom','Q5','Quantity','2'),
    ('blank_quantity','bom','Q5','Quantity',''),
    ('float_quantity','bom','Q5','Quantity','1.5'),
    ('part_drift','bom','Q5','LCSC Part #','C999999'),
    ('ambiguous_part','bom','Q5','LCSC Part #','C37577 C999999'),
    ('blank_part','bom','Q5','LCSC Part #',''),
    ('malformed_reference','bom','Q5','Designator','Q5 broken'),
    ('no_reference','cpl','Q5','Designator',''),
]:
    setattr(RoundTripTests, 'test_reject_'+name, make_rejection_test(dataset,ref,column,value))


if __name__ == '__main__':
    unittest.main()
