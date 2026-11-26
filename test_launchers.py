"""Real Windows CMD checks; no AI calls, credentials, or visible helper windows."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import launch_demo

APP = Path(__file__).resolve().parent
WRAPPERS = {"配置AI接口.bat":"ai", "启动网页版.bat":"web",
            "启动合同审查Demo.bat":"desktop", "检查运行环境.bat":"check"}


@unittest.skipUnless(os.name == "nt", "Requires real Windows CMD")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="launcher 中文 & space ")
        self.root = Path(self.temporary.name)
        for name in [*WRAPPERS, "launch_demo.bat"]:
            shutil.copy2(APP/name, self.root/name)
        (self.root/"launch_demo.py").write_text(
            'import sys\nprint("FAKE_ENTRY:"+"|".join(sys.argv[1:]),flush=True)\n',encoding="ascii")
        self.env = os.environ.copy()
        for name in ("CONTRACT_REVIEW_CONSOLE_READY", "CONTRACT_REVIEW_LAUNCH_MODE", "CONTRACT_REVIEW_PYTHON"):
            self.env.pop(name,None)
        self.env["CONTRACT_REVIEW_PYTHON"] = sys.executable

    def tearDown(self):
        self.temporary.cleanup()

    def run_cmd(self, root, name, arguments=(), env=None, stay=True):
        result = subprocess.run([os.environ["ComSpec"],"/d","/c",name,*arguments],
            cwd=root,env=env or self.env,input=b"echo CONSOLE_REMAINS_OPEN\r\nexit\r\n",
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=25)
        output = result.stdout.decode("utf-8",errors="replace")
        if stay:
            # Only cmd /k can execute this line after the application returns.
            self.assertIn("\r\nCONSOLE_REMAINS_OPEN\r\n",output,output)
        return output

    def test_all_four_launchers_keep_console_open_and_forward_arguments(self):
        for name, mode in WRAPPERS.items():
            with self.subTest(name=name):
                output = self.run_cmd(self.root,name,["--sample-argument"])
                self.assertIn("FAKE_ENTRY:"+mode+"|--sample-argument",output)

    def test_launchers_are_ascii_crlf_without_bom(self):
        for name in [*WRAPPERS,"launch_demo.bat"]:
            content = (APP/name).read_bytes()
            content.decode("ascii")
            self.assertEqual(content.count(b"\n"),content.count(b"\r\n"))
            self.assertNotIn(b"\x0b",content)

    def test_missing_python_and_invalid_override_remain_visible(self):
        env = dict(self.env,CONTRACT_REVIEW_PYTHON=str(self.root/"absent.exe"))
        output = self.run_cmd(self.root,"配置AI接口.bat",env=env)
        self.assertIn("does not point to an existing",output)
        env = dict(self.env,PATH="")
        env.pop("CONTRACT_REVIEW_PYTHON")
        output = self.run_cmd(self.root,"启动网页版.bat",env=env)
        self.assertIn("Python was not found",output)

    def test_missing_shared_files_remain_visible(self):
        (self.root/"launch_demo.py").unlink()
        self.assertIn("launch_demo.py is missing",self.run_cmd(self.root,"检查运行环境.bat"))
        (self.root/"launch_demo.bat").unlink()
        self.assertIn("launch_demo.bat is missing",self.run_cmd(self.root,"配置AI接口.bat"))

    def test_python_application_failure_remains_visible(self):
        (self.root/"launch_demo.py").write_text('raise SystemExit(7)\n',encoding="ascii")
        output = self.run_cmd(self.root,"启动合同审查Demo.bat")
        self.assertIn("Program exited with code 7",output)

    def test_local_virtual_environment_precedes_system_python(self):
        import venv
        venv.EnvBuilder(with_pip=False).create(self.root/".venv")
        (self.root/"launch_demo.py").write_text(
            'import sys\nprint("CHOSEN_PYTHON:"+sys.executable,flush=True)\n',encoding="ascii")
        env = dict(self.env)
        env.pop("CONTRACT_REVIEW_PYTHON")
        output = self.run_cmd(self.root,"配置AI接口.bat",env=env)
        self.assertIn(str(self.root/".venv"/"Scripts"/"python.exe"),output)

    def test_real_ai_status_and_web_help_without_network(self):
        env = dict(self.env,CONTRACT_REVIEW_AI_CONFIG=str(self.root/"no-settings.json"))
        for name in ("DEEPSEEK_API_KEY","DEEPSEEK_MODEL","DEEPSEEK_TIMEOUT","DEEPSEEK_MAX_TOKENS"):
            env.pop(name,None)
        output = self.run_cmd(APP,"配置AI接口.bat",["--status"],env)
        self.assertIn('"configured": false',output)
        self.assertIn("尚未配置",output)
        output = self.run_cmd(APP,"启动网页版.bat",["--help"],env)
        self.assertIn("合同审查本机浏览器版",output)
        self.assertNotIn("\ufffd",output)

    def test_parent_virtual_environment_is_found(self):
        import venv
        venv.EnvBuilder(with_pip=False).create(self.root/".venv")
        child = self.root/"child project"
        child.mkdir()
        for name in [*WRAPPERS,"launch_demo.bat","launch_demo.py"]:
            shutil.copy2(self.root/name,child/name)
        env = dict(self.env,PATH="")
        env.pop("CONTRACT_REVIEW_PYTHON")
        self.assertIn("FAKE_ENTRY:web",self.run_cmd(child,"启动网页版.bat",env=env))

    def test_real_web_port_conflict_preserves_console(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1",0))
            occupied.listen()
            output = self.run_cmd(APP,"启动网页版.bat",["--port",str(occupied.getsockname()[1]),"--no-browser"])
        self.assertIn("端口已占用或无法监听",output)
        self.assertIn("Program exited with code 1",output)

    def test_all_real_entries_support_readonly_environment_check(self):
        env = dict(self.env,CONTRACT_REVIEW_AI_CONFIG=str(self.root/"no-settings.json"))
        for name in WRAPPERS:
            with self.subTest(name=name):
                output = self.run_cmd(APP,name,["--check"],env)
                self.assertIn("运行文件： 齐全",output)

    def test_missing_module_diagnostic_does_not_expose_exception_payload(self):
        secret = "fictional-sensitive-value"
        output = io.StringIO()
        error = ModuleNotFoundError(secret,name="docx")
        with patch.object(sys,"argv",["launch_demo.py","web"]), \
             patch("runpy.run_path",side_effect=error),contextlib.redirect_stdout(output):
            self.assertEqual(launch_demo.main(),1)
        self.assertIn("缺少模块 docx",output.getvalue())
        self.assertNotIn(secret,output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
