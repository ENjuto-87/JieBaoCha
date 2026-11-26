"""Workers receive only functions and plain data/queues, never Tk widgets."""
from ocr_engine import OcrError


def run_import_job(import_fn, path, cancelled, output_queue):
    def progress(done, total, message):
        output_queue.put(("progress", (done, total, message)))
    try:
        record = import_fn(path, progress=progress, cancelled=cancelled.is_set)
        output_queue.put(("ok", record))
    except OcrError as exc:
        output_queue.put(("error", str(exc)))
    except Exception:
        output_queue.put(("error", "来源导入失败，请检查依赖、文件格式和模型。原有粘贴及 TXT 导入仍可使用。"))


def run_export_job(writer, snapshot, path, output_queue):
    try:
        writer(snapshot, path)
        output_queue.put(("ok", path))
    except PermissionError:
        output_queue.put(("error", "没有写入权限，或文件正被 Word / 其他程序占用。请关闭文件或另存到可写目录。"))
    except OSError:
        output_queue.put(("error", "无法写入文件，请检查磁盘空间、目录和文件权限。"))
    except (ValueError, TypeError, KeyError, ImportError) as exc:
        output_queue.put(("error", str(exc) if isinstance(exc, ValueError) else "报告数据或导出依赖不符合要求，请检查运行环境。"))
    except Exception:
        output_queue.put(("error", "报告生成失败，请检查依赖与文件格式。"))
