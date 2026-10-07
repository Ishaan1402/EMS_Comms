import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, RotateCw, UploadCloud } from 'lucide-react';

// Past this, a pending segment is flagged as delayed rather than just "transcribing".
const SLOW_AFTER_MS = 20000;

const formatClock = (iso) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

const formatOffset = (iso, startIso) => {
  const seconds = Math.max(0, Math.round((new Date(iso) - new Date(startIso)) / 1000));
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, '0');
  return `+${m}:${s}`;
};

/**
 * Chronological transcript for one case.
 * segments: server segments plus (EMT side only) local entries with status
 * 'uploading' or 'upload_failed' for audio not yet accepted by the server.
 */
const LiveTranscript = ({ segments, caseStartedAt, onRetry, emptyText = 'No transcript yet.' }) => {
  const scrollRef = useRef(null);
  const stickToBottom = useRef(true);
  const [now, setNow] = useState(Date.now());
  // When this screen first saw each pending state; comparing against server timestamps
  // would make every segment look delayed on a device whose clock runs fast.
  const pendingSince = useRef(new Map());

  const ordered = [...segments].sort((a, b) => a.seq - b.seq);
  const hasInFlight = ordered.some((s) => s.status === 'pending' || s.status === 'uploading');

  useEffect(() => {
    if (!hasInFlight) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasInFlight]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [segments]);

  const handleScroll = () => {
    const el = scrollRef.current;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  if (ordered.length === 0) {
    return <p className="text-sm text-gray-500 italic py-4">{emptyText}</p>;
  }

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="max-h-96 overflow-y-auto space-y-2 pr-1">
      {ordered.map((segment) => {
        const key = segment.id ? `s-${segment.id}` : `local-${segment.seq}`;
        let waitedMs = 0;
        if (segment.status === 'pending') {
          const pendingKey = `${segment.id}:${segment.updated_at}`;
          if (!pendingSince.current.has(pendingKey)) pendingSince.current.set(pendingKey, Date.now());
          waitedMs = now - pendingSince.current.get(pendingKey);
        }
        return (
          <div key={key} className="flex gap-3 text-sm">
            <div className="w-28 shrink-0 text-right whitespace-nowrap">
              <div className="font-mono text-gray-700">{formatClock(segment.recorded_at)}</div>
              {caseStartedAt && (
                <div className="font-mono text-xs text-gray-400">{formatOffset(segment.recorded_at, caseStartedAt)}</div>
              )}
            </div>
            <div className="flex-1 border-l-2 pl-3 pb-1 border-gray-200">
              {segment.status === 'completed' && (
                segment.text
                  ? <p className="text-gray-900 leading-relaxed">{segment.text}</p>
                  : <p className="text-gray-400 italic">(no speech detected)</p>
              )}

              {segment.status === 'pending' && (
                <p className={`flex items-center gap-2 ${waitedMs > SLOW_AFTER_MS ? 'text-orange-600' : 'text-gray-500'}`}>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {waitedMs > SLOW_AFTER_MS
                    ? `Transcription delayed (${Math.round(waitedMs / 1000)}s)…`
                    : 'Transcribing…'}
                </p>
              )}

              {segment.status === 'uploading' && (
                <p className="flex items-center gap-2 text-gray-500">
                  <UploadCloud className="h-4 w-4 animate-pulse" />
                  {segment.attempt > 1 ? `Uploading (attempt ${segment.attempt})…` : 'Uploading…'}
                </p>
              )}

              {(segment.status === 'failed' || segment.status === 'upload_failed') && (
                <div className="flex items-center gap-3 text-red-700">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>
                    {segment.status === 'failed'
                      ? `Transcription failed: ${segment.error || 'unknown error'}`
                      : 'Upload failed — audio kept on this device'}
                  </span>
                  {onRetry && (
                    <button
                      onClick={() => onRetry(segment)}
                      className="flex items-center gap-1 px-2 py-0.5 text-xs border border-red-300 rounded hover:bg-red-50"
                    >
                      <RotateCw className="h-3 w-3" /> Retry
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default LiveTranscript;
