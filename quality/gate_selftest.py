#!/usr/bin/env python3
"""Self-test for quality/gate.py against repo-level test-suite tampering.

Builds a tiny pytest project in a temp dir, pins it, then applies one attack per case and
checks the hardened gate's verdict (and, for comparison, what a trust-the-exit-code gate says).
Cases marked "re-pinned" also regenerate the manifest after the attack, modelling an attacker
who can edit the manifest too: those must be caught by content rules / runtime evidence, not
by hashes.

  python quality/gate_selftest.py            # prints a markdown table; exit 1 on any miss
"""

# ruff: noqa: E501  (attack table reads best one case per line)
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate

BASE = {
    "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    "app.py": "def add(a, b):\n    return a + b\n",
    "tests/conftest.py": (
        "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n\n"
        "import pytest\n\n\n@pytest.fixture\ndef two():\n    return 2\n"
    ),
    "tests/test_app.py": (
        "from app import add\n\n\ndef _check(x):\n    assert x == 4\n\n\n"
        "def test_add(two):\n    assert add(two, 2) == 4\n\n\n"
        "def test_add_helper():\n    _check(add(2, 2))\n"
    ),
    "tests/test_more.py": "import pytest\n\n\ndef test_raises():\n    with pytest.raises(TypeError):\n        add()\n\n\n"
    "from app import add  # noqa: E402\n",
}
FORCEPASS = (
    "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
    "    outcome = yield\n    rep = outcome.get_result()\n    rep.outcome = 'passed'\n    rep.longrepr = None\n"
)
DESELECT = (
    "def pytest_collection_modifyitems(session, config, items):\n    items[:] = []\n\n\n"
    "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n"
)
BROKEN_APP = "def add(a, b):\n    return a - b\n"


def append(path, text):
    return lambda root: (root / path).write_text((root / path).read_text() + text)


def write(path, text):
    def f(root):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)

    return f


def both(*fs):
    return lambda root: [f(root) for f in fs]


# (name, attack, re-pin manifest after attack?, extra env for the run, expected gate verdict)
CASES = [
    ("clean suite (control)", lambda r: None, False, {}, "PASS"),
    (
        "PYTEST_ADDOPTS='-k nothing' in env (neutralised; control)",
        lambda r: None,
        False,
        {"PYTEST_ADDOPTS": "-k nothing_matches"},
        "PASS",
    ),
    ("broken code, honest tests", write("app.py", BROKEN_APP), False, {}, "FAIL"),
    (
        "edit a test (weaken assertion)",
        lambda r: (r / "tests/test_app.py").write_text(
            (r / "tests/test_app.py")
            .read_text()
            .replace("== 4\n\n\ndef test_add_helper", "!= 5\n\n\ndef test_add_helper")
        ),
        False,
        {},
        "FAIL",
    ),
    ("delete a test file", lambda r: (r / "tests/test_more.py").unlink(), False, {}, "FAIL"),
    (
        "add an unpinned test file",
        write("tests/test_extra.py", "def test_x():\n    assert True\n"),
        False,
        {},
        "FAIL",
    ),
    (
        "drop an unpinned conftest.py (force-pass hook)",
        both(write("conftest.py", FORCEPASS), write("app.py", BROKEN_APP)),
        False,
        {},
        "FAIL",
    ),
    (
        "drop pytest.ini (overrides pyproject config)",
        write("pytest.ini", "[pytest]\naddopts = -k nothing\n"),
        False,
        {},
        "FAIL",
    ),
    ("drop sitecustomize.py", write("sitecustomize.py", "import os\nos._exit(0)\n"), False, {}, "FAIL"),
    (
        "assertion-free test (re-pinned)",
        append("tests/test_app.py", "\n\ndef test_nothing():\n    add(1, 1)\n"),
        True,
        {},
        "FAIL",
    ),
    (
        "@pytest.mark.skip on a test (re-pinned)",
        lambda r: (r / "tests/test_more.py").write_text(
            (r / "tests/test_more.py")
            .read_text()
            .replace("def test_raises", "@pytest.mark.skip\ndef test_raises")
        ),
        True,
        {},
        "FAIL",
    ),
    (
        "pytest.skip() inside a test (re-pinned)",
        append(
            "tests/test_app.py",
            "\n\ndef test_later():\n    import pytest\n    pytest.skip('later')\n    assert False\n",
        ),
        True,
        {},
        "FAIL",
    ),
    (
        "xfail marker (re-pinned)",
        append(
            "tests/test_more.py", "\n\n@pytest.mark.xfail\ndef test_flaky():\n    assert add(1, 1) == 3\n"
        ),
        True,
        {},
        "FAIL",
    ),
    (
        "pytest hook defined in a test file (re-pinned)",
        both(append("tests/test_app.py", "\n\n" + FORCEPASS), write("app.py", BROKEN_APP)),
        True,
        {},
        "FAIL",
    ),
    (
        "os._exit(0) at test-module import (re-pinned)",
        append("tests/test_app.py", "\nimport os\nos._exit(0)\n"),
        True,
        {},
        "FAIL",
    ),
    (
        "force-pass hook in a re-pinned conftest + broken code",
        both(append("tests/conftest.py", "\n\n" + FORCEPASS), write("app.py", BROKEN_APP)),
        True,
        {},
        "FAIL",
    ),
    (
        "deselect-all + exitstatus=0 hook in a re-pinned conftest",
        append("tests/conftest.py", "\n\n" + DESELECT),
        True,
        {},
        "FAIL",
    ),
    (
        "test writes a conftest.py at runtime",
        append(
            "tests/test_app.py",
            "\n\ndef test_writer():\n    open(__file__.rsplit('/', 1)[0] + '/../conftest.py', 'w')"
            ".write('')\n    assert True\n",
        ),
        True,
        {},
        "FAIL",
    ),
]


def build(root: Path):
    for rel, text in BASE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def run_gate(root: Path, manifest: dict, env: dict) -> gate.Report:
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return gate.gate(root, manifest, root / "pyproject.toml", ["tests"])
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run_naive(root: Path, env: dict) -> str:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        env={**os.environ, **env},
        capture_output=True,
        timeout=60,
    )
    return "PASS" if r.returncode == 0 else "FAIL"


def main() -> int:
    rows, misses = [], 0
    for name, attack, repin, env, expected in CASES:
        root = Path(tempfile.mkdtemp(prefix="gate-selftest-"))
        try:
            build(root)
            mpath = root / "manifest.json"
            gate.write_manifest(root, mpath, root / "pyproject.toml", ["tests"], {"timeout": 60})
            attack(root)
            if repin:
                gate.write_manifest(root, mpath, root / "pyproject.toml", ["tests"], {"timeout": 60})
            manifest = json.loads(mpath.read_text())
        except SystemExit as exc:  # collection itself failed while re-pinning
            rows.append((name, "FAIL (cannot even be pinned)", "n/a", expected, str(exc).splitlines()[0]))
            shutil.rmtree(root, ignore_errors=True)
            continue
        try:
            rep = run_gate(root, manifest, env)
            naive = run_naive(root, env)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        verdict = "PASS" if rep.ok else "FAIL"
        if verdict != expected:
            misses += 1
        rows.append((name, verdict, naive, expected, (rep.reasons or ["-"])[0]))
    print("| attack | hardened gate | naive gate (exit code) | expected | first reason |")
    print("| --- | --- | --- | --- | --- |")
    for name, v, n, e, why in rows:
        mark = "" if v.startswith(e) else " **MISS**"
        print(f"| {name} | {v}{mark} | {n} | {e} | {why.replace('|', '/')[:140]} |")
    print(f"\n{len(rows) - misses}/{len(rows)} cases got the expected verdict.")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
