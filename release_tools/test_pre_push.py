"""Exercise the installed hook against disposable local bare remotes only."""

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from install_hooks import install

HERE = Path(__file__).resolve().parent


class PushBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.git("init", "-q", "-b", "topic")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write(".gitignore", ".venv/\nbuild/\n")
        self.write("README.md", "# Public source\n")
        self.write(".pre-commit-config.yaml", "repos: []\n")
        for name in ("pre_push.py", "check_repository.py"):
            self.write("release_tools/" + name, (HERE / name).read_text())
        # Record entry to the expensive verifier; preflight must reject first.
        self.write(
            "release_tools/verify_repository.py",
            "from pathlib import Path\n"
            "Path('build').mkdir(exist_ok=True)\n"
            "Path('build/verifier-ran').write_text('yes')\n",
        )
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o755)
        self.commit()
        self.remote = Path(self.temp.name) / "remote.git"
        self.git("init", "--bare", "-q", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        install(self.root)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=check
        )

    def commit(self):
        self.git("add", "--all")  # Disposable fixture, never the user's repository.
        self.git("commit", "-q", "-m", "Fixture")

    def push(self, ref="HEAD:refs/heads/topic"):
        return self.git("push", "origin", ref, check=False)

    def test_clean_current_commit_reaches_verifier_and_local_remote(self):
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "build/verifier-ran").exists())

    def test_dirty_original_tree_is_rejected_before_any_stash(self):
        self.write("README.md", "# Unstaged change\n")
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean", result.stderr)
        self.assertFalse((self.root / "build/verifier-ran").exists())
        self.assertEqual((self.root / "README.md").read_text(), "# Unstaged change\n")

    def test_protected_destination_is_rejected_from_topic_branch(self):
        result = self.push("HEAD:refs/heads/main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected destination", result.stderr)
        self.assertFalse((self.root / "build/verifier-ran").exists())

    def test_other_commit_cannot_inherit_current_tree_validation(self):
        self.git("branch", "older")
        self.write("README.md", "# Newer public source\n")
        self.commit()
        result = self.push("older:refs/heads/older")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checked-out commit", result.stderr)

    def test_removed_private_path_is_rejected_in_history(self):
        self.write("inputs/local-manifest.json", "{}\n")
        # Pre-commit would reject this; construct historical fixture with hooks disabled.
        self.git("-c", "core.hooksPath=/dev/null", "add", "inputs/local-manifest.json")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "Old fixture")
        self.git("rm", "inputs/local-manifest.json")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "Remove fixture")
        result = self.push()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("history", result.stderr)
        self.assertFalse((self.root / "build/verifier-ran").exists())

    def test_foreign_hook_is_not_overwritten(self):
        hook = self.root / ".git/hooks/pre-push"
        hook.write_text("#!/bin/sh\nexit 42\n")
        with self.assertRaisesRegex(ValueError, "existing"):
            install(self.root)
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 42\n")


if __name__ == "__main__":
    unittest.main()
