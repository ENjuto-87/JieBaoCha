"""只检查当前进程配置；不打印 Key，不调用 API，不验证 Key 的有效性。"""
import importlib.util
from importlib.metadata import version, PackageNotFoundError
import os
from pathlib import Path
import sys
from ai_config import public_status


def main():
    print("当前解释器：", sys.executable)
    print("代码目录：", Path(__file__).resolve().parent)
    ai = public_status()
    print("AI 配置：", ai["message"])
    print("模型：", ai["model"], "；来源：", ai["source"])
    for name in ("tkinter", "openai", "docx", "PIL", "pymupdf", "rapidocr_onnxruntime", "onnxruntime"):
        present = importlib.util.find_spec(name) is not None
        print(f"{name}：", "可找到" if present else "未找到")
    try:
        print("openai 版本：", version("openai"))
    except PackageNotFoundError:
        pass
    required = ("demo_gui.py", "review_service.py", "contract_checker.py", "address_rules.py",
                "review_display.py", "report_export.py", "report_docx.py", "reliability.py", "source_import.py", "ocr_engine.py")
    missing = [name for name in required if not Path(__file__).with_name(name).is_file()]
    print("运行文件：", "齐全" if not missing else "缺少 " + "、".join(missing))
    print("本检查不发送合同、不产生 API 费用；不证明真实调用一定成功。")
    print("OCR 为可选模块；未安装时仍可粘贴、导入 TXT、审查和导出 JSON/HTML。")
    print("建议 Windows x64 Python 3.12；本版固定依赖按 3.12 验证，OCR 适配 rapidocr-onnxruntime 1.4.4。")


if __name__ == "__main__":
    main()
