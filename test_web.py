"""Real HTTP integration tests; no real AI calls. OCR tests use installed CPU models."""
import base64
from http.cookiejar import CookieJar
from io import BytesIO
import json
import os
import tempfile
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request
import xml.etree.ElementTree as ET
import zipfile

from web_server import ApiError, BrowserServer, Document, OcrError, ReviewApp, SAMPLE


class Client:
    def __init__(self, url):
        self.url = url
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.csrf = ""
        _, data, _ = self.request("/api/session")
        self.csrf = data["csrf"]

    def request(self, path, data=None, raw=None, headers=None):
        hdr = {"X-CSRF-Token": self.csrf}
        body = raw
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            hdr["Content-Type"] = "application/json"
        hdr.update(headers or {})
        request = Request(self.url + path, data=body, headers=hdr)
        try:
            response = self.opener.open(request, timeout=20)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            info = response.headers
            if info.get_content_type() == "application/json":
                content = json.loads(content)
            return response.status, content, info


class WebTests(unittest.TestCase):
    def setUp(self):
        self.ai_config_temp = tempfile.TemporaryDirectory()
        self.ai_config_patch = patch.dict(os.environ,{"CONTRACT_REVIEW_AI_CONFIG":str(Path(self.ai_config_temp.name)/"absent.json"),"DEEPSEEK_API_KEY":"","DEEPSEEK_MODEL":"","DEEPSEEK_TIMEOUT":"","DEEPSEEK_MAX_TOKENS":""})
        self.ai_config_patch.start()
        self.app = ReviewApp()
        self.server = BrowserServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(f"http://127.0.0.1:{self.server.server_port}")
        _, self.doc, _ = self.client.request("/api/documents", {})
        self.path = "/api/documents/" + self.doc["id"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.close()
        self.ai_config_patch.stop()
        self.ai_config_temp.cleanup()

    def update(self, text=SAMPLE, **options):
        data = {"revision": self.doc["revision"], "text": text, "use_ai": False,
                "anonymized": True, "partial_ack": False, **options}
        status, result, _ = self.client.request(self.path, data)
        self.assertEqual(status, 200, result)
        self.doc = result
        return result

    def wait_job(self, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, result, _ = self.client.request(self.path + "/job")
            if result["job"]["state"] != "running":
                _, self.doc, _ = self.client.request(self.path)
                return result["job"]
            time.sleep(.03)
        self.fail("background job timed out")

    def review(self):
        status, _, _ = self.client.request(self.path + "/confirm", {"revision": self.doc["revision"]})
        self.assertEqual(status, 200)
        status, data, _ = self.client.request(self.path + "/review", {"revision": self.doc["revision"]})
        self.assertEqual(status, 202, data)
        self.assertEqual(self.wait_job()["state"], "done")
        return self.doc["snapshot"]

    def upload(self, name, data):
        return self.client.request(self.path + "/import", raw=data,
            headers={"Content-Type":"application/octet-stream", "X-Filename":quote(name), "X-Revision":str(self.doc["revision"])})

    def test_end_to_end_three_exports_same_snapshot(self):
        text = SAMPLE + '\r\n<script>alert("虚构")</script>\r\n'
        self.update(text)
        snapshot = self.review()
        self.assertEqual(snapshot["result"]["status"], "local_only")
        _, exported, _ = self.client.request(self.path + "/json")
        self.assertEqual(exported, snapshot)
        _, html, _ = self.client.request(self.path + "/html")
        self.assertIn(snapshot["report_id"], html.decode())
        self.assertIn("&lt;script&gt;", html.decode())
        status, word, headers = self.client.request(self.path + "/word")
        self.assertEqual(status, 200)
        self.assertIn(snapshot["report_id"], headers["Content-Disposition"])
        with zipfile.ZipFile(BytesIO(word)) as archive:
            root = ET.fromstring(archive.read("customXml/review-snapshot.xml"))
        self.assertEqual(json.loads(base64.b64decode(root.text)), snapshot)

    def test_confirmation_revision_and_invalidation(self):
        self.update()
        self.assertEqual(self.client.request(self.path + "/review", {"revision":self.doc["revision"]})[0], 409)
        self.review()
        old = self.doc["revision"]
        self.update(SAMPLE + "\n已校正", use_ai=True)
        self.assertIsNone(self.doc["snapshot"])
        self.assertFalse(self.doc["confirmed"])
        self.assertEqual(self.client.request(self.path + "/word")[0], 409)
        self.assertEqual(self.client.request(self.path + "/confirm", {"revision":old})[0], 409)

    def test_export_failure_preserves_snapshot(self):
        self.update(); snapshot = self.review()
        with patch("web_server.save_docx", side_effect=PermissionError("synthetic fault")):
            self.assertEqual(self.client.request(self.path + "/word")[0], 500)
        self.assertEqual(self.client.request(self.path)[1]["snapshot"], snapshot)
        self.assertEqual(self.client.request(self.path + "/json")[1], snapshot)

    def test_isolation_csrf_origin_host_and_static_allowlist(self):
        self.update(); self.review()
        other = Client(self.client.url)
        self.assertEqual(other.request(self.path)[0], 404)
        self.assertEqual(self.client.request(self.path + "/confirm", {}, headers={"X-CSRF-Token":"wrong"})[0], 403)
        self.assertEqual(self.client.request("/api/session", headers={"Origin":"https://example.org"})[0], 403)
        self.assertEqual(self.client.request("/", headers={"Host":"rebind.example"})[0], 403)
        self.assertEqual(self.client.request("/../contract_checker.py")[0], 404)
        self.assertEqual(self.client.request("/contract.txt")[0], 404)
        status, _, headers = self.client.request("/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_txt_preserves_line_endings_and_requires_confirmation(self):
        self.update(); self.review()
        text = "虚构借款合同\r\n借款金额：100000元\r\n年利率：8%\r\n"
        self.assertEqual(self.upload("练习.txt", text.encode("utf-8"))[0], 202)
        self.assertEqual(self.wait_job()["state"], "done")
        self.assertEqual(self.doc["text"], text)
        self.assertFalse(self.doc["confirmed"])
        self.assertIsNone(self.doc["snapshot"])
        self.assertEqual(self.client.request(self.path + "/source")[1], text.encode("utf-8"))

    def test_failed_import_keeps_previous_result(self):
        self.update(); snapshot = self.review()
        self.assertEqual(self.upload("坏文件.pdf", b"not pdf")[0], 202)
        self.assertEqual(self.wait_job()["state"], "failed")
        self.assertEqual(self.doc["snapshot"], snapshot)
        self.assertEqual(self.doc["text"], SAMPLE)

    def test_slow_review_keeps_http_responsive_and_rejects_edits(self):
        event = threading.Event()
        original = self.app.review_fn
        def slow(text, use_ai=False):
            event.wait(3)
            return original(text, use_ai=False)
        self.app.review_fn = slow
        self.update()
        self.client.request(self.path + "/confirm", {"revision":self.doc["revision"]})
        self.client.request(self.path + "/review", {"revision":self.doc["revision"]})
        try:
            begin = time.monotonic()
            self.assertEqual(self.client.request("/")[0],200)
            self.assertLess(time.monotonic()-begin,1)
            self.assertEqual(self.client.request(self.path + "/job")[1]["job"]["state"],"running")
            self.assertEqual(self.client.request(self.path, {"revision":self.doc["revision"]})[0],409)
            self.assertEqual(self.client.request(self.path + "/json")[0],409)
        finally:
            event.set()
        self.assertEqual(self.wait_job()["state"],"done")

    def test_upload_validation_and_missing_models(self):
        self.assertEqual(self.upload("script.exe", b"test")[0],400)
        self.assertEqual(self.upload("D:escape.pdf", b"test")[0],400)
        self.assertEqual(self.upload("CON.pdf", b"test")[0],400)
        self.assertEqual(self.upload("empty.txt", b"")[0],413)
        self.update(); snapshot = self.review()
        def missing(*args, **kwargs):
            raise OcrError("缺少本地模型，请重新安装。")
        self.app.import_fn = missing
        self.upload("测试.png", b"test")
        job = self.wait_job()
        self.assertEqual(job["state"],"failed")
        self.assertIn("缺少本地模型",job["message"])
        self.assertEqual(self.doc["snapshot"],snapshot)

    def test_ai_exception_is_sanitized_and_local_retry_works(self):
        self.update()
        original = self.app.review_fn
        def fail(*args, **kwargs):
            raise RuntimeError("private-test-placeholder-do-not-serialize")
        self.app.review_fn = fail
        self.client.request(self.path + "/confirm", {"revision":self.doc["revision"]})
        self.client.request(self.path + "/review", {"revision":self.doc["revision"]})
        job = self.wait_job()
        self.assertEqual(job["state"],"failed")
        self.assertNotIn("private-test-placeholder", json.dumps(self.doc))
        self.app.review_fn = original
        self.assertEqual(self.review()["result"]["status"],"local_only")

    def test_session_expiration_and_document_limit(self):
        self.update()
        for _ in range(7):
            self.assertEqual(self.client.request("/api/documents",{})[0],201)
        self.assertEqual(self.client.request("/api/documents",{})[0],429)
        with self.app.lock:
            for session in self.app.sessions.values():
                session["touched"] -= 1801
        self.assertEqual(self.client.request(self.path)[0],401)

    def test_cancel_import_retains_prior_text(self):
        started = threading.Event()
        def delayed(path, progress, cancelled):
            started.set()
            while not cancelled():
                time.sleep(.01)
            raise OcrError("导入已取消，原有内容保留。")
        self.app.import_fn = delayed
        self.update(); snapshot = self.review()
        self.upload("练习.png",b"test")
        self.assertTrue(started.wait(2))
        self.client.request(self.path+"/cancel",{})
        self.assertEqual(self.wait_job()["state"],"cancelled")
        self.assertEqual(self.doc["snapshot"],snapshot)


class RealWebOcrTests(WebTests):
    # Separate loader selects only these two cases with --ocr, without rerunning all
    # inherited HTTP tests. The fixtures are real image-only PDFs from test_ocr.py.
    def test_real_two_page_scan_correction_and_source_immutability(self):
        folder = Path(__file__).with_name("test_samples")
        path = folder / "two_page_scan.pdf"
        self.assertTrue(path.exists(), str(path))
        content = path.read_bytes()
        self.assertEqual(self.upload(path.name,content)[0],202)
        self.assertEqual(self.wait_job(timeout=90)["state"],"done")
        source = self.doc["source"]
        self.assertEqual(source["page_count"],2)
        self.assertEqual(source["status"],"complete")
        self.assertTrue(all(p["method"] == "ocr" for p in source["pages"]))
        self.assertIn("100000",self.doc["text"])
        self.update(self.doc["text"].replace("100000","130000"))
        self.assertEqual(self.doc["source"],source)
        snapshot = self.review()
        self.assertIn("130000",snapshot["contract_text"])
        self.assertIn("100000",snapshot["source_record"]["initial_text"])
        self.assertEqual(self.client.request(self.path+"/source")[1],content)

    def test_real_partial_source_requires_acknowledgment(self):
        path = Path(__file__).with_name("test_samples") / "partial_blank.pdf"
        self.assertEqual(self.upload(path.name,path.read_bytes())[0],202)
        self.assertEqual(self.wait_job(timeout=90)["state"],"done")
        self.assertEqual(self.doc["source"]["status"],"partial")
        self.assertEqual(self.doc["source"]["failed_pages"],[2])
        self.assertEqual(self.client.request(self.path+"/confirm",{"revision":self.doc["revision"]})[0],400)
        self.update(self.doc["text"],partial_ack=True)
        snapshot = self.review()
        self.assertTrue(snapshot["source_record"]["partial_acknowledged"])
        self.assertIn("不是完整合同审查", "".join(snapshot["notices"]))


if __name__ == "__main__":
    import sys
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WebTests)
    if "--ocr" in sys.argv:
        for name in ("test_real_two_page_scan_correction_and_source_immutability", "test_real_partial_source_requires_acknowledgment"):
            suite.addTest(RealWebOcrTests(name))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
