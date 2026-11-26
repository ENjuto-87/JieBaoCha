"""Real local OCR fixtures plus negative paths. Never calls a model API."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parent.parent / ".runtime"
if RUNTIME.is_dir():
    sys.path.insert(0, str(RUNTIME))
from PIL import Image, ImageDraw, ImageFont
import pymupdf
from ocr_engine import OcrError, RapidOcrEngine
from source_import import import_source
from review_service import review_contract
from report_export import build_snapshot, save_docx, save_json, save_html

FIXTURES = Path(__file__).parent / "test_samples"
FIXTURES.mkdir(exist_ok=True)


def make_fixtures():
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 42)
    texts = [
        ["虚构借款合同 第一页", "借款人：张测试", "出借人：李示例", "借款金额：人民币100000元", "借款日期：2026年10月12日", "年利率：6%", "本样本仅用于软件测试"],
        ["虚构保证合同 第二页", "保证人：王测试", "保证方式：连带责任保证", "保证期间：主债务届满之日起二年", "送达地址：上海市示例路123号", "签署日期：2026年10月12日", "本样本不用于真实交易"]]
    images = []
    for index, lines in enumerate(texts, 1):
        image = Image.new("RGB", (1400, 1800), "white")
        draw = ImageDraw.Draw(image)
        for row, text in enumerate(lines):
            draw.text((90, 120 + row * 110), text, fill="black", font=font)
        image.save(FIXTURES / f"chinese_page{index}.png")
        images.append(image)
    images[0].save(FIXTURES / "chinese_page1.jpg", quality=95)
    doc = pymupdf.open()
    for image in images:
        output = BytesIO()
        image.save(output, format="PNG")
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=output.getvalue())
    doc.save(FIXTURES / "two_page_scan.pdf")
    # Text-first and mixed-document paths are checked without faking extraction.
    doc[0].insert_text((30, 40), "Synthetic contract 100000 CNY 2026-10-12", fontsize=11)
    doc.save(FIXTURES / "mixed_text_scan.pdf")
    doc.save(FIXTURES / "encrypted.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="fixture-owner", user_pw="fixture-only")
    doc.close()
    blank = Image.new("RGB", (800, 1000), "white")
    blank.save(FIXTURES / "blank.png")
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(FIXTURES / "chinese_page1.png"))
    doc.new_page()
    doc.save(FIXTURES / "partial_blank.pdf")
    doc.close()
    (FIXTURES / "corrupt.pdf").write_bytes(b"%PDF broken fixture")
    (FIXTURES / "corrupt.png").write_bytes(b"broken image fixture")


class OcrChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_fixtures()
        cls.engine = RapidOcrEngine()

    def engine_factory(self):
        return self.engine

    def test_real_chinese_image_png_and_jpg(self):
        for name in ("chinese_page1.png", "chinese_page1.jpg"):
            record = import_source(FIXTURES / name, engine_factory=self.engine_factory)
            self.assertEqual(record["status"], "complete")
            self.assertIn("100000", record["initial_text"])
            self.assertIn("2026", record["initial_text"])
            self.assertIn("借款", record["initial_text"])
            (FIXTURES / (name + ".ocr.json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_real_two_page_scan_and_same_snapshot_exports(self):
        progress = []
        record = import_source(FIXTURES / "two_page_scan.pdf", engine_factory=self.engine_factory, progress=lambda *args: progress.append(args))
        self.assertEqual(record["status"], "complete")
        self.assertEqual([page["method"] for page in record["pages"]], ["ocr", "ocr"])
        self.assertIn("连带责任保证", record["pages"][1]["initial_text"])
        self.assertTrue(any(item[0] == 2 for item in progress))
        original = record["initial_text"]
        corrected = original.replace("100000", "120000")
        result = review_contract(corrected, use_ai=False)
        snapshot = build_snapshot(corrected, result, False, True, source_record=record)
        self.assertEqual(record["initial_text"], original)
        self.assertIn("100000", snapshot["source_record"]["initial_text"])
        self.assertIn("120000", snapshot["contract_text"])
        out = Path(__file__).parent / "examples"
        out.mkdir(exist_ok=True)
        save_json(snapshot, out / "OCR示例报告.json")
        save_html(snapshot, out / "OCR示例报告.html")
        save_docx(snapshot, out / "OCR示例报告.docx")
        (FIXTURES / "two_page_scan.ocr.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_pdf_text_first_and_ocr_only_other_page(self):
        record = import_source(FIXTURES / "mixed_text_scan.pdf", engine_factory=self.engine_factory)
        self.assertEqual(record["status"], "complete")
        self.assertEqual([page["method"] for page in record["pages"]], ["pdf_text", "ocr"])

    def test_partial_page_and_acknowledgement(self):
        record = import_source(FIXTURES / "partial_blank.pdf", engine_factory=self.engine_factory)
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["failed_pages"], [2])
        result = review_contract(record["initial_text"], use_ai=False)
        with self.assertRaises(ValueError):
            build_snapshot(record["initial_text"], result, False, source_record=record)
        record["partial_acknowledged"] = True
        self.assertIn("不是完整合同审查", " ".join(build_snapshot(record["initial_text"], result, False, source_record=record)["notices"]))

    def test_corrupt_encrypted_empty_missing_model(self):
        for name in ("corrupt.pdf", "encrypted.pdf"):
            with self.assertRaises(OcrError):
                import_source(FIXTURES / name, engine_factory=self.engine_factory)
        for name in ("blank.png", "corrupt.png"):
            self.assertEqual(import_source(FIXTURES / name, engine_factory=self.engine_factory)["status"], "failed")
        with self.assertRaises(OcrError):
            RapidOcrEngine(models_dir=FIXTURES / "missing_models")
        def missing():
            raise OcrError("缺少 OCR 依赖")
        record = import_source(FIXTURES / "mixed_text_scan.pdf", engine_factory=missing)
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["failed_pages"], [2])
        self.assertIn("缺少 OCR 依赖", record["pages"][1]["message"])

    def test_cancellation(self):
        with self.assertRaises(OcrError):
            import_source(FIXTURES / "two_page_scan.pdf", cancelled=lambda: True)


if __name__ == "__main__":
    # Any attempted connection fails; actual OCR uses only installed local models.
    with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
