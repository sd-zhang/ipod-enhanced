"""Read-only public-source hygiene and publication inventory checks.

The default inventory is Git's index. --candidate accepts an explicit JSON object
{"files": {"repo/relative/path": "sha256", ...}} for reviewed, unstaged sources.
Neither inventory can admit private tools, credentials, firmware or build output.
No file contents or credential values are included in diagnostic messages.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml

VENDOR = "release_tools/web/vendor/"
FORBIDDEN_PARTS = {
    ".git",
    ".aws",
    ".ssh",
    ".codex",
    ".agents",
    ".venv",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "private",
    "build",
    "dist",
    "inputs",
}
FORBIDDEN_SUFFIXES = {
    ".ipsw",
    ".bin",
    ".elf",
    ".o",
    ".bsdiff",
    ".ipepatch",
    ".zip",
    ".pyc",
    ".log",
    ".tmp",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}
FORBIDDEN_FILES = {
    "gapless/flash_firmware.py",
    "gapless/test_flash_firmware.py",
    "docs/HANDOFF.md",
    ".DS_Store",
}
PERSONAL_PATH = re.compile(rb"/(?:Users|home)/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\s]+\\")
CREDENTIAL = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    rb"|(?:AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    rb"|https?://[^\s/:]+:[^\s/@]+@)"
)


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Git inventory command failed")
    return result.stdout


def listed(root: Path, *args: str) -> set[str]:
    return {
        name.decode("utf-8") for name in git(root, "ls-files", "-z", *args).split(b"\0") if name
    }


def exact_index(root: Path) -> list[str]:
    """Compare actual stage-0 blob bytes; Git's stat cache and flags are not evidence."""
    failures = []
    algorithm = git(root, "rev-parse", "--show-object-format").decode().strip()
    for item in git(root, "ls-files", "-v", "-z").split(b"\0"):
        if item and item[:1] != b"H":
            failures.append(repr(item[2:].decode()) + ": unsupported index flag")
    for item in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not item:
            continue
        fields, raw_name = item.split(b"\t", 1)
        mode, oid, stage = fields.decode().split()
        name = raw_name.decode()
        path = root / name
        if stage != "0" or mode not in {"100644", "100755"}:
            failures.append(repr(name) + ": unsupported index stage or mode")
            continue
        if path.is_symlink() or not path.is_file():
            failures.append(repr(name) + ": index file is missing or a symlink")
            continue
        data = path.read_bytes()
        blob = b"blob " + str(len(data)).encode() + b"\0" + data
        if hashlib.new(algorithm, blob).hexdigest() != oid:
            failures.append(repr(name) + ": working bytes differ from index blob")
        if bool(path.stat().st_mode & 0o111) != (mode == "100755"):
            failures.append(repr(name) + ": working mode differs from index")
    return failures


def public_path(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != name
        or "\\" in name
        or any(ord(char) < 32 for char in name)
        or any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in path.parts)
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
        or name in FORBIDDEN_FILES
        or name.startswith("docs/superpowers/")
        or path.name.startswith("Firmware-")
        or path.name in {"credentials", "id_rsa", "id_ed25519"}
        or (path.suffix.lower() == ".html" and not name.startswith("release_tools/web/"))
    )


def inventory(root: Path, candidate: Path | None = None) -> tuple[set[str], list[str]]:
    failures: list[str] = []
    expected: dict[str, str] = {}
    if candidate is None:
        names = listed(root)
    else:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or set(data) != {"files"}
                or not isinstance(data["files"], dict)
            ):
                raise ValueError
            expected = data["files"]
            if not expected or not all(
                isinstance(k, str) and isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v)
                for k, v in expected.items()
            ):
                raise ValueError
        except (OSError, UnicodeError, ValueError):
            return set(), ["invalid candidate inventory; expected files mapped to SHA-256 hashes"]
        names = set(expected)
    allowed: set[str] = set()
    for name in sorted(names):
        if not public_path(name):
            failures.append("forbidden publication path: " + repr(name))
            continue
        path = root / name
        if path.is_symlink() or any(
            parent.is_symlink() for parent in path.parents if parent != root
        ):
            failures.append(name + ": symlink publication is forbidden")
            continue
        if not path.is_file():
            failures.append(name + ": publication file is missing")
            continue
        if (
            candidate is not None
            and hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]
        ):
            failures.append(name + ": candidate hash differs from reviewed content")
            continue
        allowed.add(name)
    return allowed, failures


def pinned_actions(value: object) -> bool:
    """Require immutable remote action references anywhere in a workflow."""
    if isinstance(value, list):
        return all(pinned_actions(item) for item in value)
    if not isinstance(value, dict):
        return True
    for key, item in value.items():
        if key == "uses":
            if not isinstance(item, str):
                return False
            if item.startswith("./"):
                if ".." in PurePosixPath(item).parts:
                    return False
            elif item.startswith("docker://"):
                if not re.fullmatch(r"docker://[^@\s]+@sha256:[0-9a-f]{64}", item):
                    return False
            elif not re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", item):
                return False
        elif not pinned_actions(item):
            return False
    return True


def hygiene(root: Path, names: set[str]) -> list[str]:
    failures: list[str] = []
    folded: dict[str, str] = {}
    for name in sorted(names):
        if name.casefold() in folded:
            failures.append(name + ": case-insensitive path collision")
        folded[name.casefold()] = name
        data = (root / name).read_bytes()
        if len(data) > 500 * 1024:
            failures.append(name + ": exceeds the 500 KiB source-file limit")
        if PERSONAL_PATH.search(data):
            failures.append(name + ": personal absolute path (value redacted)")
        if CREDENTIAL.search(data):
            failures.append(name + ": possible credential (value redacted)")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            failures.append(name + ": binary or non-UTF-8 publication file")
            continue
        if "\0" in content:
            failures.append(name + ": binary publication file")
        if re.search(r"^(?:<{7}|>{7}|={7})(?: |$)", content, re.MULTILINE):
            failures.append(name + ": merge conflict marker")
        # Pinned vendor source and its license notices must retain upstream bytes.
        if not name.startswith(VENDOR):
            if re.search(r"[ \t]+$", content, re.MULTILINE) or "\r" in content:
                failures.append(name + ": trailing whitespace or non-LF line ending")
            if data and (not data.endswith(b"\n") or data.endswith(b"\n\n")):
                failures.append(name + ": expected exactly one final newline")
        try:
            if name.endswith(".json"):
                json.loads(content)
            elif name.endswith((".yaml", ".yml")):
                documents = list(yaml.safe_load_all(content))
                if name.startswith(".github/workflows/") and not all(
                    pinned_actions(doc) for doc in documents
                ):
                    failures.append(name + ": expected an immutable action pin")
        except (ValueError, yaml.YAMLError):
            failures.append(name + ": invalid JSON/YAML (content redacted)")
    manifest = VENDOR + "sources.json"
    vendor_files = {name for name in names if name.startswith(VENDOR)}
    if vendor_files:
        if manifest not in names:
            failures.append("vendor integrity manifest is missing from publication inventory")
        else:
            try:
                entries = json.loads((root / manifest).read_text())["files"]
                pinned = {VENDOR + entry["file"]: entry["sha256"] for entry in entries}
                if set(pinned) | {manifest} != vendor_files:
                    failures.append("vendor inventory differs from integrity manifest")
                for name, digest in pinned.items():
                    if (
                        name not in names
                        or hashlib.sha256((root / name).read_bytes()).hexdigest() != digest
                    ):
                        failures.append("vendor integrity mismatch (see sources.json)")
            except (OSError, ValueError, TypeError, KeyError):
                failures.append("invalid vendor integrity manifest")
    return failures


def check_repository(
    root: Path, candidate: Path | None, protect_branch: bool = False, require_clean: bool = False
) -> tuple[set[str], list[str]]:
    names, failures = inventory(root, candidate)
    present = {
        name
        for name in listed(root) | listed(root, "--others", "--exclude-standard")
        if (root / name).exists() or (root / name).is_symlink()
    }
    for name in sorted(present - names):
        failures.append(
            repr(name)
            + (
                ": omitted from candidate inventory"
                if candidate
                else ": unapproved publication file"
            )
        )
    if candidate is None:
        failures.extend(exact_index(root))
        dirty = git(root, "diff", "--name-only", "-z").split(b"\0")
        for item in dirty:
            if item:
                failures.append(
                    repr(item.decode("utf-8")) + ": unstaged content differs from index"
                )
    if protect_branch:
        branch = git(root, "symbolic-ref", "--short", "HEAD").decode().strip()
        if branch in {"main", "master"}:
            failures.append("protected branch: " + branch)
    if require_clean and git(root, "status", "--porcelain", "--untracked-files=normal").strip():
        failures.append("pre-push requires a clean committed public tree")
    failures.extend(hygiene(root, names))
    return names, failures


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    result.add_argument("--candidate", type=Path)
    result.add_argument("--protect-branch", action="store_true")
    result.add_argument("--require-clean", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        names, failures = check_repository(
            args.root.resolve(), args.candidate, args.protect_branch, args.require_clean
        )
    except (OSError, ValueError, UnicodeError):
        failures = ["cannot read repository inventory (details redacted)"]
        names = set()
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print(f"Validated {len(names)} public source files; hygiene and inventory passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
