"""Shared Windows startup diagnostics. Never reads or logs API credentials."""
from pathlib import Path
import re
import runpy
import sys
import traceback


ENTRIES = {"ai": "configure_ai.py", "web": "web_server.py",
           "desktop": "demo_gui.py", "check": "check_config.py"}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < (3, 10):
        print("启动失败：需要Python 3.10以上，建议使用已验证的Python 3.12。")
        return 1
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode not in ENTRIES:
        print("启动入口无效，请使用项目附带的启动文件。")
        return 1
    arguments = sys.argv[2:]
    if arguments[:1] == ["--check"]:
        mode, arguments = "check", arguments[1:]
    root = Path(__file__).resolve().parent
    target = root / ENTRIES[mode]
    print("使用Python：", sys.executable, flush=True)
    print("项目目录：", root, flush=True)
    if not target.is_file():
        print("启动失败：缺少", target.name, "。请完整解压代码包，不要单独移动bat文件。")
        return 1
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    sys.argv = [str(target), *arguments]
    try:
        runpy.run_path(str(target), run_name="__main__")
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except KeyboardInterrupt:
        print("\n已取消。可关闭本窗口。")
        return 130
    except ModuleNotFoundError as exc:
        name = exc.name or ""
        safe_name = name if re.fullmatch(r"[A-Za-z0-9_.]+", name) else "所需模块"
        print("启动失败：当前Python缺少模块", safe_name)
        print('请在已配置好的PyCharm终端运行：python -m pip install -r requirements.txt')
        print("OCR依赖另见模块交付说明。也可设置CONTRACT_REVIEW_PYTHON为PyCharm解释器完整路径。")
        return 1
    except Exception as exc:
        # Do not print arbitrary exception messages, local variables or credentials.
        print("启动失败，异常类型：", type(exc).__name__)
        for frame in traceback.extract_tb(exc.__traceback__):
            print("  文件：", frame.filename, "行号：", frame.lineno)
        print("窗口将保留，请记录上述异常类型和位置以便排查。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
