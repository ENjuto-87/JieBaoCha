"""第3课离线检查。模拟 API 和窗口控件；不调用真实接口，不证明实际窗口布局。"""
import json
import os
from queue import Queue
from threading import Event
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

import demo_gui
import review_service
from review_display import format_result, run_review_job

TEXT = demo_gui.SAMPLE
RISK = {"risk_level": "medium", "issue": "需核对", "reason": "测试理由", "evidence": ["保证方式：", "原文不存在的句子"]}


def sdk_response(data):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data, ensure_ascii=False)))])
    create = Mock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return SimpleNamespace(OpenAI=lambda **kwargs: client), create


def fake_app():
    """为行为测试替换窗口对象；不创建 Tk 图形窗口。"""
    app = demo_gui.ContractInputApp.__new__(demo_gui.ContractInputApp)
    app.root = Mock()
    app.prepared_text = TEXT
    app.last_result = {"old": True}
    app.busy = False
    app.closed = False
    app.review_fn = None
    app.result_queue = Queue()
    app.poll_id = None
    app.input_controls = [Mock(), Mock()]
    app.export_buttons = [Mock(), Mock(), Mock()]
    app.report_snapshot = None
    app.prepared_source = None
    app.source_record = None
    for name in ("editor", "encoding_box", "review_button", "progress", "status", "tabs", "preview", "local_output", "ai_output", "message_output", "use_ai", "anonymized"):
        setattr(app, name, Mock())
    app.editor.get.return_value = TEXT
    app.use_ai.get.return_value = False
    return app


class Day3Checks(unittest.TestCase):
    def test_original_text_is_passed_without_trimming(self):
        text = "  合同\r\n保证方式：\n "
        q = Queue()
        fn = Mock(return_value={"status": "local_only", "local_findings": [], "ai_findings": [], "messages": []})
        run_review_job(text, False, q, fn)
        fn.assert_called_once_with(text, use_ai=False)
        self.assertEqual(q.get()[0], "ok")

    def test_local_mode_never_calls_api(self):
        q = Queue()
        with patch.object(review_service, "call_deepseek", side_effect=AssertionError("unexpected API")):
            run_review_job(TEXT, False, q)
        kind, result, displays = q.get()
        self.assertEqual(kind, "ok")
        self.assertEqual(result["status"], "local_only")
        self.assertIn("本次关闭 AI", displays[3])
        self.assertTrue(result["local_findings"])

    def test_missing_key_keeps_local_findings(self):
        q = Queue()
        isolated_env = {"CONTRACT_REVIEW_AI_CONFIG": str(os.path.join(os.path.dirname(__file__), ".nonexistent-test-ai-config.json"))}
        with patch.dict(os.environ, isolated_env, clear=True), patch.object(review_service, "call_deepseek", side_effect=AssertionError("unexpected API")):
            run_review_job(TEXT, True, q)
        kind, result, displays = q.get()
        self.assertEqual(result["status"], "local_only")
        self.assertIn("未配置 DEEPSEEK_API_KEY", displays[3])

    def test_success_displays_both_evidence_categories(self):
        sdk, create = sdk_response({"risks": [RISK]})
        q = Queue()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-only"}), patch.dict(sys.modules, {"openai": sdk}):
            run_review_job(TEXT, True, q)
        kind, result, displays = q.get()
        self.assertEqual(result["status"], "completed")
        create.assert_called_once()
        self.assertIn("[已匹配] 保证方式：", displays[2])
        self.assertIn("[未匹配] 原文不存在的句子", displays[2])

    def test_bad_ai_data_keeps_local_findings(self):
        sdk, _ = sdk_response({"risks": [dict(RISK, risk_level="HIGH")]})
        q = Queue()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-only"}), patch.dict(sys.modules, {"openai": sdk}):
            run_review_job(TEXT, True, q)
        _, result, displays = q.get()
        self.assertEqual(result["status"], "ai_incomplete")
        self.assertTrue(result["local_findings"])
        self.assertIn("AI 审查未完成", displays[3])

    def test_unexpected_error_does_not_expose_payload(self):
        q = Queue()
        run_review_job(TEXT, True, q, Mock(side_effect=RuntimeError("private-contract-and-key")))
        self.assertEqual(q.get(), ("error", "RuntimeError", None))

    def test_empty_ai_result_is_not_safe_verdict(self):
        result = {"status": "completed", "local_findings": [], "ai_findings": [], "messages": ["仍须人工复核"]}
        displays = format_result(result)
        self.assertIn("不表示", displays[1])
        self.assertIn("没有可展示", displays[2])

    def test_edit_invalidates_old_result_and_confirmation(self):
        app = fake_app()
        app.invalidate_input()
        self.assertIsNone(app.prepared_text)
        self.assertIsNone(app.last_result)
        app.review_button.configure.assert_called_with(state="disabled")

    def test_changed_text_is_blocked_even_before_modified_event(self):
        app = fake_app()
        app.editor.get.return_value = TEXT.replace("100000", "200000")
        with patch.object(demo_gui, "Thread") as thread, patch.object(demo_gui.messagebox, "showwarning"):
            app.start_review()
        thread.assert_not_called()
        self.assertIsNone(app.prepared_text)

    def test_one_background_request_and_controls_recover(self):
        app = fake_app()
        entered, release = Event(), Event()
        calls = []
        def delayed(text, use_ai):
            calls.append((text, use_ai))
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test worker wait")
            return review_service.review_contract(text, use_ai=False)
        app.review_fn = delayed
        try:
            app.start_review()
            self.assertTrue(entered.wait(2))
            self.assertTrue(app.busy)
            app.editor.configure.assert_called_with(state="disabled")
            app.start_review()
            self.assertEqual(len(calls), 1)
            release.set()
            event = app.result_queue.get(timeout=3)
            app.result_queue.put(event)
            app.poll_result()
            self.assertFalse(app.busy)
            self.assertEqual(app.last_result["status"], "local_only")
            app.editor.configure.assert_called_with(state="normal")
        finally:
            release.set()

    def test_error_allows_retry_and_does_not_show_old_result(self):
        app = fake_app()
        app.clear_results()
        app.set_busy(True)
        app.result_queue.put(("error", "RuntimeError", None))
        app.poll_result()
        self.assertFalse(app.busy)
        self.assertIsNone(app.last_result)
        app.review_button.configure.assert_called_with(state="normal")

    def test_close_cancels_poll_without_accessing_destroyed_window(self):
        app = fake_app()
        app.poll_id = "poll-test"
        app.close()
        app.root.after_cancel.assert_called_once_with("poll-test")
        app.root.destroy.assert_called_once()
        app.poll_result()
        app.status.set.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
