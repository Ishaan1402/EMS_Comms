import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Radio, Square, Play } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import LiveTranscript from './LiveTranscript';

// Each segment is recorded by its own MediaRecorder so every upload is a complete,
// independently decodable file (timeslice chunks after the first lack headers).
const SEGMENT_MS = 8000;
const MAX_UPLOAD_ATTEMPTS = 4;

const pickMimeType = () => {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
  return candidates.find((type) => window.MediaRecorder && MediaRecorder.isTypeSupported(type)) || '';
};

const extensionFor = (mimeType) => {
  if (mimeType.includes('mp4')) return 'mp4';
  if (mimeType.includes('ogg')) return 'ogg';
  return 'webm';
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const isRetryable = (error) => !error.response || error.response.status >= 500 || error.response.status === 408;

const LiveCaseRecorder = () => {
  const [patientInfo, setPatientInfo] = useState('');
  const [activeCase, setActiveCase] = useState(null);
  const [phase, setPhase] = useState('idle'); // idle | recording | finishing | needs_attention
  const [segments, setSegments] = useState([]);
  const [localUploads, setLocalUploads] = useState({});

  const caseRef = useRef(null);
  const streamRef = useRef(null);
  const recorderRef = useRef(null);
  const segmentTimerRef = useRef(null);
  const recordingRef = useRef(false);
  const seqRef = useRef(0);
  const inFlightRef = useRef(new Set());
  const failedUploadsRef = useRef({});
  const unmountedRef = useRef(false);
  caseRef.current = activeCase;

  const setLocal = (seq, entry) => {
    setLocalUploads((current) => {
      const next = { ...current };
      if (entry) next[seq] = entry;
      else delete next[seq];
      return next;
    });
    if (entry && entry.status === 'upload_failed') failedUploadsRef.current[seq] = entry;
    else delete failedUploadsRef.current[seq];
  };

  const upsertSegment = (segment) =>
    setSegments((list) => {
      const others = list.filter((s) => s.id !== segment.id);
      return [...others, segment];
    });

  const fetchSegments = useCallback(async (caseId) => {
    const response = await axios.get(`/api/cases/${caseId}/segments`);
    setSegments(response.data);
    return response.data;
  }, []);

  // Resume an active case left open by a page refresh.
  useEffect(() => {
    unmountedRef.current = false;
    axios.get('/api/cases?status=active')
      .then(async (response) => {
        const open = response.data[0];
        if (!open) return;
        setActiveCase(open);
        const existing = await fetchSegments(open.id);
        seqRef.current = existing.reduce((max, s) => Math.max(max, s.seq + 1), 0);
        setPhase('needs_attention');
      })
      .catch((error) => console.error('Error checking for active case:', error));
    return () => {
      // Upload what was captured but leave the case open; the EMT can resume or end it later.
      unmountedRef.current = true;
      recordingRef.current = false;
      clearTimeout(segmentTimerRef.current);
      if (recorderRef.current && recorderRef.current.state === 'recording') recorderRef.current.stop();
      if (streamRef.current) streamRef.current.getTracks().forEach((t) => t.stop());
    };
  }, [fetchSegments]);

  const handleEvent = useCallback((type, data) => {
    const current = caseRef.current;
    if (!current) return;
    if (type === 'ready') {
      fetchSegments(current.id).catch(() => {});
    } else if ((type === 'segment.created' || type === 'segment.updated') && data.case_id === current.id) {
      upsertSegment(data.segment);
    }
  }, [fetchSegments]);

  const connection = useCaseEvents(handleEvent, !!activeCase);

  const uploadSegment = async (item) => {
    for (let attempt = 1; attempt <= MAX_UPLOAD_ATTEMPTS; attempt += 1) {
      setLocal(item.seq, { ...item, status: 'uploading', attempt });
      try {
        const form = new FormData();
        form.append('audio', item.blob, `segment-${item.seq}.${extensionFor(item.blob.type)}`);
        form.append('seq', String(item.seq));
        form.append('recorded_at', item.recorded_at);
        form.append('duration_ms', String(item.duration_ms));
        const response = await axios.post(`/api/cases/${item.caseId}/segments`, form, { timeout: 30000 });
        setLocal(item.seq, null);
        upsertSegment(response.data);
        return true;
      } catch (error) {
        console.error(`Segment ${item.seq} upload attempt ${attempt} failed:`, error);
        if (!isRetryable(error)) break;
        if (attempt < MAX_UPLOAD_ATTEMPTS) await sleep(1000 * 2 ** (attempt - 1));
      }
    }
    setLocal(item.seq, { ...item, status: 'upload_failed' });
    return false;
  };

  const enqueueUpload = (item) => {
    const promise = uploadSegment(item).finally(() => inFlightRef.current.delete(promise));
    inFlightRef.current.add(promise);
  };

  const closeCase = async () => {
    const current = caseRef.current;
    try {
      const response = await axios.post(`/api/cases/${current.id}/close`);
      setActiveCase(response.data);
      setLocalUploads({});
      failedUploadsRef.current = {};
      setPhase('idle');
      toast.success(`Case #${current.id} closed`);
    } catch (error) {
      console.error('Error closing case:', error);
      toast.error('Failed to close case — try again');
      setPhase('needs_attention');
    }
  };

  const finishCase = async () => {
    setPhase('finishing');
    while (inFlightRef.current.size > 0) {
      await Promise.allSettled([...inFlightRef.current]);
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (Object.keys(failedUploadsRef.current).length > 0) {
      toast.error('Some audio segments failed to upload. Retry them or end the case anyway.');
      setPhase('needs_attention');
      return;
    }
    await closeCase();
  };

  const recordSegment = () => {
    const stream = streamRef.current;
    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    const startedAt = new Date();
    const caseId = caseRef.current.id;

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || 'audio/webm' });
      if (blob.size > 0) {
        enqueueUpload({
          caseId,
          seq: seqRef.current++,
          recorded_at: startedAt.toISOString(),
          duration_ms: Date.now() - startedAt.getTime(),
          blob,
        });
      }
      if (recordingRef.current) recordSegment();
      else if (!unmountedRef.current) finishCase();
    };
    recorder.onerror = (event) => {
      console.error('MediaRecorder error:', event.error);
      toast.error('Recording error — stopping live case audio');
      recordingRef.current = false;
    };

    recorderRef.current = recorder;
    recorder.start();
    segmentTimerRef.current = setTimeout(() => {
      if (recorder.state === 'recording') recorder.stop();
    }, SEGMENT_MS);
  };

  const startRecording = async (caseToUse) => {
    caseRef.current = caseToUse;
    recordingRef.current = true;
    setPhase('recording');
    recordSegment();
  };

  const getMicrophone = async () => {
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      return true;
    } catch (error) {
      console.error('Microphone error:', error);
      toast.error(error.name === 'NotAllowedError'
        ? 'Microphone access denied. Allow microphone permissions to start a live case.'
        : `Could not access microphone: ${error.message}`);
      return false;
    }
  };

  const startLiveCase = async () => {
    if (!(await getMicrophone())) return;
    try {
      const response = await axios.post('/api/cases', { patient_info: patientInfo.trim() || null });
      setActiveCase(response.data);
      setSegments([]);
      setLocalUploads({});
      failedUploadsRef.current = {};
      seqRef.current = 0;
      toast.success(`Live case #${response.data.id} started — doctors can follow along`);
      startRecording(response.data);
    } catch (error) {
      console.error('Error starting case:', error);
      toast.error('Failed to start live case');
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  };

  const resumeRecording = async () => {
    if (!(await getMicrophone())) return;
    startRecording(caseRef.current);
  };

  const stopRecording = () => {
    recordingRef.current = false;
    clearTimeout(segmentTimerRef.current);
    if (recorderRef.current && recorderRef.current.state === 'recording') {
      recorderRef.current.stop(); // onstop uploads the final segment, then finishes the case
    } else {
      finishCase();
    }
  };

  const retry = async (segment) => {
    if (segment.status === 'upload_failed') {
      enqueueUpload({ ...segment, status: undefined });
      return;
    }
    try {
      const response = await axios.post(`/api/cases/${segment.case_id}/segments/${segment.id}/retry`);
      upsertSegment(response.data);
    } catch (error) {
      console.error('Retry failed:', error);
      toast.error('Could not retry transcription');
    }
  };

  const transcriptItems = [
    ...segments,
    ...Object.values(localUploads).filter((u) => !segments.some((s) => s.seq === u.seq)),
  ];
  const isOpen = activeCase && activeCase.status === 'active';

  return (
    <div className="bg-white rounded-lg shadow-md p-6 mb-8">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Radio className="h-5 w-5 text-red-600" /> Live Case — Stream Updates to Doctors
        </h2>
        {isOpen && (
          <span className={`text-xs font-medium ${connection === 'live' ? 'text-green-700' : 'text-orange-600'}`}>
            {connection === 'live' ? '● Connected' : 'Reconnecting…'}
          </span>
        )}
      </div>

      {!isOpen && (
        <div className="space-y-3">
          <textarea
            value={patientInfo}
            onChange={(e) => setPatientInfo(e.target.value)}
            placeholder="Patient summary (optional): age, sex, chief complaint, location…"
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
            rows="2"
          />
          <button
            onClick={startLiveCase}
            className="flex items-center space-x-2 px-6 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
          >
            <Radio className="h-5 w-5" />
            <span>Start Live Case</span>
          </button>
        </div>
      )}

      {isOpen && (
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <span className="font-medium text-gray-900">Case #{activeCase.id}</span>
          {phase === 'recording' && (
            <>
              <span className="flex items-center gap-2 text-red-600 animate-pulse">
                <span className="h-2 w-2 rounded-full bg-red-600" /> Live — transcript updates every ~{SEGMENT_MS / 1000}s
              </span>
              <button
                onClick={stopRecording}
                className="flex items-center space-x-2 px-4 py-2 bg-gray-700 text-white rounded-lg hover:bg-gray-800"
              >
                <Square className="h-4 w-4" />
                <span>Stop & End Case</span>
              </button>
            </>
          )}
          {phase === 'finishing' && <span className="text-gray-600">Uploading final audio…</span>}
          {phase === 'needs_attention' && (
            <>
              <span className="text-orange-700 text-sm">Case is still open and not recording.</span>
              <button
                onClick={resumeRecording}
                className="flex items-center space-x-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Play className="h-4 w-4" />
                <span>Resume Recording</span>
              </button>
              <button
                onClick={closeCase}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
              >
                End Case
              </button>
            </>
          )}
        </div>
      )}

      {activeCase && (
        <div className="mt-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">
            {isOpen ? 'Transcript (what the doctor sees)' : `Transcript — Case #${activeCase.id} (closed)`}
          </h3>
          <LiveTranscript
            segments={transcriptItems}
            caseStartedAt={activeCase.started_at}
            onRetry={isOpen ? retry : undefined}
            emptyText={phase === 'recording' ? 'Listening… first transcript arrives in a few seconds.' : 'No transcript yet.'}
          />
        </div>
      )}
    </div>
  );
};

export default LiveCaseRecorder;
