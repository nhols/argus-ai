import { log } from './logger.js';
import {
  HOMEBASE_SN,
  DOORBELL_SN,
  EUFY_TIME_ZONE,
  RECORDING_AVAILABILITY_POLL_DELAYS,
  QUERY_RESPONSE_TIMEOUT_MS,
} from './config.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const TARGET_TIME_TOLERANCE_MS = 30_000;

function zonedDateParts(date, timeZone) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date);

  return Object.fromEntries(
    parts
      .filter(({ type }) => type !== 'literal')
      .map(({ type, value }) => [type, Number(value)]),
  );
}

function compactCalendarDate({ year, month, day }) {
  return `${year}${String(month).padStart(2, '0')}${String(day).padStart(2, '0')}`;
}

function addCalendarDays(parts, days) {
  const shifted = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + days));
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
  };
}

function localWallClockMs(date, timeZone) {
  const parts = zonedDateParts(date, timeZone);
  return Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
}

function eventWallClockMs(event) {
  const match = event.storage_path?.match(/\/(\d{14})(?:\/|\.zxvideo)/);
  if (!match) return null;

  const value = match[1];
  return Date.UTC(
    Number(value.slice(0, 4)),
    Number(value.slice(4, 6)) - 1,
    Number(value.slice(6, 8)),
    Number(value.slice(8, 10)),
    Number(value.slice(10, 12)),
    Number(value.slice(12, 14)),
  );
}

export function eventMatchesTrigger(event, triggeredAt, timeZone = EUFY_TIME_ZONE) {
  const eventTime = eventWallClockMs(event);
  if (eventTime === null || !triggeredAt) return false;
  return Math.abs(eventTime - localWallClockMs(triggeredAt, timeZone)) <= TARGET_TIME_TOLERANCE_MS;
}

/**
 * Polls `station.database_query_by_date` until a
 * new doorbell event that hasn't been sent upstream yet appears.
 *
 * Because the WS bridge returns query results as an *event* (not a direct
 * response), the poller works cooperatively with the event handler:
 *   1. `queryAndWait()` sends the command and returns a Promise.
 *   2. The event handler calls `onQueryResult(data)` when results arrive,
 *      which resolves the Promise.
 */
export class QueryPoller {
  constructor(wsSend, {
    timeZone = EUFY_TIME_ZONE,
    pollDelays = RECORDING_AVAILABILITY_POLL_DELAYS,
    now = () => new Date(),
  } = {}) {
    this.wsSend = wsSend;
    this.timeZone = timeZone;
    this.pollDelays = pollDelays;
    this.now = now;
    /** @type {((data: any[]) => void) | null} */
    this.pendingResolve = null;
    this.pendingTimeout = null;
    this.polling = false;
  }

  // ── called by the event handler ──────────────────────────────────────

  /** Forward DB results to the pending `queryAndWait` promise, if any. */
  onQueryResult(data) {
    if (this.pendingResolve) {
      const resolve = this.pendingResolve;
      this.pendingResolve = null;
      if (this.pendingTimeout) {
        clearTimeout(this.pendingTimeout);
        this.pendingTimeout = null;
      }
      resolve(data);
    }
  }

  // ── public API ───────────────────────────────────────────────────────

  /**
   * Poll until new doorbell events are found.
   * Returns the new events, or `[]` if none found after all retries.
   *
   * Only one poll loop runs at a time — concurrent calls return `[]`.
   */
  async pollForNewEvents(sentEvents, triggeredAt = null) {
    if (this.polling) {
      log('⏳ Poll already in progress, skipping');
      return [];
    }
    this.polling = true;
    const discoveredEvents = new Map();

    try {
      for (const delay of this.pollDelays) {
        if (delay > 0) {
          log(`⏳ Waiting ${delay / 1000}s before querying…`);
          await sleep(delay);
        } else {
          log('🔎 Querying immediately for completed recording…');
        }

        // Keep querying the recording's local calendar date even if the
        // retry loop crosses midnight.
        const data = await this.queryAndWait(triggeredAt ?? this.now());
        const newEvents = (data || []).filter(
          (event) => event.device_sn === DOORBELL_SN
            && !sentEvents.has(event.storage_path)
            && !discoveredEvents.has(event.storage_path),
        );

        if (newEvents.length > 0) {
          for (const event of newEvents) discoveredEvents.set(event.storage_path, event);
          log(`✅ Found ${newEvents.length} new event(s); ${discoveredEvents.size} accumulated`);

          if (!triggeredAt || newEvents.some((event) => eventMatchesTrigger(event, triggeredAt, this.timeZone))) {
            return [...discoveredEvents.values()];
          }

          log('Found older unseen event(s), continuing to wait for the current recording…');
          continue;
        }
        log('No new events yet, retrying…');
      }

      if (discoveredEvents.size === 0) {
        log('⚠️ No new events found after all retries');
      } else {
        log('⚠️ Current recording not found after all retries; returning accumulated older event(s)');
      }
      return [...discoveredEvents.values()];
    } finally {
      this.polling = false;
    }
  }

  /** Run one reconciliation query without waiting or retrying. */
  async findNewEvents(sentEvents) {
    if (this.polling) return [];
    this.polling = true;
    try {
      const now = this.now();
      const data = [...(await this.queryAndWait(now))];
      const localHour = zonedDateParts(now, this.timeZone).hour;
      if (localHour < 2) {
        data.push(...(await this.queryAndWait(now, -1)));
      }

      const unseen = data.filter(
        (event) => event.device_sn === DOORBELL_SN && !sentEvents.has(event.storage_path),
      );
      return [...new Map(unseen.map((event) => [event.storage_path, event])).values()];
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
  queryAndWait(referenceDate = this.now(), dayOffset = 0) {
    return new Promise((resolve) => {
      this.pendingResolve = resolve;
      this.wsSend('station.database_query_by_date', this.buildParams(referenceDate, dayOffset));

      // Safety-net timeout so we never hang forever
      this.pendingTimeout = setTimeout(() => {
        if (this.pendingResolve === resolve) {
          log('⚠️ Query response timeout');
          this.pendingResolve = null;
          this.pendingTimeout = null;
          resolve([]);
        }
      }, QUERY_RESPONSE_TIMEOUT_MS);
    });
  }

  /** @private Build the params for today → tomorrow. */
  buildParams(referenceDate = this.now(), dayOffset = 0) {
    const referenceDay = zonedDateParts(referenceDate, this.timeZone);
    const today = addCalendarDays(referenceDay, dayOffset);
    const tomorrow = addCalendarDays(today, 1);

    const params = {
      serialNumber: HOMEBASE_SN,
      serialNumbers: [],
      startDate: compactCalendarDate(today),
      endDate: compactCalendarDate(tomorrow),
      eventType: 0,
      detectionType: 0,
      storageType: 0,
    };
    log('📤 database_query_by_date params:', JSON.stringify(params));
    return params;
  }
}
