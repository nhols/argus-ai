/**
 * Eufy → API bridge
 *
 * Connects to a eufy-security-ws server, listens for doorbell events,
 * downloads recordings (video + audio), converts to mp4, and POSTs them
 * to the analysis API.
 *
 * See src/ for the individual modules:
 *   config.js          – environment variables & constants
 *   ws-client.js       – WebSocket with auto-reconnect
 *   query-poller.js    – exponential-backoff DB polling
 *   download-manager.js – serial download queue, ffmpeg mux, API delivery
 *   event-handlers.js  – message dispatcher & named handlers
 */

import { EUFY_WS_URL } from './src/config.js';
import { log } from './src/logger.js';
import { WsClient } from './src/ws-client.js';
import { QueryPoller } from './src/query-poller.js';
import { DownloadManager } from './src/download-manager.js';
import { CaptchaServer } from './src/captcha-server.js';
import { createMessageHandler } from './src/event-handlers.js';

// Track storage_paths already sent upstream (in-memory; lost on restart)
const sentEvents = new Set();

// Initialise components
const ws = new WsClient(EUFY_WS_URL);
const send = (cmd, params) => ws.send(cmd, params);
const queryPoller = new QueryPoller(send);
const downloadManager = new DownloadManager(send);
const captchaServer = new CaptchaServer(send);
captchaServer.start();

const { handleOpen, handleMessage } = createMessageHandler({
  queryPoller,
  downloadManager,
  captchaServer,
  sentEvents,
});

// Wire up
ws.on('open', () => handleOpen(ws));
ws.on('message', handleMessage);

log('🚀 Eufy bridge starting…');
