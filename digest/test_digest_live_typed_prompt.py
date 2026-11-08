"""Live Phase-2 oneshot on the full 2026-08-15 daily board (opt-in).

Enable with ``HERMES_DIGEST_LIVE_LLM=1``. Default model is kimi-k3 (Kimi Code,
thinking off, temperature 0.6 so tool_choice works). Override with
``HERMES_DIGEST_LIVE_MODEL``. Copies the
production daily file byte-for-byte; fails if the copy was truncated. Prints
token savings vs the last ungated k3 heavy-day Phase-2 attempt.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from conftest import load_plugin_module

_LIVE_TYPED = pytest.mark.skipif(
    os.environ.get("HERMES_DIGEST_LIVE_LLM", "").strip() not in {"1", "true", "yes"},
    reason="Set HERMES_DIGEST_LIVE_LLM=1 to run live typed-prompt tests",
)

_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "memories"
    / "staging"
    / "daily"
    / "2026-08-15.md"
)

_MIXED_BASELINE = {
    "label": "mixed-list Phase 2 (prior probe, no max_tokens)",
    "input_tokens": 10717,
    "output_tokens": 9508,
    "reasoning_tokens": 9320,
    "total_tokens": 20225,
    "wall_seconds": 232,
    "max_tokens": None,
}

# Last gated kimi-k3 Phase-2 on the same 34-card file with typed arrays
# plus a Markdown mem-id pair list (2026-08-22 live pytest).
_LAST_K3_GATED_TYPED = {
    "label": "gated Phase 2 typed arrays + id pairs (2026-08-22)",
    "input_tokens": 12925,
    "output_tokens": 1928,
    "reasoning_tokens": 0,
    "total_tokens": 14853,
    "wall_seconds": 55.8,
    "max_tokens": 8192,
}
_LAST_K3_HEAVY_PHASE2 = {
    "label": "last k3 Phase 2 heavy daytime (ungated)",
    "input_tokens": 9468,
    "output_tokens": 4435,
    "reasoning_tokens": 0,
    "total_tokens": 13903,
    "wall_seconds": 152.3,
    "max_tokens": None,
}

_K3_CODING_BASE = "https://api.kimi.com/coding/v1"


def _digest():
    return load_plugin_module("digest.py", "memory_digest_live_typed_prompt")


def _dedup():
    return load_plugin_module("dedup_prompt.py", "memory_digest_live_typed_dedup")


@_LIVE_TYPED
def test_live_typed_phase2_and_wrapup_on_full_2026_08_15(tmp_path, monkeypatch):
    digest = _digest()
    dedup = _dedup()
    assert _SOURCE.is_file(), f"missing source daily file {_SOURCE}"
    source_text = _SOURCE.read_text(encoding="utf-8")
    source_n = len(digest._daily_blocks(source_text))
    dest = tmp_path / "memories" / "staging" / "daily" / "2026-08-15.md"
    dest.parent.mkdir(parents=True)
    shutil.copyfile(_SOURCE, dest)
    copied = dest.read_text(encoding="utf-8")
    blocks = digest._daily_blocks(copied)
    assert copied == source_text
    assert len(copied) >= 18000
    assert len(blocks) == source_n == 34

    prompt = dedup.build_proposer_prompt(blocks, [])
    typed_n = 0
    for kind in ("events", "facts", "procedures", "decisions"):
        heading = f"### Existing {kind}"
        assert heading in prompt
        section = prompt.split(heading, 1)[1].split("###", 1)[0].strip()
        payload = json.loads(section) if section.startswith("[") else []
        typed_n += len(payload)
    assert typed_n == 34
    assert "Existing blocks already in the file" not in prompt
    assert "submit_operations" in prompt

    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    live_model = (
        os.environ.get("HERMES_DIGEST_LIVE_MODEL", "").strip() or "kimi-k3"
    )
    import worker_llm as wl

    orig_runtime = wl._plugin_worker_runtime
    orig_creds = wl._oneshot_credentials
    orig_oneshot = digest.run_worker_llm_oneshot

    def overlay_runtime(plugin, purpose=""):
        out = dict(orig_runtime(plugin, purpose) or {})
        out["model"] = live_model
        if live_model.startswith("kimi"):
            out["provider"] = "kimi-coding"
        return out

    def overlay_creds(provider):
        plug = str(provider or "").strip().lower()
        if plug == "kimi-coding":
            key = (
                os.environ.get("KIMI_API_KEY")
                or os.environ.get("KIMI_CODING_API_KEY")
                or ""
            ).strip()
            if not key:
                raise ValueError("missing KIMI_API_KEY for kimi-k3 live Phase 2")
            base = (
                os.environ.get("KIMI_BASE_URL") or _K3_CODING_BASE
            ).strip()
            return key, base
        return orig_creds(provider)

    def overlay_oneshot(*args, **kwargs):
        if live_model == "kimi-k3":
            kwargs["temperature"] = 0.6
        return orig_oneshot(*args, **kwargs)

    from openai.resources.chat.completions import Completions

    orig_create = Completions.create

    def patched_create(self, *args, **kwargs):
        if live_model == "kimi-k3":
            kwargs["temperature"] = 0.6
            extra = dict(kwargs.get("extra_body") or {})
            extra["thinking"] = {"type": "disabled"}
            kwargs["extra_body"] = extra
        return orig_create(self, *args, **kwargs)

    monkeypatch.setattr(Completions, "create", patched_create)
    monkeypatch.setattr(wl, "_plugin_worker_runtime", overlay_runtime)
    monkeypatch.setattr(wl, "_oneshot_credentials", overlay_creds)
    monkeypatch.setattr(digest, "run_worker_llm_oneshot", overlay_oneshot)

    captures: list[dict] = []
    orig = digest._invoke_digest_oneshot_tool

    def wrapping(prompt_text, platform, *, purpose="", force_tool_name="", **kwargs):
        t0 = time.perf_counter()
        out = orig(
            prompt_text,
            platform,
            purpose=purpose,
            force_tool_name=force_tool_name,
            **kwargs,
        )
        packed = dict(out)
        packed["wall_seconds"] = round(time.perf_counter() - t0, 1)
        packed["purpose"] = purpose
        packed["force_tool_name"] = force_tool_name
        packed["prompt"] = prompt_text
        captures.append(packed)
        return packed

    monkeypatch.setattr(digest, "_invoke_digest_oneshot_tool", wrapping)

    t_p2 = time.perf_counter()
    proposer = digest.make_oneshot_proposer(session_id="live-typed", run_id="step7")
    ops = proposer(blocks, [], attempt=1)
    p2_wall = round(time.perf_counter() - t_p2, 1)
    assert isinstance(ops, list)
    p2_caps = [
        c for c in captures if c.get("force_tool_name") == "submit_operations"
    ]
    skipped = not p2_caps
    if skipped:
        p2 = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "wall_seconds": p2_wall,
            "failed": False,
            "finish_reason": "skip",
        }
    else:
        p2 = p2_caps[0]
        assert p2.get("failed") is False
        args = p2.get("tool_args") or {}
        assert isinstance(args.get("operations"), list)
        gated_prompt = str(p2.get("prompt") or "")
        assert "## Filtered candidate board" in gated_prompt
        live_board = gated_prompt.rsplit("## Filtered candidate board", 1)[1]
        assert "### Existing events" not in live_board
        assert "Compare only these same-type pairs" not in live_board
        start = live_board.find("{")
        end = live_board.rfind("}")
        assert start >= 0 and end > start
        compact_board = json.loads(live_board[start : end + 1])
        assert compact_board
        for bucket in compact_board.values():
            assert bucket.get("cards")
            for left, right in bucket.get("pairs") or []:
                assert isinstance(left, int) and isinstance(right, int)

    t_w = time.perf_counter()
    wrap = digest.run_day_wrapup(dest)
    wrap_wall = round(time.perf_counter() - t_w, 1)
    assert wrap.get("outcome") == "written"
    phrase = str(wrap.get("phrase") or "")
    assert phrase
    for line in phrase.splitlines():
        assert len(line.lstrip("- ").strip()) <= digest.MAX_WRAPUP_CHARS
    wcap = next(c for c in captures if c.get("force_tool_name") == "submit_day_wrapup")
    assert wcap.get("failed") is False

    def _row(label, rec, wall, cap):
        return (
            f"{label}: input={rec.get('input_tokens')} output={rec.get('output_tokens')} "
            f"reasoning={rec.get('reasoning_tokens')} total={rec.get('total_tokens')} "
            f"wall={wall}s max_tokens={cap} finish_reason={rec.get('finish_reason')}"
        )

    p2_reason = int(p2.get("reasoning_tokens") or 0)
    base_reason = int(_MIXED_BASELINE["reasoning_tokens"])
    delta = p2_reason - base_reason
    pct = (100.0 * delta / base_reason) if base_reason else 0.0
    k3_in = int(_LAST_K3_HEAVY_PHASE2["input_tokens"])
    k3_tot = int(_LAST_K3_HEAVY_PHASE2["total_tokens"])
    this_in = int(p2.get("input_tokens") or 0)
    this_tot = int(p2.get("total_tokens") or 0)
    in_delta = this_in - k3_in
    tot_delta = this_tot - k3_tot
    in_pct = (100.0 * in_delta / k3_in) if k3_in else 0.0
    tot_pct = (100.0 * tot_delta / k3_tot) if k3_tot else 0.0
    gated_in = int(_LAST_K3_GATED_TYPED["input_tokens"])
    gated_tot = int(_LAST_K3_GATED_TYPED["total_tokens"])
    vs_gated_in = this_in - gated_in
    vs_gated_tot = this_tot - gated_tot
    vs_gated_in_pct = (100.0 * vs_gated_in / gated_in) if gated_in else 0.0
    vs_gated_tot_pct = (100.0 * vs_gated_tot / gated_tot) if gated_tot else 0.0
    summary = "\n".join(
        [
            "=== STEP7 TOKEN COMPARISON (full 2026-08-15.md, 34 cards) ===",
            f"live model={live_model} skipped_llm={skipped}",
            _row(
                _MIXED_BASELINE["label"],
                _MIXED_BASELINE,
                _MIXED_BASELINE["wall_seconds"],
                _MIXED_BASELINE["max_tokens"],
            ),
            _row(
                _LAST_K3_HEAVY_PHASE2["label"],
                _LAST_K3_HEAVY_PHASE2,
                _LAST_K3_HEAVY_PHASE2["wall_seconds"],
                _LAST_K3_HEAVY_PHASE2["max_tokens"],
            ),
            _row(
                _LAST_K3_GATED_TYPED["label"],
                _LAST_K3_GATED_TYPED,
                _LAST_K3_GATED_TYPED["wall_seconds"],
                _LAST_K3_GATED_TYPED["max_tokens"],
            ),
            _row(
                "gated Phase 2 (this run, 2026-08-15)",
                p2,
                p2.get("wall_seconds") or p2_wall,
                digest.ONESHOT_DIGEST_MAX_TOKENS,
            ),
            _row(
                "wrap-up oneshot (this run)",
                wcap,
                wcap.get("wall_seconds") or wrap_wall,
                digest.ONESHOT_WRAPUP_MAX_TOKENS,
            ),
            f"reasoning delta vs mixed-list: {delta:+d} ({pct:+.1f}%)",
            f"input delta vs last k3 heavy: {in_delta:+d} ({in_pct:+.1f}%)",
            f"total delta vs last k3 heavy: {tot_delta:+d} ({tot_pct:+.1f}%)",
            f"input delta vs last gated typed: {vs_gated_in:+d} ({vs_gated_in_pct:+.1f}%)",
            f"total delta vs last gated typed: {vs_gated_tot:+d} ({vs_gated_tot_pct:+.1f}%)",
            f"wrap-up phrase: {phrase}",
        ]
    )
    print(summary)
    dest_text = dest.read_text(encoding="utf-8")
    assert digest.DAY_WRAPUP_HEADING in dest_text
    assert phrase in dest_text


def _load_live_dotenv(live_home: Path) -> None:
    """Load live hermes-home/.env before HERMES_HOME is pointed at tmp_path."""
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv(hermes_home=live_home)
        return
    except Exception:
        pass
    env_path = live_home / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if not val:
            continue
        if not (os.environ.get(key) or "").strip():
            os.environ[key] = val


def _anthropic_messages_url() -> str:
    """MiniMax Anthropic host from .env; never hardcode a MiniMax URL in plugin code."""
    explicit = (os.environ.get("MINIMAX_CN_ANTHROPIC_URL") or "").strip().rstrip("/")
    if explicit:
        if explicit.endswith("/v1/messages"):
            return explicit
        return f"{explicit}/v1/messages"
    base = (os.environ.get("MINIMAX_CN_BASE_URL") or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/anthropic/v1/messages"


def _probe_minimax_anthropic_tool_use(model: str) -> dict:
    """Observational: MiniMax Messages + tool_use. 4xx is a recorded finding, not a fail."""
    import urllib.error
    import urllib.request

    key = (os.environ.get("MINIMAX_CN_API_KEY") or "").strip()
    url = _anthropic_messages_url()
    payload = {
        "model": model,
        "max_tokens": 64,
        "tools": [
            {
                "name": "ping",
                "description": "noop ping",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "tool_choice": {"type": "tool", "name": "ping"},
        "messages": [{"role": "user", "content": "Call ping."}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            body = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            err_body = str(exc)
        return {
            "anthropic_tool_use_ok": False,
            "anthropic_http_status": int(exc.code or 0),
            "anthropic_error": err_body or str(exc),
        }
    except Exception as exc:
        return {
            "anthropic_tool_use_ok": False,
            "anthropic_http_status": 0,
            "anthropic_error": str(exc)[:400],
        }
    content = body.get("content") if isinstance(body, dict) else None
    has_tool_use = False
    if isinstance(content, list):
        has_tool_use = any(
            isinstance(part, dict) and str(part.get("type") or "") == "tool_use"
            for part in content
        )
    return {
        "anthropic_tool_use_ok": bool(status >= 200 and status < 300 and has_tool_use),
        "anthropic_http_status": status,
        "anthropic_error": "" if has_tool_use else json.dumps(body)[:400],
    }


def _oneshot_ping(wl, *, plugin: str, purpose: str) -> dict:
    """Tiny OpenAI-tool ping so live smoke does not run a full digest/weekly/monthly job."""
    ping_schema = {
        "name": "ping",
        "description": "noop ping",
        "parameters": {"type": "object", "properties": {}},
    }
    started = time.perf_counter()
    capture = wl.run_worker_llm_oneshot(
        "Call ping.",
        plugin=plugin,
        purpose=purpose,
        force_tool_name="ping",
        tool_schema=ping_schema,
        max_tokens=128,
    )
    return {
        "ms": max(0, int((time.perf_counter() - started) * 1000)),
        "failed": bool(capture.get("failed")),
        "api_calls": int(capture.get("api_calls") or 0),
        "total_tokens": int(capture.get("total_tokens") or 0),
        "tool_name": capture.get("tool_name"),
        "tool_calls": bool(capture.get("tool_calls") or capture.get("tool_name")),
    }


def test_live_prefetch_and_digest_follow_chat(tmp_path, monkeypatch):
    """Prove follow-chat + .env URL; record prefetch vs digest and MiniMax tool formats.

    REAL_LLM_TEST=1 also smokes digest-phase1, wrapup, weekly, and monthly oneshots
    so an Anthropic chat base_url cannot silently break OpenAI worker /v1.
    """
    if os.environ.get("REAL_LLM_TEST", "").strip().lower() in {"0", "false", "no"}:
        pytest.skip("REAL_LLM_TEST=0")
    live_ok = os.environ.get("REAL_LLM_TEST", "").strip() in {"1", "true", "yes"}
    typed_ok = os.environ.get("HERMES_DIGEST_LIVE_LLM", "").strip() in {"1", "true", "yes"}
    if not live_ok and not typed_ok:
        pytest.skip("Set REAL_LLM_TEST=1 to run live prefetch/digest/weekly/monthly smoke")
    live_home = Path(__file__).resolve().parents[3]
    _load_live_dotenv(live_home)
    src_cfg = live_home / "config.yaml"
    import yaml

    raw_live = yaml.safe_load(src_cfg.read_text(encoding="utf-8")) or {}
    model_bag = raw_live.get("model") if isinstance(raw_live, dict) else {}
    yaml_url = str((model_bag or {}).get("base_url") or "").strip()
    key = (
        (os.environ.get("MINIMAX_CN_API_KEY") or "").strip()
        or (os.environ.get("MINIMAX_API_KEY") or "").strip()
    )
    url = (os.environ.get("MINIMAX_CN_BASE_URL") or "").strip()
    if not key or not url:
        pytest.skip("missing MiniMax API key or base URL")
    if "/anthropic" in url.rstrip("/").lower():
        pytest.skip("MINIMAX_CN_BASE_URL is Anthropic; workers need OpenAI /v1")
    if not (os.environ.get("MINIMAX_CN_BASE_URL") or "").strip():
        os.environ["MINIMAX_CN_BASE_URL"] = url
    if not (os.environ.get("MINIMAX_CN_API_KEY") or "").strip():
        os.environ["MINIMAX_CN_API_KEY"] = key

    dest_cfg = tmp_path / "config.yaml"
    shutil.copyfile(src_cfg, dest_cfg)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-09-10.md").write_text(
        "---\nid: mem-live-1\ntype: fact\nentity: Casey\n---\n"
        "Casey decided workers follow chat then overlay then .env.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_CN_API_KEY", key)
    monkeypatch.setenv("MINIMAX_CN_BASE_URL", url)

    raw = yaml.safe_load(dest_cfg.read_text(encoding="utf-8")) or {}
    model_bag = raw.get("model") if isinstance(raw, dict) else {}
    chat_model = str((model_bag or {}).get("default") or "").strip()
    chat_provider = str((model_bag or {}).get("provider") or "").strip()
    assert chat_model == "MiniMax-M3"
    assert chat_provider == "minimax-cn"

    plugin_root = live_home / "plugins" / "MyMemory"
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    if str(plugin_root.parent) not in sys.path:
        sys.path.insert(0, str(plugin_root.parent))

    from MyMemory.digest import digest as digest_mod
    from MyMemory.provider import MyMemoryProvider
    from MyMemory.weekly import weekly as weekly_mod

    monkeypatch.setattr(digest_mod, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(weekly_mod, "on_pre_llm_call", lambda **_: None)

    provider = MyMemoryProvider()
    t0 = time.perf_counter()
    prefetch_text = provider.prefetch(
        "what did we decide about memory", session_id="live-probe"
    )
    prefetch_ms = max(0, int((time.perf_counter() - t0) * 1000))

    import worker_llm as wl

    phase1 = _oneshot_ping(wl, plugin="memory-digest", purpose="digest-phase1")
    wrapup = _oneshot_ping(wl, plugin="memory-digest", purpose="digest-wrapup")
    weekly = _oneshot_ping(wl, plugin="memory-weekly", purpose="weekly")
    monthly = _oneshot_ping(wl, plugin="memory-monthly", purpose="monthly")
    openai_tools_ok = (
        not wrapup["failed"]
        and wrapup["tool_calls"]
        and wrapup["api_calls"] >= 1
        and wrapup["total_tokens"] > 0
    )
    anth = _probe_minimax_anthropic_tool_use(chat_model)
    probe = {
        "model": chat_model,
        "provider": chat_provider,
        "chat_base_url": yaml_url,
        "prefetch_ms": prefetch_ms,
        "prefetch_chars": len(prefetch_text or ""),
        "digest_phase1": phase1,
        "digest_phase2": wrapup,
        "weekly": weekly,
        "monthly": monthly,
        "openai_tools_ok": openai_tools_ok,
        "anthropic_tool_use_ok": bool(anth.get("anthropic_tool_use_ok")),
        "anthropic_http_status": int(anth.get("anthropic_http_status") or 0),
        "anthropic_error": str(anth.get("anthropic_error") or ""),
    }
    dest = tmp_path / "live-probe.json"
    dest.write_text(json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    assert dest.is_file()
    assert openai_tools_ok is True
    assert isinstance(probe["anthropic_tool_use_ok"], bool)
    for lane_name, lane in (
        ("digest_phase1", phase1),
        ("digest_phase2", wrapup),
        ("weekly", weekly),
        ("monthly", monthly),
    ):
        assert lane["failed"] is False, lane_name
        assert lane["api_calls"] >= 1, lane_name
        assert lane["total_tokens"] > 0, lane_name
