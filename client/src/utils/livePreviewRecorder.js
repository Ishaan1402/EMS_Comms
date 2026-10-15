/** Independently playable draft clips. The uninterrupted recording owns the microphone. */
export default function startLivePreview(stream, options, onClip, onError) {
  let active = true;
  let current = null;
  let timer;
  let finished = Promise.resolve();
  const stop = () => {
    active = false;
    clearTimeout(timer);
    if (current?.state === 'recording') current.stop();
    return finished;
  };
  const next = () => {
    const chunks = [];
    const startedAt = new Date();
    let resolve;
    finished = new Promise((done) => { resolve = done; });
    try {
      const recorder = new MediaRecorder(stream, options);
      current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => {
        active = false;
        onError('Live captions paused. Send will still transcribe the complete recording.');
      };
      recorder.onstop = () => {
        clearTimeout(timer);
        const blob = new Blob(chunks, { type: recorder.mimeType || options.mimeType || 'audio/webm' });
        if (blob.size) onClip({ blob, recordedAt: startedAt.toISOString(),
          durationMs: Date.now() - startedAt.getTime(), isPreview: true });
        current = null;
        resolve();
        if (active) next();
      };
      recorder.start();
      timer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 8000);
    } catch (error) {
      active = false;
      current = null;
      resolve();
      onError('Live captions unavailable. Send will still transcribe the complete recording.');
    }
  };
  next();
  return { stop };
}
