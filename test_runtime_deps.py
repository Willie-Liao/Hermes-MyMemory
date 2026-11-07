"""Plugin load must install missing requirements.txt lines without importing them.

A Hermes update creates an environment without these packages. Collection
sets PYTEST_CURRENT_TEST so importing MyMemory does not run pip.
A finished install still cannot register inside the load that Hermes already
abandoned, so the success path must restart the gateway from outside it.
distribution() reads metadata only, so tokenizers does not load the GTE stack.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import MyMemory  # noqa: E402


_EXTRA_URL = "https://pypi.org/simple"
_FILE_BODY = """--extra-index-url https://pypi.org/simple
onnxruntime
pyyaml
pkg-mac; sys_platform == "darwin"
pkg-other; sys_platform != "darwin"
"""
_HEAVY_IMPORTS = {
    "tokenizers",
    "yaml",
    "pyyaml",
    "openai",
    "numpy",
    "onnxruntime",
}


def _allow_install(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MYMEMORY_SKIP_RUNTIME_DEPS", raising=False)


def _applicable_specs() -> list[str]:
    """The marker line that matches this OS, so the other pin is not installed."""
    marked = "pkg-mac" if sys.platform == "darwin" else "pkg-other"
    return ["onnxruntime", "pyyaml", marked]


def _applicable_names() -> list[str]:
    marked = "pkg-mac" if sys.platform == "darwin" else "pkg-other"
    return ["onnxruntime", "pyyaml", marked]


def _index_args() -> list[str]:
    return [
        "--extra-index-url",
        _EXTRA_URL,
    ]


def _point_requirements(monkeypatch, tmp_path, body: str = _FILE_BODY) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(MyMemory, "_REQUIREMENTS_FILE", path)


def _pip_present(monkeypatch) -> None:
    real_spec = MyMemory.importlib.util.find_spec

    def spec_for(name):
        if name == "pip":
            return object()
        return real_spec(name)

    monkeypatch.setattr(MyMemory.importlib.util, "find_spec", spec_for)


def _distribution(monkeypatch, missing: set[str], installed: dict | None = None) -> None:
    """Report requirement names as absent without importing those packages."""

    def distribution(name):
        if installed is not None and installed.get("ok"):
            return object()
        if name in missing:
            raise MyMemory.importlib.metadata.PackageNotFoundError(name)
        return object()

    monkeypatch.setattr(MyMemory.importlib.metadata, "distribution", distribution)


def test_missing_imports_install_requirements_with_pip(monkeypatch, tmp_path):
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _point_requirements(monkeypatch, tmp_path)
    _pip_present(monkeypatch)
    _distribution(monkeypatch, set(_applicable_names()))
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(MyMemory.subprocess, "run", fake_run)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("gateway restart must not run when modules stay missing")

    monkeypatch.setattr(MyMemory.subprocess, "Popen", fail_popen)
    MyMemory.ensure_runtime_deps()
    assert calls == [[sys.executable, "-m", "pip", "install", *_index_args(), *_applicable_specs()]]


def test_present_imports_do_not_spawn_pip(monkeypatch, tmp_path, caplog):
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _point_requirements(monkeypatch, tmp_path)
    _distribution(monkeypatch, set())
    real_import = MyMemory.importlib.import_module

    def refuse_runtime_import(name, package=None):
        if name in _HEAVY_IMPORTS:
            raise AssertionError(f"startup must not import {name}")
        return real_import(name, package)

    monkeypatch.setattr(MyMemory.importlib, "import_module", refuse_runtime_import)

    def fail_run(*_args, **_kwargs):
        raise AssertionError("pip must not run when runtime imports succeed")

    monkeypatch.setattr(MyMemory.subprocess, "run", fail_run)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("gateway restart must not run when runtime imports succeed")

    monkeypatch.setattr(MyMemory.subprocess, "Popen", fail_popen)
    with caplog.at_level("INFO", logger="mymemory.runtime"):
        MyMemory.ensure_runtime_deps()
    assert f"{len(_applicable_names())} specs in " in caplog.text
    assert "missing=0" in caplog.text


def test_only_extra_requirement_line_is_installed(monkeypatch, tmp_path):
    """A line added to requirements.txt is installed when the others are already present."""
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _point_requirements(monkeypatch, tmp_path, _FILE_BODY + "openai\n")
    _pip_present(monkeypatch)
    _distribution(monkeypatch, {"openai"})
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(MyMemory.subprocess, "run", fake_run)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("gateway restart must not run when the new line stays missing")

    monkeypatch.setattr(MyMemory.subprocess, "Popen", fail_popen)
    MyMemory.ensure_runtime_deps()
    assert calls == [[sys.executable, "-m", "pip", "install", *_index_args(), "openai"]]


def _notice_path(home: Path) -> Path:
    return home / "logs" / "mymemory-runtime-deps-notice.json"


def test_json5_missing_is_installed_from_real_requirements(monkeypatch, tmp_path):
    """A fresh Hermes env must install the shipped json5 line, not a stand-in file."""
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _pip_present(monkeypatch)
    installed = {"ok": False}
    _distribution(monkeypatch, {"json5"}, installed)
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        installed["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    spawned: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        spawned.append(list(cmd))
        return object()

    monkeypatch.setattr(MyMemory.subprocess, "run", fake_run)
    monkeypatch.setattr(MyMemory.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        MyMemory.shutil, "which", lambda name: "/usr/bin/hermes" if name == "hermes" else None
    )
    clock = __import__("MyMemory.weekly.weekly_clock", fromlist=["send_weekly_brief_weixin"])
    monkeypatch.setattr(clock, "send_weekly_brief_weixin", lambda text: {"outcome": "sent"})

    MyMemory.ensure_runtime_deps()

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd == [sys.executable, "-m", "pip", "install", "json5>=0.9"]
    assert len(spawned) == 1
    notice = json.loads(_notice_path(tmp_path).read_text(encoding="utf-8"))
    assert notice["missing"] == ["json5"]


def test_json5_present_is_not_reinstalled(monkeypatch, tmp_path):
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _distribution(monkeypatch, set())

    def fail_run(*_args, **_kwargs):
        raise AssertionError("pip must not run when json5 is already installed")

    monkeypatch.setattr(MyMemory.subprocess, "run", fail_run)
    MyMemory.ensure_runtime_deps()


def test_missing_imports_restart_gateway_after_install(monkeypatch, tmp_path):
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _point_requirements(monkeypatch, tmp_path)
    _pip_present(monkeypatch)
    installed = {"ok": False}
    _distribution(monkeypatch, set(_applicable_names()), installed)
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        installed["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    spawned: list[tuple[list[str], dict]] = []

    def fake_popen(cmd, **kwargs):
        spawned.append((list(cmd), kwargs))
        return object()

    sent: list[str] = []

    def fake_send(text):
        sent.append(text)
        return {"outcome": "sent"}

    monkeypatch.setattr(MyMemory.subprocess, "run", fake_run)
    monkeypatch.setattr(MyMemory.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(MyMemory.shutil, "which", lambda name: "/usr/bin/hermes" if name == "hermes" else None)
    clock = __import__("MyMemory.weekly.weekly_clock", fromlist=["send_weekly_brief_weixin"])
    monkeypatch.setattr(clock, "send_weekly_brief_weixin", fake_send)

    MyMemory.ensure_runtime_deps()

    assert calls == [[sys.executable, "-m", "pip", "install", *_index_args(), *_applicable_specs()]]
    assert len(spawned) == 1
    cmd, kwargs = spawned[0]
    assert cmd == ["/usr/bin/hermes", "gateway", "restart"]
    assert kwargs.get("start_new_session") is True
    names = ", ".join(_applicable_names())
    assert sent == [
        f"MyMemory was missing: {names}. Installed them and restarting the gateway so memory prefetch can register."
    ]
    notice = json.loads(_notice_path(tmp_path).read_text(encoding="utf-8"))
    assert notice["missing"] == _applicable_names()
    assert notice["sent"] is True
    assert isinstance(notice["restart_requested_at"], float)


def test_failed_install_does_not_restart(monkeypatch, tmp_path):
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _point_requirements(monkeypatch, tmp_path)
    _pip_present(monkeypatch)
    _distribution(monkeypatch, set(_applicable_names()))

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no wheel")

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("gateway restart must not run when pip fails")

    monkeypatch.setattr(MyMemory.subprocess, "run", fake_run)
    monkeypatch.setattr(MyMemory.subprocess, "Popen", fail_popen)
    MyMemory.ensure_runtime_deps()
    assert not _notice_path(tmp_path).exists()


def test_recent_restart_notice_does_not_install_again(monkeypatch, tmp_path):
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    notice = _notice_path(tmp_path)
    notice.parent.mkdir(parents=True)
    notice.write_text(
        json.dumps(
            {
                "missing": ["onnxruntime"],
                "sent": False,
                "restart_requested_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    _point_requirements(monkeypatch, tmp_path)
    _pip_present(monkeypatch)
    _distribution(monkeypatch, {"onnxruntime"})

    def fail_run(*_args, **_kwargs):
        raise AssertionError("pip must not run again inside the restart guard")

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("gateway restart must not loop")

    monkeypatch.setattr(MyMemory.subprocess, "run", fail_run)
    monkeypatch.setattr(MyMemory.subprocess, "Popen", fail_popen)
    MyMemory.ensure_runtime_deps()


def test_systemd_restart_uses_transient_unit(monkeypatch, tmp_path):
    _allow_install(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _point_requirements(monkeypatch, tmp_path)
    _pip_present(monkeypatch)
    installed = {"ok": False}
    _distribution(monkeypatch, set(_applicable_names()), installed)

    def fake_run(cmd, **_kwargs):
        installed["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    spawned: list[list[str]] = []

    def fake_popen(cmd, **_kwargs):
        spawned.append(list(cmd))
        return object()

    def which(name):
        return {
            "systemctl": "/bin/systemctl",
            "systemd-run": "/bin/systemd-run",
            "hermes": "/usr/bin/hermes",
        }.get(name)

    monkeypatch.setattr(MyMemory.subprocess, "run", fake_run)
    monkeypatch.setattr(MyMemory.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(MyMemory.shutil, "which", which)
    monkeypatch.setattr(MyMemory.os.path, "isdir", lambda path: path == "/run/systemd/system")
    clock = __import__("MyMemory.weekly.weekly_clock", fromlist=["send_weekly_brief_weixin"])
    monkeypatch.setattr(clock, "send_weekly_brief_weixin", lambda _text: {"outcome": "skipped"})

    MyMemory.ensure_runtime_deps()

    assert spawned == [[
        "systemd-run",
        "--collect",
        "--unit",
        f"mymemory-runtime-deps-restart-{os.getpid()}",
        "systemctl",
        "restart",
        "hermes-gateway",
    ]]
    notice = json.loads(_notice_path(tmp_path).read_text(encoding="utf-8"))
    assert notice["sent"] is False


class _Ctx:
    def __init__(self) -> None:
        self.provider = None

    def register_memory_provider(self, provider) -> None:
        self.provider = provider


class _InlineThread:
    def __init__(self, target=None, name=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        if self._target is not None:
            self._target()


def test_register_sends_unsent_notice_once(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    notice = _notice_path(tmp_path)
    notice.parent.mkdir(parents=True)
    notice.write_text(
        json.dumps({"missing": ["onnxruntime", "tokenizers"], "sent": False}),
        encoding="utf-8",
    )
    sent: list[str] = []

    def fake_send(text):
        sent.append(text)
        return {"outcome": "sent"}

    clock = __import__("MyMemory.weekly.weekly_clock", fromlist=["send_weekly_brief_weixin"])
    monkeypatch.setattr(clock, "send_weekly_brief_weixin", fake_send)
    monkeypatch.setattr(MyMemory.threading, "Thread", _InlineThread)

    MyMemory.register(_Ctx())

    assert sent == [
        "MyMemory installed the missing modules (onnxruntime, tokenizers) and restarted. Memory prefetch is registered."
    ]
    assert not notice.exists()


def test_register_stops_notice_after_send_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    notice = _notice_path(tmp_path)
    notice.parent.mkdir(parents=True)
    notice.write_text(
        json.dumps({"missing": ["onnxruntime"], "sent": False}),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_send(text):
        calls.append(text)
        return {"outcome": "error", "error": "prepare failed"}

    clock = __import__("MyMemory.weekly.weekly_clock", fromlist=["send_weekly_brief_weixin"])
    monkeypatch.setattr(clock, "send_weekly_brief_weixin", fake_send)
    monkeypatch.setattr(MyMemory.threading, "Thread", _InlineThread)

    MyMemory.register(_Ctx())

    assert calls == [
        "MyMemory installed the missing modules (onnxruntime) and restarted. Memory prefetch is registered."
    ]
    assert notice.is_file()


def test_register_drops_notice_already_sent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    notice = _notice_path(tmp_path)
    notice.parent.mkdir(parents=True)
    notice.write_text(
        json.dumps({"missing": ["onnxruntime"], "sent": True}),
        encoding="utf-8",
    )

    def fail_send(_text):
        raise AssertionError("already-sent notice must not send again")

    clock = __import__("MyMemory.weekly.weekly_clock", fromlist=["send_weekly_brief_weixin"])
    monkeypatch.setattr(clock, "send_weekly_brief_weixin", fail_send)
    monkeypatch.setattr(MyMemory.threading, "Thread", _InlineThread)

    MyMemory.register(_Ctx())
    assert not notice.exists()
