"""Real Tk widgets/event loop on Windows; no external AI requests."""
import copy
import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from demo_gui import ContractInputApp, SAMPLE
from review_service import review_contract
from source_import import import_source
from ocr_engine import OcrError, RapidOcrEngine


class GuiChecks(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = ContractInputApp(self.root)

    def tearDown(self):
        if not self.app.closed:
            self.app.close()
        del self.app
        del self.root
        gc.collect()  # Finalize destroyed Tk roots on the main thread between tests.

    def pump(self, condition, timeout=10):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(condition(), "background job did not finish")

    def reviewed(self):
        self.app.replace_text(SAMPLE)
        self.app.prepare_input()
        self.app.start_review()
        self.pump(lambda: not self.app.busy)
        self.assertIsNotNone(self.app.report_snapshot)

    def test_review_responsive_and_edit_invalidates(self):
        ticks = []
        def slow_review(text, use_ai):
            time.sleep(0.25)
            return review_contract(text, use_ai=False)
        self.app.review_fn = slow_review
        self.app.replace_text(SAMPLE)
        self.app.prepare_input()
        self.root.after(30, lambda: ticks.append(True))
        self.app.start_review()
        self.pump(lambda: not self.app.busy)
        self.assertTrue(ticks)
        self.app.editor.insert("end", "修改金额")
        self.root.update()
        self.assertIsNone(self.app.report_snapshot)
        self.assertTrue(all(str(button["state"]) == "disabled" for button in self.app.export_buttons))

    def test_export_cancel_and_permission_failure_keep_snapshot(self):
        self.reviewed()
        before = copy.deepcopy(self.app.report_snapshot)
        with patch("demo_gui.filedialog.asksaveasfilename", return_value=""):
            self.app.export_report("docx")
        self.assertEqual(before, self.app.report_snapshot)
        with patch("demo_gui.filedialog.asksaveasfilename", return_value="blocked.docx"), \
             patch("demo_gui.save_docx", side_effect=PermissionError), \
             patch("demo_gui.messagebox.showerror") as error:
            self.app.export_report("docx")
            self.pump(lambda: not self.app.busy)
            error.assert_called_once()
        self.assertEqual(before, self.app.report_snapshot)
        self.assertTrue(all(str(button["state"]) == "normal" for button in self.app.export_buttons))

    def test_setting_invalidates_snapshot(self):
        self.reviewed()
        self.app.use_ai.set(True)
        self.app.on_mode_change()
        self.assertIsNone(self.app.report_snapshot)

    def test_real_ocr_edit_confirm_review_and_responsiveness(self):
        fixtures = Path(__file__).parent / "test_samples"
        if not (fixtures / "two_page_scan.pdf").is_file():
            self.skipTest("Run test_ocr.py first to create fictional fixtures")
        ticks = []
        self.root.after(20, lambda: ticks.append(True))
        self.app.start_import(fixtures / "two_page_scan.pdf")
        self.pump(lambda: not self.app.busy, timeout=40)
        self.assertTrue(ticks)
        self.assertIsNone(self.app.prepared_text)
        self.assertIsNone(self.app.report_snapshot)
        original = self.app.source_record["initial_text"]
        corrected = original.replace("100000", "130000")
        self.app.editor.delete("1.0", "end")
        self.app.editor.insert("1.0", corrected)
        self.root.update()
        calls = []
        def review(text, use_ai):
            calls.append(text)
            return review_contract(text, use_ai=False)
        self.app.review_fn = review
        self.app.prepare_input()
        self.app.start_review()
        self.pump(lambda: not self.app.busy)
        self.assertEqual(calls, [corrected])
        self.assertEqual(self.app.report_snapshot["contract_text"], corrected)
        self.assertEqual(self.app.report_snapshot["source_record"]["initial_text"], original)
        self.assertEqual(self.app.import_history[-1]["initial_text"], original)

    def test_ocr_fail_keeps_result_and_txt_paste_available(self):
        self.reviewed()
        before = copy.deepcopy(self.app.report_snapshot)
        def failure(*args, **kwargs):
            raise OcrError("缺少依赖的测试场景")
        self.app.ocr_import_fn = failure
        with patch("demo_gui.messagebox.showerror"):
            self.app.start_import("fake.png")
            self.pump(lambda: not self.app.busy)
        self.assertEqual(self.app.report_snapshot, before)
        self.assertEqual(str(self.app.editor["state"]), "normal")
        self.app.replace_text("失败后仍可粘贴合同")
        self.app.prepare_input()
        self.assertEqual(self.app.prepared_text, "失败后仍可粘贴合同")

    def test_partial_source_blocks_confirmation_without_ack(self):
        fixtures = Path(__file__).parent / "test_samples"
        if not (fixtures / "partial_blank.pdf").is_file():
            self.skipTest("Run test_ocr.py first")
        record = import_source(fixtures / "partial_blank.pdf")
        self.app.replace_text(record["initial_text"], source_record=record)
        with patch("demo_gui.messagebox.showwarning"):
            self.app.prepare_input()
        self.assertIsNone(self.app.prepared_text)
        self.app.partial_ack.set(True)
        self.app.prepare_input()
        self.app.start_review()
        self.pump(lambda: not self.app.busy)
        self.assertIn("来源不完整", self.app.status.get())
        self.assertTrue(self.app.report_snapshot["source_record"]["partial_acknowledged"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
