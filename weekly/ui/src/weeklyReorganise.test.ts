import { describe, expect, it, vi } from 'vitest';
import { runReorganiseSequence } from './weeklyReorganise';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('runReorganiseSequence', () => {
  it('calls digest/run only (Phase-2), not weekly/generate', async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, body });
      if (url === '/api/digest/run') {
        return jsonResponse(200, {
          outcome: 'rewritten',
          path: '/tmp/staging/daily/2026-07-27.md',
          date: '2026-07-27',
        });
      }
      return jsonResponse(500, { error: `unexpected ${url}` });
    });

    const result = await runReorganiseSequence(fetchImpl, {
      date: '2026-07-27',
      week: '2026-W31',
    });

    expect(calls.map((c) => c.url)).toEqual(['/api/digest/run']);
    expect(calls[0]?.body).toEqual({ date: '2026-07-27', wait: false });
    expect(result).toEqual({
      pathLabel: '/tmp/staging/daily/2026-07-27.md',
      week: '2026-W31',
      digestOutcome: 'rewritten',
    });
  });

  it('does not call generate when digest/run fails', async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (url: string) => {
      calls.push(url);
      if (url === '/api/digest/run') {
        return jsonResponse(502, { error: 'digest failed' });
      }
      return jsonResponse(200, { outcome: 'generated' });
    });

    await expect(
      runReorganiseSequence(fetchImpl, { date: '2026-07-27', week: '2026-W31' }),
    ).rejects.toMatchObject({
      message: 'digest failed',
      stage: 'digest',
    });
    expect(calls).toEqual(['/api/digest/run']);
  });

  it('treats in_flight as a started background job', async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      expect(url).toBe('/api/digest/run');
      return jsonResponse(202, {
        outcome: 'in_flight',
        path: '/tmp/staging/daily/2026-07-27.md',
        date: '2026-07-27',
      });
    });
    const result = await runReorganiseSequence(fetchImpl, {
      date: '2026-07-27',
      week: '2026-W31',
    });
    expect(result.digestOutcome).toBe('in_flight');
  });

  it('never calls the obsolete resummarise endpoint', async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      expect(url).not.toContain('resummarise');
      return jsonResponse(200, {
          outcome: 'rewritten',
          path: '2026-07-27.md',
        });
    });

    await runReorganiseSequence(fetchImpl, {
      date: '2026-07-27',
      week: '2026-W31',
    });
    expect(fetchImpl).toHaveBeenCalled();
  });
});
