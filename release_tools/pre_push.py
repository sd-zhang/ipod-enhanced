"""Validate the original worktree and outgoing history before the full push gate.

This runs directly as Git's pre-push hook, outside pre-commit's temporary stash.
It performs no network requests or pushes. The full verifier audits dependencies.
"""

import re
import subprocess
import sys
from pathlib import Path

from check_repository import CREDENTIAL, PERSONAL_PATH, check_repository, git, public_path


def history_failures(root: Path, commit: str) -> list[str]:
    failures = []
    seen = set()
    for revision in git(root, "rev-list", commit).decode().splitlines():
        for entry in git(root, "ls-tree", "-r", "-l", "-z", revision).split(b"\0"):
            if not entry:
                continue
            fields, raw_name = entry.split(b"\t", 1)
            mode, kind, oid, size = fields.decode().split()
            name = raw_name.decode()
            if (name, oid) in seen:
                continue
            seen.add((name, oid))
            label = repr(name) + " in history " + revision[:12]
            if not public_path(name) or mode not in {"100644", "100755"} or kind != "blob":
                failures.append(label + ": forbidden publication path or mode")
                continue
            if int(size) > 500 * 1024:
                failures.append(label + ": exceeds source-file limit")
                continue
            data = git(root, "cat-file", "blob", oid)
            if CREDENTIAL.search(data) or PERSONAL_PATH.search(data):
                failures.append(label + ": possible credential or personal path (redacted)")
            try:
                if "\0" in data.decode("utf-8"):
                    failures.append(label + ": binary publication blob")
            except UnicodeDecodeError:
                failures.append(label + ": binary publication blob")
    return failures


def preflight(root: Path, updates: str) -> list[str]:
    _, failures = check_repository(root, None, protect_branch=True, require_clean=True)
    head = git(root, "rev-parse", "HEAD").decode().strip()
    commits = set()
    for line in updates.splitlines():
        fields = line.split()
        if len(fields) != 4 or not all(
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", fields[i]) for i in (1, 3)
        ):
            failures.append("invalid pre-push ref record")
            continue
        _, local_sha, destination, _ = fields
        if destination in {"refs/heads/main", "refs/heads/master"}:
            failures.append("protected destination: " + destination)
        if set(local_sha) == {"0"}:
            continue  # Branch deletion sends no new objects.
        commit = git(root, "rev-parse", local_sha + "^{commit}").decode().strip()
        if commit != head:
            failures.append("outgoing ref must resolve to the checked-out commit")
        commits.add(commit)
    for commit in commits:
        failures.extend(history_failures(root, commit))
    return failures


def main() -> int:
    root = Path.cwd()
    try:
        failures = preflight(root, sys.stdin.read())
    except (OSError, ValueError, UnicodeError):
        failures = ["Cannot validate outgoing refs and repository history"]
    if failures:
        print("Pre-push refused:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    return subprocess.run(
        [
            str(root / ".venv/bin/python"),
            "-B",
            str(root / "release_tools/verify_repository.py"),
            "--require-clean",
        ],
        cwd=root,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
