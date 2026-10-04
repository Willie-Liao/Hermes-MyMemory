"""purge_old_logs: delete files under logs/ older than the chosen age."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path


def _load_retention():
    plugins = Path(__file__).resolve().parents[1]
    if str(plugins) not in sys.path:
        sys.path.insert(0, str(plugins))
    path = Path(__file__).with_name("retention.py")
    spec = importlib.util.spec_from_file_location("memory_retention_purge_logs", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _touch_old(path: Path, *, days_ago: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old\n", encoding="utf-8")
    age = time.time() - days_ago * 86400
    os.utime(path, (age, age))


def test_purge_old_logs_deletes_only_files_past_cutoff(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)

    logs = tmp_path / "logs"
    keep = logs / "agent.log"
    drop = logs / "agent.log.1"
    nested = logs / "curator" / "old.log"
    _touch_old(keep, days_ago=10)
    _touch_old(drop, days_ago=100)
    _touch_old(nested, days_ago=200)

    out = ret.purge_old_logs(months=3)  # ~90 days
    assert out["purged_logs"] == 2
    assert keep.exists()
    assert not drop.exists()
    assert not nested.exists()
    assert (logs / "curator").is_dir()


def test_purge_old_logs_rejects_unknown_months(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "logs").mkdir()
    out = ret.purge_old_logs(months=4)
    assert out == {"purged_logs": 0, "error": "unsupported months: 4"}


def test_purge_old_logs_noop_when_logs_missing(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    assert ret.purge_old_logs(months=1) == {"purged_logs": 0}
