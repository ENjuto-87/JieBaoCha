"""One command for regression checks; --ocr runs actual local OCR too."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr", action="store_true", help="include real local OCR and Windows Tk checks")
    args = parser.parse_args()
    temporary_root = Path(__file__).parent / ".test-tmp"
    temporary_root.mkdir(exist_ok=True)
    tempfile.tempdir = str(temporary_root)
    # Regression checks must never load a user's encrypted credential file.
    os.environ["CONTRACT_REVIEW_AI_CONFIG"] = str(temporary_root / "nonexistent-test-ai-config.json")
    os.environ["DEEPSEEK_API_KEY"] = ""
    for name in ("DEEPSEEK_MODEL","DEEPSEEK_TIMEOUT","DEEPSEEK_MAX_TOKENS"):
        os.environ.pop(name,None)
    modules = [
        "verify_day1", "verify_day3", "verify_day4", "verify_day6",
        "test_guarantee_mode_regression", "test_guarantee_mode_boundaries",
        "test_narrative_and_redlines", "test_word", "test_ai",
    ]
    if args.ocr:
        modules += ["test_ocr", "test_gui"]
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    address = subprocess.run([sys.executable, str(Path(__file__).with_name("day11_address_regression.py"))], check=False)
    return 0 if result.wasSuccessful() and address.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
