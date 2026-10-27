"""A regenerated config gets the saved worker block back, and a missing file stays missing."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import worker_config  # noqa: E402


def _home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_missing_config_is_not_created(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert worker_config.ensure_worker_block(home) is False
    assert not (home / "config.yaml").exists()
    assert not worker_config.pin_path(home).exists()


def test_complete_config_is_not_rewritten_and_pin_is_saved(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    block = worker_config.default_worker_block()
    block["weekly"]["weekly_brief_weixin"] = "keep-me@im.wechat"
    text = yaml.safe_dump(
        {"model": {"default": "MiniMax-M3", "provider": "minimax-cn"}, "plugins": {"entries": {"MyMemory": block}}},
        sort_keys=False,
    )
    path = home / "config.yaml"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    assert worker_config.ensure_worker_block(home) is False
    assert path.read_bytes() == before
    pin = yaml.safe_load(worker_config.pin_path(home).read_text(encoding="utf-8"))
    assert pin["weekly"]["weekly_brief_weixin"] == "keep-me@im.wechat"
    assert pin["digest"]["model"] == "MiniMax-M2.5"


def test_stock_file_is_filled_from_the_pin(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    pin = worker_config.default_worker_block()
    pin["digest"]["provider"] = "xiaomi"
    pin["digest"]["model"] = "mimo-v2.6-flash"
    for lane in pin["digest"]["worker_llm"].values():
        lane["provider"] = "xiaomi"
        lane["model"] = "mimo-v2.6-flash"
    pin["weekly"]["weekly_brief_weixin"] = "keep-me@im.wechat"
    worker_config.pin_path(home).write_text(
        yaml.safe_dump(pin, sort_keys=False),
        encoding="utf-8",
    )
    (home / "config.yaml").write_text("model:\n  default: MiniMax-M3\n", encoding="utf-8")
    assert worker_config.ensure_worker_block(home) is True
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    digest = data["plugins"]["entries"]["MyMemory"]["digest"]
    assert digest["provider"] == "xiaomi"
    assert digest["model"] == "mimo-v2.6-flash"
    assert digest["worker_llm"]["phase1"]["model"] == "mimo-v2.6-flash"
    assert data["plugins"]["entries"]["MyMemory"]["weekly"]["weekly_brief_weixin"] == "keep-me@im.wechat"
    assert data["model"]["default"] == "MiniMax-M3"
    assert "MyMemory" in data["plugins"]["enabled"]
    assert "disabled" not in data["plugins"]


def test_stock_file_without_a_pin_uses_minimax_m2_5(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "config.yaml").write_text("model:\n  default: MiniMax-M3\n", encoding="utf-8")
    assert worker_config.ensure_worker_block(home) is True
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    monthly = data["plugins"]["entries"]["MyMemory"]["monthly"]
    assert monthly["provider"] == "minimax-cn"
    assert monthly["model"] == "MiniMax-M2.5"
    assert monthly["worker_llm"]["monthly"]["model"] == "MiniMax-M2.5"
    assert monthly["reduce_max_tokens"] == 16384
    assert monthly["write_at"] == "00:05"
    assert monthly["checkpoint_days"] == [10, 20]
    assert monthly["retry_days"] == 3


def test_corrupt_config_does_not_raise(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "config.yaml").write_text(":\n  [\n", encoding="utf-8")
    assert worker_config.ensure_worker_block(home) is False
