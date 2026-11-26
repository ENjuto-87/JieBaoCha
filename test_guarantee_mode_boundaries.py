"""Boundary tests through the existing local service, no API and no private paths."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import contract_checker
import review_service
from report_export import build_snapshot, save_json

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'test_samples' / 'guarantee_mode'

def cases():
    return json.loads((DATA / 'boundary-cases.json').read_bytes())['cases']

def observe(case):
    text = case['text']
    assert hashlib.sha256(text.encode()).hexdigest() == case['input_sha256']
    with patch.object(review_service, 'call_deepseek', side_effect=AssertionError('No AI in local tests')):
        result = review_service.review_contract(text, use_ai=False)
    mode = contract_checker.recognize_explicit_guarantee_mode(text)
    missing, empty = contract_checker.check_guarantee_terms(text)
    guarantee = [f for f in result['local_findings'] if f['risk_name'] == '保证方式']
    conflicts = [f for f in guarantee if '冲突' in f['risk_message']]
    reasons = []
    if mode not in case['allowed_classifications']:
        reasons.append(f'classifier returned {mode!r}; expected {case["allowed_classifications"]!r}')
    if bool(conflicts) != case['expected_conflict_prompt']:
        reasons.append('conflict prompt does not match expectation')
    if mode is None and not any(('人工' in f['risk_message'] or '核实' in f['risk_message']) for f in guarantee):
        reasons.append('no manual-review finding for unconfirmed candidate')
    if case['expected_conflict_prompt'] and any(
        any(word in f['risk_message'] for word in ('未找到', '未约定', '未识别到')) for f in guarantee
    ):
        reasons.append('conflict was also described as missing/unagreed')
    if set(result) != {'status', 'local_findings', 'ai_findings', 'messages'}:
        reasons.append('external result keys changed')
    if any(set(f) != {'risk_name', 'risk_message'} for f in result['local_findings']):
        reasons.append('local finding schema changed')
    return {'id': case['id'], 'roles': case['roles'], 'text': text,
            'input_sha256': case['input_sha256'], 'expected': case,
            'classification': mode, 'missing_terms': missing, 'empty_terms': empty,
            'result': result, 'passed': not reasons, 'failure_reasons': reasons,
            'requires_human_legal_review': True}

class GuaranteeBoundaryTests(unittest.TestCase):
    def test_task_boundaries(self):
        for case in cases():
            if case['id'].startswith('B'):
                with self.subTest(case=case['id']):
                    actual = observe(case)
                    self.assertTrue(actual['passed'], actual['failure_reasons'])

    def test_engineering_variants(self):
        for case in cases():
            if case['id'].startswith('S'):
                with self.subTest(case=case['id']):
                    actual = observe(case)
                    self.assertTrue(actual['passed'], actual['failure_reasons'])

    def test_existing_export_schema_and_roundtrip(self):
        import tempfile
        for case in cases():
            with self.subTest(case=case['id']):
                actual = observe(case)
                snapshot = build_snapshot(case['text'], actual['result'], False)
                self.assertEqual(snapshot['schema_version'], '1.1')
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'local.json'
                    save_json(snapshot, path)
                    saved = json.loads(path.read_bytes())
                self.assertEqual(saved, snapshot)
                self.assertEqual(saved['contract_text'], case['text'])
                self.assertEqual(saved['result'], actual['result'])

    def test_commitment_quote_fidelity_endings(self):
        import re
        for text in ('甲向乙承诺承担一般保证责任', '甲向乙承诺承担一般保证责任。', '甲向乙承诺承担一般保证责任；'):
            with self.subTest(text=text):
                result = review_service.review_contract(text, use_ai=False)
                guarantee = [f for f in result['local_findings'] if f['risk_name'] == '保证方式']
                self.assertEqual(len(guarantee), 1)
                msg = guarantee[0]['risk_message']
                match = re.search(r'原文“([^”]*)”', msg)
                self.assertIsNotNone(match, msg)
                self.assertEqual(match.group(1), text)
                self.assertIn('作出承诺的是甲', msg)
                self.assertIn('保证人', msg)
                self.assertIn('身份', msg)

    def test_multiline_fields_and_positive_legal_modifiers(self):
        positive_cases = {
            'multiline_no_period': ('保证人：丙公司\n保证方式：连带责任保证\n保证期间：两年', 'explicit_joint'),
            'multiline_followed_by_negation_elsewhere': ('保证方式：一般保证\n本合同未尽事宜双方另行协商。', 'explicit_general'),
            'separate_paragraph_loan_condition': ('放款条件：借款人已开立指定账户。\n\n保证人：丙公司\n保证方式：连带责任保证', 'explicit_joint'),
            'irrevocable_joint': ('丙不可撤销地承担连带责任保证责任。', 'explicit_joint'),
            'unconditional_general': ('丙无条件承担一般保证责任。', 'explicit_general'),
            'combined_modifiers_field': ('保证方式：无条件、不可撤销的连带责任保证。', 'explicit_joint'),
        }
        for name, (text, expected) in positive_cases.items():
            with self.subTest(name=name):
                self.assertEqual(contract_checker.recognize_explicit_guarantee_mode(text), expected)
                findings = review_service.review_contract(text, use_ai=False)['local_findings']
                self.assertFalse([f for f in findings if f['risk_name'] == '保证方式'])

        conservative_cases = {
            'negated_irrevocable': '丙不承担不可撤销的连带责任保证责任。',
            'conditional_unconditional': '如乙逾期未还，丙无条件承担一般保证责任。',
            'missing_subject_modifier': '无条件承担一般保证责任。',
            'adjacent_condition_field': '条件：乙逾期未还\n保证方式：连带责任保证。',
            'adjacent_remark_field': '备注：待各方另行确认\n保证方式：一般保证。',
            'global_guarantee_condition_field': '保证条件：丙完成内部审批后生效\n\n保证方式：连带责任保证。',
        }
        for name, text in conservative_cases.items():
            with self.subTest(name=name):
                self.assertIsNone(contract_checker.recognize_explicit_guarantee_mode(text))
                findings = review_service.review_contract(text, use_ai=False)['local_findings']
                self.assertTrue(any(f['risk_name'] == '保证方式' for f in findings))

    def test_party_role_conflict_and_single_precise_prompt(self):
        role_cases = [
            ('借款人：乙\n乙承担连带责任保证责任。', '乙承担连带责任保证责任。', '乙', '借款人'),
            ('出借人：甲\n甲承担一般保证责任。', '甲承担一般保证责任。', '甲', '出借人'),
            ('甲向乙出借人民币100万元。\n乙承担连带责任保证责任。', '乙承担连带责任保证责任。', '乙', '借款人'),
        ]
        for text, expected_quote, expected_party, expected_role in role_cases:
            with self.subTest(text=text):
                self.assertIsNone(contract_checker.recognize_explicit_guarantee_mode(text))
                res = review_service.review_contract(text, use_ai=False)
                guarantee = [f for f in res['local_findings'] if f['risk_name'] == '保证方式']
                self.assertEqual(len(guarantee), 1, guarantee)
                msg = guarantee[0]['risk_message']
                self.assertIn(f'原文“{expected_quote}”', msg)
                self.assertIn(f'主体{expected_party}', msg)
                self.assertIn(expected_role, msg)
                self.assertIn('人工核实', msg)

    def test_gold001_guarantee_applicability_gate(self):
        gold_001 = '甲向乙出借人民币100万元。乙以其享有的土地经营权提供抵押担保。本合同没有第三人提供保证，双方也不存在其他保证合同。'
        res = review_service.review_contract(gold_001, use_ai=False)
        names = [f['risk_name'] for f in res['local_findings']]
        for inapplicable in ('保证方式', '保证期间', '保证范围'):
            self.assertNotIn(inapplicable, names)
        for general in ('主债权金额', '争议解决', '实现债权费用', '送达地址'):
            self.assertIn(general, names)
        self.assertTrue(any('未套用保证方式、保证期间及保证范围规则' in m for m in res['messages']))

        pure_mortgage_warranty = '甲向乙出借100万元，乙以名下房产提供抵押担保，乙方保证对抵押物享有合法所有权。'
        res_m = review_service.review_contract(pure_mortgage_warranty, use_ai=False)
        self.assertFalse(any(f['risk_name'] in ('保证方式', '保证期间', '保证范围') for f in res_m['local_findings']))

        mixed_security = '甲向乙出借100万元，乙以房产提供抵押担保，丙作为保证人对上述债务提供保证。'
        res_mix = review_service.review_contract(mixed_security, use_ai=False)
        mix_names = [f['risk_name'] for f in res_mix['local_findings']]
        self.assertIn('保证方式', mix_names)
        self.assertIn('保证期间', mix_names)
        self.assertIn('保证范围', mix_names)

if __name__ == '__main__':
    unittest.main(verbosity=2)
