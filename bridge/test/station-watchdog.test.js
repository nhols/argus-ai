import assert from 'node:assert/strict';
import test from 'node:test';

process.env.HOMEBASE_SN = 'homebase-test';
process.env.DOORBELL_SN = 'doorbell-test';
process.env.VID_ANALYSER_API_URL = 'http://analyser.test';
process.env.VID_ANALYSER_API_KEY = 'test-key';

const { StationWatchdog } = await import('../src/station-watchdog.js');
const { QueryPoller } = await import('../src/query-poller.js');

const flush = () => new Promise((r) => setImmediate(r));

function watchdog(options = {}) {
  const sent = [];
  let clock = 0;
  const dog = new StationWatchdog((command, params) => sent.push({ command, ...params }), {
    timeoutThreshold: 3,
    cooldownMs: 1000,
    stationAttempts: 2,
    delay: async () => {},
    now: () => clock,
    ...options,
  });
  return { dog, sent, tick: (ms) => { clock += ms; } };
}

test('does nothing until the timeout threshold is reached', async () => {
  const { dog, sent } = watchdog();
  dog.onQueryTimeout();
  dog.onQueryTimeout();
  await flush();
  assert.deepEqual(sent, []);
});

test('reconnects the homebase station after repeated timeouts', async () => {
  const { dog, sent } = watchdog();
  for (let i = 0; i < 3; i += 1) dog.onQueryTimeout();
  await flush();
  assert.deepEqual(sent, [
    { command: 'station.is_connected', serialNumber: 'homebase-test' },
    { command: 'station.disconnect', serialNumber: 'homebase-test' },
    { command: 'station.connect', serialNumber: 'homebase-test' },
  ]);
});

test('waits for the cooldown before trying again', async () => {
  const { dog, sent, tick } = watchdog();
  for (let i = 0; i < 6; i += 1) dog.onQueryTimeout();
  await flush();
  assert.equal(sent.length, 3);

  tick(1000);
  dog.onQueryTimeout();
  await flush();
  assert.equal(sent.length, 6);
});

test('escalates to a driver reconnect when station reconnects do not help', async () => {
  const { dog, sent, tick } = watchdog();
  for (let attempt = 0; attempt < 2; attempt += 1) {
    for (let i = 0; i < 3; i += 1) dog.onQueryTimeout();
    await flush();
    tick(1000);
  }
  sent.length = 0;

  for (let i = 0; i < 3; i += 1) dog.onQueryTimeout();
  await flush();
  assert.deepEqual(sent, [{ command: 'driver.disconnect' }, { command: 'driver.connect' }]);
});

test('a query result resets the timeout count and escalation', async () => {
  const { dog, sent, tick } = watchdog();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    for (let i = 0; i < 3; i += 1) dog.onQueryTimeout();
    await flush();
    tick(1000);
  }
  dog.onQueryResult();
  sent.length = 0;

  dog.onQueryTimeout();
  dog.onQueryTimeout();
  await flush();
  assert.deepEqual(sent, []);

  dog.onQueryTimeout();
  await flush();
  assert.equal(sent[0].command, 'station.is_connected');
});

test('query poller reports unanswered queries to the watchdog', async () => {
  let timeouts = 0;
  const poller = new QueryPoller(() => {}, {
    pollDelays: [0],
    responseTimeoutMs: 1,
    onTimeout: () => { timeouts += 1; },
    now: () => new Date('2026-07-04T12:00:00Z'),
  });

  const found = await poller.findNewEvents(new Set());

  assert.deepEqual(found, []);
  assert.equal(timeouts, 1);
});
