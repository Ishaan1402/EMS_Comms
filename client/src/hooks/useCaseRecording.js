import { useCallback, useEffect, useRef } from 'react';
import useSegmentRecorder from './useSegmentRecorder';

/** Keep one microphone session and its final clip bound to the patient it started for. */
export default function useCaseRecording({ caseRef, nextSeqRef, uploads, segmentMs, onError, hasUnsentReport }) {
  const owner = useRef(null);
  const currentRecorder = useRef(null);
  const caseId = caseRef.current?.id;
  const status = caseRef.current?.status;
  const arrivedAt = caseRef.current?.arrived_at;
  const recorder = useSegmentRecorder({
    segmentMs,
    onSegment: ({ blob, recordedAt, durationMs }) => {
      const session = owner.current;
      if (!session) return;
      const seq = session.nextSeq++;
      if (caseRef.current?.id === session.caseId) nextSeqRef.current = session.nextSeq;
      session.report.clips += 1;
      uploads.enqueue({ caseId: session.caseId, seq, report_id: session.report.id,
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
    owner.current = { caseId: report.caseId, arrivedAt, report, nextSeq: nextSeqRef.current };
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
