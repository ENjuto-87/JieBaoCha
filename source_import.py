"""File/page provenance and PDF text-first import, independent of Tk and review."""
from datetime import datetime, timezone, timedelta
import hashlib
from io import BytesIO
from pathlib import Path
import re

from ocr_engine import OcrError, RapidOcrEngine

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_PAGES = 200
MAX_IMAGE_PIXELS = 30_000_000
SOURCE_NOTICE = "引用匹配依据用户确认后的审查文本；匹配通过不代表 OCR 与原件一致。请逐页校正金额、日期、利率、姓名及遗漏内容。"


def usable_pdf_text(text):
    compact = re.sub(r"\s+", "", text)
    useful = sum(char.isalnum() for char in compact)
    return len(compact) >= 12 and useful / len(compact) >= 0.5 and "\ufffd" not in compact


def source_summary(record):
    lines = [f"来源：{record['file_name']}", f"路径：{record['file_path']}",
             f"文件 SHA-256：{record['file_sha256']}", f"导入时间：{record['imported_at']}",
             f"总页数：{record['page_count']}；状态：{record['status']}",
             "失败或空白页：" + ("、".join(map(str, record["failed_pages"])) or "无"), SOURCE_NOTICE,
             "初始提取文本独立保留；下方文字不会随编辑区校正而改变。", ""]
    for page in record["pages"]:
        lines.extend([f"第 {page['page_number']} 页 | {page['method']} | {page['status']}", page["message"], page["initial_text"] or "（无可用文本）", ""])
    return "\n".join(lines)


def import_source(path, progress=None, engine_factory=RapidOcrEngine, cancelled=None):
    """No review calls. All state is plain data; callbacks may only write queues.

    Empty/failed pages remain explicit. Returned initial_text is never replaced by
    a later user correction; the GUI holds corrected text separately.
    """
    path = Path(path).resolve()
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".pdf"):
        raise OcrError("请选择 PNG、JPG/JPEG 图片或 PDF 文件。")
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise OcrError("来源文件超过 100 MB，请先拆分或压缩副本。")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise OcrError("来源文件超过 100 MB，请先拆分或压缩副本。")
    except OSError as exc:
        raise OcrError("无法读取来源文件，请检查路径、文件权限及是否仍存在。") from exc
    record = {"file_name": path.name, "file_path": str(path), "file_sha256": hashlib.sha256(data).hexdigest(),
              "imported_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
              "source_type": "pdf" if path.suffix.lower() == ".pdf" else "image", "pages": [],
              "page_count": 0, "failed_pages": [], "status": "failed", "engine": "", "engine_version": ""}
    engine = None
    engine_error = None

    def notify(done, total, message):
        if progress:
            progress(done, total, message)

    def check_cancel():
        if cancelled and cancelled():
            raise OcrError("导入已取消，原有文本与审查结果仍保留。")

    def ocr(image_bytes):
        nonlocal engine, engine_error
        check_cancel()
        if engine_error:
            raise OcrError(engine_error)
        if engine is None:
            notify(len(record["pages"]), record["page_count"], "正在加载本地 CPU OCR 模型")
            try:
                engine = engine_factory()
            except Exception as exc:
                engine_error = str(exc) if isinstance(exc, OcrError) else "本地 OCR 引擎无法初始化。"
                raise OcrError(engine_error) from exc
            record["engine"] = engine.name
            record["engine_version"] = getattr(engine, "version", "自定义")
        return engine.recognize(image_bytes)

    def page_record(number, method, extract):
        check_cancel()
        notify(number - 1, record["page_count"], f"正在处理第 {number} / {record['page_count']} 页")
        page = {"page_number": number, "method": method, "status": "failed", "initial_text": "", "message": "", "lines": []}
        try:
            content = extract()
            page["initial_text"] = content["text"]
            page["lines"] = content.get("lines", [])
            if not page["initial_text"].strip():
                page["status"] = "empty"
                page["message"] = "本页未提取到可用文本，可能为空白、低清晰度或漏识别，须核对原件。"
            else:
                page["status"] = "ok"
                page["message"] = "已提取，尚未人工核对。" + ("OCR 置信度不等于法律或原件准确性。" if method == "ocr" else "文本层可能遗漏图片内文字，请对照原件。")
        except OcrError as exc:
            check_cancel()
            page["message"] = str(exc)
        except Exception:
            page["message"] = "本页读取或识别失败，请检查文件内容或另行导入清晰副本。"
        record["pages"].append(page)
        if page["status"] != "ok":
            record["failed_pages"].append(number)
        notify(number, record["page_count"], f"第 {number} 页：{page['status']}")

    if record["source_type"] == "image":
        record["page_count"] = 1
        def extract_image():
            try:
                from PIL import Image, ImageOps
                with Image.open(BytesIO(data)) as image:
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise OcrError("图片超过 3000 万像素，请缩小副本后导入。")
                    if getattr(image, "n_frames", 1) != 1:
                        raise OcrError("不支持多帧图片，请转换为多页 PDF。")
                    normalized = ImageOps.exif_transpose(image).convert("RGB")
                    output = BytesIO()
                    normalized.save(output, format="PNG")
                return ocr(output.getvalue())
            except ImportError as exc:
                raise OcrError("缺少 Pillow 图像依赖，请安装 requirements-ocr.txt。") from exc
        page_record(1, "ocr", extract_image)
    else:
        try:
            import pymupdf
        except ImportError as exc:
            raise OcrError("缺少 PyMuPDF，请安装 requirements-ocr.txt。") from exc
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise OcrError("PDF 无法打开，可能已损坏或格式不正确。") from exc
        with document:
            if document.needs_pass:
                raise OcrError("PDF 已加密，请先用有权限的工具解密副本后再导入。")
            record["page_count"] = len(document)
            if not 1 <= len(document) <= MAX_PAGES:
                raise OcrError("PDF 页数为空或超过 200 页，请拆分后导入。")
            for index in range(len(document)):
                check_cancel()
                try:
                    page = document.load_page(index)
                    text = page.get_text("text", sort=True)
                    method = "pdf_text" if usable_pdf_text(text) else "ocr"
                    def extract_pdf_page(page=page, text=text, method=method):
                        if method == "pdf_text":
                            return {"text": text}
                        if page.rect.width * page.rect.height * (180 / 72) ** 2 > MAX_IMAGE_PIXELS:
                            raise OcrError("本页渲染尺寸过大，请先缩小 PDF 副本。")
                        image = page.get_pixmap(dpi=180, colorspace=pymupdf.csRGB, alpha=False)
                        return ocr(image.tobytes("png"))
                    page_record(index + 1, method, extract_pdf_page)
                except OcrError:
                    raise
                except Exception:
                    def failed_page():
                        raise OcrError("本页无法读取，请核对来源 PDF。")
                    page_record(index + 1, "unreadable", failed_page)
    successful = sum(page["status"] == "ok" for page in record["pages"])
    record["status"] = "complete" if successful == record["page_count"] else ("partial" if successful else "failed")
    record["initial_text"] = "\n\n".join(f"【来源第 {page['page_number']} 页】\n" + (page["initial_text"] if page["status"] == "ok" else "【本页识别失败或为空，须补充原件内容】") for page in record["pages"])
    record["initial_text_sha256"] = hashlib.sha256(record["initial_text"].encode("utf-8")).hexdigest()
    return record
