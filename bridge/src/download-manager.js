import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import axios from 'axios';
import { log } from './logger.js';
import {
  OUTPUT_DIR,
  VID_ANALYSER_API_URL,
  HOMEBASE_SN,
} from './config.js';

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
  constructor(wsSend) {
    this.wsSend = wsSend;

    /** @type {{ device_sn: string, storage_path: string, cipher_id?: number }[]} */
    this.queue = [];
    this.isDownloading = false;

    /** Info about the download currently in flight. */
    this.currentDownload = null;

    /**
     * Per-device chunk buffers.
     * Map<serialNumber, { videoChunks: Buffer[], audioChunks: Buffer[], metadata: object }>
     */
    this.activeDownloads = new Map();
  }

  // ── queue management ─────────────────────────────────────────────────

  /** Push one or more DB event rows onto the download queue. */
  enqueue(events) {
    for (const evt of events) {
      log(`📋 Queued download: ${evt.storage_path}`);
      this.queue.push(evt);
    }
    this.processQueue();
  }

  /** @private Start the next queued download if nothing is in flight. */
  processQueue() {
    if (this.isDownloading || this.queue.length === 0) return;

    const evt = this.queue.shift();
    this.isDownloading = true;

    const basename = evt.storage_path.split('/').pop().replace('.zxvideo', '');
    this.currentDownload = {
      serialNumber: evt.device_sn,
      storagePath: evt.storage_path,
      cipherId: evt.cipher_id,
      outputBasename: basename,
      startTime: evt.start_time,
      endTime: evt.end_time,
    };

    this.activeDownloads.set(evt.device_sn, {
      videoChunks: [],
      audioChunks: [],
      metadata: null,
    });

    log(`📥 start_download: ${evt.storage_path}`);
    this.wsSend('device.start_download', {
      serialNumber: evt.device_sn,
      path: evt.storage_path,
      cipherId: evt.cipher_id,
    });
  }

  // ── chunk collection (called by event handlers) ──────────────────────

  /** Capture stream metadata from the `download started` event. */
  onDownloadStarted(serialNumber, metadata) {
    log(`⬇️  Download started for ${serialNumber}`);
    const dl = this.activeDownloads.get(serialNumber);
    if (dl) {
      dl.videoChunks = [];
      dl.audioChunks = [];
      dl.metadata = metadata ?? {};
    }
  }

  /** Append a video buffer chunk. */
  onVideoData(serialNumber, bufferData) {
    const dl = this.activeDownloads.get(serialNumber);
    if (!dl) return;
    dl.videoChunks.push(Buffer.from(bufferData));
    this.logProgress(dl);
  }

  /** Append an audio buffer chunk. */
  onAudioData(serialNumber, bufferData) {
    const dl = this.activeDownloads.get(serialNumber);
    if (!dl) return;
    dl.audioChunks.push(Buffer.from(bufferData));
  }

  /** @private Write a progress counter every 200 total chunks. */
  logProgress(dl) {
    const total = dl.videoChunks.length + dl.audioChunks.length;
    if (total % 200 === 0) {
      process.stdout.write(`  ${dl.videoChunks.length}v / ${dl.audioChunks.length}a chunks\r`);
    }
  }

  // ── finalisation ─────────────────────────────────────────────────────

  /** Mux video+audio → mp4 with ffmpeg, POST to the API, then process the next item. */
  async onDownloadFinished(serialNumber) {
    const dl = this.activeDownloads.get(serialNumber);
    if (!dl) {
      log(`⚠️  download finished for unknown device ${serialNumber}`);
      this.isDownloading = false;
      this.processQueue();
      return;
    }

    const basename = this.currentDownload?.outputBasename ?? serialNumber;
    this.activeDownloads.delete(serialNumber);

    fs.mkdirSync(OUTPUT_DIR, { recursive: true });

    const videoRawPath = path.join(OUTPUT_DIR, `${basename}.video.raw`);
    const audioRawPath = path.join(OUTPUT_DIR, `${basename}.audio.raw`);
    const mp4Path = path.join(OUTPUT_DIR, `${basename}.mp4`);

    try {
      // ── write raw streams ──────────────────────────────────────────
      const videoBuffer = Buffer.concat(dl.videoChunks);
      fs.writeFileSync(videoRawPath, videoBuffer);
      log(`💾 Video: ${videoBuffer.length} bytes (${dl.videoChunks.length} chunks) → ${videoRawPath}`);

      const hasAudio = dl.audioChunks.length > 0;
      if (hasAudio) {
        const audioBuffer = Buffer.concat(dl.audioChunks);
        fs.writeFileSync(audioRawPath, audioBuffer);
        log(`💾 Audio: ${audioBuffer.length} bytes (${dl.audioChunks.length} chunks) → ${audioRawPath}`);
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
          `-f ${videoFmt} -framerate ${videoFPS} -i ${videoRawPath}`,
          `-f aac -i ${audioRawPath}`,
          '-c:v copy -c:a copy -movflags +faststart',
          mp4Path,
        ].join(' ');
      } else {
        ffmpegCmd = [
          'ffmpeg -y',
          `-f ${videoFmt} -framerate ${videoFPS} -i ${videoRawPath}`,
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

      // ── clean up intermediate and final files ──────────────────────
      try { fs.unlinkSync(videoRawPath); } catch { /* ignore */ }
      if (hasAudio) try { fs.unlinkSync(audioRawPath); } catch { /* ignore */ }
      try { fs.unlinkSync(mp4Path); log(`🗑️  Deleted ${mp4Path}`); } catch { /* ignore */ }
    } catch (e) {
      log('❌ Failed to convert/send video:', e.message);
    } finally {
      this.isDownloading = false;
      this.currentDownload = null;
      this.processQueue();
    }
  }

  /** @private POST a finished mp4 to the analysis API as base64. */
  async sendToApi(mp4Path, { startTime, endTime } = {}) {
    const mp4Data = fs.readFileSync(mp4Path);
    const mp4Base64 = mp4Data.toString('base64');

    const resp = await axios.post(
      VID_ANALYSER_API_URL,
      {
        receivedAt: new Date().toISOString(),
        stationSerialNumber: HOMEBASE_SN,
        startTime,
        endTime,
        data: {
          mimeType: 'video/mp4',
          base64: mp4Base64,
          filename: path.basename(mp4Path),
        },
      },
      { timeout: 30_000 },
    );

    log('📨 Sent video to API');
    log('API response:', JSON.stringify({
      status: resp.status,
      statusText: resp.statusText,
      data: resp.data,
    }));
  }
}
