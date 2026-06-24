import { log } from './logger.js';
import { DOORBELL_SN, HOMEBASE_SN, CONNECT_TIMEOUT_MS } from './config.js';

const RECORDING_EDGE_EVENTS = new Set([
  'motion detected',
  'person detected',
  'vehicle detected',
  'pet detected',
  'rings',
]);

/**
 * Creates a message-handler pair (`handleOpen`, `handleMessage`) that wires
 * together the QueryPoller, DownloadManager, and CaptchaServer via clean,
 * named functions.
 *
 * @param {object}          deps
 * @param {import('./query-poller.js').QueryPoller}       deps.queryPoller
 * @param {import('./download-manager.js').DownloadManager} deps.downloadManager
 * @param {import('./captcha-server.js').CaptchaServer}   deps.captchaServer
 * @param {Set<string>}     deps.sentEvents   – storage_paths already sent upstream
 */
export function createMessageHandler({ queryPoller, downloadManager, captchaServer, sentEvents }) {
  let activeWsClient = null;
  let connectTimeout = null;
  let doorbellReady = false;
  let initialQueryFired = false;
  const lastRecordingEdgeState = new Map();
  let cloudRefreshRetryTimer = null;
  let cloudRefreshRetryDelayMs = 5_000;
  const maxCloudRefreshRetryDelayMs = 2 * 60_000;

  // ── lifecycle ──────────────────────────────────────────────────────

  /** Called once per (re)connection. */
  function handleOpen(wsClient) {
    activeWsClient = wsClient;
    doorbellReady = false;
    initialQueryFired = false;
    clearCloudRefreshRetry();
    cloudRefreshRetryDelayMs = 5_000;

    log('Connected — setting up API schema and driver…');
    wsClient.send('set_api_schema', { schemaVersion: 21 });
    wsClient.send('driver.connect');

    connectTimeout = setTimeout(() => {
      log('⏱️  10-minute timeout reached, firing initial query…');
      queryPoller.fireQuery();
    }, CONNECT_TIMEOUT_MS);
  }

  // ── top-level dispatcher ───────────────────────────────────────────

  function handleMessage(msg) {
    const eventName = msg.event?.event ?? (msg.result?.state ? 'state' : '');

    // Log non-streaming events for our devices only
    if (eventName !== 'download audio data' && eventName !== 'download video data') {
      const sn = msg.event?.serialNumber;
      if (!sn || sn === DOORBELL_SN || sn === HOMEBASE_SN) {
        log(msg);
      }
    }

    // driver.connect success
    if (msg.type === 'result' && msg.success === true && msg.command === 'driver.connect') {
      handleDriverConnected();
    }

    // start_listening state snapshot
    if (msg.type === 'result' && msg.success === true && msg.command === 'start_listening' && msg.result?.state) {
      handleStateSnapshot(msg.result.state);
    }

    // cloud refresh completed
    if (msg.type === 'result' && msg.success === true && msg.command === 'driver.poll_refresh') {
      handleCloudRefreshComplete();
    }

    // detection / trigger events
    if (RECORDING_EDGE_EVENTS.has(eventName)) handleRecordingEdge(msg);

    // captcha
    if (eventName === 'captcha request') handleCaptchaRequest(msg);

    // database query responses
    if (eventName === 'database query by date') handleDatabaseQueryResult(msg);

    // download lifecycle
    if (eventName === 'download started')    handleDownloadStarted(msg);
    if (eventName === 'download video data') handleDownloadVideoData(msg);
    if (eventName === 'download audio data') handleDownloadAudioData(msg);
    if (eventName === 'download finished')   handleDownloadFinished(msg);

    if (msg.success === false) handleError(msg);
  }

  // ── individual handlers ────────────────────────────────────────────

  function handleDriverConnected() {
    log('✅ Driver connected, clearing timeout and firing initial query…');
    if (connectTimeout) {
      clearTimeout(connectTimeout);
      connectTimeout = null;
    }
    activeWsClient?.send('start_listening');
  }

  function handleStateSnapshot(state) {
    const stations = Array.isArray(state.stations) ? state.stations : [];
    const devices = Array.isArray(state.devices) ? state.devices : [];
    const hasHomebase = containsSerial(stations, HOMEBASE_SN);
    const hasDoorbell = containsSerial(devices, DOORBELL_SN);

    log(`State snapshot: ${stations.length} station(s), ${devices.length} device(s), homebase=${hasHomebase}, doorbell=${hasDoorbell}`);

    if (hasDoorbell) {
      doorbellReady = true;
      clearCloudRefreshRetry();
      cloudRefreshRetryDelayMs = 5_000;
      if (!initialQueryFired) {
        initialQueryFired = true;
        queryPoller.fireQuery();
      }
      return;
    }

    doorbellReady = false;
    log(`⚠️ Doorbell ${DOORBELL_SN} missing from eufy-ws state; refreshing Eufy cloud device data…`);
    scheduleCloudRefresh(0);
  }

  function handleCloudRefreshComplete() {
    log('Cloud/device refresh completed; requesting fresh state snapshot…');
    activeWsClient?.send('start_listening');
  }

  function scheduleCloudRefresh(delayMs = cloudRefreshRetryDelayMs) {
    if (cloudRefreshRetryTimer) return;

    cloudRefreshRetryTimer = setTimeout(() => {
      cloudRefreshRetryTimer = null;
      activeWsClient?.send('driver.poll_refresh');
      cloudRefreshRetryDelayMs = Math.min(cloudRefreshRetryDelayMs * 2, maxCloudRefreshRetryDelayMs);
    }, delayMs);
  }

  function clearCloudRefreshRetry() {
    if (cloudRefreshRetryTimer) {
      clearTimeout(cloudRefreshRetryTimer);
      cloudRefreshRetryTimer = null;
    }
  }

  function containsSerial(items, serialNumber) {
    if (!serialNumber) return false;
    return items.some((item) => JSON.stringify(item).includes(serialNumber));
  }

  function handleCaptchaRequest(msg) {
    captchaServer.onCaptchaRequest(msg.event?.captchaId, msg.event?.captcha);
  }

  /**
   * Generic handler for recording-related state events.
   * When detection ends, the recording may now be available in the database.
   */
  async function handleRecordingEdge(msg) {
    if (msg.event?.serialNumber !== DOORBELL_SN) return;

    const eventName = msg.event?.event;
    const state = msg.event?.state;
    log(`${eventName} (state=${state})`);

    if (typeof state !== 'boolean') return;

    const previousState = lastRecordingEdgeState.get(eventName);
    lastRecordingEdgeState.set(eventName, state);
    if (state !== false || previousState === false) return;

    const newEvents = await queryPoller.pollForNewEvents(sentEvents);
    if (newEvents.length > 0) {
      for (const evt of newEvents) sentEvents.add(evt.storage_path);
      downloadManager.enqueue(newEvents);
    }
  }

  /**
   * Handle `database query by date` results.
   *
   * Two paths:
   *   1. If the QueryPoller is waiting → forward data so its Promise resolves.
   *   2. If this is the *initial* query (no active poll) → mark all existing
   *      events as "seen" and download only the most recent one.
   */
  function handleDatabaseQueryResult(msg) {
    const data = msg.event?.data ?? [];
    log(`=== DB Query Results: ${data.length} events ===`);

    // Always forward to the poller (no-op if it isn't waiting)
    queryPoller.onQueryResult(data);

    if (!doorbellReady) {
      log(`Doorbell ${DOORBELL_SN} is not loaded in eufy-ws yet; deferring DB result handling.`);
      return;
    }

    // For the initial (non-polled) query, handle directly
    if (!queryPoller.polling) {
      const doorbellEvents = data.filter((e) => e.device_sn === DOORBELL_SN);
      log(`Doorbell events: ${doorbellEvents.length}`);

      if (doorbellEvents.length === 0) return;

      // Mark ALL existing events as seen so we only download truly new ones later
      for (const evt of doorbellEvents) sentEvents.add(evt.storage_path);

      // Download the most recent
      doorbellEvents.sort((a, b) => new Date(b.start_time) - new Date(a.start_time));
      const mostRecent = doorbellEvents[0];
      log('Most recent event:', JSON.stringify(mostRecent, null, 2));
      downloadManager.enqueue([mostRecent]);
    }
  }

  function handleDownloadStarted(msg) {
    downloadManager.onDownloadStarted(
      msg.event?.serialNumber,
      msg.event?.metadata ?? {},
    );
  }

  function handleDownloadVideoData(msg) {
    if (msg.event?.buffer?.data) {
      downloadManager.onVideoData(msg.event.serialNumber, msg.event.buffer.data);
    }
  }

  function handleDownloadAudioData(msg) {
    if (msg.event?.buffer?.data) {
      downloadManager.onAudioData(msg.event.serialNumber, msg.event.buffer.data);
    }
  }

  function handleDownloadFinished(msg) {
    downloadManager.onDownloadFinished(msg.event?.serialNumber).catch((e) => {
      log('❌ Error finalising download:', e.message);
    });
  }

  function handleError(msg) {
    log('❌ ERROR:', msg.error);
    if (msg.command === 'driver.poll_refresh') {
      log(`Cloud/device refresh failed; retrying in ${cloudRefreshRetryDelayMs / 1000}s…`);
      scheduleCloudRefresh();
    }
  }

  return { handleOpen, handleMessage };
}
