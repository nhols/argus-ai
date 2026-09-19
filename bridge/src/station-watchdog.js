import { log } from './logger.js';
import {
  HOMEBASE_SN,
  WATCHDOG_TIMEOUT_THRESHOLD,
  WATCHDOG_COOLDOWN_MS,
  WATCHDOG_RECONNECT_DELAY_MS,
  WATCHDOG_STATION_ATTEMPTS,
} from './config.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Recovers from a silently dead homebase P2P session.
 *
 * eufy-ws can sit for hours with a station connection that looks alive but
 * never answers `station.database_query_by_date`; its own heartbeat may not
 * notice. Once queries time out repeatedly, this forces a station reconnect,
 * escalating to a full driver reconnect if that doesn't help.
 */
export class StationWatchdog {
  constructor(wsSend, {
    stationSn = HOMEBASE_SN,
    timeoutThreshold = WATCHDOG_TIMEOUT_THRESHOLD,
    cooldownMs = WATCHDOG_COOLDOWN_MS,
    stationAttempts = WATCHDOG_STATION_ATTEMPTS,
    delay = () => sleep(WATCHDOG_RECONNECT_DELAY_MS),
    now = () => Date.now(),
  } = {}) {
    this.wsSend = wsSend;
    this.stationSn = stationSn;
    this.timeoutThreshold = timeoutThreshold;
    this.cooldownMs = cooldownMs;
    this.stationAttempts = stationAttempts;
    this.delay = delay;
    this.now = now;
    this.consecutiveTimeouts = 0;
    this.recoveryAttempts = 0;
    this.lastRecoveryAt = null;
    this.recovering = false;
  }

  onQueryResult() {
    if (this.recoveryAttempts > 0) log('✅ Station is answering queries again');
    this.consecutiveTimeouts = 0;
    this.recoveryAttempts = 0;
  }

  onQueryTimeout() {
    this.consecutiveTimeouts += 1;
    if (this.consecutiveTimeouts < this.timeoutThreshold) return;
    if (this.recovering) return;
    if (this.lastRecoveryAt !== null && this.now() - this.lastRecoveryAt < this.cooldownMs) return;

    this.recover().catch((error) => log('❌ Station recovery failed:', error.message));
  }

  async recover() {
    this.recovering = true;
    this.lastRecoveryAt = this.now();
    this.recoveryAttempts += 1;
    try {
      if (this.recoveryAttempts <= this.stationAttempts) {
        log(`🩺 ${this.consecutiveTimeouts} consecutive query timeouts; reconnecting station ${this.stationSn} (attempt ${this.recoveryAttempts})…`);
        this.wsSend('station.is_connected', { serialNumber: this.stationSn });
        this.wsSend('station.disconnect', { serialNumber: this.stationSn });
        await this.delay();
        this.wsSend('station.connect', { serialNumber: this.stationSn });
      } else {
        log(`🩺 Station reconnect did not help after ${this.stationAttempts} attempt(s); reconnecting driver…`);
        this.wsSend('driver.disconnect');
        await this.delay();
        this.wsSend('driver.connect');
      }
    } finally {
      this.recovering = false;
    }
  }
}
