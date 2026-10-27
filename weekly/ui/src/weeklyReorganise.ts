/**
 * Browser-safe Weekly UI Reorganise: Phase-2 consolidate for the active date.
 * Does not regenerate the weekly brief (use Rescan).
 */
export type ReorganiseFetch = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

export type ReorganiseSequenceResult = {
  pathLabel: string;
  week: string;
  digestOutcome: string;
};

export type ReorganiseSequenceError = {
  message: string;
  stage: 'digest';
};

function jsonBody(data: unknown): string {
  return JSON.stringify(data);
}

async function readJson(res: Response): Promise<Record<string, unknown>> {
  return (await res.json().catch(() => ({}))) as Record<string, unknown>;
}

/**
 * POST /api/digest/run (oneshot Phase-2 on the daily file).
 */
export async function runReorganiseSequence(
  fetchImpl: ReorganiseFetch,
  opts: { date: string; week: string },
): Promise<ReorganiseSequenceResult> {
  const digestRes = await fetchImpl('/api/digest/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: jsonBody({ date: opts.date, wait: false }),
  });
  const digestData = await readJson(digestRes);
  if (digestData.outcome === 'in_flight' || digestRes.status === 202) {
    const pathLabel =
      typeof digestData.path === 'string' && digestData.path
        ? digestData.path
        : `${opts.date}.md`;
    return {
      pathLabel,
      week: opts.week,
      digestOutcome: 'in_flight',
    };
  }
  if (!digestRes.ok) {
    const err: ReorganiseSequenceError = {
      stage: 'digest',
      message:
        typeof digestData.error === 'string'
          ? digestData.error
          : "Failed to reorganise this day's staging blocks.",
    };
    throw Object.assign(new Error(err.message), err);
  }

  const pathLabel =
    typeof digestData.path === 'string' && digestData.path
      ? digestData.path
      : `${opts.date}.md`;
  return {
    pathLabel,
    week: opts.week,
    digestOutcome:
      typeof digestData.outcome === 'string' ? digestData.outcome : 'ok',
  };
}
