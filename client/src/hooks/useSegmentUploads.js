import { useReducer, useRef } from 'react';
import axios from 'axios';

const MAX_ATTEMPTS = 4;
const UPLOAD_TIMEOUT_MS = 30000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Network errors, timeouts and 5xx may succeed later; other 4xx responses won't.
const isRetryable = (error) => !error.response || error.response.status >= 500 || error.response.status === 408;

const extensionFor = (mimeType) => {
  if (mimeType.includes('mp4')) return 'mp4';
  if (mimeType.includes('ogg')) return 'ogg';
  return 'webm';
};

/**
 * Upload queue for live-case audio segments. Retries with backoff and keeps the
 * audio of segments that still fail so the EMT can retry them by hand.
 *
 * Items: { caseId, seq, recorded_at, duration_ms, blob }.
 * `pending` lists items not yet accepted by the server, with status
 * 'uploading' | 'upload_failed' (shape-compatible with server segments for display).
 * onUploaded(segment) receives the server's segment for each accepted upload.
 */
export default function useSegmentUploads(onUploaded) {
  const entries = useRef(new Map());
  const inFlight = useRef(new Set());
  const [, rerender] = useReducer((n) => n + 1, 0);
  const onUploadedRef = useRef(onUploaded);
  onUploadedRef.current = onUploaded;

  const setEntry = (seq, entry) => {
    if (entry) entries.current.set(seq, entry);
    else entries.current.delete(seq);
    rerender();
  };

  const upload = async (item) => {
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
      setEntry(item.seq, { ...item, status: 'uploading', attempt });
      try {
        const form = new FormData();
        form.append('audio', item.blob, `segment-${item.seq}.${extensionFor(item.blob.type)}`);
        form.append('seq', String(item.seq));
        form.append('recorded_at', item.recorded_at);
        form.append('duration_ms', String(item.duration_ms));
        const response = await axios.post(`/api/cases/${item.caseId}/segments`, form, { timeout: UPLOAD_TIMEOUT_MS });
        setEntry(item.seq, null);
        onUploadedRef.current(response.data);
        return;
      } catch (error) {
        console.error(`Segment ${item.seq} upload attempt ${attempt} failed:`, error);
        if (!isRetryable(error)) break;
        if (attempt < MAX_ATTEMPTS) await sleep(1000 * 2 ** (attempt - 1));
      }
    }
    setEntry(item.seq, { ...item, status: 'upload_failed' });
  };

  const enqueue = (item) => {
    const promise = upload(item).finally(() => inFlight.current.delete(promise));
    inFlight.current.add(promise);
  };

  const retry = (seq) => {
    const entry = entries.current.get(seq);
    if (entry && entry.status === 'upload_failed') enqueue(entry);
  };

  /** Resolves once no upload is in flight (including ones started while waiting). */
  const waitForIdle = async () => {
    while (inFlight.current.size > 0) {
      await Promise.allSettled([...inFlight.current]);
    }
  };

  const failedCount = () => [...entries.current.values()].filter((e) => e.status === 'upload_failed').length;

  const reset = () => {
    entries.current.clear();
    rerender();
  };

  return {
    pending: [...entries.current.values()],
    enqueue,
    retry,
    waitForIdle,
    failedCount,
    reset,
  };
}
