import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  parseTunnelPid,
  resolveTunnelPidPath,
  stopTrackedTunnel,
  createUiLifecycle,
} from './uiLifecycle.ts';

describe('ui lifecycle', () => {
  it('parseTunnelPid accepts integer pid text', () => {
    assert.equal(parseTunnelPid('4242\n'), 4242);
    assert.equal(parseTunnelPid('  99  '), 99);
    assert.equal(parseTunnelPid(''), null);
    assert.equal(parseTunnelPid('abc'), null);
    assert.equal(parseTunnelPid('1.5'), null);
  });

  it('resolveTunnelPidPath uses HERMES_HOME cache', () => {
    const home = '/tmp/fake-hermes';
    assert.equal(
      resolveTunnelPidPath(home),
      path.join(home, 'cache', 'weekly-ui-tunnel.pid'),
    );
  });

  it('stopTrackedTunnel kills only recorded pid and clears file', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'weekly-ui-life-'));
    const pidPath = path.join(dir, 'weekly-ui-tunnel.pid');
    fs.writeFileSync(pidPath, '6161\n', 'utf8');
    const killed: Array<{ pid: number; signal: string }> = [];

    const result = stopTrackedTunnel({
      pidPath,
      isAlive: (pid) => pid === 6161,
      kill: (pid, signal) => {
        killed.push({ pid, signal: String(signal) });
      },
    });

    assert.equal(result.stopped, true);
    assert.equal(result.pid, 6161);
    assert.deepEqual(killed, [{ pid: 6161, signal: 'SIGTERM' }]);
    assert.equal(fs.existsSync(pidPath), false);
  });

  it('stopTrackedTunnel is a no-op when pid missing or dead', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'weekly-ui-life-'));
    const pidPath = path.join(dir, 'weekly-ui-tunnel.pid');
    fs.writeFileSync(pidPath, '7000\n', 'utf8');
    const killed: number[] = [];

    const dead = stopTrackedTunnel({
      pidPath,
      isAlive: () => false,
      kill: (pid) => {
        killed.push(pid);
      },
    });
    assert.equal(dead.stopped, false);
    assert.equal(killed.length, 0);
    assert.equal(fs.existsSync(pidPath), false);

    const missing = stopTrackedTunnel({
      pidPath: path.join(dir, 'missing.pid'),
      kill: (pid) => {
        killed.push(pid);
      },
    });
    assert.equal(missing.stopped, false);
    assert.equal(killed.length, 0);
  });

  it('createUiLifecycle shares deadline and shuts down once on idle', () => {
    let now = 1_000;
    const events: string[] = [];
    const life = createUiLifecycle({
      now: () => now,
      idleTimeoutMs: 5_000,
      onShutdown: (reason) => {
        events.push(reason);
      },
      stopTunnel: () => {
        events.push('tunnel');
      },
    });

    assert.equal(life.touch(), 1_000);
    now = 5_999;
    assert.equal(life.checkIdle(), false);
    now = 6_000;
    assert.equal(life.checkIdle(), true);
    assert.deepEqual(events, ['tunnel', 'idle']);
    // Idempotent: second idle/manual shutdown does not re-fire.
    assert.equal(life.shutdown('manual'), false);
    assert.deepEqual(events, ['tunnel', 'idle']);
  });

  it('activity touch resets shared idle deadline', () => {
    let now = 0;
    const events: string[] = [];
    const life = createUiLifecycle({
      now: () => now,
      idleTimeoutMs: 10_000,
      onShutdown: (reason) => events.push(reason),
      stopTunnel: () => events.push('tunnel'),
    });

    now = 9_000;
    life.touch();
    now = 18_999;
    assert.equal(life.checkIdle(), false);
    now = 19_000;
    assert.equal(life.checkIdle(), true);
    assert.deepEqual(events, ['tunnel', 'idle']);
  });
});
