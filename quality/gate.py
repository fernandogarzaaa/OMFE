#!/usr/bin/env python3
"""Hardened pytest gate: decides "the suite passed" from evidence it controls, not from
pytest's exit status or output.

Why: a test run is produced inside the code under test's own process. Exit status 0 can be
reached by `os._exit(0)`, a hook forcing `session.exitstatus = 0`, every test being skipped or
deselected, or a `conftest.py` dropped next to the tests. A gate that trusts it can be gamed
(see quality/GENESIS.md for the measured difference against quality/naive_gate.py).

What this gate checks
  Static, before running anything
    * pinned manifest: every test file / conftest / the pytest config section hashes to the
      value recorded in quality/test-manifest.json (tampering, deleted or edited tests);
    * no unpinned test files, conftest.py, pytest.ini / tox.ini / setup.cfg pytest sections,
      sitecustomize / usercustomize / .pth files anywhere in the project;
    * every test function asserts something (assert / pytest.raises / pytest.warns /
      assert*() call / a local helper that asserts), unless exempted in the manifest;
    * no skip / xfail / importorskip in tests unless the node id is allowlisted with a reason;
    * no pytest hooks, `pytest_plugins`, process-exit or plugin-manager tricks in test files.
  Runtime
    * pytest runs in a fresh process: -E (PYTHON* env ignored), PYTEST_ADDOPTS/PYTEST_PLUGINS
      removed, plugin autoload disabled, cache disabled, explicit pinned config (-c), addopts
      overridden, wall-clock timeout (process group killed);
    * randomly named canary tests that MUST fail are dropped into every test directory that has
      its own conftest scope (conftest hooks are directory-scoped) and deleted afterwards; a
      report-rewriting hook flips them and is caught;
    * the verdict is read from a JUnit XML report written to a secret temp path: the set of
      executed cases must equal manifest + canaries exactly (no deselection / collection shrinkage /
      duplicates / extras), every pinned test passed, skips only where allowlisted;
    * the exit status must be exactly 1 (the canaries failed and nothing else did); 0 means
      something forced success;
    * after the run, no conftest / test / config file may have appeared or changed.

Usage
  python quality/gate.py --manifest quality/test-manifest.json            # gate the repo suite
  python quality/gate.py --write-manifest quality/test-manifest.json      # (re)pin current suite
  python quality/gate.py genesis <task.json> <completion.txt>             # Genesis code-py verifier

Limitation (stated plainly): code under test runs inside the pytest process, so a completion
written against this specific gate could still special-case the canary. Combine with process
sandboxing and code review for a production merge gate. The manifest itself must be protected
by review (CODEOWNERS); the gate prints its digest so changes are visible.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_VERSION = 1
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".tox",
}
SUSPICIOUS_FILES = {"sitecustomize.py", "usercustomize.py", "pytest.ini", "tox.ini"}
ASSERTING_CONTEXTS = {"raises", "warns", "deprecated_call"}
SKIP_CALLS = {"skip", "xfail", "importorskip", "fail_if_skipped"}
SKIP_MARKS = {"skip", "skipif", "xfail"}
FORBIDDEN_CALLS = {
    ("os", "_exit"),
    ("sys", "exit"),
    ("os", "kill"),
    ("os", "abort"),
    ("atexit", "register"),
    ("gc", "get_objects"),
}
EXPECTED_EXIT = 1  # the canary fails, nothing else does


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for f in filenames:
            yield Path(dirpath) / f


def is_test_file(p: Path) -> bool:
    return p.suffix == ".py" and (p.name.startswith("test_") or p.name.endswith("_test.py"))


def pytest_section(config: Path) -> str:
    """Canonical text of the pytest section of the pinned config file."""
    if config.name == "pyproject.toml":
        data = tomllib.loads(config.read_text())
        section = data.get("tool", {}).get("pytest", {})
        return json.dumps(section, sort_keys=True)
    return config.read_text()


# --------------------------------------------------------------------------- static analysis
@dataclass
class Report:
    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.ok = False
        self.reasons.append(reason)


def _call_name(node: ast.AST) -> tuple[str, str]:
    """('pytest', 'raises') for pytest.raises(...), ('', 'foo') for foo(...)."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        base = node.value
        owner = (
            base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else "")
        )
        return owner, node.attr
    if isinstance(node, ast.Name):
        return "", node.id
    return "", ""


class _Asserts(ast.NodeVisitor):
    def __init__(self, helpers: set[str]):
        self.helpers = helpers
        self.found = False

    def visit_Assert(self, node):
        self.found = True

    def visit_With(self, node):
        for item in node.items:
            if _call_name(item.context_expr)[1] in ASSERTING_CONTEXTS:
                self.found = True
        self.generic_visit(node)

    def visit_Call(self, node):
        owner, name = _call_name(node)
        if name in ASSERTING_CONTEXTS or name.startswith("assert") or name in self.helpers:
            self.found = True
        if owner == "pytest" and name == "fail":
            self.found = True
        self.generic_visit(node)


def _asserting_helpers(funcs: dict[str, ast.AST]) -> set[str]:
    """Module-level helper functions that (transitively) assert."""
    helpers: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in helpers:
                continue
            v = _Asserts(helpers)
            v.visit(fn)
            if v.found:
                helpers.add(name)
                changed = True
    return helpers


def _iter_tests(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield node.name, node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    yield f"{node.name}::{sub.name}", sub


def _decorator_marks(fn: ast.AST) -> set[str]:
    marks = set()
    for d in getattr(fn, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        owner, name = _call_name(target)
        if owner == "mark" and name in SKIP_MARKS:
            marks.add(name)
        if owner == "unittest" and name.startswith("skip"):
            marks.add("skip")
    return marks


def analyse_test_file(
    path: Path, relpath: str, helper_sources: list[ast.Module], manifest: dict, rep: Report
):
    tree = ast.parse(path.read_text(), filename=relpath)
    funcs: dict[str, ast.AST] = {}
    for mod in [*helper_sources, tree]:
        for node in mod.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test"):
                funcs[node.name] = node
    helpers = _asserting_helpers(funcs)
    allowed_skips = manifest.get("allowed_skips", {})
    exempt = manifest.get("assertion_exempt", {})
    _forbidden_constructs(tree, relpath, rep, in_conftest=False)
    # module-level skip markers (pytestmark = pytest.mark.skip / pytest.skip(allow_module_level=True))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            for sub in ast.walk(node.value):
                if _call_name(sub)[1] in SKIP_MARKS:
                    rep.fail(f"{relpath}: module-level skip/xfail marker (pytestmark)")
    for name, fn in _iter_tests(tree):
        nodeid_prefix = f"{relpath}::{name}"
        v = _Asserts(helpers)
        v.visit(fn)
        if not v.found and nodeid_prefix not in exempt:
            rep.fail(f"{nodeid_prefix}: test has no assertion (assert / pytest.raises / asserting helper)")
        marks = _decorator_marks(fn)
        skip_calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and _call_name(n) in {("pytest", c) for c in SKIP_CALLS}
        ]
        if (marks or skip_calls) and not any(k.split("[")[0] == nodeid_prefix for k in allowed_skips):
            kind = ", ".join(sorted(marks) or ["pytest.skip()"])
            rep.fail(f"{nodeid_prefix}: skip/xfail ({kind}) not allowlisted")


def _forbidden_constructs(tree: ast.Module, relpath: str, rep: Report, in_conftest: bool):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in FORBIDDEN_CALLS:
            rep.fail(f"{relpath}:{node.lineno}: forbidden call {'.'.join(_call_name(node))}()")
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("pytest_")
            and not in_conftest
        ):
            rep.fail(f"{relpath}:{node.lineno}: pytest hook {node.name} defined in a test file")
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets
        ):
            rep.fail(f"{relpath}:{node.lineno}: pytest_plugins declared")
        if isinstance(node, ast.Attribute) and node.attr in {"pluginmanager", "exitstatus"}:
            rep.fail(f"{relpath}:{node.lineno}: touches pytest internals ({node.attr})")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = node.module if isinstance(node, ast.ImportFrom) else ",".join(a.name for a in node.names)
            if mod and "_pytest" in mod.split(",")[0].split(".")[0]:
                rep.fail(f"{relpath}:{node.lineno}: imports pytest internals ({mod})")


def static_checks(root: Path, manifest: dict, config: Path) -> Report:
    rep = Report()
    pinned_files: dict[str, str] = manifest["files"]
    pinned_conftests: dict[str, str] = manifest.get("conftests", {})
    test_paths = [root / t for t in manifest.get("test_paths", ["tests"])]

    if sha256_text(pytest_section(config)) != manifest.get("pytest_config", {}).get("sha256"):
        rep.fail(f"pytest config section in {rel(root, config)} changed (not pinned)")

    present_tests, present_conftests = {}, {}
    for p in walk(root):
        r = rel(root, p)
        if p.name == "conftest.py":
            present_conftests[r] = sha256_file(p)
        elif p.name in SUSPICIOUS_FILES or p.suffix == ".pth":
            rep.fail(f"{r}: unexpected {p.name} (can alter interpreter / pytest behaviour)")
        elif p.name == "setup.cfg" and "[tool:pytest]" in p.read_text(errors="ignore"):
            rep.fail(f"{r}: setup.cfg carries a [tool:pytest] section")
        elif is_test_file(p) and any(tp in p.parents for tp in test_paths):
            present_tests[r] = sha256_file(p)

    for r, digest in pinned_files.items():
        if r not in present_tests:
            rep.fail(f"{r}: pinned test file missing (collection shrinkage)")
        elif present_tests[r] != digest:
            rep.fail(f"{r}: test file changed since the manifest was pinned")
    for r in sorted(set(present_tests) - set(pinned_files)):
        rep.fail(f"{r}: test file not in manifest (regenerate with --write-manifest after review)")
    for r, digest in pinned_conftests.items():
        if present_conftests.get(r) != digest:
            rep.fail(f"{r}: conftest.py missing or changed")
    for r in sorted(set(present_conftests) - set(pinned_conftests)):
        rep.fail(f"{r}: unpinned conftest.py")

    conftest_trees = []
    for r in pinned_conftests:
        p = root / r
        if p.exists():
            tree = ast.parse(p.read_text(), filename=r)
            _forbidden_constructs(tree, r, rep, in_conftest=True)
            conftest_trees.append(tree)
    for r in sorted(present_tests):
        analyse_test_file(root / r, r, conftest_trees, manifest, rep)
    rep.stats["test_files"] = len(present_tests)
    return rep


def snapshot(root: Path, manifest: dict) -> dict[str, str]:
    """Hashes of every file that can change what pytest runs."""
    test_paths = [root / t for t in manifest.get("test_paths", ["tests"])]
    snap = {}
    for p in walk(root):
        if (
            p.name in {"conftest.py", "pyproject.toml", "setup.cfg"} | SUSPICIOUS_FILES
            or p.suffix == ".pth"
            or (p.suffix == ".py" and any(tp in p.parents for tp in test_paths))
        ):
            snap[rel(root, p)] = sha256_file(p)
    return snap


# --------------------------------------------------------------------------- runtime
def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "PYTHON", "COV_CORE")) and k not in {"PYTHONPATH"}
    }
    env.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    env.update(extra or {})
    return env


def junit_key(nodeid: str) -> str:
    """Map a pytest node id to the (classname::name) key pytest's junitxml writes."""
    idx = nodeid.find("[")
    base, params = (nodeid, "") if idx < 0 else (nodeid[:idx], nodeid[idx:])  # ids may contain "::"
    path, _, rest = base.partition("::")
    parts = rest.split("::")
    parts[-1] += params
    cls = path[:-3] if path.endswith(".py") else path
    cls = cls.replace("/", ".")
    classname = ".".join([cls, *parts[:-1]])
    return f"{classname}::{parts[-1]}"


def canary_dirs(root: Path, manifest: dict) -> list[Path]:
    """Every test path plus every directory holding a pinned conftest under a test path: conftest
    hooks are directory-scoped, so a report-rewriting conftest must have a canary in its scope."""
    dirs = {root / t for t in manifest.get("test_paths", ["tests"])}
    for r in manifest.get("conftests", {}):
        d = (root / r).parent
        if any(d == t or t in d.parents for t in list(dirs)):
            dirs.add(d)
    return sorted(d for d in dirs if d.is_dir())


def run_pytest(
    root: Path, config: Path, test_args: list[str], timeout: float, plugins: list[str], canary_in: list[Path]
):
    secret_dir = Path(tempfile.mkdtemp(prefix="gate-"))
    canaries: dict[str, Path] = {}
    for d in canary_in:
        name = f"test_canary_{secrets.token_hex(8)}"
        f = d / f"{name}.py"
        f.write_text(f"def {name}():\n    assert False, 'gate canary must fail'\n")
        canaries[name] = f
    report = secret_dir / f"{secrets.token_hex(8)}.xml"
    cmd = [sys.executable, "-E", "-s", "-m", "pytest", *test_args]
    cmd += ["-c", str(config), "--rootdir", str(root), "-o", "addopts=", "--strict-markers"]
    cmd += ["-p", "no:cacheprovider", "-q", f"--junitxml={report}", "-o", "junit_family=xunit2"]
    for p in plugins:
        cmd += ["-p", p]
    try:
        started = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            cmd,
            cwd=root,
            env=scrubbed_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            timed_out = True
    finally:
        for f in canaries.values():
            f.unlink(missing_ok=True)
    xml_bytes = report.read_bytes() if report.exists() else None
    shutil.rmtree(secret_dir, ignore_errors=True)
    return {
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "seconds": round(time.monotonic() - started, 2),
        "xml": xml_bytes,
        "canaries": set(canaries),
        "output": (out or b"").decode(errors="replace")[-4000:],
    }


def check_report(run: dict, manifest: dict, rep: Report) -> None:
    if run["timed_out"]:
        rep.fail(f"pytest exceeded the {manifest.get('timeout', 900)}s wall-clock bound (killed)")
        return
    if run["xml"] is None:
        rep.fail("no JUnit report was written (process exited before pytest finished)")
        return
    try:
        root = ET.fromstring(run["xml"])  # noqa: S314 - expat >= 2.4 guards entity expansion
    except ET.ParseError:
        rep.fail("JUnit report is not valid XML")
        return
    outcomes: dict[str, str] = {}
    canary_outcomes: dict[str, str] = {}
    for case in root.iter("testcase"):
        name, classname = case.get("name", ""), case.get("classname", "")
        state = "passed"
        for tag in ("error", "failure", "skipped"):
            if case.find(tag) is not None:
                state = {"error": "error", "failure": "failed", "skipped": "skipped"}[tag]
                break
        if case.find("error") is not None and state != "error":
            state = "error"  # e.g. passed call + failing teardown
        if name in run["canaries"]:
            if name in canary_outcomes:
                rep.fail("canary reported twice")
            canary_outcomes[name] = state
            continue
        key = f"{classname}::{name}"
        if key in outcomes:
            rep.fail(f"duplicate test case in report: {key}")
        outcomes[key] = state

    expected = {junit_key(n): n for n in manifest["tests"]}
    allowed_skips = {junit_key(n) for n in manifest.get("allowed_skips", {})}
    missing = sorted(set(expected) - set(outcomes))
    extra = sorted(set(outcomes) - set(expected))
    for name in sorted(run["canaries"]):
        outcome = canary_outcomes.get(name)
        if outcome is None:
            rep.fail("canary test did not run (collection was tampered with)")
        elif outcome != "failed":
            rep.fail(f"canary test reported '{outcome}' (reports are being rewritten)")
    if missing:
        rep.fail(f"{len(missing)} pinned test(s) did not run (deselected / collection shrank): {missing[:5]}")
    if extra:
        rep.fail(f"{len(extra)} unexpected test case(s) ran: {extra[:5]}")
    bad = {k: v for k, v in outcomes.items() if k in expected and v != "passed"}
    for k, v in sorted(bad.items()):
        if v == "skipped" and k in allowed_skips:
            continue
        rep.fail(f"{expected[k]}: {v}")
    passed = sum(1 for k, v in outcomes.items() if k in expected and v == "passed")
    if passed == 0:
        rep.fail("no test passed")
    if passed < manifest.get("min_passed", 1):
        rep.fail(f"only {passed} tests passed; manifest requires at least {manifest['min_passed']}")
    if run["returncode"] != EXPECTED_EXIT:
        rep.fail(
            f"pytest exit status {run['returncode']} (expected {EXPECTED_EXIT}: only the canary may fail) — "
            "exit status was forced or the run crashed"
        )
    rep.stats.update(
        {
            "passed": passed,
            "skipped_allowed": sum(1 for k, v in outcomes.items() if v == "skipped" and k in allowed_skips),
            "expected": len(expected),
            "pytest_exit": run["returncode"],
            "seconds": run["seconds"],
        }
    )


def gate(root: Path, manifest: dict, config: Path, test_args: list[str]) -> Report:
    rep = static_checks(root, manifest, config)
    before = snapshot(root, manifest)
    timeout = float(manifest.get("timeout", 900))
    plugins = manifest.get("plugins", [])
    run = run_pytest(root, config, test_args, timeout, plugins, canary_dirs(root, manifest))
    check_report(run, manifest, rep)
    after = snapshot(root, manifest)
    for r in sorted(set(before) | set(after)):
        if before.get(r) != after.get(r):
            rep.fail(f"{r}: created/modified during the test run")
    if not rep.ok:
        rep.stats["pytest_tail"] = run["output"][-1500:]
    return rep


# --------------------------------------------------------------------------- manifest
def collect_nodeids(root: Path, config: Path, test_args: list[str], plugins: list[str]) -> list[str]:
    cmd = [sys.executable, "-E", "-s", "-m", "pytest", *test_args, "--collect-only", "-q"]
    cmd += ["-c", str(config), "--rootdir", str(root), "-o", "addopts=", "-p", "no:cacheprovider"]
    for p in plugins:
        cmd += ["-p", p]
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, cwd=root, env=scrubbed_env(), capture_output=True, text=True, timeout=300
    )
    ids = [ln.strip() for ln in out.stdout.splitlines() if "::" in ln and not ln.startswith(" ")]
    if out.returncode != 0 or not ids:
        raise SystemExit(
            f"collection failed (exit {out.returncode}):\n{out.stdout[-3000:]}{out.stderr[-2000:]}"
        )
    return ids


def write_manifest(root: Path, path: Path, config: Path, test_paths: list[str], old: dict) -> dict:
    plugins = old.get("plugins", [])
    ids = collect_nodeids(root, config, test_paths, plugins)
    files, conftests = {}, {}
    for p in walk(root):
        r = rel(root, p)
        if p.name == "conftest.py":
            conftests[r] = sha256_file(p)
        elif is_test_file(p) and any((root / t) in p.parents for t in test_paths):
            files[r] = sha256_file(p)
    manifest = {
        "version": MANIFEST_VERSION,
        "description": (
            "Pinned pytest suite for quality/gate.py. Regenerate ONLY after reviewing test changes: "
            "python quality/gate.py --write-manifest quality/test-manifest.json"
        ),
        "test_paths": test_paths,
        "pytest_config": {"file": rel(root, config), "sha256": sha256_text(pytest_section(config))},
        "timeout": old.get("timeout", 900),
        "plugins": plugins,
        "min_passed": max(1, len(ids) - len(old.get("allowed_skips", {}))),
        "allowed_skips": old.get("allowed_skips", {}),
        "assertion_exempt": old.get("assertion_exempt", {}),
        "conftests": dict(sorted(conftests.items())),
        "files": dict(sorted(files.items())),
        "tests": ids,
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


# --------------------------------------------------------------------------- Genesis code-py mode
def genesis_verify(task_file: str, completion_file: str) -> int:
    """Genesis `code-py` verifier contract: prints {"reward": 0|1}. The trusted inputs are the task's
    tests (they become the pinned manifest); the completion is untrusted solution.py."""
    import re

    task = json.loads(Path(task_file).read_text())
    completion = Path(completion_file).read_text()
    tests = list(task.get("visible_tests", [])) + list(task.get("hidden_tests", []))
    names = []
    for src in tests:
        m = re.match(r"\s*def\s+(test_\w+)\s*\(", src)
        if not m:
            print(json.dumps({"reward": 0}))
            return 0
        names.append(m.group(1))
    work = Path(tempfile.mkdtemp(prefix="gate-genesis-"))
    try:
        (work / "solution.py").write_text(completion)
        test_dir = work / "tests"
        test_dir.mkdir()
        (test_dir / "test_task.py").write_text(
            "import sys, os\n"
            "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
            "from solution import solve  # noqa: E402\n\n\n" + "\n\n\n".join(tests) + "\n"
        )
        cfg = work / "pyproject.toml"
        cfg.write_text("[tool.pytest.ini_options]\n")
        manifest = {
            "test_paths": ["tests"],
            "pytest_config": {"sha256": sha256_text(pytest_section(cfg))},
            "files": {"tests/test_task.py": sha256_file(test_dir / "test_task.py")},
            "conftests": {},
            "tests": [f"tests/test_task.py::{n}" for n in names],
            "min_passed": len(names),
            "timeout": float(os.environ.get("GATE_GENESIS_TIMEOUT", "10")),
        }
        rep = gate(work, manifest, cfg, ["tests"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if os.environ.get("GATE_DEBUG"):
        print(json.dumps({"ok": rep.ok, "reasons": rep.reasons}), file=sys.stderr)
    print(json.dumps({"reward": 1 if rep.ok else 0}))
    return 0


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "genesis":
        if len(argv) != 3:
            print("usage: gate.py genesis <task.json> <completion.txt>", file=sys.stderr)
            return 2
        return genesis_verify(argv[1], argv[2])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", default="quality/test-manifest.json")
    ap.add_argument("--write-manifest", metavar="PATH", help="pin the current suite into PATH and exit")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--config", default="pyproject.toml", help="pytest config file to pin and use (-c)")
    ap.add_argument("--json", action="store_true", help="print the full verdict as JSON")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    config = (root / args.config).resolve()

    if args.write_manifest:
        path = (root / args.write_manifest).resolve()
        old = json.loads(path.read_text()) if path.exists() else {}
        m = write_manifest(root, path, config, old.get("test_paths", ["tests"]), old)
        print(f"pinned {len(m['tests'])} tests in {len(m['files'])} files -> {rel(root, path)}")
        return 0

    mpath = (root / args.manifest).resolve()
    raw = mpath.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("version") != MANIFEST_VERSION:
        print(f"GATE FAIL: unsupported manifest version {manifest.get('version')}")
        return 1
    rep = gate(root, manifest, config, manifest.get("test_paths", ["tests"]))
    rep.stats["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    if args.json:
        print(json.dumps({"ok": rep.ok, "reasons": rep.reasons, "stats": rep.stats}, indent=2))
    else:
        s = rep.stats
        print(
            f"manifest {rel(root, mpath)} sha256={str(s['manifest_sha256'])[:16]}… "
            f"({len(manifest['tests'])} pinned tests)"
        )
        if rep.ok:
            print(
                f"GATE PASS: {s.get('passed')} passed, {s.get('skipped_allowed', 0)} allowlisted skip(s), "
                f"canary failed as required, {s.get('seconds')}s"
            )
        else:
            print(f"GATE FAIL ({len(rep.reasons)} reason(s)):")
            for r in rep.reasons[:50]:
                print(f"  - {r}")
            if s.get("pytest_tail"):
                print("--- pytest output (tail) ---")
                print(s["pytest_tail"])
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
