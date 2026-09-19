import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import axios from 'axios';
import FormData from 'form-data';
import { log } from './logger.js';
import {
  OUTPUT_DIR,
  VID_ANALYSER_USE_SHARED_INPUT,
  VID_ANALYSER_API_URL,
  VID_ANALYSER_API_KEY,
  HOMEBASE_SN,
  DOWNLOAD_TIMEOUT_MS,
} from './config.js';

const MAX_DOWNLOAD_RETRIES = 1;

function closeWriteStream(stream) {
  return new Promise((resolve, reject) => {
    stream.on('error', reject);
    stream.end(resolve);
  });
}

function deleteIfExists(filePath) {
  try {
    fs.unlinkSync(filePath);
    log(`🗑️  Deleted ${filePath}`);
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      log(`⚠️  Failed to delete ${filePath}: ${error.message}`);
    }
  }
}

/**
 * Manages a serial download queue and collects both video *and* audio chunks
 * for each active download, keyed by device serial number.
 *
 * The eufy-security-ws bridge enforces one download per device at a time, so
 * we maintain a FIFO queue and only start the next download after the
 * previous one finishes.
 *
 * Download lifecycle:
 *   enqueue(events)
 *     → processQueue()  → device.start_download
 *     → onDownloadStarted()            (clear buffers, capture metadata)
 *     → onVideoData() / onAudioData()  (collect chunks)
 *     → onDownloadFinished()           (mux with ffmpeg, POST to API, next)
 */
export class DownloadManager {
  constructor(wsSend, {
    timeoutMs = DOWNLOAD_TIMEOUT_MS,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  } = {}) {
    this.wsSend = wsSend;
    this.timeoutMs = timeoutMs;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;

    /** @type {{ device_sn: string, storage_path: string, cipher_id?: number }[]} */
    this.queue = [];
    this.isDownloading = false;

    /** Info about the download currently in flight. */
    this.currentDownload = null;
    this.deadlineTimer = null;

    /**
     * Per-device download state.
     */
    this.activeDownloads = new Map();
  }

  // ── queue management ─────────────────────────────────────────────────

  /** Push one or more DB event rows onto the download queue. */
  enqueue(events) {
    for (const evt of events) {
      if (this.isPending(evt.storage_path)) continue;
      log(`📋 Queued download: ${evt.storage_path}`);
      this.queue.push(evt);
    }
    this.processQueue();
  }

  /** @private */
  isPending(storagePath) {
    return this.currentDownload?.storagePath === storagePath
      || this.queue.some((evt) => evt.storage_path === storagePath);
  }

  /** Continue with the next queued download once eufy-ws is ready again. */
  resume() {
    this.processQueue();
  }

  /**
   * Drop the in-flight download. Used when the eufy-ws connection is lost
   * (its download stream dies with it) or when a download stalls.
   */
  abortCurrent(reason, { requeue = false } = {}) {
    if (!this.isDownloading) return;

    const { serialNumber, storagePath, event } = this.currentDownload;
    log(`⚠️  Aborting download of ${storagePath}: ${reason}`);
    this.clearDeadline();

    const dl = this.activeDownloads.get(serialNumber);
    if (dl) {
      // The file may not be open yet, so delete once the stream has closed.
      dl.videoStream.once('close', () => deleteIfExists(dl.videoRawPath));
      dl.audioStream.once('close', () => deleteIfExists(dl.audioRawPath));
      dl.videoStream.destroy();
      dl.audioStream.destroy();
      this.activeDownloads.delete(serialNumber);
    }

    const retries = event.retries ?? 0;
    if (requeue && retries < MAX_DOWNLOAD_RETRIES) {
      log(`🔁 Will retry download of ${storagePath}`);
      this.queue.unshift({ ...event, retries: retries + 1 });
    } else if (requeue) {
      log(`❌ Giving up on ${storagePath} after ${retries} retry attempt(s)`);
    }

    this.isDownloading = false;
    this.currentDownload = null;
  }

  /** @private */
  clearDeadline() {
    if (this.deadlineTimer) {
      this.clearTimer(this.deadlineTimer);
      this.deadlineTimer = null;
    }
  }

  /** @private Start the next queued download if nothing is in flight. */
  processQueue() {
    if (this.isDownloading || this.queue.length === 0) return;

    const evt = this.queue.shift();
    this.isDownloading = true;

    const basename = evt.storage_path.split('/').pop().replace('.zxvideo', '');
    this.currentDownload = {
      event: evt,
      serialNumber: evt.device_sn,
      storagePath: evt.storage_path,
      cipherId: evt.cipher_id,
      outputBasename: basename,
      startTime: evt.start_time,
      endTime: evt.end_time,
    };

    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    const videoRawPath = path.join(OUTPUT_DIR, `${basename}.video.raw`);
    const audioRawPath = path.join(OUTPUT_DIR, `${basename}.audio.raw`);
    this.activeDownloads.set(evt.device_sn, {
      videoRawPath,
      audioRawPath,
      videoStream: fs.createWriteStream(videoRawPath),
      audioStream: fs.createWriteStream(audioRawPath),
      videoChunkCount: 0,
      audioChunkCount: 0,
      videoBytes: 0,
      audioBytes: 0,
      metadata: null,
    });

    log(`📥 start_download: ${evt.storage_path}`);
    this.wsSend('device.start_download', {
      serialNumber: evt.device_sn,
      path: evt.storage_path,
      cipherId: evt.cipher_id,
    });

    this.deadlineTimer = this.setTimer(() => {
      this.deadlineTimer = null;
      this.abortCurrent(`no download finished within ${this.timeoutMs / 1000}s`, { requeue: true });
      this.processQueue();
    }, this.timeoutMs);
  }

  // ── chunk collection (called by event handlers) ──────────────────────

  /** Capture stream metadata from the `download started` event. */
  onDownloadStarted(serialNumber, metadata) {
    log(`⬇️  Download started for ${serialNumber}`);
    const dl = this.activeDownloads.get(serialNumber);
    if (dl) {
      dl.metadata = metadata ?? {};
    }
  }

  /** Append a video buffer chunk. */
  onVideoData(serialNumber, bufferData) {
    const dl = this.activeDownloads.get(serialNumber);
    if (!dl) return;
    const chunk = Buffer.from(bufferData);
    dl.videoChunkCount += 1;
    dl.videoBytes += chunk.length;
    dl.videoStream.write(chunk);
    this.logProgress(dl);
  }

  /** Append an audio buffer chunk. */
  onAudioData(serialNumber, bufferData) {
    const dl = this.activeDownloads.get(serialNumber);
    if (!dl) return;
    const chunk = Buffer.from(bufferData);
    dl.audioChunkCount += 1;
    dl.audioBytes += chunk.length;
    dl.audioStream.write(chunk);
  }

  /** @private Write a progress counter every 200 total chunks. */
  logProgress(dl) {
    const total = dl.videoChunkCount + dl.audioChunkCount;
    if (total % 200 === 0) {
      process.stdout.write(`  ${dl.videoChunkCount}v / ${dl.audioChunkCount}a chunks\r`);
    }
  }

  // ── finalisation ─────────────────────────────────────────────────────

  /** Mux video+audio → mp4 with ffmpeg, POST to the API, then process the next item. */
  async onDownloadFinished(serialNumber) {
    this.clearDeadline();
    const dl = this.activeDownloads.get(serialNumber);
    if (!dl) {
      log(`⚠️  download finished for unknown device ${serialNumber}`);
      this.isDownloading = false;
      this.currentDownload = null;
      this.processQueue();
      return;
    }

    const basename = this.currentDownload?.outputBasename ?? serialNumber;
    this.activeDownloads.delete(serialNumber);

    const mp4Path = path.join(OUTPUT_DIR, `${basename}.mp4`);

    try {
      await closeWriteStream(dl.videoStream);
      await closeWriteStream(dl.audioStream);
      log(`💾 Video: ${dl.videoBytes} bytes (${dl.videoChunkCount} chunks) → ${dl.videoRawPath}`);

      const hasAudio = dl.audioBytes > 0;
      if (hasAudio) {
        log(`💾 Audio: ${dl.audioBytes} bytes (${dl.audioChunkCount} chunks) → ${dl.audioRawPath}`);
      }

      // ── ffmpeg mux ─────────────────────────────────────────────────
      // Use metadata from the `download started` event when available,
      // fall back to sensible defaults for eufy doorbell cameras.
      const videoCodec = dl.metadata?.videoCodec ?? 'hevc';
      const videoFPS = dl.metadata?.videoFPS ?? 15;
      const videoFmt = videoCodec === 'h264' ? 'h264' : 'hevc';

      let ffmpegCmd;
      if (hasAudio) {
        ffmpegCmd = [
          'ffmpeg -y',
          `-f ${videoFmt} -framerate ${videoFPS} -i ${dl.videoRawPath}`,
          `-f aac -i ${dl.audioRawPath}`,
          '-c:v copy -c:a copy -movflags +faststart',
          mp4Path,
        ].join(' ');
      } else {
        ffmpegCmd = [
          'ffmpeg -y',
          `-f ${videoFmt} -framerate ${videoFPS} -i ${dl.videoRawPath}`,
          '-c copy -movflags +faststart',
          mp4Path,
        ].join(' ');
      }

      log(`🎬 Running: ${ffmpegCmd}`);
      execSync(ffmpegCmd, { stdio: 'inherit' });
      log(`✅ Converted to ${mp4Path}`);

      // ── send to the API ────────────────────────────────────────────
      await this.sendToApi(mp4Path, {
        startTime: this.currentDownload?.startTime,
        endTime: this.currentDownload?.endTime,
      });
    } catch (e) {
      log('❌ Failed to convert/send video:', e.message);
    } finally {
      deleteIfExists(dl.videoRawPath);
      deleteIfExists(dl.audioRawPath);
      this.isDownloading = false;
      this.currentDownload = null;
      this.processQueue();
    }
  }

  /** @private POST a finished mp4 to the analysis API as multipart form-data. */
  async sendToApi(mp4Path, { startTime, endTime } = {}) {
    let resp;
    if (VID_ANALYSER_USE_SHARED_INPUT) {
      resp = await axios.post(
        `${VID_ANALYSER_API_URL.replace(/\/$/, '')}/shared`,
        {
          shared_video_path: mp4Path,
          content_type: 'video/mp4',
          received_at: new Date().toISOString(),
          start_time: startTime ?? null,
          end_time: endTime ?? null,
        },
        {
          timeout: 30_000,
          headers: {
            'X-API-Key': VID_ANALYSER_API_KEY,
          },
        },
      );
    } else {
      const form = new FormData();
      form.append('video', fs.createReadStream(mp4Path), {
        contentType: 'video/mp4',
        filename: path.basename(mp4Path),
      });
      form.append('received_at', new Date().toISOString());
      if (HOMEBASE_SN) form.append('station_serial_number', HOMEBASE_SN);
      if (this.currentDownload?.serialNumber) form.append('device_serial_number', this.currentDownload.serialNumber);
      if (this.currentDownload?.storagePath) form.append('storage_path', this.currentDownload.storagePath);
      if (startTime) form.append('start_time', startTime);
      if (endTime) form.append('end_time', endTime);

      resp = await axios.post(
        VID_ANALYSER_API_URL,
        form,
        {
          timeout: 30_000,
          headers: {
            ...form.getHeaders(),
            'X-API-Key': VID_ANALYSER_API_KEY,
          },
        },
      );
    }

    log('📨 Sent video to API');
    log('API response:', JSON.stringify({
      status: resp.status,
      statusText: resp.statusText,
      data: resp.data,
    }));
  }
}
