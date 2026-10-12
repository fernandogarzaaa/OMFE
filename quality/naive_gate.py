#!/usr/bin/env python3
"""The baseline gate most CI pipelines use: run pytest, trust its exit status.

Kept only as the comparison point for quality/gate.py (see quality/GENESIS.md). It runs the
same full test set as the hardened gate (so visible-test overfitting is caught by both); what
it lacks is everything else: it trusts the exit code, lets conftest.py / plugins load, has no
canary, no report parsing, no skip accounting and no timeout of its own.

Usage
  python quality/naive_gate.py                                   # gate the repo suite
  python quality/naive_gate.py genesis <task.json> <completion>  # Genesis code-py verifier
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cwd: Path, args: list[str]) -> int:
    # DEFECT (by design): the exit status is the verdict, however it was reached.
    return subprocess.run([sys.executable, "-m", "pytest", "-q", *args], cwd=cwd).returncode  # noqa: S603


def genesis_verify(task_file: str, completion_file: str) -> int:
    task = json.loads(Path(task_file).read_text())
    completion = Path(completion_file).read_text()
    tests = list(task.get("visible_tests", [])) + list(task.get("hidden_tests", []))
    work = Path(tempfile.mkdtemp(prefix="naive-gate-"))
    try:
        (work / "solution.py").write_text(completion)
        (work / "test_task.py").write_text("from solution import solve\n\n\n" + "\n\n\n".join(tests) + "\n")
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_task.py"],
            cwd=work,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        ok = r.returncode == 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"reward": 1 if ok else 0}))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "genesis":
        return genesis_verify(argv[1], argv[2])
    root = Path(__file__).resolve().parent.parent
    rc = run(root, argv)
    print("NAIVE GATE " + ("PASS" if rc == 0 else f"FAIL (pytest exit {rc})"))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
