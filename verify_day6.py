"""第6课离线检查：验证交付文件、启动脚本和核心模块，不访问 API。"""
from pathlib import Path
import importlib
import unittest

import launch_demo


ROOT = Path(__file__).resolve().parent
WRAPPERS = ("启动合同审查Demo.bat", "检查运行环境.bat", "启动网页版.bat", "配置AI接口.bat")


class Day6DeliveryChecks(unittest.TestCase):
    def test_required_delivery_files_exist(self):
        required = {
            "demo_gui.py", "review_service.py", "contract_checker.py",
            "address_rules.py", "review_display.py", "report_export.py",
            "reliability.py", "check_config.py", "requirements.txt",
            "report_docx.py", "source_import.py", "ocr_engine.py", "requirements-ocr.txt",
            "launch_demo.bat", "launch_demo.py", "web_server.py",
            "ai_config.py", "ai_client.py", "configure_ai.py",
            *WRAPPERS, "交付使用说明.md",
            "三分钟交付演示脚本.md", "最终验收记录模板.md",
        }
        self.assertFalse([name for name in required if not (ROOT / name).is_file()])

    def test_launchers_resolve_own_directory(self):
        for name in WRAPPERS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('%~dp0', text)
            self.assertIn('launch_demo.bat', text)
        shared = (ROOT / "launch_demo.bat").read_text(encoding="utf-8")
        self.assertIn('pushd "%~dp0"', shared)

    def test_main_launcher_targets_gui(self):
        text = (ROOT / "启动合同审查Demo.bat").read_text(encoding="utf-8")
        self.assertIn('CONTRACT_REVIEW_LAUNCH_MODE=desktop', text)
        self.assertEqual(launch_demo.ENTRIES.get("desktop"), "demo_gui.py")

    def test_config_launcher_targets_safe_checker(self):
        text = (ROOT / "检查运行环境.bat").read_text(encoding="utf-8")
        self.assertIn('CONTRACT_REVIEW_LAUNCH_MODE=check', text)
        self.assertEqual(launch_demo.ENTRIES.get("check"), "check_config.py")

    def test_launchers_do_not_assign_api_key(self):
        for name in (*WRAPPERS, "launch_demo.bat"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("DEEPSEEK_API_KEY=", text)

    def test_requirements_is_minimal(self):
        lines = [x.strip() for x in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("openai"))
        self.assertTrue(lines[1].startswith("python-docx"))

    def test_core_modules_import_without_running_review(self):
        for name in ("reliability", "review_display", "report_export", "review_service", "demo_gui"):
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.import_module(name))

    def test_user_guide_states_human_review_boundary(self):
        text = (ROOT / "交付使用说明.md").read_text(encoding="utf-8")
        self.assertIn("不能替代律师判断", text)
        self.assertIn("不会自动脱敏", text)
        self.assertIn("报告包含完整输入原文", text)

    def test_demo_script_uses_anonymized_material(self):
        text = (ROOT / "三分钟交付演示脚本.md").read_text(encoding="utf-8")
        self.assertIn("脱敏合同", text)
        self.assertIn("误报、漏报", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
