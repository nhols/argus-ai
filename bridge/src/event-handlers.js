import { log } from './logger.js';
import { DOORBELL_SN, HOMEBASE_SN, CONNECT_TIMEOUT_MS } from './config.js';

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
  let connectTimeout = null;

  // ── lifecycle ──────────────────────────────────────────────────────

  /** Called once per (re)connection. */
  function handleOpen(wsClient) {
    log('Connected — setting up API schema and driver…');
    wsClient.send('set_api_schema', { schemaVersion: 21 });
    wsClient.send('start_listening');
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
    if (msg.type === 'result' && msg.success === true && connectTimeout) {
      handleDriverConnected();
    }

    // detection / trigger events
    if (eventName === 'motion detected')  handleDetection(msg, '🚨 Motion detected');
    if (eventName === 'person detected')  handleDetection(msg, '🚶 Person detected');
    if (eventName === 'rings')            handleDetection(msg, '🔔 Doorbell ring');

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
    clearTimeout(connectTimeout);
    connectTimeout = null;
    queryPoller.fireQuery();
  }

  function handleCaptchaRequest(msg) {
    captchaServer.onCaptchaRequest(msg.event?.captchaId, msg.event?.captcha);
  }

  /**
   * Generic handler for motion / person / ring events.
   * Triggers exponential-backoff polling for new recordings.
   */
  async function handleDetection(msg, label) {
    if (msg.event?.serialNumber !== DOORBELL_SN) return;
    log(`${label} (state=${msg.event.state})`);

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
  }

  return { handleOpen, handleMessage };
}
