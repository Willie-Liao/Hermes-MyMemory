"""Installer pins MyMemory workers from chat config without hanging or leaking keys."""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import yaml

INSTALL_SH = Path(__file__).resolve().parent / "scripts" / "install.sh"


def _stub_hermes(bin_dir: Path) -> Path:
    path = bin_dir / "hermes"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run_install(home: Path, hermes: Path, extra: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["HERMES_BIN"] = str(hermes)
    env["MYMEMORY_SKIP_GTE"] = "1"
    env["PATH"] = f"{hermes.parent}:{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", str(INSTALL_SH), "--skip-tests", *extra],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        input="",
    )


def test_install_sh_bash_n():
    subprocess.run(["bash", "-n", str(INSTALL_SH)], check=True)


def test_worker_from_chat_pins_lanes_and_keeps_chat_model(tmp_path: Path):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        textwrap.dedent(
            """\
            model:
              default: MiniMax-M3
              provider: minimax-cn
              base_url: https://api.minimax.chat/v1
            plugins:
              enabled: []
              entries:
                MyMemory:
                  weekly:
                    weekly_brief_weixin: keep-me@im.wechat
                  retention:
                    allow_tool_override: false
            """
        ),
        encoding="utf-8",
    )
    (home / ".env").write_text("MINIMAX_CN_API_KEY=secret-not-for-stdout\n", encoding="utf-8")
    (tmp_path / "bin").mkdir(exist_ok=True)
    hermes = _stub_hermes(tmp_path / "bin")

    proc = _run_install(home, hermes, ["--worker-from-chat"])
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "secret-not-for-stdout" not in combined
    assert "--worker-from-chat" in combined or "pinning workers" in combined or "Current chat" in combined

    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert data["model"]["default"] == "MiniMax-M3"
    assert data["model"]["provider"] == "minimax-cn"
    digest = data["plugins"]["entries"]["MyMemory"]["digest"]
    assert digest["model"] == "MiniMax-M3"
    assert digest["provider"] == "minimax-cn"
    assert digest["worker_llm"]["phase1"]["model"] == "MiniMax-M3"
    weekly = data["plugins"]["entries"]["MyMemory"]["weekly"]
    assert weekly["weekly_brief_weixin"] == "keep-me@im.wechat"
    assert (home / "config.yaml.bak-mymemory-install").is_file()


def test_custom_worker_env_upserts_key_only(tmp_path: Path):
    """Non-TTY custom pin (MYMEMORY_WORKER_*) writes the provider key into .env only."""
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        textwrap.dedent(
            """\
            model:
              default: kimi-k2.6
              provider: kimi-coding
              base_url: https://api.kimi.com/coding/v1
            plugins:
              entries:
                MyMemory:
                  weekly:
                    weekly_brief_weixin: keep-me@im.wechat
            """
        ),
        encoding="utf-8",
    )
    (home / ".env").write_text("OTHER=1\nKIMI_API_KEY=chat-key\n", encoding="utf-8")
    (tmp_path / "bin").mkdir(exist_ok=True)
    hermes = _stub_hermes(tmp_path / "bin")
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["HERMES_BIN"] = str(hermes)
    env["MYMEMORY_SKIP_GTE"] = "1"
    env["MYMEMORY_WORKER_PROVIDER"] = "minimax-cn"
    env["MYMEMORY_WORKER_MODEL"] = "MiniMax-M2.5"
    env["MYMEMORY_WORKER_BASE_URL"] = "https://api.minimax.chat/v1"
    env["MYMEMORY_WORKER_API_KEY"] = "mm-install-secret"
    proc = subprocess.run(
        ["bash", str(INSTALL_SH), "--skip-tests"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        input="",
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "mm-install-secret" not in combined
    text = (home / ".env").read_text(encoding="utf-8")
    assert "MINIMAX_CN_API_KEY=mm-install-secret" in text
    assert "OTHER=1" in text
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert data["model"]["default"] == "kimi-k2.6"
    assert data["plugins"]["entries"]["MyMemory"]["digest"]["model"] == "MiniMax-M2.5"
    assert data["plugins"]["entries"]["MyMemory"]["weekly"]["weekly_brief_weixin"] == "keep-me@im.wechat"
