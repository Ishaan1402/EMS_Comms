import { useCallback, useEffect, useRef } from 'react';
import useSegmentRecorder from './useSegmentRecorder';

/** Keep one microphone session and its final clip bound to the patient it started for. */
export default function useCaseRecording({ caseRef, nextSeqRef, uploads, onError, hasUnsentReport }) {
  const owner = useRef(null);
  const currentRecorder = useRef(null);
  const caseId = caseRef.current?.id;
  const status = caseRef.current?.status;
  const arrivedAt = caseRef.current?.arrived_at ?? null;
  const recorder = useSegmentRecorder({
    onSegment: ({ blob, recordedAt, durationMs, isPreview = false }) => {
      const session = owner.current;
      if (!session) return;
      // Another device or a replay can add clips during this recording.
      const seq = Math.max(session.nextSeq, caseRef.current?.id === session.caseId ? nextSeqRef.current : 0);
      session.nextSeq = seq + 1;
      if (caseRef.current?.id === session.caseId) nextSeqRef.current = session.nextSeq;
      if (!isPreview) session.report.clips += 1;
      uploads.enqueue({ caseId: session.caseId, seq, report_id: session.report.id, is_preview: isPreview,
        recorded_at: recordedAt, duration_ms: durationMs, blob });
    },
    onError,
  });
  currentRecorder.current = recorder;

  const start = (stream, report) => {
    if (owner.current) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    // Read the case now, not from the last render: a case started with audio is set just before this call.
    owner.current = { caseId: report.caseId, arrivedAt: caseRef.current?.arrived_at ?? null, report,
      nextSeq: nextSeqRef.current };
    if (recorder.start(stream)) return true;
    owner.current = null;
    return false;
  };
  const stop = useCallback(() => {
    const session = owner.current;
    if (!session) return Promise.resolve();
    // Several close/reconnect events can arrive together; await the same final-clip handoff.
    if (!session.stopping) session.stopping = currentRecorder.current.stop().finally(() => {
      if (owner.current === session) owner.current = null;
    });
    return session.stopping;
  }, []);

  useEffect(() => {
    const session = owner.current;
    if (session && (caseId !== session.caseId || status !== 'active' || arrivedAt !== session.arrivedAt)) stop();
  }, [caseId, status, arrivedAt, stop]);

  useEffect(() => {
    if (!recorder.isRecording && uploads.pending.length === 0 && !hasUnsentReport) return undefined;
    const warnBeforeLeaving = (event) => {
      event.preventDefault();
      event.returnValue = ''; // Browsers supply their own confirmation wording.
    };
    window.addEventListener('beforeunload', warnBeforeLeaving);
    return () => window.removeEventListener('beforeunload', warnBeforeLeaving);
  }, [recorder.isRecording, uploads.pending.length, hasUnsentReport]);

  return { isRecording: recorder.isRecording, start, stop };
}
