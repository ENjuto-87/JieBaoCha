"""AI adapter checks: fictional credentials, real Windows DPAPI and local HTTP SDK.

No external AI call. HTTP fixtures are explicitly synthetic model responses.
"""
import base64
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import ai_config
from ai_client import completion,test_connection as probe_connection
from ai_config import AiSettings,ConfigError,get_settings,public_status,save_settings
import review_service
from test_web import Client
from web_server import BrowserServer,ReviewApp

TEXT = "虚构借款合同\n保证方式：连带责任保证\n借款金额：100000元。"
FAKE_KEY = "offline-fictional-credential-for-tests"


class FakeProvider(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append({"path":self.path,"authorization":self.headers.get("Authorization"),"body":data})
        status = self.server.reply_status
        content = self.server.content
        if status == 200:
            payload = {"id":"synthetic-local-response","object":"chat.completion","created":1,"model":"offline-model",
                       "choices":[{"index":0,"finish_reason":self.server.finish,"message":{"role":"assistant","content":content}}],
                       "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}
        else:
            payload = {"error":{"message":"synthetic failure contains "+FAKE_KEY,"type":"test_error","code":"test"}}
        body = json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)


class AiTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).with_name(".test-tmp")
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.path = Path(self.temp.name)/".ai-settings.json"
        self.env = patch.dict(os.environ,{"CONTRACT_REVIEW_AI_CONFIG":str(self.path),"DEEPSEEK_API_KEY":"",
                "DEEPSEEK_MODEL":"","DEEPSEEK_TIMEOUT":"","DEEPSEEK_MAX_TOKENS":"",
                "NO_PROXY":"127.0.0.1,localhost","no_proxy":"127.0.0.1,localhost"})
        self.env.start()
        self.provider = ThreadingHTTPServer(("127.0.0.1",0),FakeProvider)
        self.provider.reply_status = 200
        self.provider.content = json.dumps({"risks":[]})
        self.provider.finish = "stop"
        self.provider.requests = []
        self.thread = threading.Thread(target=self.provider.serve_forever,daemon=True)
        self.thread.start()
        self.settings = AiSettings(FAKE_KEY,"offline-model",10,256,"离线HTTP测试",f"http://127.0.0.1:{self.provider.server_port}/v1")

    def tearDown(self):
        self.provider.shutdown(); self.provider.server_close(); self.thread.join()
        self.env.stop(); self.temp.cleanup()

    def run_review(self):
        with patch("review_service.get_settings",return_value=self.settings):
            return review_service.review_contract(TEXT,use_ai=True)

    def test_real_sdk_http_serialization_and_three_evidence_groups(self):
        self.provider.content = json.dumps({"risks":[{"risk_level":"medium","issue":"虚构测试意见","reason":"需核对材料",
            "evidence":["借款金额：100000元。","保证方式： 连带责任保证","原文不存在的句子"]}]},ensure_ascii=False)
        result = self.run_review()
        self.assertEqual(result["status"],"completed")
        finding = result["ai_findings"][0]
        self.assertEqual(finding["verified_evidence"],["借款金额：100000元。"])
        self.assertEqual(finding["whitespace_matched_evidence"],["保证方式： 连带责任保证"])
        self.assertEqual(finding["unverified_evidence"],["原文不存在的句子"])
        request = self.provider.requests[0]
        self.assertEqual(request["path"],"/v1/chat/completions")
        self.assertEqual(request["authorization"],"Bearer "+FAKE_KEY)
        self.assertEqual(request["body"]["messages"][1]["content"],TEXT)
        self.assertEqual(request["body"]["model"],"offline-model")
        self.assertEqual(request["body"]["response_format"],{"type":"json_object"})
        self.assertEqual(request["body"]["thinking"],{"type":"disabled"})
        self.assertNotIn(FAKE_KEY,json.dumps(result))

    def test_actual_http_errors_preserve_local_and_do_not_retry_or_leak(self):
        for status in (401,402,403,404,429,500):
            with self.subTest(status=status):
                self.provider.reply_status = status
                before = len(self.provider.requests)
                result = self.run_review()
                self.assertEqual(result["status"],"ai_incomplete")
                self.assertTrue(result["local_findings"])
                self.assertEqual(len(self.provider.requests)-before,1)
                self.assertNotIn(FAKE_KEY,json.dumps(result))

    def test_truncated_and_malformed_responses_not_completed(self):
        for content,finish in [('{"risks":[]}',"length"),('{"risks":[]}',"content_filter"),("not-json","stop"),('{"risks":[{}]}',"stop"),("","stop")]:
            self.provider.content,self.provider.finish = content,finish
            self.assertEqual(self.run_review()["status"],"ai_incomplete")

    def test_long_contract_not_sent_or_silently_truncated(self):
        with patch("review_service.get_settings",return_value=self.settings):
            result = review_service.review_contract("虚构"*16000,True)
        self.assertEqual(result["status"],"ai_incomplete")
        self.assertEqual(self.provider.requests,[])
        self.assertIn("未发送、未截断","".join(result["messages"]))

    def test_missing_key_and_config_corruption_keep_local_rules(self):
        self.assertEqual(review_service.review_contract(TEXT,True)["status"],"local_only")
        self.path.write_text("{bad",encoding="utf-8")
        self.assertEqual(review_service.review_contract(TEXT,True)["status"],"ai_incomplete")
        self.assertEqual(review_service.review_contract(TEXT,False)["status"],"local_only")

    @unittest.skipUnless(os.name == "nt","Windows DPAPI")
    def test_actual_dpapi_encrypted_storage_roundtrip_and_atomic_failure(self):
        save_settings(FAKE_KEY,"deepseek-flash")
        before = self.path.read_bytes()
        self.assertNotIn(FAKE_KEY.encode(),before)
        self.assertEqual(get_settings().api_key,FAKE_KEY)
        self.assertNotIn(FAKE_KEY,repr(get_settings()))
        self.assertNotIn(FAKE_KEY,json.dumps(public_status()))
        with patch("ai_config.os.replace",side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                save_settings("another-fictional-credential")
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(list(self.path.parent.iterdir()),[self.path])

    def test_env_key_and_model_override_local_file(self):
        self.path.write_text("{bad",encoding="utf-8")
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":FAKE_KEY,"DEEPSEEK_MODEL":"deepseek-v4-pro","DEEPSEEK_TIMEOUT":"90"}):
            settings = get_settings()
            self.assertEqual(settings.api_key,FAKE_KEY)
            self.assertEqual(settings.model,"deepseek-v4-pro")
            self.assertEqual(settings.timeout,90)

    def test_invalid_config_fields_and_empty_key_rejected(self):
        for fields in ({"DEEPSEEK_TIMEOUT":"abc"},{"DEEPSEEK_MAX_TOKENS":"-1"},{"DEEPSEEK_MODEL":"bad\nmodel"}):
            with patch.dict(os.environ,fields):
                with self.assertRaises(ConfigError):
                    get_settings()
        with self.assertRaises(ConfigError):
            save_settings("")
        self.assertFalse(self.path.exists())
        with patch("ai_config.Path.exists",side_effect=PermissionError):
            self.assertEqual(review_service.review_contract(TEXT,True)["status"],"ai_incomplete")
            self.assertEqual(review_service.review_contract(TEXT,False)["status"],"local_only")

    def test_probe_real_local_http_never_sends_contract(self):
        self.provider.content = '{"ok":true}'
        result = probe_connection(self.settings)
        self.assertTrue(result["ok"])
        body = self.provider.requests[0]["body"]
        self.assertEqual(body["max_tokens"],64)
        self.assertNotIn(TEXT,json.dumps(body,ensure_ascii=False))

    def test_web_status_and_background_probe_are_secret_free(self):
        app = ReviewApp()
        server = BrowserServer(("127.0.0.1",0),app)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            client = Client(f"http://127.0.0.1:{server.server_port}")
            _,doc,_ = client.request("/api/documents",{})
            path = "/api/documents/"+doc["id"]
            with patch("web_server.get_settings",return_value=self.settings), patch("ai_config.get_settings",return_value=self.settings):
                self.provider.content = '{"ok":true}'
                status,info,_ = client.request("/api/ai/status")
                self.assertEqual(status,200); self.assertNotIn(FAKE_KEY,json.dumps(info))
                self.assertTrue(info["configured"])
                self.assertEqual(client.request(path+"/ai-test",{"revision":0})[0],202)
                import time
                for _ in range(100):
                    job = client.request(path+"/job")[1]["job"]
                    if job["state"] != "running": break
                    time.sleep(.03)
                self.assertEqual(job["state"],"done")
                info = client.request("/api/ai/status")[1]
                self.assertTrue(info["last_test"]["ok"])
                self.assertNotIn(FAKE_KEY,json.dumps(info))
                self.assertEqual(client.request(path+"/ai-test",{"revision":0})[0],429)
            self.assertEqual(client.request("/.ai-settings.json")[0],404)
        finally:
            server.shutdown(); server.server_close(); thread.join(); app.close()

    def test_backend_config_change_invalidates_confirmed_ai_document(self):
        app = ReviewApp()
        try:
            from web_server import Document
            doc = Document(text=TEXT,use_ai=True)
            with patch("web_server.get_settings",return_value=self.settings):
                app.view(doc)
                app.confirm(doc,{"revision":0})
            changed = AiSettings(FAKE_KEY,"different-model")
            with patch("web_server.get_settings",return_value=changed):
                view = app.view(doc)
            self.assertEqual(view["revision"],1)
            self.assertFalse(view["confirmed"])
            self.assertIsNone(view["snapshot"])
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
