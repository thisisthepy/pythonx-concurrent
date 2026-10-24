"""Distribution, namespace package, pure-Python and TypedPython gate checks (SPEC S1.1, S1.2, S9.3)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO / "pythonx" / "concurrent"


def _find_pmp() -> Path | None:
    env = os.environ.get("PYTHONMULTIPLATFORM_HOME")
    if env:
        candidate = Path(env)
        return candidate if candidate.is_dir() else None
    for ancestor in REPO.parents:
        sibling = ancestor / "PythonMultiplatform"
        if sibling.is_dir():
            return sibling
    return None


class PackageDistributionTests(unittest.TestCase):
    """SPEC S1.1, S1.2, S9.3."""

    def test_pythonx_stays_a_namespace_package_on_disk(self) -> None:
        self.assertFalse((REPO / "pythonx" / "__init__.py").exists())
        self.assertTrue((PACKAGE_DIR / "__init__.py").is_file())
        self.assertTrue((PACKAGE_DIR / "py.typed").is_file())

    def test_public_surface_exports_milestone_1_names(self) -> None:
        import pythonx.concurrent as pc

        expected = {
            "CancelToken",
            "CoroutineScope",
            "Deferred",
            "Dispatcher",
            "Dispatchers",
            "Job",
            "KotlinEventLoop",
            "Scope",
            "TimeoutFlag",
            "non_cancellable",
            "run_blocking",
            "run_blocking_call",
            "supervisor_scope",
            "with_context",
            "with_timeout",
            "with_timeout_or_none",
        }
        missing = expected - set(dir(pc))
        self.assertEqual(set(), missing)

    def test_pure_python_does_not_reenable_gil_on_free_threaded_build(self) -> None:
        before = sys._is_gil_enabled()
        import pythonx.concurrent  # noqa: F401

        after = sys._is_gil_enabled()
        self.assertEqual(before, after)

    def test_wheel_carries_py_typed_and_no_namespace_init_or_native_extension(self) -> None:
        scratch_root = REPO / ".tmp"
        scratch_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch_root) as tmp:
            copy_dir = Path(tmp) / "src"
            shutil.copytree(
                REPO,
                copy_dir,
                ignore=shutil.ignore_patterns(".git", ".worktrees", ".tmp", ".venv*", "build", "dist", "*.egg-info", "__pycache__"),
            )
            dist_dir = Path(tmp) / "dist"
            uv = shutil.which("uv")
            if uv is None:
                self.skipTest("uv is not on PATH")
            build = subprocess.run(
                [uv, "build", "--wheel", "--python", sys.executable, "--out-dir", str(dist_dir), str(copy_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, build.returncode, build.stdout + build.stderr)
            wheels = list(dist_dir.glob("*.whl"))
            self.assertEqual(1, len(wheels))
            with zipfile.ZipFile(wheels[0]) as zf:
                names = set(zf.namelist())
            self.assertNotIn("pythonx/__init__.py", names)
            self.assertIn("pythonx/concurrent/__init__.py", names)
            self.assertIn("pythonx/concurrent/py.typed", names)
            native = [n for n in names if n.endswith((".so", ".pyd", ".dylib"))]
            self.assertEqual([], native)

    def test_typedpython_gate_reports_zero_errors(self) -> None:
        pmp = _find_pmp()
        if pmp is None:
            self.skipTest("PythonMultiplatform checkout not found")
        if importlib.util.find_spec("pyrefly") is None:
            self.skipTest("pyrefly is not installed in this interpreter")
        gate_root = pmp / "python-multiplatform-ksp" / "src" / "main" / "python"
        if not (gate_root / "typedpython" / "gate.py").is_file():
            self.skipTest("typedpython gate not found in PythonMultiplatform")
        env = {**os.environ, "PYTHONPATH": str(gate_root), "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "typedpython",
                "check",
                "--format",
                "json",
                "--search-path",
                str(REPO),
                str(REPO / "pythonx"),
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
        )
        if done.returncode not in (0, 1):
            self.fail(f"TypedPython gate failed to run (exit {done.returncode}):\n{done.stderr}")
        findings = json.loads(done.stdout)
        self.assertEqual([], findings, f"TypedPython findings: {findings}")


if __name__ == "__main__":
    unittest.main()
