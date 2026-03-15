import { log } from './logger.js';
import {
  HOMEBASE_SN,
  DOORBELL_SN,
  BACKOFF_DELAYS,
  QUERY_RESPONSE_TIMEOUT_MS,
} from './config.js';

const fmt = (d) => d.toISOString().slice(0, 10).replace(/-/g, '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Polls `station.database_query_by_date` with exponential back-off until a
 * new doorbell event that hasn't been sent upstream yet appears.
 *
 * Because the WS bridge returns query results as an *event* (not a direct
 * response), the poller works cooperatively with the event handler:
 *   1. `queryAndWait()` sends the command and returns a Promise.
 *   2. The event handler calls `onQueryResult(data)` when results arrive,
 *      which resolves the Promise.
 */
export class QueryPoller {
  constructor(wsSend) {
    this.wsSend = wsSend;
    /** @type {((data: any[]) => void) | null} */
    this.pendingResolve = null;
    this.polling = false;
  }

  // ── called by the event handler ──────────────────────────────────────

  /** Forward DB results to the pending `queryAndWait` promise, if any. */
  onQueryResult(data) {
    if (this.pendingResolve) {
      const resolve = this.pendingResolve;
      this.pendingResolve = null;
      resolve(data);
    }
  }

  // ── public API ───────────────────────────────────────────────────────

  /**
   * Poll with exponential back-off until new doorbell events are found.
   * Returns the new events, or `[]` if none found after all retries.
   *
   * Only one poll loop runs at a time — concurrent calls return `[]`.
   */
  async pollForNewEvents(sentEvents) {
    if (this.polling) {
      log('⏳ Poll already in progress, skipping');
      return [];
    }
    this.polling = true;

    try {
      for (const delay of BACKOFF_DELAYS) {
        log(`⏳ Waiting ${delay / 1000}s before querying…`);
        await sleep(delay);

        const data = await this.queryAndWait();
        const newEvents = (data || []).filter(
          (e) => e.device_sn === DOORBELL_SN && !sentEvents.has(e.storage_path),
        );

        if (newEvents.length > 0) {
          log(`✅ Found ${newEvents.length} new event(s) after back-off`);
          return newEvents;
        }
        log('No new events yet, retrying…');
      }

      log('⚠️ No new events found after all retries');
      return [];
    } finally {
      this.polling = false;
    }
  }

  /** Fire a single immediate query (used for the initial startup check). */
  fireQuery() {
    this.wsSend('station.database_query_by_date', this.buildParams());
  }

  // ── internals ────────────────────────────────────────────────────────

  /** @private Send the query and wait for the event-handler to resolve it. */
  queryAndWait() {
    return new Promise((resolve) => {
      this.pendingResolve = resolve;
      this.wsSend('station.database_query_by_date', this.buildParams());

      // Safety-net timeout so we never hang forever
      setTimeout(() => {
        if (this.pendingResolve === resolve) {
          log('⚠️ Query response timeout');
          this.pendingResolve = null;
          resolve([]);
        }
      }, QUERY_RESPONSE_TIMEOUT_MS);
    });
  }

  /** @private Build the params for today → tomorrow. */
  buildParams() {
    const today = new Date();
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);

    const params = {
      serialNumber: HOMEBASE_SN,
      serialNumbers: [],
      startDate: fmt(today),
      endDate: fmt(tomorrow),
      eventType: 0,
      detectionType: 0,
      storageType: 0,
    };
    log('📤 database_query_by_date params:', JSON.stringify(params));
    return params;
  }
}
