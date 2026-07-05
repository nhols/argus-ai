import assert from 'node:assert/strict';
import test from 'node:test';

process.env.HOMEBASE_SN = 'homebase-test';
process.env.DOORBELL_SN = 'doorbell-test';
process.env.VID_ANALYSER_API_URL = 'http://analyser.test';
process.env.VID_ANALYSER_API_KEY = 'test-key';

const { QueryPoller, eventMatchesTrigger } = await import('../src/query-poller.js');

const event = (timestamp) => ({
  device_sn: 'doorbell-test',
  storage_path: `/zx/hdd_data0/Camera00/202606/${timestamp}/${timestamp}.zxvideo`,
});

function pollerForResponses(responses, options = {}) {
  let calls = 0;
  let poller;
  const wsSend = () => {
    const response = responses[Math.min(calls, responses.length - 1)];
    calls += 1;
    queueMicrotask(() => poller.onQueryResult(response));
  };
  poller = new QueryPoller(wsSend, { pollDelays: responses.map(() => 0), ...options });
  return { poller, calls: () => calls };
}

test('continues polling when only an older unseen recording is found', async () => {
  const older = event('20260629121119');
  const current = event('20260629123418');
  const { poller, calls } = pollerForResponses([
    [older],
    [older, current],
  ]);

  const found = await poller.pollForNewEvents(
    new Set(),
    new Date('2026-06-29T11:34:24Z'),
  );

  assert.equal(calls(), 2);
  assert.deepEqual(found.map((item) => item.storage_path), [older.storage_path, current.storage_path]);
});

test('stops immediately when the current recording is available', async () => {
  const current = event('20260629123418');
  const { poller, calls } = pollerForResponses([[current], []]);

  const found = await poller.pollForNewEvents(
    new Set(),
    new Date('2026-06-29T11:34:24Z'),
  );

  assert.equal(calls(), 1);
  assert.deepEqual(found, [current]);
});

test('reconciliation returns all unseen doorbell recordings in one query', async () => {
  const seen = event('20260629120000');
  const unseen = event('20260629120100');
  const otherDevice = { ...event('20260629120200'), device_sn: 'other-doorbell' };
  const { poller, calls } = pollerForResponses([[seen, unseen, otherDevice]]);

  const found = await poller.findNewEvents(new Set([seen.storage_path]));

  assert.equal(calls(), 1);
  assert.deepEqual(found, [unseen]);
});

test('reconciliation also checks the previous local day shortly after midnight', async () => {
  const today = event('20260705001000');
  const yesterday = event('20260704235930');
  const { poller, calls } = pollerForResponses([[today], [yesterday]], {
    now: () => new Date('2026-07-04T23:30:00Z'),
  });

  const found = await poller.findNewEvents(new Set());

  assert.equal(calls(), 2);
  assert.deepEqual(found, [today, yesterday]);
});

test('matches recording paths against trigger time in the configured timezone', () => {
  assert.equal(
    eventMatchesTrigger(
      event('20260629123418'),
      new Date('2026-06-29T11:34:24Z'),
      'Europe/London',
    ),
    true,
  );
  assert.equal(
    eventMatchesTrigger(
      event('20260629121119'),
      new Date('2026-06-29T11:34:24Z'),
      'Europe/London',
    ),
    false,
  );
  assert.equal(
    eventMatchesTrigger(
      event('20260630125559'),
      new Date('2026-06-30T11:56:52Z'),
      'Europe/London',
    ),
    false,
  );
});

test('builds query dates using Europe/London across BST and GMT', () => {
  const summer = new QueryPoller(() => {}, {
    timeZone: 'Europe/London',
    now: () => new Date('2026-07-04T23:57:18Z'),
  });
  assert.deepEqual(
    { startDate: summer.buildParams().startDate, endDate: summer.buildParams().endDate },
    { startDate: '20260705', endDate: '20260706' },
  );

  const winter = new QueryPoller(() => {}, {
    timeZone: 'Europe/London',
    now: () => new Date('2026-11-15T00:30:00Z'),
  });
  assert.deepEqual(
    { startDate: winter.buildParams().startDate, endDate: winter.buildParams().endDate },
    { startDate: '20261115', endDate: '20261116' },
  );
});

test('event polling keeps the trigger date when retries cross local midnight', async () => {
  const params = [];
  let poller;
  const wsSend = (_command, value) => {
    params.push(value);
    queueMicrotask(() => poller.onQueryResult([]));
  };
  poller = new QueryPoller(wsSend, {
    timeZone: 'Europe/London',
    pollDelays: [0],
    now: () => new Date('2026-07-04T23:00:10Z'),
  });

  await poller.pollForNewEvents(new Set(), new Date('2026-07-04T22:59:55Z'));

  assert.equal(params[0].startDate, '20260704');
  assert.equal(params[0].endDate, '20260705');
});
