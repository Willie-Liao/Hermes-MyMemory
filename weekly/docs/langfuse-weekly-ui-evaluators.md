# Langfuse evaluators — Weekly UI latency suite

Operator checklist for project **Hermes Local**. This doc configures **Running Evaluators** only; it does not add Python evaluator APIs and does not modify `HermesPromptCompliance`.

## Prerequisites

- Local Langfuse running (`langfuse-local` stack; web at `http://localhost:3000`).
- `HERMES_LANGFUSE_*` keys in `hermes-home/.env`.
- `observability/langfuse` enabled in `hermes-home/config.yaml`.
- Weekly UI latency suite (Tasks 2–3) stamps traces via env vars set on each bridge spawn.

### Trace tags and metadata (from suite)

When the suite runs, each op sets:

| Env var | Trace effect |
|---------|----------------|
| `HERMES_SUITE_WEEKLY_UI=1` | Tag `weekly_ui`; metadata `weekly_ui: true` |
| `HERMES_SUITE_OP=<op>` | Tag `op_<op>`; metadata `op` |
| `HERMES_SUITE_RUN_ID=<uuid>` | metadata `suite_run_id` |

Ops (defaults): `rescan` (Weekly UI Rescan), `reorganise` (Weekly UI Reorganise = resummarise + generate).

Optional low-level bridge names still work if listed in config: `generate_week`, `tighten_hot_entry`, `hot_health`, `chronicle`, `request_resummarise`.

For `reorganise`, each half also sets `HERMES_SUITE_STAGE=request_resummarise|generate_week` (suite report `stages` always has both ms; Langfuse may only tag `op_reorganise` unless the plugin maps `HERMES_SUITE_STAGE`).

LLM generations under each Hermes turn already carry `api_duration_s` in Langfuse Tracing. E2E wall-clock for the whole bridge call is in the suite report (`e2e_ms` per row); v1 does not require `HERMES_SUITE_E2E_MS` on the trace.

### Latency budgets (from `suite_config.example.yaml`)

| Op | E2E budget |
|----|------------|
| `rescan` | 120 000 ms (120 s) |
| `reorganise` | 240 000 ms (240 s) — covers both halves |

Tune in `scripts/suite_config.yaml` (`budgets_ms`); keep evaluator prompts or rules aligned if you change defaults.

---

## 1. Open Evaluators

Go to:

**http://localhost:3000/project/hermes-local/evals**

## 2. Confirm default judge model

Before creating evaluators, confirm the project default judge model is **minimax-cn / MiniMax-M3** (via the eval proxy). New LLM-as-judge evaluators inherit this unless you override per evaluator.

## 3. Set up evaluator: `weekly_ui_latency`

1. Click **Set up evaluator** (or **New evaluator**).
2. **Score name:** `weekly_ui_latency`
3. **Filter** (trace must match):
   - Metadata `weekly_ui` = `true`, **or**
   - Tag `weekly_ui`
4. **Scoring method** (prefer rule-based if the UI exposes duration fields):
   - **Rule / duration (preferred):** If Langfuse lets you score on observation duration or generation `api_duration_s`, compare against the budget for `metadata.op` (table above). Score **1** if under budget, **0** if over.
   - **LLM-as-judge (fallback):** Use when rule access to timing metadata is insufficient. Example prompt:

   ```
   You score Weekly UI latency for Hermes suite traces.

   Inputs:
   - Trace metadata: weekly_ui, op, suite_run_id (if present)
   - Generation observations: api_duration_s per LLM call
   - E2E timing may appear in metadata as e2e_ms when set

   Budget table (milliseconds, by op):
   - rescan: 120000
   - reorganise: 240000

   Use metadata.op to select the budget. Prefer e2e_ms when present; otherwise
   use the root observation duration or sum of generation api_duration_s as E2E proxy.
   For op=reorganise, two Hermes turns share the same suite_run_id (resummarise then generate).

   Return 1 if under budget, 0 if over budget or op is missing/unknown.
   ```

5. Save the evaluator (leave **Inactive** until step 5 below if you want to test tracing first).

## 4. Set up evaluator: `weekly_ui_quality`

1. Click **Set up evaluator** again.
2. **Score name:** `weekly_ui_quality`
3. **Filter:** same as `weekly_ui_latency` (metadata `weekly_ui` = `true` or tag `weekly_ui`).
4. **Scoring method:** LLM-as-judge (PromptCompliance-style, scoped to suite-tagged Weekly UI turns). Example prompt:

   ```
   You score Weekly UI output quality for Hermes memory-weekly / memory-digest workers.

   Judge the assistant outputs in this trace (Event-First weekly markdown,
   Distill YAML when present, tighten text, etc.).

   Event-First weekly reviews must expose these four sections (when the op
   produces a weekly brief):

   1. Weekly Brief by date (Mon–Sun): each day is
      "<Weekday> — YYYY-MM-DD · Events [N]…" then one **named** plain paragraph
      per event (``**summary title**`` + concise body, blank-line separated; no
      Beginning/Course/Outcome labels), or exactly "No record for this day." when empty.
   2. Conflict
   3. Hypothesis
   4. Possible overdue report — always `- None.` in Brief. Weekly UI Possible
      overdue comes only from memory-digest ``validate_weekly_spans``
      (explicit|high ``mem-*``); Worker 1 must not invent Brief overdue rows.

   Criteria (score 1 only if all relevant checks pass for this op; else 0):

   - rescan / generate_week / reorganise generate half: four sections present and
     ordered as above; Mon–Sun coverage; empty days use "No record for this day.";
     Events markers only where events exist; Brief overdue is `- None.`;
     citations grounded in source digests; no fabricated facts or invented events.
   - Legacy Distill/Brief (older weeks only): valid YAML/structure when Distill is
     present; themes reflect distilled content; no hallucinated themes.
   - reorganise / request_resummarise: daily staging rewrite stays consistent with
     inputs; no policy violations or obvious hallucinations.

   Return 1 if compliant, 0 if not.
   ```

   Align intent with existing `HermesPromptCompliance`; do **not** edit that evaluator.

5. Save the evaluator.

### Event-First four-section reference (fixture)

Golden layout: `fixtures/weekly_four_part_w31.md`. Latency suite default week is
`2026-W31` (`scripts/suite_config.yaml`). After `rescan` / `reorganise`, inspect
`$HERMES_HOME/memories/weekly/` (or the week file the UI loads) for the four
section titles and the overdue span visibility rule above.

## 5. Activate both evaluators

On the Evaluators list, set **Active** for:

- `weekly_ui_latency`
- `weekly_ui_quality`

Leave `HermesPromptCompliance` unchanged (may stay Inactive).

## 6. Do not edit `HermesPromptCompliance`

Weekly UI suite scoring is separate. Adding or changing `HermesPromptCompliance` is out of scope for v1.

---

## Run the suite

```bash
export HERMES_HOME=...   # path to hermes-home
cp hermes-home/plugins/memory-weekly/scripts/suite_config.example.yaml \
   hermes-home/plugins/memory-weekly/scripts/suite_config.yaml
# edit fixtures (week_key, digest_date, tighten text, etc.)
python3 hermes-home/plugins/memory-weekly/scripts/weekly_ui_latency_suite.py \
  --config hermes-home/plugins/memory-weekly/scripts/suite_config.yaml
```

Report default: `$HERMES_HOME/metrics/weekly-ui-latency-last.json` (plus sibling `.md` table).

---

## 7. Verification

1. Run the suite once (command above).
2. **Tracing:** Langfuse → Tracing → filter tag `weekly_ui` or metadata `weekly_ui: true`. Expect one Hermes turn per configured op; nested LLM calls show `api_duration_s`.
3. **Scores:** After evaluators are Active, new suite traces should get scores `weekly_ui_latency` and `weekly_ui_quality` on the trace or observation (exact placement depends on Langfuse UI version).
4. **Report:** Confirm `weekly-ui-latency-last.json` has one row per op with `e2e_ms`, `budget_ms`, `over_budget`.

If traces appear but Scores stay empty: confirm both evaluators are **Active**, filter matches `weekly_ui`, and the default judge model / eval proxy is reachable.

### Verified 2026-07-31 (Task 5 smoke)

- Langfuse web `http://localhost:3000` returned HTTP 200; `langfuse-local-langfuse-web-1` running.
- `HERMES_LANGFUSE_*` present in `hermes-home/.env` (5 keys); `observability/langfuse` in `config.yaml` plugins.enabled.
- Suite run (`HERMES_HOME=hermes-home`): `hot_health` ok (2.0 s, likely hash-skip, no trace); `tighten_hot_entry` ok after fixture fix (29.2 s E2E, under 30 s budget).
- Langfuse API: 1 trace tagged `weekly_ui` / `op_tighten_hot_entry`; nested `LLM call 1` had `api_duration_s: 3.64`.
- Scores empty on that trace — **Scores empty until evaluators Active** (not activated in this smoke run).

### Event-First verify (Step 6)

When scoring or manually reviewing `rescan` / `reorganise` for week `2026-W31` (or the week in `suite_config.yaml`):

1. Confirm report rows for both ops with `e2e_ms`, `stages`, and `over_budget`.
2. Open the generated weekly markdown under `$HERMES_HOME/memories/staging/weekly/` (or UI Chronicle payload) and check the four sections + span visibility rule above.
3. Offline fallback: parse `fixtures/weekly_four_part_w31.md` if the live LLM suite cannot run.

#### Live W31 run (2026-08-02)

- Config: `scripts/suite_config.yaml` (`week_key: 2026-W31`, ops `rescan` + `reorganise`).
- Report: `$HERMES_HOME/metrics/weekly-ui-latency-last.{json,md}` (`suite_run_id` `2ac91832-…`).
- `rescan`: ok, e2e ≈ 48.7 s, stage `generate_week_ms`, under 120 s budget.
- `reorganise`: ok, e2e ≈ 107.8 s, stages `request_resummarise_ms` + `generate_week_ms`, under 240 s budget.
- Generated `memories/staging/weekly/2026-W31.md` includes Event-First four sections (Weekly Brief Mon–Sun with named plain event paragraphs / `No record for this day.`, Conflict, Hypothesis, Possible overdue report as `- None.`).

---

## Related

- Suite script: `scripts/weekly_ui_latency_suite.py`
- Example config: `scripts/suite_config.example.yaml`
- Design: `docs/superpowers/specs/2026-07-31-weekly-ui-langfuse-latency-eval-design.md` (repo root)
