import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import base64
import xml.etree.ElementTree as ET

TEMP_ROOT = Path(__file__).parent / ".test-tmp"
TEMP_ROOT.mkdir(exist_ok=True)
tempfile.tempdir = str(TEMP_ROOT)

from report_export import build_snapshot, save_docx, save_json, save_html
from review_service import review_contract


class WordChecks(unittest.TestCase):
    def test_same_snapshot_in_all_formats(self):
        text = "虚构借款合同\r\n甲借款100000元。\n保证方式：连带责任保证\n送达地址：上海市测试路1号\n" + "长合同正文。" * 1500
        snapshot = build_snapshot(text, review_contract(text, use_ai=False), False, True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_docx(snapshot, path / "报告.docx")
            save_json(snapshot, path / "报告.json")
            save_html(snapshot, path / "报告.html")
            with zipfile.ZipFile(path / "报告.docx") as archive:
                embedded = ET.fromstring(archive.read("customXml/review-snapshot.xml")).text
                self.assertEqual(json.loads(base64.b64decode(embedded)), snapshot)
                xml = archive.read("word/document.xml").decode("utf-8")
                self.assertIn("宋体", archive.read("word/styles.xml").decode("utf-8"))
                self.assertIn("人工复核记录", xml)
                self.assertIn(snapshot["contract_sha256"], xml)
                self.assertIn("PAGE", archive.read("word/footer1.xml").decode())
            self.assertEqual(json.loads((path / "报告.json").read_text(encoding="utf-8")), snapshot)
            self.assertIn(snapshot["report_id"], (path / "报告.html").read_text(encoding="utf-8"))

    def test_atomic_failure_preserves_file(self):
        snapshot = build_snapshot("虚构合同", review_contract("虚构合同", use_ai=False), False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.docx"
            path.write_bytes(b"original")
            with patch("report_docx.os.replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    save_docx(snapshot, path)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    @unittest.skipUnless(__import__("sys").platform == "win32", "Windows sharing violation")
    def test_actual_windows_file_lock_preserves_existing_report(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = build_snapshot("虚构合同", review_contract("虚构合同", use_ai=False), False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locked.docx"
            save_docx(snapshot, path)
            before = path.read_bytes()
            handle = api.CreateFileW(str(path.resolve()), 0x80000000, 0, None, 3, 0, None)
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            try:
                with self.assertRaises(PermissionError):
                    save_docx(snapshot, path)
            finally:
                api.CloseHandle(handle)
            self.assertEqual(path.read_bytes(), before)

    def test_missing_word_dependency_keeps_json_available(self):
        import sys
        snapshot = build_snapshot("虚构合同", review_contract("虚构合同", use_ai=False), False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.dict(sys.modules, {"docx": None}):
                with self.assertRaisesRegex(ValueError, "python-docx"):
                    save_docx(snapshot, path / "report.docx")
                save_json(snapshot, path / "report.json")
            self.assertTrue((path / "report.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
