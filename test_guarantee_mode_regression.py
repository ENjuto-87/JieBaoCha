import json
import hashlib
import unittest
from pathlib import Path

import contract_checker
from review_service import review_contract

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'test_samples' / 'guarantee_mode'
FROZEN_SHA256 = {
    'GOLD-004-original.json': '7e3d413c45dc8352b33c7370b5a549aeb4ee5f4ded294149bffc68f9101b4805',
    'GOLD-004-A-contract.txt': 'a243e669a5c5d51ba0a03b302405f28344fac5984408c2a426cd5f2e569b2bb1',
    'GOLD-004-B-contract.txt': '31ad84299cc7711abf77bc16e4305c781a0ce534e40f85abdce58bd2c7aab6e9',
}
CASES = {
    'T01': ('保证方式：一般保证。', 'explicit_general'),
    'T02': ('丙对乙的上述借款债务承担一般保证责任。', 'explicit_general'),
    'T03': ('丙承担连带责任保证。', 'explicit_joint'),
    'T04': ('合同未另行约定保证方式。', None),
    'T05': ('丙不承担一般保证责任。', None),
    'T06': ('保证方式约定不明确。', None),
}


class GuaranteeModeTests(unittest.TestCase):
    def test_classification_and_field_prompt(self):
        for name, (text, expected) in CASES.items():
            with self.subTest(name=name):
                classify = getattr(contract_checker, 'recognize_explicit_guarantee_mode', lambda text: None)
                self.assertEqual(classify(text), expected)
                missing, _ = contract_checker.check_guarantee_terms(text)
                self.assertEqual('保证方式' in missing, expected is None)

    def test_frozen_contracts(self):
        # Read verified project-local fixtures, preserving line endings exactly.
        for name, expected_sha256 in FROZEN_SHA256.items():
            self.assertEqual(hashlib.sha256((DATA / name).read_bytes()).hexdigest(), expected_sha256)
        baseline = json.loads((DATA / 'GOLD-004-original.json').read_bytes())
        cases = {
            'A': (DATA / 'GOLD-004-A-contract.txt').read_bytes().decode('utf-8'),
            'B': (DATA / 'GOLD-004-B-contract.txt').read_bytes().decode('utf-8'),
            'C': baseline['contract_text'],
        }
        for name in ('A', 'B'):
            self.assertEqual(cases[name].rsplit('\n\n', 1)[0], cases['C'].rsplit('\n\n', 1)[0])
        classify = getattr(contract_checker, 'recognize_explicit_guarantee_mode', lambda text: None)
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify(text), None if name == 'C' else 'explicit_general')
                findings = review_contract(text, use_ai=False)['local_findings']
                missing = any(x['risk_name'] == '保证方式' and '未找到' in x['risk_message'] for x in findings)
                self.assertEqual(missing, name == 'C')

    def test_conservative_cases(self):
        classify = getattr(contract_checker, 'recognize_explicit_guarantee_mode', lambda text: None)
        for text in ('未约定一般保证。', '丙不承担一般保证责任。',
                     '丙承担一般保证责任或连带责任保证。',
                     '丙承担一般保证责任。但不承担一般保证责任。',
                     '保证方式：一般保证\n保证方式：'):
            with self.subTest(text=text):
                if text.endswith('保证方式：'):
                    self.assertIn('保证方式', contract_checker.check_guarantee_terms(text)[1])
                    self.assertIsNone(classify(text))
                else:
                    self.assertIsNone(classify(text))


if __name__ == '__main__':
    unittest.main(verbosity=2)
