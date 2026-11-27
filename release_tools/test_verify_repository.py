"""The verification runner must fail closed and keep security evidence useful."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class VerifyTests(unittest.TestCase):
    def test_medium_and_untriaged_low_security_findings_fail(self):
        # Running the public report checker catches policy bypasses without a scanner mock.
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "bandit.json"
            cases = [
                ("B603", "LOW", "release_tools/build_web.py", 0),
                ("B603", "MEDIUM", "release_tools/build_web.py", 1),
                ("B105", "LOW", "release_tools/build_web.py", 1),
                ("B311", "LOW", "release_tools/build_web.py", 1),
                ("B311", "LOW", "navigation_qol/verify_hotpaths.py", 0),
                ("B101", "LOW", "navigation_qol/verify_hotpaths.py", 0),
                ("B101", "LOW", "clock_pomodoro/pomodoro_patch.py", 0),
                ("B101", "LOW", "release_tools/apply_patch.py", 1),
                ("B101", "LOW", "targets/ipod55g/new_parser.py", 1),
            ]
            for test_id, severity, filename, expected in cases:
                with self.subTest(test_id=test_id, severity=severity, filename=filename):
                    report.write_text(
                        json.dumps(
                            {
                                "errors": [],
                                "results": [
                                    {
                                        "test_id": test_id,
                                        "issue_severity": severity,
                                        "filename": filename,
                                        "line_number": 1,
                                        "issue_text": "scanner description",
                                        "code": "potential-secret-never-echo",
                                    }
                                ],
                            }
                        )
                    )
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(HERE / "verify_repository.py"),
                            "--bandit-report",
                            str(report),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertNotIn("potential-secret-never-echo", result.stdout + result.stderr)
                    self.assertIn(severity, result.stdout)

    def test_scanner_errors_are_not_a_clean_security_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "bandit.json"
            report.write_text(
                json.dumps(
                    {"errors": [{"filename": "bad.py", "reason": "parse failed"}], "results": []}
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "verify_repository.py"),
                    "--bandit-report",
                    str(report),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("scanner errors", result.stdout)


if __name__ == "__main__":
    unittest.main()
