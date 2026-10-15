import { useEffect, useRef, useState } from 'react';
import startLivePreview from '../utils/livePreviewRecorder';

const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
export const MAX_REPORT_MS = 120000;
const pickMimeType = () =>
  MIME_CANDIDATES.find((type) => window.MediaRecorder && MediaRecorder.isTypeSupported(type)) || '';

export const getMicrophone = () =>
  navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });

/**
 * One uninterrupted MediaRecorder per report. Its timeslice chunks share an encoder and
 * headers, so they are assembled into one complete file and uploaded only on Send.
 * Cutting into independently transcribed files can split a spoken number.
 * A separate preview recorder supplies live draft clips. Only the uninterrupted recording
 * becomes a complete report for the AI; onSegment marks previews with isPreview.
 */
export default function useSegmentRecorder({ onSegment, onError }) {
  const [isRecording, setIsRecording] = useState(false);
  const callbacks = useRef({ onSegment, onError });
  callbacks.current = { onSegment, onError };
  const session = useRef(null);

  const stop = () => {
    const current = session.current;
    if (!current) return Promise.resolve();
    clearTimeout(current.timer);
    current.preview?.stop();
    if (current.recorder.state === 'recording') current.recorder.stop();
    return current.finished;
  };

  const start = (stream) => {
    if (session.current) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    const mimeType = pickMimeType();
    const current = { stream, startedAt: new Date(), chunks: [] };
    current.finished = new Promise((resolve) => { current.resolve = resolve; });
    const finish = () => {
      clearTimeout(current.timer);
      stream.getTracks().forEach((track) => track.stop());
      if (session.current === current) session.current = null;
      setIsRecording(false);
      current.resolve();
    };
    try {
      const options = { ...(mimeType ? { mimeType } : {}), audioBitsPerSecond: 128000 };
      const recorder = new MediaRecorder(stream, options);
      current.recorder = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size) current.chunks.push(event.data);
      };
      recorder.onerror = () => callbacks.current.onError('Recording error — send the captured audio or record again');
      recorder.onstop = async () => {
        try {
          await current.preview?.stop();
          const blob = new Blob(current.chunks, { type: recorder.mimeType || mimeType || 'audio/webm' });
          if (blob.size) callbacks.current.onSegment({ blob, recordedAt: current.startedAt.toISOString(),
            durationMs: Date.now() - current.startedAt.getTime() });
        } finally {
          finish();
        }
      };
      session.current = current;
      recorder.start(1000);
      current.preview = startLivePreview(stream, options,
        (clip) => callbacks.current.onSegment(clip), (message) => callbacks.current.onError(message));
      setIsRecording(true);
      current.timer = setTimeout(() => {
        callbacks.current.onError('Two-minute limit reached. Send this report before recording more.');
        stop();
      }, MAX_REPORT_MS);
      return true;
    } catch (error) {
      finish();
      callbacks.current.onError('Could not start recording. Check microphone permissions and browser support.');
      return false;
    }
  };

  // Finish the original report on unmount; its upload owner is captured by useCaseRecording.
  useEffect(() => () => {
    const current = session.current;
    if (!current) return;
    clearTimeout(current.timer);
    current.preview?.stop();
    if (current.recorder.state === 'recording') current.recorder.stop();
    current.stream.getTracks().forEach((track) => track.stop());
  }, []);

  return { isRecording, start, stop };
}
