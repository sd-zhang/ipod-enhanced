"""Publication boundaries are checked against real disposable Git repositories."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q", "-b", "topic")
        self.write(".gitignore", "/build/\n")
        self.write("README.md", "# Hello *world*\n\n[Self](#hello-world)\n")
        self.git("add", ".")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def run_check(self, script="check_repository.py", *args):
        return subprocess.run(
            [sys.executable, str(HERE / script), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
        )

    def candidate(self, paths):
        contents = {
            "files": {
                name: hashlib.sha256((self.root / name).read_bytes()).hexdigest() for name in paths
            }
        }
        path = self.write("build/candidate.json", json.dumps(contents))
        return str(path)

    def test_clean_index_passes(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dirty_index_content_is_rejected(self):
        self.write("README.md", "# Changed\n")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("unstaged", result.stderr)

    def test_index_flags_cannot_hide_staged_bytes(self):
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                path = self.root / "hidden.txt"
                path.write_bytes(b"\0secret staged bytes\n")
                self.git("add", "hidden.txt")
                self.git("update-index", "--" + flag, "hidden.txt")
                path.write_text("harmless working copy\n")
                result = self.run_check()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("index", result.stderr)
                self.git("update-index", "--no-" + flag, "hidden.txt")
                self.git("rm", "-f", "hidden.txt")

    def test_unapproved_public_file_is_rejected(self):
        self.write("new.py", "answer = 42\n")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("unapproved", result.stderr)

    def test_candidate_covers_new_files_and_locks_their_bytes(self):
        self.write("new.py", "answer = 42\n")
        candidate = self.candidate([".gitignore", "README.md", "new.py"])
        result = self.run_check("check_repository.py", "--candidate", candidate)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write("new.py", "answer = 43\n")
        result = self.run_check("check_repository.py", "--candidate", candidate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("candidate hash", result.stderr)

    def test_candidate_cannot_hide_an_existing_public_file(self):
        candidate = self.candidate(["README.md"])
        result = self.run_check("check_repository.py", "--candidate", candidate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("omitted", result.stderr)

    def test_private_generated_and_credential_paths_never_publish(self):
        for name in (
            "build/report.json",
            "dist/index.html",
            "secret.ipepatch",
            "targets/ipod55g/private/install.py",
            "gapless/flash_firmware.py",
            ".env",
            ".aws/credentials",
            "inputs/local-manifest.json",
        ):
            with self.subTest(name=name):
                path = self.write(name, "{}\n")
                candidate = self.candidate([".gitignore", "README.md", name])
                for script in ("check_repository.py", "check_doc_links.py"):
                    result = self.run_check(script, "--candidate", candidate)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("forbidden publication path", result.stderr)
                path.unlink()

    def test_tracked_artifact_is_rejected_even_when_gitignored(self):
        self.write("build/artifact.txt", "generated\n")
        self.git("add", "-f", "build/artifact.txt")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("forbidden publication path", result.stderr)

    def test_secrets_are_rejected_without_echoing_values(self):
        token = "ghp_" + "A" * 36
        self.write("config.py", "TOKEN = " + repr(token) + "\n")
        self.git("add", "config.py")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("credential", result.stderr)
        self.assertNotIn(token, result.stderr + result.stdout)

    def test_personal_absolute_paths_are_rejected_without_exposing_account(self):
        for value in (
            "/Users/" + "private-account/project",
            "/home/" + "private-account/project",
            "C:" + chr(92) + "Users" + chr(92) + "private-account" + chr(92) + "project",
        ):
            with self.subTest(style=value[0]):
                self.write("local.txt", value + "\n")
                self.git("add", "local.txt")
                result = self.run_check()
                self.assertEqual(result.returncode, 1)
                self.assertIn("personal absolute path", result.stderr)
                self.assertNotIn("private-account", result.stderr + result.stdout)

    def test_workflow_actions_require_immutable_pins(self):
        name = ".github/workflows/verify.yml"
        for reference, expected in [
            ("actions/checkout@v4", 1),
            ("actions/checkout@" + "a" * 40, 0),
            ("docker://example/image:latest", 1),
        ]:
            self.write(name, "jobs:\n  test:\n    steps:\n      - uses: " + reference + "\n")
            self.git("add", name)
            result = self.run_check()
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected:
                self.assertIn("immutable action pin", result.stderr)

    def test_symlink_cannot_publish_a_private_file(self):
        private = self.write("build/private.txt", "private\n")
        (self.root / "public.txt").symlink_to(private)
        self.git("add", "public.txt")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink", result.stderr)

    def test_hygiene_is_check_only(self):
        path = self.write("bad.py", "value = 1  \n\n")
        self.git("add", "bad.py")
        before = path.read_bytes()
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("whitespace", result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_main_and_master_are_protected(self):
        for name in ("main", "master"):
            self.git("symbolic-ref", "HEAD", "refs/heads/" + name)
            result = self.run_check("check_repository.py", "--protect-branch")
            self.assertEqual(result.returncode, 1)
            self.assertIn("protected branch", result.stderr)

    def test_exact_links_rendered_headings_and_embedded_html(self):
        self.write(
            "README.md",
            "# Hello *world*\n\n# Hello world\n\n"
            '<a id="Exact"></a>\n\n[Self](#hello-world-1)\n\n'
            "[HTML](#Exact)\n\n[Dir](docs/#code-name)\n",
        )
        self.write("docs/README.md", "# `Code` name\n")
        self.git("add", ".")
        result = self.run_check("check_doc_links.py")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_links_and_exact_anchor_case_are_rejected(self):
        for target in (
            "missing.md",
            "#Hello-world",
            "/README.md",
            "../outside.md",
            "readme.md",
            "build/report.md",
            ".git/HEAD",
        ):
            with self.subTest(target=target):
                self.write("README.md", "# Hello world\n\n[Bad](" + target + ")\n")
                self.git("add", "README.md")
                result = self.run_check("check_doc_links.py")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("README.md ->", result.stderr)

    def test_candidate_link_targets_must_be_approved(self):
        self.write("README.md", "[New](new.md#heading)\n")
        self.write("new.md", "# Heading\n")
        self.git("add", "README.md")
        result = self.run_check("check_doc_links.py")
        self.assertEqual(result.returncode, 1)
        candidate = self.candidate([".gitignore", "README.md", "new.md"])
        result = self.run_check("check_doc_links.py", "--candidate", candidate)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_credential_urls_are_redacted_in_link_failures(self):
        url_value = "private-token-value"
        self.write("README.md", "[Secret](https://user:" + url_value + "@example.com)\n")
        self.git("add", "README.md")
        result = self.run_check("check_doc_links.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("credential", result.stderr)
        self.assertNotIn(url_value, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
