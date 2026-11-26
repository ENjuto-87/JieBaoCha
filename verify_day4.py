"""第4课离线检查：不启动图形窗口，不调用 API，不读取真实 Key。"""
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
from queue import Queue
import tempfile
import unittest
import time
from unittest.mock import Mock, patch

from demo_gui import ContractInputApp
from report_export import build_snapshot, render_html, save_json, save_html

TEXT = '  脱敏借款合同\r\n甲向乙借款100000元。\n<script>alert("测试")</script> & <img src=x>\n'


def result(status="completed"):
    return {"status": status,
            "local_findings": [{"risk_name": "保证方式", "risk_message": "请人工核查是否适用保证规则"}],
            "ai_findings": [{"risk_level": "medium", "issue": "离线样例：人工核对",
                             "reason": "这是导出验收数据，不是法律结论。",
                             "evidence": ["甲向乙借款100000元。", "不存在的引用"],
                             "verified_evidence": ["甲向乙借款100000元。"],
                             "unverified_evidence": ["不存在的引用"]}] if status == "completed" else [],
            "messages": ["离线测试数据，未调用真实 API。"]}


def snapshot(status="completed"):
    return build_snapshot(TEXT, result(status), True, True, "offline_simulation")


def app_stub():
    app = ContractInputApp.__new__(ContractInputApp)
    app.root = Mock()
    app.prepared_text = TEXT
    app.last_result = result()
    app.report_snapshot = snapshot()
    app.busy = False
    app.closed = False
    app.review_fn = Mock()
    app.result_queue = Queue()
    app.export_queue = Queue()
    app.export_poll_id = None
    app.poll_id = None
    app.input_controls = [Mock()]
    app.export_buttons = [Mock(), Mock()]
    for name in ("editor", "encoding_box", "review_button", "progress", "status", "tabs",
                 "preview", "local_output", "ai_output", "message_output", "use_ai", "anonymized"):
        setattr(app, name, Mock())
    app.editor.get.return_value = TEXT
    app.use_ai.get.return_value = True
    app.anonymized.get.return_value = True
    return app


def finish_export(app):
    deadline = time.monotonic() + 10
    while app.export_queue.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    if app.export_queue.empty():
        raise AssertionError("export worker did not complete")
    app.poll_export()


class TagReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


class Day4Checks(unittest.TestCase):
    def test_json_roundtrip_preserves_original_and_unicode(self):
        data = snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "结果.json"
            save_json(data, path)
            raw = path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
        self.assertEqual(loaded["contract_text"], TEXT)
        self.assertEqual(loaded["result"], data["result"])
        self.assertIn("甲向乙", raw)

    def test_snapshot_cannot_follow_later_result_mutation(self):
        response = result()
        data = build_snapshot(TEXT, response, True)
        response["ai_findings"][0]["verified_evidence"].clear()
        self.assertEqual(data["result"]["ai_findings"][0]["verified_evidence"], ["甲向乙借款100000元。"])

    def test_extra_key_and_config_fields_not_saved(self):
        response = result()
        response["api_key"] = "never-save-this-key"
        response["sdk_response"] = {"secret": "never-save-sdk"}
        response["ai_findings"][0]["extra_config"] = "never-save-config"
        serialized = json.dumps(build_snapshot(TEXT, response, True))
        for secret in ("never-save-this-key", "never-save-sdk", "never-save-config"):
            self.assertNotIn(secret, serialized)

    def test_html_treats_contract_as_text(self):
        content = render_html(snapshot())
        reader = TagReader()
        reader.feed(content)
        self.assertNotIn("script", reader.tags)
        self.assertNotIn("img", reader.tags)
        self.assertIn("&lt;script&gt;", content)
        self.assertIn("&amp;", content)

    def test_evidence_and_review_limits_remain_visible(self):
        content = render_html(snapshot())
        for text in ("不存在的引用", "原文精确匹配", "未匹配的引用", "不证明法律判断正确",
                     "主体身份缺失类意见", "人工复核记录", "附件：本次审查原文"):
            self.assertIn(text, content)

    def test_incomplete_ai_and_local_only_can_be_saved(self):
        for status in ("ai_incomplete", "local_only"):
            data = snapshot(status)
            content = render_html(data)
            self.assertIn(status, content)
            self.assertIn("没有可展示的 AI 意见", content)
            self.assertTrue(data["result"]["local_findings"])

    def test_simulation_does_not_look_like_real_ai_report(self):
        self.assertIn("离线模拟 / 自定义入口测试报告", render_html(snapshot()))

    def test_invalid_input_has_no_review_report(self):
        with self.assertRaises(ValueError):
            snapshot("invalid_input")

    def test_distinct_reviews_get_distinct_ids(self):
        self.assertNotEqual(snapshot()["report_id"], snapshot()["report_id"])

    def test_failed_replace_preserves_old_file_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_text("old report", encoding="utf-8")
            with patch("report_export.os.replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    save_html(snapshot(), path)
            self.assertEqual(path.read_text(), "old report")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_cancel_does_not_write_or_call_review(self):
        app = app_stub()
        with patch("demo_gui.filedialog.asksaveasfilename", return_value=""), patch("demo_gui.save_json") as writer:
            app.export_report("json")
        writer.assert_not_called()
        app.review_fn.assert_not_called()
        self.assertIsNotNone(app.report_snapshot)

    def test_edit_before_modified_event_blocks_old_export(self):
        app = app_stub()
        app.editor.get.return_value = TEXT.replace("100000", "200000")
        with patch("demo_gui.messagebox.showwarning"), patch("demo_gui.filedialog.asksaveasfilename") as dialog:
            app.export_report("html")
        dialog.assert_not_called()
        self.assertIsNone(app.report_snapshot)
        for button in app.export_buttons:
            button.configure.assert_called_with(state="disabled")

    def test_mode_change_invalidates_report(self):
        app = app_stub()
        app.on_mode_change()
        self.assertIsNone(app.report_snapshot)
        self.assertEqual(app.prepared_text, TEXT)

    def test_busy_blocks_export(self):
        app = app_stub()
        app.set_busy(True)
        with patch("demo_gui.filedialog.asksaveasfilename") as dialog:
            app.export_report("json")
        dialog.assert_not_called()
        for button in app.export_buttons:
            button.configure.assert_called_with(state="disabled")

    def test_save_error_keeps_result_available(self):
        app = app_stub()
        before = copy.deepcopy(app.report_snapshot)
        with patch("demo_gui.filedialog.asksaveasfilename", return_value="report.json"), \
             patch("demo_gui.save_json", side_effect=PermissionError), patch("demo_gui.messagebox.showerror") as error:
            app.export_report("json")
            finish_export(app)
        error.assert_called_once()
        self.assertEqual(app.report_snapshot, before)

    def test_json_and_html_use_same_snapshot_without_api(self):
        app = app_stub()
        before = copy.deepcopy(app.report_snapshot)
        with tempfile.TemporaryDirectory() as directory:
            jp, hp = Path(directory) / "result.json", Path(directory) / "report.html"
            with patch("demo_gui.filedialog.asksaveasfilename", side_effect=[str(jp), str(hp)]), \
                 patch("demo_gui.messagebox.showinfo"):
                app.export_report("json")
                finish_export(app)
                app.export_report("html")
                finish_export(app)
            saved = json.loads(jp.read_text(encoding="utf-8"))
            content = hp.read_text(encoding="utf-8")
        self.assertEqual(saved, before)
        self.assertIn(before["report_id"], content)
        app.review_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
