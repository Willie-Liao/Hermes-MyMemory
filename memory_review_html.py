"""Static WeCom-round-trip HTML for daily digest + weekly review.

No localhost server. Confirm embeds decisions and attempts auto-send of the
updated HTML file back into WeCom chat (bridge → share → download last resort).
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any

_plugins_root = Path(__file__).resolve().parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from memory_staging import (  # noqa: E402
    daily_staging_dir,
    daily_staging_path,
    hermes_local_now,
    patch_daily_block_status,
    tidy_decisions_path,
    weekly_staging_dir,
)

MARKER = "<!-- hermes-memory-review:v1 -->"
SCHEMA_VERSION = 1
_PAYLOAD_RE = re.compile(
    r'<script\s+type=["\']application/json["\']\s+id=["\']hermes-payload["\']\s*>'
    r"(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _hermes_home() -> Path:
    import os

    raw = (os.environ.get("HERMES_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermes"


def weekly_html_path(hermes_home: Path, week_key: str) -> Path:
    return weekly_staging_dir(hermes_home) / f"{week_key}.html"


def daily_html_path(hermes_home: Path, date_str: str) -> Path:
    return daily_staging_dir(hermes_home) / f"{date_str}.html"


def parse_review_html(source: str | Path) -> dict[str, Any]:
    """Parse a review HTML document into its embedded payload."""
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    else:
        text = source
    if MARKER not in text:
        raise ValueError("missing hermes-memory-review marker")
    match = _PAYLOAD_RE.search(text)
    if not match:
        raise ValueError("missing hermes-payload script")
    raw = match.group(1).strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("hermes-payload must be a JSON object")
    return payload


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_review_html(payload: dict[str, Any]) -> str:
    """Render a self-contained review page (weekly or daily)."""
    mode = str(payload.get("mode") or "weekly")
    week_or_day = str(payload.get("id") or "")
    status = str(payload.get("status") or "open")
    view = payload.get("view") if isinstance(payload.get("view"), dict) else {}
    title = f"Hermes memory review · {week_or_day}"
    gate_note = (
        "Hot MEMORY/USER writes open on agent apply / staging review — "
        "not from this page."
    )

    if mode == "daily":
        body = _render_daily_body(view)
        cta = "Confirm and send"
    else:
        body = _render_weekly_body(view)
        cta = "Confirm and send"

    payload_json = _payload_json(payload)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>
{MARKER}
<style>
:root {{
  --bg:#0f1117; --card:#171a22; --ink:#e7e9ef; --muted:#8b93a7;
  --line:#2a3040; --accent:#4f6ef7; --danger:#d14b4b; --ok:#2f9e6b;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:var(--bg); color:var(--ink); padding:1rem 1rem 6rem;
}}
main {{ max-width:42rem; margin:0 auto; }}
h1 {{ font-size:1.25rem; margin:0 0 .35rem; }}
.sub,.meta {{ color:var(--muted); font-size:.85rem; margin:0 0 1rem; }}
.card {{
  background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:.9rem 1rem; margin:.55rem 0;
}}
.card.promote {{ border-color:#4457b8; }}
.card.discard,.card.reject {{ opacity:.55; border-color:#5a3030; }}
.card.approve {{ border-color:#2f6b4e; }}
.row {{ display:flex; flex-wrap:wrap; gap:.4rem; margin-top:.65rem; }}
.btn {{
  font:inherit; border:1px solid var(--line); background:#12151d; color:var(--ink);
  border-radius:999px; padding:.35rem .75rem; cursor:pointer;
}}
.btn.active {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
.btn.danger.active {{ background:var(--danger); border-color:var(--danger); }}
.btn.ok.active {{ background:var(--ok); border-color:var(--ok); }}
.badge {{
  display:inline-block; font-size:.7rem; text-transform:uppercase; letter-spacing:.04em;
  border:1px solid var(--line); border-radius:999px; padding:.1rem .45rem; color:var(--muted);
}}
textarea,input[type=text] {{
  width:100%; margin-top:.4rem; background:#10131a; color:var(--ink);
  border:1px solid var(--line); border-radius:8px; padding:.45rem .55rem; font:inherit;
}}
.bar {{
  position:fixed; left:0; right:0; bottom:0; background:#12151d; border-top:1px solid var(--line);
  padding:.75rem 1rem;
}}
.bar-inner {{ max-width:42rem; margin:0 auto; display:flex; gap:.75rem; align-items:center; justify-content:space-between; }}
.primary {{
  background:var(--accent); color:#fff; border:0; border-radius:12px;
  padding:.7rem 1.1rem; font-weight:700; cursor:pointer;
}}
.msg {{ margin:.75rem 0; padding:.65rem .8rem; border-radius:8px; font-size:.9rem; }}
.msg.ok {{ background:#143528; color:#b7f0d2; }}
.msg.err {{ background:#3a1a1a; color:#ffc9c9; }}
ul.story {{ padding-left:1.1rem; color:var(--muted); }}
</style>
</head>
<body>
<main>
  <h1>{_esc(title)}</h1>
  <p class="sub">{_esc(gate_note)}</p>
  <p class="meta">mode={_esc(mode)} · status=<span id="status-label">{_esc(status)}</span></p>
  <div id="flash"></div>
  {body}
</main>
<div class="bar">
  <div class="bar-inner">
    <span class="meta" id="counts">Choose Keep / MEMORY / DISCARD, then Confirm.</span>
    <button type="button" class="primary" id="confirm-btn">{_esc(cta)}</button>
  </div>
</div>
<script type="application/json" id="hermes-payload">
{payload_json}
</script>
<script>
(function () {{
  const MARKER = {json.dumps(MARKER)};
  const payloadEl = document.getElementById('hermes-payload');
  let payload = JSON.parse(payloadEl.textContent);
  const mode = payload.mode;
  const flash = document.getElementById('flash');
  const counts = document.getElementById('counts');
  const statusLabel = document.getElementById('status-label');
  const decisions = {{}};

  function show(type, text) {{
    flash.innerHTML = '<div class="msg ' + type + '">' + text + '</div>';
  }}

  function syncCounts() {{
    const vals = Object.values(decisions);
    if (mode === 'daily') {{
      const a = vals.filter(v => v.action === 'approve').length;
      const r = vals.filter(v => v.action === 'reject').length;
      counts.textContent = a + ' approve · ' + r + ' reject';
    }} else {{
      const p = vals.filter(v => v.action === 'promote').length;
      const d = vals.filter(v => v.action === 'discard').length;
      counts.textContent = p + ' promote · ' + d + ' discard';
    }}
  }}

  function setDecision(id, patch) {{
    decisions[id] = Object.assign({{ record_id: id, block_id: id, label: '', source: '', hot_target: '' }}, decisions[id] || {{}}, patch);
    const card = document.querySelector('[data-record-id="' + id + '"]');
    if (card) {{
      card.classList.remove('promote','discard','approve','reject');
      if (patch.action) card.classList.add(patch.action === 'promote' ? 'promote' : patch.action);
      card.querySelectorAll('[data-act]').forEach(btn => {{
        const act = btn.getAttribute('data-act');
        const target = btn.getAttribute('data-target') || '';
        const on = act === patch.action && (act !== 'promote' || target === (patch.hot_target || ''));
        btn.classList.toggle('active', on);
      }});
    }}
    syncCounts();
  }}

  document.body.addEventListener('click', (e) => {{
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const card = btn.closest('[data-record-id]');
    if (!card) return;
    const id = card.getAttribute('data-record-id');
    const act = btn.getAttribute('data-act');
    const target = btn.getAttribute('data-target') || '';
    const label = card.getAttribute('data-label') || '';
    const source = card.getAttribute('data-source') || '';
    const blockId = card.getAttribute('data-block-id') || id;
    if (mode === 'daily') {{
      setDecision(id, {{ action: act, label, block_id: blockId }});
    }} else {{
      setDecision(id, {{
        action: act,
        hot_target: act === 'promote' ? target : '',
        label, source, block_id: blockId
      }});
    }}
  }});

  document.body.addEventListener('input', (e) => {{
    const el = e.target;
    if (!el.matches('[data-edit]')) return;
    const card = el.closest('[data-record-id]');
    if (!card) return;
    const id = card.getAttribute('data-record-id');
    const field = el.getAttribute('data-edit');
    const base = decisions[id] || {{
      record_id: id,
      block_id: card.getAttribute('data-block-id') || id,
      label: card.getAttribute('data-label') || '',
      source: card.getAttribute('data-source') || '',
      action: mode === 'daily' ? 'approve' : 'promote',
      hot_target: mode === 'daily' ? '' : 'MEMORY.md'
    }};
    base[field] = el.value;
    decisions[id] = base;
  }});

  function collectDecisions() {{
    const out = [];
    document.querySelectorAll('[data-record-id]').forEach(card => {{
      const id = card.getAttribute('data-record-id');
      const d = decisions[id];
      if (!d || !d.action) return;
      if (mode === 'daily') {{
        const bodyEl = card.querySelector('[data-edit="body"]');
        out.push({{
          record_id: id,
          block_id: d.block_id || id,
          action: d.action,
          label: d.label || '',
          body: bodyEl ? bodyEl.value : (d.body || '')
        }});
      }} else {{
        const propEl = card.querySelector('[data-edit="proposed_text"]');
        out.push({{
          record_id: id,
          block_id: d.block_id || id,
          action: d.action,
          hot_target: d.hot_target || '',
          label: d.label || '',
          source: d.source || '',
          proposed_text: propEl ? propEl.value : (d.proposed_text || '')
        }});
      }}
    }});
    return out;
  }}

  function serializeDocument() {{
    payload.status = 'confirmed';
    payload.decisions = collectDecisions();
    payloadEl.textContent = '\\n' + JSON.stringify(payload, null, 2) + '\\n';
    statusLabel.textContent = 'confirmed';
    let doc = '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
    if (!doc.includes(MARKER)) {{
      doc = doc.replace('</title>', '</title>\\n' + MARKER);
    }}
    return doc;
  }}

  async function tryWeComSend(file) {{
    // Desktop/mobile WeCom webview bridges (spike order).
    try {{
      if (typeof ww !== 'undefined' && ww.invoke) {{
        await new Promise((resolve, reject) => {{
          ww.invoke('sendChatMessage', {{ msgtype: 'file', file }}, (res) => {{
            if (res && (res.err_msg === 'sendChatMessage:ok' || res.errMsg === 'sendChatMessage:ok')) resolve(res);
            else reject(new Error((res && (res.err_msg || res.errMsg)) || 'ww.sendChatMessage failed'));
          }});
        }});
        return true;
      }}
    }} catch (err) {{ /* continue */ }}
    try {{
      if (typeof WeixinJSBridge !== 'undefined' && WeixinJSBridge.invoke) {{
        await new Promise((resolve, reject) => {{
          WeixinJSBridge.invoke('sendChatMessage', {{ msgtype: 'file' }}, (res) => {{
            if (res && String(res.err_msg || '').indexOf(':ok') >= 0) resolve(res);
            else reject(new Error('WeixinJSBridge send failed'));
          }});
        }});
        return true;
      }}
    }} catch (err) {{ /* continue */ }}
    return false;
  }}

  async function tryShare(file) {{
    if (!navigator.share || !navigator.canShare) return false;
    try {{
      if (!navigator.canShare({{ files: [file] }})) return false;
      await navigator.share({{ files: [file], title: file.name }});
      return true;
    }} catch (err) {{
      if (err && err.name === 'AbortError') return false;
      return false;
    }}
  }}

  function downloadFallback(file, text) {{
    const blob = new Blob([text], {{ type: 'text/html;charset=utf-8' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }}

  document.getElementById('confirm-btn').addEventListener('click', async () => {{
    const text = serializeDocument();
    const name = (payload.id || 'review') + '.confirmed.html';
    const file = new File([text], name, {{ type: 'text/html' }});
    try {{
      if (await tryWeComSend(file)) {{
        show('ok', 'Sent — waiting for agent');
        return;
      }}
      if (await tryShare(file)) {{
        show('ok', 'Sent — waiting for agent');
        return;
      }}
      downloadFallback(file, text);
      show('err', 'Auto-send unavailable here. Downloaded ' + name + ' — send that file back in WeCom chat.');
    }} catch (err) {{
      show('err', (err && err.message) ? err.message : String(err));
    }}
  }});

  syncCounts();
}})();
</script>
</body>
</html>
"""


def _render_weekly_body(view: dict[str, Any]) -> str:
    story = view.get("story") or []
    needs = view.get("needs") or []
    remember = view.get("remember") or []
    story_html = "".join(f"<li>{_esc(s)}</li>" for s in story) or "<li class='meta'>No story lines.</li>"
    needs_html = "".join(f"<li>{_esc(n)}</li>" for n in needs) or "<li class='meta'>Nothing needs you.</li>"
    cards = []
    for item in remember:
        rid = str(item.get("record_id") or "")
        block_id = str(item.get("block_id") or rid)
        label = str(item.get("label") or item.get("text") or "")
        text = str(item.get("text") or label)
        section = str(item.get("section") or "")
        typ = str(item.get("type") or "")
        cards.append(
            f"""<article class="card" data-record-id="{_esc(rid)}" data-block-id="{_esc(block_id)}"
 data-label="{_esc(label)}" data-source="{_esc(section)}">
  <p><span class="badge">{_esc(typ or 'item')}</span> <span class="meta">{_esc(section)}</span></p>
  <p>{_esc(text)}</p>
  <div class="row">
    <button type="button" class="btn" data-act="promote" data-target="MEMORY.md">MEMORY</button>
    <button type="button" class="btn" data-act="promote" data-target="USER.md">USER</button>
    <button type="button" class="btn danger" data-act="discard">DISCARD</button>
  </div>
  <label class="meta">Hot bullet</label>
  <textarea data-edit="proposed_text" rows="2">{_esc(text)}</textarea>
</article>"""
        )
    return (
        f"<h2>What happened</h2><ul class='story'>{story_html}</ul>"
        f"<h2>Needs you</h2><ul class='story'>{needs_html}</ul>"
        f"<h2>Remember?</h2>{''.join(cards) or '<p class=\"meta\">No remember candidates.</p>'}"
    )


def _render_daily_body(view: dict[str, Any]) -> str:
    blocks = view.get("blocks") or []
    cards = []
    for item in blocks:
        rid = str(item.get("id") or item.get("record_id") or "")
        body = str(item.get("body") or "")
        typ = str(item.get("type") or "")
        status = str(item.get("status") or "candidate")
        cards.append(
            f"""<article class="card" data-record-id="{_esc(rid)}" data-block-id="{_esc(rid)}"
 data-label="{_esc(body[:80])}">
  <p><span class="badge">{_esc(typ)}</span> <span class="meta">{_esc(status)}</span></p>
  <div class="row">
    <button type="button" class="btn ok" data-act="approve">KEEP</button>
    <button type="button" class="btn danger" data-act="reject">DISCARD</button>
  </div>
  <p class="meta">Hot promote is a weekly step.</p>
  <textarea data-edit="body" rows="3">{_esc(body)}</textarea>
</article>"""
        )
    return f"<h2>Daily candidates</h2>{''.join(cards) or '<p class=\"meta\">No blocks.</p>'}"


def _load_weekly_tidy() -> Any:
    import importlib.util

    tidy_path = _plugins_root / "weekly" / "weekly_tidy.py"
    spec = importlib.util.spec_from_file_location("memory_weekly_tidy_html", tidy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("weekly_tidy.py not loadable")
    weekly_tidy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(weekly_tidy)
    return weekly_tidy


def build_weekly_payload(week_key: str, *, hermes_home: Path | None = None) -> dict[str, Any]:
    home = hermes_home or _hermes_home()
    weekly_tidy = _load_weekly_tidy()
    candidates = weekly_tidy.parse_weekly_candidates(week_key)
    remember = []
    for cand in candidates:
        remember.append(
            {
                "record_id": cand.get("record_id") or "",
                "block_id": cand.get("block_id") or "",
                "label": cand.get("label") or "",
                "section": cand.get("section") or "",
                "text": cand.get("label") or "",
                "type": "",
            }
        )
    story: list[str] = []
    needs: list[str] = []
    md_path = _weekly_md_path(home, week_key)
    if md_path and md_path.exists():
        story, needs = _extract_story_needs(md_path.read_text(encoding="utf-8"))

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "weekly",
        "id": week_key,
        "generated_at": hermes_local_now().isoformat(),
        "status": "open",
        "view": {"story": story[:5], "needs": needs[:5], "remember": remember[:20]},
        "decisions": [],
    }


def _weekly_md_path(home: Path, week_key: str) -> Path | None:
    from memory_staging import parse_week_key, resolve_weekly_path

    if parse_week_key(week_key) is None:
        return None
    return resolve_weekly_path(home, week_key)


def _extract_story_needs(text: str) -> tuple[list[str], list[str]]:
    story: list[str] = []
    needs: list[str] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^##\s*0\.\s*Scope", stripped, re.I):
            section = "scope"
            continue
        if re.match(r"^##\s*2\.\s*Hypotheses", stripped, re.I):
            section = "hyp"
            continue
        if re.match(r"^##\s*4\.\s*Conflicts", stripped, re.I):
            section = "conflict"
            continue
        if stripped.startswith("## "):
            section = ""
            continue
        if not stripped.startswith("- "):
            if section == "scope" and stripped and not stripped.startswith("#"):
                story.append(stripped)
            continue
        body = stripped[2:].strip()
        if section in ("hyp", "conflict"):
            cf = body.casefold()
            if "no other hypothesis" in cf or "no conflicts" in cf or "none this week" in cf:
                continue
            needs.append(body)
        elif section == "scope":
            story.append(body)
    return story, needs


def build_daily_payload(date_str: str, *, hermes_home: Path | None = None) -> dict[str, Any]:
    home = hermes_home or _hermes_home()
    path = daily_staging_path(home, date_str)
    blocks: list[dict[str, Any]] = []
    if path.exists():
        digest = _load_digest()
        text = path.read_text(encoding="utf-8")
        for _line_no, raw_fm, body in digest._frontmatter_blocks(text):
            try:
                import yaml

                parsed = yaml.safe_load(raw_fm)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            blocks.append(
                {
                    "id": str(parsed.get("id") or ""),
                    "type": str(parsed.get("type") or ""),
                    "status": str(parsed.get("status") or "candidate"),
                    "confidence": str(parsed.get("confidence") or ""),
                    "body": (body or "").strip(),
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "daily",
        "id": date_str,
        "generated_at": hermes_local_now().isoformat(),
        "status": "open",
        "view": {"blocks": blocks},
        "decisions": [],
    }


def _load_digest() -> Any:
    import importlib.util

    path = _plugins_root / "digest" / "digest.py"
    spec = importlib.util.spec_from_file_location("memory_digest_for_review_html", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("digest.py not loadable")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def write_weekly_html(week_key: str, *, hermes_home: Path | None = None) -> Path | None:
    home = hermes_home or _hermes_home()
    if _weekly_md_path(home, week_key) is None:
        return None
    payload = build_weekly_payload(week_key, hermes_home=home)
    path = weekly_html_path(home, week_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_review_html(payload), encoding="utf-8")
    return path


def write_daily_html(date_str: str, *, hermes_home: Path | None = None) -> Path | None:
    home = hermes_home or _hermes_home()
    md = daily_staging_path(home, date_str)
    if not md.exists():
        return None
    payload = build_daily_payload(date_str, hermes_home=home)
    path = daily_html_path(home, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_review_html(payload), encoding="utf-8")
    return path


def apply_review_html(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"outcome": "missing", "path": str(p)}
    try:
        payload = parse_review_html(p)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        return {"outcome": "invalid", "error": str(exc), "path": str(p)}
    return apply_review_payload(payload, hermes_home=_hermes_home())


def apply_review_html_text(text: str, *, hermes_home: Path | None = None) -> dict[str, Any]:
    try:
        payload = parse_review_html(text)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"outcome": "invalid", "error": str(exc)}
    return apply_review_payload(payload, hermes_home=hermes_home or _hermes_home())


def apply_review_payload(
    payload: dict[str, Any], *, hermes_home: Path | None = None
) -> dict[str, Any]:
    home = hermes_home or _hermes_home()
    if str(payload.get("status") or "") != "confirmed":
        return {"outcome": "not_confirmed", "id": payload.get("id")}
    mode = str(payload.get("mode") or "")
    if mode == "weekly":
        return _apply_weekly(payload, home)
    if mode == "daily":
        return _apply_daily(payload, home)
    return {"outcome": "bad_mode", "mode": mode}


def _apply_weekly(payload: dict[str, Any], home: Path) -> dict[str, Any]:
    week = str(payload.get("id") or "")
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    tidy_path = tidy_decisions_path(home, week)
    tidy_path.parent.mkdir(parents=True, exist_ok=True)
    tidy_path.write_text(
        json.dumps({"week": week, "decisions": decisions}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Confirm finalizes the week: set week_status reviewed before decision apply.
    from memory_staging import (
        mark_week_reviewed,
        parse_week_key,
        patch_daily_block_status,
        week_file_path,
        week_is_reviewed,
    )

    parsed = parse_week_key(week)
    year = week_no = None
    if parsed is not None:
        year, week_no = parsed
        if not week_is_reviewed(home, year, week_no):
            mark_week_reviewed(home, week)

    patched = 0
    for row in decisions:
        if not isinstance(row, dict):
            continue
        block_id = str(row.get("block_id") or row.get("record_id") or "").strip()
        action = str(row.get("action") or "").strip().casefold()
        if not block_id:
            continue
        if action in {"promote", "approve"}:
            ok = patch_daily_block_status(
                home,
                block_id,
                status="approved",
                timestamp_field="promoted_at",
            )
        elif action in {"discard", "reject"}:
            ok = patch_daily_block_status(
                home,
                block_id,
                status="rejected",
                timestamp_field="discarded_at",
            )
        else:
            continue
        if ok:
            patched += 1

    if tidy_path.exists():
        try:
            tidy_path.unlink()
        except OSError:
            pass

    path_str = ""
    if year is not None and week_no is not None:
        path_str = str(week_file_path(home, year, week_no))

    return {
        "outcome": "applied",
        "week": week,
        "decisions": len(decisions),
        "patched": patched,
        "path": path_str,
        "html_mode": "weekly",
    }


def _apply_daily(payload: dict[str, Any], home: Path) -> dict[str, Any]:
    date_str = str(payload.get("id") or "")
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    patched = 0
    for row in decisions:
        if not isinstance(row, dict):
            continue
        block_id = str(row.get("block_id") or row.get("record_id") or "").strip()
        action = str(row.get("action") or "").strip()
        if not block_id or action not in ("approve", "reject"):
            continue
        status = "approved" if action == "approve" else "rejected"
        ts_field = "approved_at" if action == "approve" else "rejected_at"
        body = row.get("body")
        ok = False
        if isinstance(body, str) and body.strip():
            ok = _patch_daily_block(home, block_id, status=status, timestamp_field=ts_field, body=body)
        else:
            ok = patch_daily_block_status(
                home, block_id, status=status, timestamp_field=ts_field
            )
        if ok:
            patched += 1
    return {
        "outcome": "applied",
        "html_mode": "daily",
        "id": date_str,
        "patched": patched,
        "decisions": len(decisions),
    }


def _patch_daily_block(
    hermes_home: Path,
    block_id: str,
    *,
    status: str,
    timestamp_field: str,
    body: str,
) -> bool:
    import yaml

    digest = _load_digest()
    daily_dir = daily_staging_dir(hermes_home)
    from memory_staging import iter_daily_staging_files

    ts = hermes_local_now().isoformat()
    for path in iter_daily_staging_files(daily_dir):
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rendered: list[str] = []
        replaced = False
        for _line_no, raw_fm, block_body in digest._frontmatter_blocks(original):
            try:
                parsed = yaml.safe_load(raw_fm)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict):
                continue
            if str(parsed.get("id", "")).strip() != block_id:
                rendered.append(digest._render_digest_block(parsed, block_body))
                continue
            parsed["status"] = status
            parsed[timestamp_field] = ts
            rendered.append(digest._render_digest_block(parsed, body.strip() + "\n"))
            replaced = True
        if replaced:
            path.write_text("\n\n".join(rendered).rstrip() + "\n", encoding="utf-8")
            return True
    return False
