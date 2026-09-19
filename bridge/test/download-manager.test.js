import assert from 'node:assert/strict';
import { once } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

process.env.HOMEBASE_SN = 'homebase-test';
process.env.DOORBELL_SN = 'doorbell-test';
process.env.VID_ANALYSER_API_URL = 'http://analyser.test';
process.env.VID_ANALYSER_API_KEY = 'test-key';
process.env.OUTPUT_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'bridge-dl-'));

const { DownloadManager } = await import('../src/download-manager.js');

const event = (name) => ({
  device_sn: 'doorbell-test',
  storage_path: `/zx/hdd_data0/Camera00/202609/${name}/${name}.zxvideo`,
});

function manager() {
  const sent = [];
  const timers = [];
  const dm = new DownloadManager((command, params) => sent.push({ command, ...params }), {
    timeoutMs: 1000,
    setTimer: (fn) => { timers.push(fn); return timers.length; },
    clearTimer: (id) => { timers[id - 1] = null; },
  });
  const starts = () => sent.filter((m) => m.command === 'device.start_download').map((m) => m.path);
  const fireDeadline = () => {
    const fn = timers.find(Boolean);
    assert.ok(fn, 'expected an armed deadline timer');
    timers[timers.indexOf(fn)] = null;
    fn();
  };
  return { dm, starts, fireDeadline, timers };
}

test('a connection reset requeues the in-flight download once and resumes when ready', () => {
  const { dm, starts } = manager();
  const clip = event('20260919182016');

  dm.enqueue([clip]);
  assert.deepEqual(starts(), [clip.storage_path]);
  assert.equal(dm.isDownloading, true);

  dm.abortCurrent('eufy-ws connection was reset', { requeue: true });
  assert.equal(dm.isDownloading, false);
  assert.equal(dm.activeDownloads.size, 0);
  assert.deepEqual(starts(), [clip.storage_path]);

  dm.resume();
  assert.deepEqual(starts(), [clip.storage_path, clip.storage_path]);

  dm.abortCurrent('eufy-ws connection was reset', { requeue: true });
  dm.resume();
  assert.deepEqual(starts(), [clip.storage_path, clip.storage_path]);
  assert.equal(dm.isDownloading, false);
});

test('a stalled download is aborted after the deadline and the queue moves on', () => {
  const { dm, starts, fireDeadline } = manager();
  const first = event('20260919182016');
  const second = event('20260919190508');

  dm.enqueue([first, second]);
  assert.deepEqual(starts(), [first.storage_path]);

  fireDeadline();
  assert.deepEqual(starts(), [first.storage_path, first.storage_path]);

  fireDeadline();
  assert.deepEqual(starts(), [first.storage_path, first.storage_path, second.storage_path]);
  assert.equal(dm.currentDownload.storagePath, second.storage_path);
});

test('aborting cleans up the partial raw files', async () => {
  const { dm } = manager();
  dm.enqueue([event('20260919182016')]);
  const { videoRawPath, audioRawPath, videoStream, audioStream } = dm.activeDownloads.get('doorbell-test');
  await Promise.all([once(videoStream, 'open'), once(audioStream, 'open')]);
  assert.ok(fs.existsSync(videoRawPath));

  dm.abortCurrent('test');
  await Promise.all([once(videoStream, 'close'), once(audioStream, 'close')]);

  assert.equal(fs.existsSync(videoRawPath), false);
  assert.equal(fs.existsSync(audioRawPath), false);
});

test('aborting before the raw files are open still removes them', async () => {
  const { dm } = manager();
  dm.enqueue([event('20260919182016')]);
  const { videoRawPath, videoStream, audioStream } = dm.activeDownloads.get('doorbell-test');

  dm.abortCurrent('test');
  await Promise.all([once(videoStream, 'close'), once(audioStream, 'close')]);

  assert.equal(fs.existsSync(videoRawPath), false);
});

test('an event already queued or in flight is not queued twice', () => {
  const { dm, starts } = manager();
  const clip = event('20260919182016');
  const other = event('20260919190508');

  dm.enqueue([clip]);
  dm.enqueue([clip, other, other]);

  assert.deepEqual(starts(), [clip.storage_path]);
  assert.deepEqual(dm.queue.map((e) => e.storage_path), [other.storage_path]);
});

test('finishing a download disarms its deadline', async () => {
  const { dm, timers } = manager();
  dm.enqueue([event('20260919182016')]);
  assert.ok(timers.some(Boolean));

  await dm.onDownloadFinished('unknown-device');

  assert.equal(timers.some(Boolean), false);
  assert.equal(dm.isDownloading, false);
});
