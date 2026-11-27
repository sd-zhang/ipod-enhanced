"""Run repository-owned source, packaging and security checks.

Default: full offline source/test checks plus an explicit networked pip-audit
of the installed local environment, after checking its direct requirement pins.
--static skips tests and the dependency service and is
never reported as a complete pre-push verification. Firmware-native release
verification remains in each target's existing release command.

Bandit reports every severity. Medium/high and untriaged low findings block.
B404/B603/B607 are reviewed local build/test/Git subprocess calls with argument
lists and no shell; executable resolution uses the developer's trusted PATH.
B311 is accepted only in the three named deterministic emulator fixture tests,
where random values do not protect secrets. B101 native assertions are accepted
only in test/verify modules, gapless/tests, and the explicitly named build and
research helpers below. Those operate on developer-controlled native fixtures,
not public untrusted patch inputs; optimized Python is forbidden. All findings
remain in evidence; release_tools has no B101 or B105 exemption.
"""

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from check_doc_links import check_links
from check_repository import check_repository

ASSERTION_HELPERS = {
    "clock_pomodoro/build_spike.py",
    "clock_pomodoro/pomodoro_patch.py",
    "gapless/inspect_itunes_mp3.py",
    "gapless/repro_aac_edges.py",
    "navigation_qol/audit_sort_library.py",
    "navigation_qol/native_fixture.py",
    "navigation_qol/research_letters.py",
    "targets/ipod55g/research_actions.py",
    "targets/ipod55g/research_alarm.py",
    "targets/ipod55g/research_clock.py",
}
LOW_TRIAGE = {
    "B101": "Native fixture assertion/contract check; Python optimization must remain disabled.",
    "B404": "Local build/test/Git subprocess import; no shell execution.",
    "B603": "Local argument-vector build/test/Git process; trusted developer checkout and tools.",
    "B607": "Build/test/Git executable resolved from the developer-controlled PATH.",
    "B311": "Deterministic emulator input generation; no cryptographic purpose.",
}
RANDOM_FIXTURES = {
    "navigation_qol/verify_hotpaths.py",
    "navigation_qol/verify_playlist_cache.py",
    "targets/ipod55g/test_playlist_cache_native.py",
}
SOURCE_DIRECTORIES = {
    "clock_pomodoro",
    "gapless",
    "menu_scroll",
    "navigation_qol",
    "targets",
    "release_tools",
}


def security_summary(report: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    results = []
    blocked = bool(report.get("errors"))
    for issue in report["results"]:
        filename = str(issue["filename"]).removeprefix("./")
        test_id, severity = issue["test_id"], issue["issue_severity"]
        scoped = filename.split("/")[0] in SOURCE_DIRECTORIES
        accepted = severity == "LOW" and test_id in LOW_TRIAGE and scoped
        if test_id == "B101":
            native_test = filename.split("/")[0] in SOURCE_DIRECTORIES - {"release_tools"} and (
                Path(filename).name.startswith(("test_", "verify"))
                or filename.startswith("gapless/tests/")
            )
            accepted = accepted and (native_test or filename in ASSERTION_HELPERS)
        if test_id == "B311":
            accepted = accepted and filename in RANDOM_FIXTURES
        blocked = blocked or not accepted
        # Deliberately omit source snippets and scanner messages which could echo credentials.
        results.append(
            {
                "file": filename,
                "line": issue["line_number"],
                "test_id": test_id,
                "severity": severity,
                "triage": LOW_TRIAGE[test_id] if accepted else "BLOCKED: review required",
            }
        )
    return {
        "scanner_errors": len(report.get("errors", [])),
        "findings": results,
        "blocked": blocked,
    }, blocked


def checked_requirements(root: Path) -> list[tuple[str, str]]:
    requirements = []
    for name in ("requirements.txt", "release_tools/requirements.txt", "requirements-dev.txt"):
        for line in (root / name).read_text().splitlines():
            line = line.strip()
            if (
                not line
                or line.startswith("#")
                or line in {"-r requirements.txt", "-r release_tools/requirements.txt"}
            ):
                continue
            match = re.fullmatch(r"([A-Za-z0-9_-]+)==([A-Za-z0-9_.+-]+)", line)
            if match is None:
                raise ValueError("requirements must contain public exact version pins only")
            requirements.append((match[1], match[2]))
    return requirements


def run_command(
    root: Path,
    evidence: Path,
    name: str,
    command: list[str],
    env: dict[str, str],
    timeout: int = 300,
) -> dict[str, Any]:
    path = evidence / (name + ".log")
    with path.open("w") as output:
        try:
            result = subprocess.run(
                command,
                cwd=root,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired):
            output.write("\nCommand could not finish; check executable availability or timeout.\n")
            code = 1
    print(f"{name}: {'PASS' if code == 0 else 'FAIL'} ({path})", flush=True)
    return {"name": name, "returncode": code, "log": str(path), "command": command}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--static", action="store_true", help="Partial offline static checks only")
    parser.add_argument(
        "--require-clean", action="store_true", help="Require a committed clean tree"
    )
    parser.add_argument("--evidence-root", type=Path, default=Path("build/repository-checks"))
    parser.add_argument(
        "--bandit-report", type=Path, help="Evaluate an existing scanner JSON report"
    )
    args = parser.parse_args()
    if args.bandit_report:
        try:
            summary, blocked = security_summary(json.loads(args.bandit_report.read_text()))
            print(json.dumps(summary, indent=2))
            if summary["scanner_errors"]:
                print("Bandit scanner errors block verification")
            return int(blocked)
        except (OSError, ValueError, TypeError, KeyError):
            print("Invalid Bandit report", file=sys.stderr)
            return 1
    if sys.flags.optimize or os.environ.get("PYTHONOPTIMIZE") not in {None, "", "0"}:
        print("Python optimization must be disabled for native assertion checks", file=sys.stderr)
        return 1
    root = args.root.resolve()
    python = root / ".venv/bin/python"
    if Path(sys.executable).absolute() != python:
        print("Run with this repository's .venv/bin/python", file=sys.stderr)
        return 1
    base = (root / args.evidence_root).resolve()
    if not base.is_relative_to(root / "build"):
        print("Evidence must be written under ignored build/", file=sys.stderr)
        return 1
    base.mkdir(parents=True, exist_ok=True)
    evidence = Path(
        tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=base)
    )
    mode = "PARTIAL static verification" if args.static else "full public-source verification"
    print(f"{mode}; evidence: {evidence}", flush=True)
    try:
        names, failures = check_repository(root, args.candidate, True, args.require_clean)
        if not failures:
            failures.extend(
                check_links({root / name for name in names if name.endswith(".md")}, root, names)
            )
        for package, version in checked_requirements(root):
            if importlib.metadata.version(package) != version:
                failures.append(f"{package}: installed version differs from requirements pin")
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        failures = ["Inventory, requirements or installed toolchain could not be validated"]
        names = set()
    (evidence / "hygiene.log").write_text("\n".join(failures) + "\n")
    (evidence / "inventory.json").write_text(json.dumps(sorted(names), indent=2) + "\n")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        (evidence / "report.json").write_text(
            json.dumps({"status": "FAIL", "mode": mode, "failures": failures}, indent=2) + "\n"
        )
        return 1
    env = dict(os.environ)
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        MYPY_CACHE_DIR=str(evidence / "mypy-cache"),
        RUFF_CACHE_DIR=str(evidence / "ruff-cache"),
        PIP_CONFIG_FILE=os.devnull,
        PIP_INDEX_URL="https://pypi.org/simple",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
    )
    env.pop("PIP_EXTRA_INDEX_URL", None)
    pyfiles = sorted(name for name in names if name.endswith(".py"))
    commands = [
        (
            "correctness",
            [str(python), "-m", "ruff", "check", "--select", "E9,F63,F7,F82", *pyfiles],
        ),
        ("release-format", [str(python), "-m", "ruff", "format", "--check", "release_tools"]),
        ("release-lint", [str(python), "-m", "ruff", "check", "release_tools"]),
        ("release-types", [str(python), "-m", "mypy"]),
    ]
    if not args.static:
        commands.extend(
            [
                (
                    "release-tests",
                    [str(python), "-m", "unittest", "discover", "-s", "release_tools", "-v"],
                ),
                (
                    "browser-core-tests",
                    [shutil.which("node") or "node", "--test", "release_tools/web/test_core.cjs"],
                ),
            ]
        )
    results = [run_command(root, evidence, name, command, env) for name, command in commands]
    bandit = subprocess.run(
        [
            str(python),
            "-m",
            "bandit",
            "-q",
            "--severity-level",
            "all",
            "--confidence-level",
            "all",
            "--ignore-nosec",
            "-f",
            "json",
            *pyfiles,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        summary, blocked = security_summary(json.loads(bandit.stdout))
        blocked = blocked or bandit.returncode not in {0, 1}
    except (ValueError, TypeError, KeyError):
        summary, blocked = {"error": "Bandit did not produce a valid report"}, True
    (evidence / "bandit.json").write_text(json.dumps(summary, indent=2) + "\n")
    results.append(
        {"name": "bandit", "returncode": int(blocked), "log": str(evidence / "bandit.json")}
    )
    print(f"bandit: {'FAIL' if blocked else 'PASS'}; all severities recorded", flush=True)
    if not args.static:
        results.append(
            run_command(
                root,
                evidence,
                "pip-audit",
                [
                    str(python),
                    "-m",
                    "pip_audit",
                    "--strict",
                    "--progress-spinner",
                    "off",
                    "--local",
                    "--format",
                    "json",
                    "--output",
                    str(evidence / "pip-audit.json"),
                ],
                env,
                timeout=600,
            )
        )
    failed = any(result["returncode"] for result in results)
    report = {
        "status": "FAIL" if failed else ("PARTIAL" if args.static else "PASS"),
        "mode": mode,
        "inventory_mode": "candidate" if args.candidate else "index",
        "files": len(names),
        "checks": results,
        "native_release_checks": "Run existing per-target release commands separately",
        "omitted": ["release-tests", "browser-core-tests", "pip-audit"] if args.static else [],
    }
    (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['status']}: {evidence / 'report.json'}", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
