"""Loopback browser adapter. Existing review/OCR/export modules remain authoritative.

This is a single-computer Demo, not an Internet-facing production server.
Sessions, original uploads and snapshots live in memory and expire after inactivity.
"""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
import webbrowser

from ocr_engine import OcrError
from report_export import build_snapshot, save_docx, save_html, save_json
from review_service import review_contract
from source_import import import_source
from ai_config import public_status, get_settings, ConfigError
from ai_client import test_connection

MAX_UPLOAD = 20 * 1024 * 1024
MAX_TEXT = 500_000
SESSION_TTL = 30 * 60
STATIC = Path(__file__).with_name("web")
SAMPLE = """借款与保证合同（虚构练习样本）
出借人：甲公司；借款人：乙公司；保证人：丙公司。
借款金额：人民币100000元。
借款期限：2026年10月12日至2027年10月11日。
借款利率：年利率8%。
保证方式：连带责任保证。
保证范围：本金、利息和实现债权的合理费用。
保证期间：主债务履行期限届满之日起两年。
争议解决：向有管辖权的人民法院提起诉讼。
送达地址：双方另行确认。
本样本仅用于功能演示，不作为可直接签署的条款模板。"""


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass
class Document:
    text: str = ""
    revision: int = 0
    use_ai: bool = False
    anonymized: bool = False
    partial_ack: bool = False
    confirmed_revision: int = -1
    snapshot: dict | None = None
    source: dict | None = None
    original: bytes | None = None
    filename: str = ""
    job: dict | None = None
    ai_signature: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def busy(self):
        return bool(self.job and self.job["state"] == "running")

    def invalidate(self):
        self.revision += 1
        self.confirmed_revision = -1
        self.snapshot = None


class ReviewApp:
    def __init__(self, review_fn=review_contract, import_fn=import_source):
        self.lock = threading.RLock()
        self.sessions = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="browser-job")
        self.slots = threading.BoundedSemaphore(2)
        self.review_fn = review_fn
        self.import_fn = import_fn
        self.last_probe = None
        self.last_probe_started = -100.0

    def ai_status(self):
        data = public_status()
        try:
            signature = get_settings().signature()
        except ConfigError:
            signature = None
        probe = self.last_probe
        data["last_test"] = copy.deepcopy(probe["result"]) if probe and probe["signature"] == signature else None
        return data

    def probe(self, doc, data):
        self.check_revision(doc,data)
        try:
            settings = get_settings()
        except ConfigError as exc:
            raise ApiError(str(exc)) from None
        if not settings.api_key:
            raise ApiError("尚未配置AI密钥，请双击“配置AI接口.bat”。")
        if time.monotonic()-self.last_probe_started < 15:
            raise ApiError("连接测试间隔至少15秒，请稍后再试。",429)
        def work(job):
            result = test_connection(settings)
            with self.lock:
                self.last_probe = {"signature":settings.signature(),"result":result}
                job["message"] = result["message"]
            if not result["ok"]:
                raise ApiError(result["message"])
        job = self.start_job(doc,"ai_test",work)
        self.last_probe_started = time.monotonic()
        return job

    def session(self, sid):
        with self.lock:
            now = time.monotonic()
            for key, session in list(self.sessions.items()):
                if now - session["touched"] > SESSION_TTL and not any(d.busy() for d in session["documents"].values()):
                    del self.sessions[key]
            session = self.sessions.get(sid)
            fresh = session is None
            if fresh:
                if len(self.sessions) >= 32:
                    raise ApiError("本机打开的会话过多，请稍后重试或重启网页服务。", 429)
                sid = secrets.token_hex(32)
                session = {"csrf": secrets.token_hex(32), "touched": now, "documents": {}}
                self.sessions[sid] = session
            session["touched"] = now
            return sid, session, fresh

    def document(self, session, doc_id):
        doc = session["documents"].get(doc_id)
        if doc is None:
            raise ApiError("此页面的记录已失效，请刷新网页重新导入。", 404)
        return doc

    def check_revision(self, doc, data, idle=True):
        if type(data.get("revision")) is not int or data["revision"] != doc.revision:
            raise ApiError("输入版本已变化，请重新同步并确认文本。", 409)
        if idle and doc.busy():
            raise ApiError("当前任务仍在处理，请等待完成。", 409)

    def view(self, doc):
        self.ensure_ai_revision(doc)
        return copy.deepcopy({"text": doc.text, "revision": doc.revision,
            "use_ai": doc.use_ai, "anonymized": doc.anonymized, "partial_ack": doc.partial_ack,
            "confirmed": doc.confirmed_revision == doc.revision,
            "snapshot": doc.snapshot, "source": doc.source, "filename": doc.filename,
            "job": doc.job})

    def ensure_ai_revision(self,doc):
        if not doc.use_ai:
            doc.ai_signature = None
            return
        try:
            signature = get_settings().signature()
        except ConfigError:
            signature = "invalid"
        if doc.ai_signature is not None and doc.ai_signature != signature:
            doc.invalidate()
        doc.ai_signature = signature

    def update(self, doc, data):
        self.check_revision(doc, data)
        text = data.get("text")
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            raise ApiError("合同文本须为文字，且不得超过50万字符。")
        for key in ("use_ai", "anonymized", "partial_ack"):
            if type(data.get(key)) is not bool:
                raise ApiError("审查设置格式不正确。")
        changed = any(getattr(doc, key) != data[key] for key in ("text", "use_ai", "anonymized", "partial_ack"))
        if data.get("clear_source") is True:
            changed = True
            doc.source = None
            doc.original = None
            doc.filename = ""
        if changed:
            for key in ("text", "use_ai", "anonymized", "partial_ack"):
                setattr(doc, key, data[key])
            doc.invalidate()
        return self.view(doc)

    def confirm(self, doc, data):
        self.ensure_ai_revision(doc)
        self.check_revision(doc, data)
        if not doc.text.strip():
            raise ApiError("请先输入合同文本。")
        if doc.source and doc.source["status"] == "partial" and not doc.partial_ack:
            raise ApiError("来源存在失败或空白页，请补充核对并勾选不完整来源确认。")
        doc.confirmed_revision = doc.revision
        return self.view(doc)

    def start_job(self, doc, kind, work):
        if not self.slots.acquire(blocking=False):
            raise ApiError("本机已有两个后台任务，请等待其中一个完成后重试。", 429)
        job = {"id": secrets.token_hex(12), "kind": kind, "state": "running",
               "message": {"review":"正在审查…", "import":"正在读取来源文件…", "ai_test":"正在测试DeepSeek接口（不发送合同）…"}[kind], "done": 0, "total": 0}
        doc.job = job
        doc.cancel = threading.Event()

        def run():
            try:
                work(job)
                with self.lock:
                    job["state"] = "done"
            except (OcrError, ApiError) as exc:
                with self.lock:
                    job["state"] = "cancelled" if doc.cancel.is_set() else "failed"
                    job["message"] = str(exc)
            except Exception as exc:
                # SDK errors may contain secrets/request data. Never serialize raw errors.
                with self.lock:
                    job["state"] = "failed"
                    job["message"] = f"任务未完成（{type(exc).__name__}），已保留当前文本。请检查本地环境后重试。"
            finally:
                self.slots.release()
        try:
            self.executor.submit(run)
        except Exception:
            self.slots.release()
            doc.job = None
            raise ApiError("后台服务正在关闭，请重启后重试。", 503)
        return copy.deepcopy(job)

    def review(self, doc, data):
        self.ensure_ai_revision(doc)
        self.check_revision(doc, data)
        if doc.confirmed_revision != doc.revision:
            raise ApiError("请先核对并确认本次文本。", 409)
        text, use_ai, anonymized = doc.text, doc.use_ai, doc.anonymized
        revision = doc.revision
        source = copy.deepcopy(doc.source)
        if source:
            source["partial_acknowledged"] = doc.partial_ack
        def work(job):
            result = self.review_fn(text, use_ai=use_ai)
            snapshot = build_snapshot(text, result, use_ai, anonymized, source_record=source)
            with self.lock:
                self.ensure_ai_revision(doc)
                if doc.revision != revision:
                    raise ApiError("AI配置在处理期间变化，已丢弃本次结果，请重新确认审查。",409)
                doc.snapshot = snapshot
                job["message"] = "审查已结束，请复核意见与流程消息。"
        job = self.start_job(doc, "review", work)
        doc.snapshot = None
        return job

    def import_file(self, doc, data, filename, content):
        self.check_revision(doc, data)
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 180 or any(ord(c) < 32 for c in filename):
            raise ApiError("文件名无效或过长，请重命名后再试。")
        if any(c in filename for c in '<>:"|?*') or filename.endswith((".", " ")) or Path(filename).stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise ApiError("文件名含 Windows 保留字符或名称，请重命名后再试。")
        suffix = Path(filename).suffix.lower()
        if suffix not in (".txt", ".png", ".jpg", ".jpeg", ".pdf"):
            raise ApiError("请选择 TXT、PNG、JPG/JPEG 或 PDF。")
        if not content or len(content) > MAX_UPLOAD:
            raise ApiError("网页导入支持非空且不超过20 MB的文件。", 413)
        retained = sum(len(d.original or b"") for s in self.sessions.values() for d in s["documents"].values())
        if retained - len(doc.original or b"") + len(content) > 128 * 1024 * 1024:
            raise ApiError("本机已保留较多来源文件，请下载报告并重启服务释放内存后再导入。", 429)

        def progress(done, total, message):
            with self.lock:
                doc.job.update(done=done, total=total, message=message)

        def work(job):
            if suffix == ".txt":
                text = None
                for encoding in ("utf-8-sig", "gb18030"):
                    try:
                        text = content.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        pass
                if text is None or not text.strip() or "\0" in text:
                    raise ApiError("TXT 为空或编码不支持，请另存为 UTF-8 后导入。")
                record = None
            else:
                with tempfile.TemporaryDirectory(prefix="contract-browser-") as folder:
                    path = Path(folder) / filename
                    path.write_bytes(content)
                    record = self.import_fn(path, progress=progress, cancelled=doc.cancel.is_set)
                if record["status"] == "failed":
                    detail = "；".join(p["message"] for p in record["pages"][:3])
                    raise OcrError("来源没有可用文本，原有输入与结果已保留。" + detail)
                # Do not export an already deleted temporary filesystem path.
                record["file_path"] = "浏览器选择：" + filename + "（临时副本已清理；原文件请自行保留）"
                record["browser_upload"] = True
                text = record["initial_text"]
            if len(text) > MAX_TEXT:
                raise ApiError("提取文本超过50万字符，请拆分文件后导入。")
            if doc.cancel.is_set():
                raise OcrError("导入已取消，原有输入与结果已保留。")
            with self.lock:
                doc.text, doc.source = text, copy.deepcopy(record)
                doc.original, doc.filename = content, filename
                doc.partial_ack = False
                doc.invalidate()
                job["message"] = "导入完成，请对照原件校正并确认。" if not record or record["status"] == "complete" else "部分页面失败，须补充核对后确认。"
        return self.start_job(doc, "import", work)

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                for doc in session["documents"].values():
                    doc.cancel.set()
        self.executor.shutdown(wait=True, cancel_futures=True)


class BrowserServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, app=None):
        if address[0] != "127.0.0.1":
            raise ValueError("本版本仅支持绑定127.0.0.1。")
        self.app = app or ReviewApp()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "ContractDemo"
    sys_version = ""
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *args):
        pass  # No contract paths, uploaded filenames, bodies or credentials in logs.

    def send_bytes(self, content, content_type, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def json_response(self, data, status=200, extra=None):
        self.send_bytes(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status, extra)

    def read_body(self, maximum=MAX_UPLOAD):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError("不支持分块上传，请使用页面文件选择入口。", 400)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise ApiError("请求长度无效。")
        if not 0 <= length <= maximum:
            raise ApiError("上传内容超过限制或缺少长度信息。", 413)
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError("文件未完整传输，请重新选择。")
        return body

    def json_body(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError("请求格式应为 JSON。", 415)
        try:
            data = json.loads(self.read_body(4 * 1024 * 1024))
        except (UnicodeDecodeError, ValueError):
            raise ApiError("请求内容无法解析。")
        if not isinstance(data, dict):
            raise ApiError("请求内容格式不正确。")
        return data

    def dispatch(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            raise ApiError("此服务仅接受本机访问。", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            raise ApiError("拒绝跨来源请求。", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ApiError("拒绝跨站请求。", 403)
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.command == "GET" and path in assets:
            name, mime = assets[path]
            return self.send_bytes((STATIC / name).read_bytes(), mime)
        if not path.startswith("/api/"):
            raise ApiError("页面不存在。", 404)
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        sid = cookie["contract_session"].value if "contract_session" in cookie else ""
        with self.server.app.lock:
            sid, session, fresh = self.server.app.session(sid)
            if self.command == "GET" and path == "/api/session":
                return self.json_response({"csrf": session["csrf"], "max_upload": MAX_UPLOAD, "sample": SAMPLE}, extra={"Set-Cookie": f"contract_session={sid}; HttpOnly; SameSite=Strict; Path=/"})
            if fresh:
                # Do not create orphan sessions for arbitrary API requests.
                del self.server.app.sessions[sid]
                raise ApiError("会话已失效，请刷新页面。", 401)
            if self.command == "POST" and not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
                raise ApiError("会话校验失败，请刷新页面。", 403)
            if self.command == "GET" and path == "/api/ai/status":
                return self.json_response(self.server.app.ai_status())
            if self.command == "POST" and path == "/api/documents":
                if len(session["documents"]) >= 8:
                    raise ApiError("当前会话最多打开8份合同，请关闭不用的页面或重启服务。", 429)
                doc_id = secrets.token_hex(16)
                session["documents"][doc_id] = Document()
                return self.json_response({"id": doc_id, **self.server.app.view(session["documents"][doc_id])}, 201)
            parts = path.strip("/").split("/")
            if len(parts) not in (3, 4) or parts[:2] != ["api", "documents"]:
                raise ApiError("接口不存在。", 404)
            doc = self.server.app.document(session, parts[2])
            action = parts[3] if len(parts) == 4 else ""
            if self.command == "GET":
                if action == "":
                    return self.json_response(self.server.app.view(doc))
                if action == "job":
                    return self.json_response({"job": copy.deepcopy(doc.job), "revision": doc.revision})
                if action == "source":
                    if doc.original is None:
                        raise ApiError("当前没有上传的来源原件。", 404)
                    return self.send_bytes(doc.original, "application/octet-stream", extra={"Content-Disposition": "attachment; filename=source" + Path(doc.filename).suffix.lower()})
                if action in ("word", "json", "html"):
                    self.server.app.ensure_ai_revision(doc)
                    if doc.busy() or doc.snapshot is None:
                        raise ApiError("没有有效审查快照，请确认当前文本并完成审查。", 409)
                    snapshot = copy.deepcopy(doc.snapshot)
                    revision = doc.revision
                else:
                    raise ApiError("接口不存在。", 404)
            elif self.command == "POST":
                # Reading large request bodies must not hold the application lock.
                pass
            else:
                raise ApiError("不支持此请求方法。", 405)
        if self.command == "GET":
            suffix, mime, writer = {
                "word": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", save_docx),
                "json": (".json", "application/json; charset=utf-8", save_json),
                "html": (".html", "text/html; charset=utf-8", save_html),
            }[action]
            try:
                with tempfile.TemporaryDirectory(prefix="contract-report-") as folder:
                    target = Path(folder) / (snapshot["report_id"] + suffix)
                    writer(snapshot, target)
                    content = target.read_bytes()
            except Exception as exc:
                raise ApiError(f"报告生成失败（{type(exc).__name__}），当前结果已保留。请检查 Word 依赖、磁盘空间或临时目录权限。", 500) from exc
            with self.server.app.lock:
                self.server.app.ensure_ai_revision(doc)
                if doc.revision != revision or doc.snapshot != snapshot or doc.busy():
                    raise ApiError("生成期间输入已变化，请重新审查后导出。", 409)
            return self.send_bytes(content, mime, extra={"Content-Disposition": f'attachment; filename="{snapshot["report_id"]}{suffix}"'})
        if action == "import":
            body = self.read_body()
            try:
                data = {"revision": int(self.headers.get("X-Revision", "-1"))}
            except ValueError:
                raise ApiError("输入版本无效。")
            filename = unquote(self.headers.get("X-Filename", ""))
        else:
            data = self.json_body()
        with self.server.app.lock:
            # Another request may have changed the document while the body arrived.
            if action == "":
                response = self.server.app.update(doc, data)
            elif action == "confirm":
                response = self.server.app.confirm(doc, data)
            elif action == "review":
                response = self.server.app.review(doc, data)
            elif action == "ai-test":
                response = self.server.app.probe(doc, data)
            elif action == "import":
                response = self.server.app.import_file(doc, data, filename, body)
            elif action == "cancel":
                if doc.busy() and doc.job["kind"] == "import":
                    doc.cancel.set()
                response = {"message": "已请求取消，当前页处理完成后生效。"}
            else:
                raise ApiError("接口不存在。", 404)
        self.json_response(response, 202 if action in ("review", "import", "ai-test") else 200)

    def handle_request(self):
        try:
            self.dispatch()
        except ApiError as exc:
            self.json_response({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.json_response({"error": "本机服务遇到异常，请保留输入并检查运行环境。"}, 500)

    do_GET = handle_request
    do_POST = handle_request


def main():
    parser = argparse.ArgumentParser(description="合同审查本机浏览器版")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口须为1到65535。")
    try:
        server = BrowserServer(("127.0.0.1", args.port))
    except OSError:
        print("启动失败：端口已占用或无法监听。关闭旧服务，或添加 --port 8766 重试。")
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"合同审查浏览器版已启动：{url}", flush=True)
    print("保持此窗口运行；按 Ctrl+C 停止。合同记录仅保留在本机内存，请及时下载报告。", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("正在停止服务，等待当前后台调用返回…")
    finally:
        server.server_close()
        server.app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
