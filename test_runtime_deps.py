"""Plugin load must install requirements.txt before digest imports yaml.

A Hermes update creates an environment without these packages. Collection
sets PYTEST_CURRENT_TEST so importing MyMemory does not run pip.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import MyMemory  # noqa: E402


def _allow_install(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MYMEMORY_SKIP_RUNTIME_DEPS", raising=False)


def test_missing_imports_install_requirements_with_pip(monkeypatch):
    _allow_install(monkeypatch)
    real_spec = MyMemory.importlib.util.find_spec

    def spec_for(name):
        if name in MyMemory._RUNTIME_MODULES:
            return None
        if name == "pip":
            return object()
        return real_spec(name)

    monkeypatch.setattr(MyMemory.importlib.util, "find_spec", spec_for)
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(MyMemory.subprocess, "run", fake_run)
    MyMemory.ensure_runtime_deps()
    req = str(Path(MyMemory.__file__).resolve().parent / "requirements.txt")
    assert calls == [[sys.executable, "-m", "pip", "install", "-r", req]]


def test_present_imports_do_not_spawn_pip(monkeypatch):
    _allow_install(monkeypatch)
    real_import = MyMemory.importlib.import_module

    def refuse_runtime_import(name, package=None):
        if name in MyMemory._RUNTIME_MODULES:
            raise AssertionError(f"startup must not import {name}")
        return real_import(name, package)

    monkeypatch.setattr(MyMemory.importlib, "import_module", refuse_runtime_import)
    monkeypatch.setattr(
        MyMemory.importlib.util, "find_spec", lambda name: object()
    )

    def fail_run(*_args, **_kwargs):
        raise AssertionError("pip must not run when runtime imports succeed")

    monkeypatch.setattr(MyMemory.subprocess, "run", fail_run)
    MyMemory.ensure_runtime_deps()
