import { useEffect, useRef, useState } from 'react';

const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];

const pickMimeType = () =>
  MIME_CANDIDATES.find((type) => window.MediaRecorder && MediaRecorder.isTypeSupported(type)) || '';

export const getMicrophone = () =>
  navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });

const isLive = (stream) => stream.getAudioTracks().some((track) => track.readyState === 'live');

/**
 * Records a microphone stream as back-to-back segments of `segmentMs`.
 * Each segment uses a fresh MediaRecorder, so every blob is a complete,
 * independently decodable file (timeslice chunks after the first lack headers).
 *
 * onSegment({ blob, recordedAt, durationMs }) fires once per finished segment,
 * including the partial one captured when recording stops.
 * onError(message) fires if recording stops on its own (device lost, recorder error).
 */
export default function useSegmentRecorder({ segmentMs, onSegment, onError }) {
  const [isRecording, setIsRecording] = useState(false);
  const callbacks = useRef({ onSegment, onError });
  callbacks.current = { onSegment, onError };

  const streamRef = useRef(null);
  const recorderRef = useRef(null);
  const timerRef = useRef(null);
  const activeRef = useRef(false);
  const onStoppedRef = useRef(null);

  const finish = () => {
    activeRef.current = false;
    if (streamRef.current) streamRef.current.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
    setIsRecording(false);
    if (onStoppedRef.current) onStoppedRef.current();
    onStoppedRef.current = null;
  };

  const recordNextSegment = () => {
    const stream = streamRef.current;
    if (!isLive(stream)) {
      callbacks.current.onError('Microphone disconnected — recording stopped');
      finish();
      return;
    }

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    const startedAt = new Date();

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onerror = (event) => {
      console.error('MediaRecorder error:', event.error);
      if (activeRef.current) callbacks.current.onError('Recording error — recording stopped');
      activeRef.current = false;
    };
    recorder.onstop = () => {
      clearTimeout(timerRef.current);
      const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || 'audio/webm' });
      if (blob.size > 0) {
        callbacks.current.onSegment({
          blob,
          recordedAt: startedAt.toISOString(),
          durationMs: Date.now() - startedAt.getTime(),
        });
      }
      if (activeRef.current) recordNextSegment();
      else finish();
    };

    recorderRef.current = recorder;
    recorder.start();
    timerRef.current = setTimeout(() => {
      if (recorder.state === 'recording') recorder.stop();
    }, segmentMs);
  };

  /** Begin recording from a stream obtained with getMicrophone(); this hook takes ownership of it. */
  const start = (stream) => {
    streamRef.current = stream;
    activeRef.current = true;
    setIsRecording(true);
    recordNextSegment();
  };

  /** Stop recording. Resolves after the final partial segment has been passed to onSegment. */
  const stop = () => new Promise((resolve) => {
    activeRef.current = false;
    clearTimeout(timerRef.current);
    const recorder = recorderRef.current;
    if (recorder && recorder.state === 'recording') {
      onStoppedRef.current = resolve;
      recorder.stop();
    } else {
      finish();
      resolve();
    }
  });

  // Unmounting hands off the last partial segment and releases the microphone.
  useEffect(() => () => {
    activeRef.current = false;
    clearTimeout(timerRef.current);
    const recorder = recorderRef.current;
    if (recorder && recorder.state === 'recording') recorder.stop();
    else if (streamRef.current) streamRef.current.getTracks().forEach((track) => track.stop());
  }, []);

  return { isRecording, start, stop };
}
