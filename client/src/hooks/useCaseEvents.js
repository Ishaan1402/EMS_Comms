import { useEffect, useRef, useState } from 'react';

// The server sends a comment every 15s; silence beyond this means the connection is dead.
const STALE_AFTER_MS = 40000;
const MAX_BACKOFF_MS = 15000;

function parseFrame(frame) {
  let event = 'message';
  const data = [];
  frame.split('\n').forEach((line) => {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  });
  if (data.length === 0) return null;
  try {
    return { event, data: JSON.parse(data.join('\n')) };
  } catch {
    return null;
  }
}

/**
 * Subscribes to /api/cases/events (Server-Sent Events) and calls onEvent(type, data).
 * Uses fetch rather than EventSource so the JWT goes in the Authorization header.
 * Reconnects with backoff; a `ready` event arrives on every (re)connect, which is
 * the cue to re-fetch state because events sent while disconnected are not replayed.
 *
 * Returns the connection status: 'connecting' | 'live' | 'reconnecting'.
 */
export default function useCaseEvents(onEvent, enabled = true) {
  const [status, setStatus] = useState('connecting');
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let controller = null;
    let retryTimer = null;
    let staleTimer = null;
    let failures = 0;

    const resetStaleTimer = () => {
      clearTimeout(staleTimer);
      staleTimer = setTimeout(() => controller && controller.abort(), STALE_AFTER_MS);
    };

    const connect = async () => {
      controller = new AbortController();
      try {
        const response = await fetch('/api/cases/events', {
          headers: {
            Accept: 'text/event-stream',
            Authorization: `Bearer ${localStorage.getItem('token')}`,
          },
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        resetStaleTimer();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          resetStaleTimer();
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
          let boundary;
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const parsed = parseFrame(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
            if (!parsed) continue;
            if (parsed.event === 'ready') {
              failures = 0;
              setStatus('live');
            }
            handlerRef.current(parsed.event, parsed.data);
          }
        }
        throw new Error('stream closed');
      } catch (error) {
        clearTimeout(staleTimer);
        if (cancelled) return;
        failures += 1;
        setStatus('reconnecting');
        retryTimer = setTimeout(connect, Math.min(1000 * 2 ** (failures - 1), MAX_BACKOFF_MS));
      }
    };

    setStatus('connecting');
    connect();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      clearTimeout(staleTimer);
      if (controller) controller.abort();
    };
  }, [enabled]);

  return status;
}
