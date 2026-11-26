"""Local CPU OCR adapter. No downloads, URL inputs or remote OCR requests."""
from importlib.metadata import version
from pathlib import Path


class OcrError(ValueError):
    """Controlled, user-readable OCR error."""


class RapidOcrEngine:
    name = "RapidOCR ONNX CPU"

    def __init__(self, models_dir=None):
        try:
            import rapidocr_onnxruntime
            from rapidocr_onnxruntime import RapidOCR
        except (ImportError, OSError) as exc:
            raise OcrError("缺少本地 OCR 依赖或 Windows 运行库，请安装 requirements-ocr.txt；DLL 加载失败时检查 Microsoft VC++ x64 运行库。") from exc
        model_root = Path(models_dir) if models_dir else Path(rapidocr_onnxruntime.__file__).parent / "models"
        paths = {"det": model_root / "ch_PP-OCRv4_det_infer.onnx",
                 "cls": model_root / "ch_ppocr_mobile_v2.0_cls_infer.onnx",
                 "rec": model_root / "ch_PP-OCRv4_rec_infer.onnx"}
        if any(not path.is_file() for path in paths.values()):
            raise OcrError("本地 OCR 模型文件不完整，请重新安装 rapidocr-onnxruntime==1.4.4 或检查模型目录。程序不会在线下载模型。")
        try:
            self._engine = RapidOCR(
                **{f"{name}_model_path": str(path) for name, path in paths.items()},
                det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
                det_use_dml=False, cls_use_dml=False, rec_use_dml=False,
                intra_op_num_threads=2, inter_op_num_threads=1, print_verbose=False)
        except Exception as exc:
            raise OcrError("本地 OCR 模型初始化失败，请检查模型文件、ONNX Runtime 与 Windows 运行库。") from exc
        self.version = version("rapidocr-onnxruntime")

    def recognize(self, image_bytes):
        """Return text and per-line confidence; replacement engines implement this method."""
        if not isinstance(image_bytes, bytes):
            raise OcrError("OCR 只接受本地图像字节。")
        try:
            rows, _ = self._engine(image_bytes)
        except Exception as exc:
            raise OcrError("本地 OCR 识别失败，请检查图片是否损坏、过大或清晰度不足。") from exc
        lines = [{"text": row[1], "confidence": float(row[2])} for row in (rows or []) if row[1].strip()]
        return {"text": "\n".join(line["text"] for line in lines), "lines": lines}
