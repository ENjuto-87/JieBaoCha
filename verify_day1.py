"""第1课离线验收：模拟 SDK，不读取真实 Key，不产生 API 费用。"""

import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import contract_checker
import review_service


TEXT = "甲向乙借款100000元。\n保证方式：\n送达地址：另行提供。"


def sdk_response(content):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
    return SimpleNamespace(OpenAI=lambda **kwargs: client)


class Day1Checks(unittest.TestCase):
    def run_response(self, data):
        content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), patch.dict(sys.modules, {"openai": sdk_response(content)}):
            return review_service.review_contract(TEXT)

    def test_import_has_no_side_effects(self):
        # 独立进程，拦截文件读取、网络连接和 SDK 导入，防止模块缓存掩盖问题。
        script = """
import pathlib, socket, sys
def forbidden(*args, **kwargs):
    raise AssertionError('import caused I/O')
pathlib.Path.read_text = forbidden
socket.socket = forbidden
sys.modules['openai'] = None
import review_service
"""
        completed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_local_only_never_calls_ai(self):
        with patch.object(review_service, "call_deepseek", side_effect=AssertionError("unexpected AI")):
            result = review_service.review_contract(TEXT, use_ai=False)
        self.assertEqual(result["status"], "local_only")
        self.assertTrue(result["local_findings"])
        self.assertEqual(result["ai_findings"], [])
        self.assertEqual(set(result), {"status", "local_findings", "ai_findings", "messages"})

    def test_invalid_input_never_calls_ai(self):
        with patch.object(review_service, "call_deepseek", side_effect=AssertionError("unexpected AI")):
            for text in (None, "", " \n\t", 123):
                with self.subTest(text=text):
                    self.assertEqual(review_service.review_contract(text)["status"], "invalid_input")

    def test_missing_key(self):
        isolated_env = {"CONTRACT_REVIEW_AI_CONFIG": str(Path(__file__).with_name(".nonexistent-test-ai-config.json"))}
        with patch.dict(os.environ, isolated_env, clear=True), patch.object(review_service, "call_deepseek", side_effect=AssertionError("unexpected AI")):
            result = review_service.review_contract(TEXT)
        self.assertEqual(result["status"], "local_only")

    def test_empty_ai_risks(self):
        self.assertEqual(self.run_response({"risks": []})["status"], "completed")

    def test_evidence_matching_preserves_original(self):
        risk = {"risk_level": "medium", "issue": "需核查", "reason": "资料不完整", "evidence": ["甲向乙借款100000元。", "原文不存在的句子"]}
        result = self.run_response({"risks": [risk]})
        self.assertEqual(result["status"], "completed")
        item = result["ai_findings"][0]
        self.assertEqual(item["verified_evidence"], [risk["evidence"][0]])
        self.assertEqual(item["unverified_evidence"], [risk["evidence"][1]])

    def test_no_evidence_keeps_complete_result(self):
        result = self.run_response({"risks": [{"risk_level": "low", "issue": "需核查", "reason": "未写明事项", "evidence": []}]})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["ai_findings"][0]["verified_evidence"], [])

    def test_invalid_ai_data_preserves_local_result(self):
        base = {"risk_level": "medium", "issue": "需核查", "reason": "缺少信息", "evidence": []}
        bad_cases = [
            "not json", [], {}, {"risks": {}}, {"risks": [None]},
            *[{"risks": [dict(base, risk_level=value)]} for value in ("HIGH", "high ", None)],
            {"risks": [dict(base, issue=" ")]},
            {"risks": [dict(base, reason=" ")]},
            {"risks": [dict(base, evidence="原文")]},
            {"risks": [dict(base, evidence=[123])]},
            {"risks": [dict(base, evidence=[" "])]},
        ]
        for data in bad_cases:
            with self.subTest(data=data):
                result = self.run_response(data)
                self.assertEqual(result["status"], "ai_incomplete")
                self.assertTrue(result["local_findings"])
                self.assertEqual(result["ai_findings"], [])

    def test_request_failure_preserves_local_result(self):
        def fail(**kwargs):
            raise TimeoutError("private payload must not be printed")
        sdk = SimpleNamespace(OpenAI=fail)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}), patch.dict(sys.modules, {"openai": sdk}):
            result = review_service.review_contract(TEXT)
        self.assertEqual(result["status"], "ai_incomplete")
        self.assertTrue(result["local_findings"])
        self.assertNotIn("private payload", " ".join(result["messages"]))

    def test_repeated_empty_field_is_retained(self):
        _, empty = contract_checker.check_guarantee_terms("保证期间：一年\n保证期间：")
        self.assertIn("保证期间", empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
