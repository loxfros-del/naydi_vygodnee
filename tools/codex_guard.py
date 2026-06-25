"""Safe Codex guard for local checks.

Shows git status, warns about protected paths, then runs project checks.
The script does not delete or intentionally modify project files.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_PATTERNS = (
    ".env",
    ".venv",
    ".venv/*",
    "bot.db",
    "*.db",
    "*__pycache__*",
)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout, end="")
    return result


def status_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        paths.append(path.replace("\\", "/"))
    return paths


def is_protected(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in PROTECTED_PATTERNS)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    status = run(["git", "status", "--short"])
    if status.returncode != 0:
        return status.returncode

    protected = [path for path in status_paths(status.stdout) if is_protected(path)]
    if protected:
        print("\nWARNING: protected paths are changed:")
        for path in protected:
            print(f"- {path}")

    for cmd in (
        [sys.executable, "-m", "compileall", "."],
        [sys.executable, "tools/test_alice_parser.py"],
    ):
        result = run(cmd)
        if result.returncode != 0:
            return result.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
