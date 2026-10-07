import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Radio, Square, Play } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import useSegmentRecorder, { getMicrophone } from '../hooks/useSegmentRecorder';
import useSegmentUploads from '../hooks/useSegmentUploads';
import LiveTranscript from './LiveTranscript';

const SEGMENT_MS = 8000;

const CONNECTION_LABELS = {
  connecting: 'Connecting…',
  live: '● Connected',
  reconnecting: 'Reconnecting…',
  unauthorized: 'Session expired — log in again',
};

const microphoneErrorMessage = (error) => (error.name === 'NotAllowedError'
  ? 'Microphone access denied. Allow microphone permissions to start a live case.'
  : `Could not access microphone: ${error.message}`);

const upsertById = (list, item) => [...list.filter((x) => x.id !== item.id), item];

const LiveCaseRecorder = () => {
  const [patientInfo, setPatientInfo] = useState('');
  const [activeCase, setActiveCase] = useState(null);
  const [segments, setSegments] = useState([]);
  const [busy, setBusy] = useState(false);
  const caseRef = useRef(null);
  const nextSeqRef = useRef(0);
  caseRef.current = activeCase;

  const addSegment = useCallback((segment) => setSegments((list) => upsertById(list, segment)), []);
  const uploads = useSegmentUploads(addSegment);
  const recorder = useSegmentRecorder({
    segmentMs: SEGMENT_MS,
    onSegment: ({ blob, recordedAt, durationMs }) => uploads.enqueue({
      caseId: caseRef.current.id,
      seq: nextSeqRef.current++,
      recorded_at: recordedAt,
      duration_ms: durationMs,
      blob,
    }),
    onError: (message) => toast.error(message),
  });

  const fetchSegments = useCallback(async (caseId) => {
    const response = await axios.get(`/api/cases/${caseId}/segments`);
    setSegments(response.data);
    return response.data;
  }, []);

  // A case left open by a page refresh can be resumed or ended.
  useEffect(() => {
    axios.get('/api/cases?status=active')
      .then(async (response) => {
        const open = response.data[0];
        if (!open || caseRef.current) return;
        setActiveCase(open);
        const existing = await fetchSegments(open.id);
        nextSeqRef.current = existing.reduce((max, s) => Math.max(max, s.seq + 1), 0);
      })
      .catch((error) => console.error('Error checking for an open case:', error));
  }, [fetchSegments]);

  const handleEvent = useCallback((type, data) => {
    const current = caseRef.current;
    if (!current) return;
    if (type === 'ready') {
      fetchSegments(current.id).catch((error) => console.error('Error re-syncing transcript:', error));
    } else if ((type === 'segment.created' || type === 'segment.updated') && data.case_id === current.id) {
      addSegment(data.segment);
    }
  }, [fetchSegments, addSegment]);

  const connection = useCaseEvents(handleEvent, !!activeCase);

  const startLiveCase = async () => {
    let stream;
    try {
      stream = await getMicrophone();
    } catch (error) {
      toast.error(microphoneErrorMessage(error));
      return;
    }
    try {
      const response = await axios.post('/api/cases', { patient_info: patientInfo.trim() || null });
      caseRef.current = response.data;
      setActiveCase(response.data);
      setSegments([]);
      uploads.reset();
      nextSeqRef.current = 0;
      recorder.start(stream);
      toast.success(`Live case #${response.data.id} started — doctors can follow along`);
    } catch (error) {
      console.error('Error starting case:', error);
      stream.getTracks().forEach((track) => track.stop());
      toast.error('Failed to start live case');
    }
  };

  const resumeRecording = async () => {
    try {
      recorder.start(await getMicrophone());
    } catch (error) {
      toast.error(microphoneErrorMessage(error));
    }
  };

  const closeCase = async () => {
    try {
      const response = await axios.post(`/api/cases/${caseRef.current.id}/close`);
      setActiveCase(response.data);
      uploads.reset();
      setPatientInfo('');
      toast.success(`Case #${response.data.id} closed`);
    } catch (error) {
      console.error('Error closing case:', error);
      toast.error('Failed to close case — try again');
    }
  };

  const stopAndEndCase = async () => {
    setBusy(true);
    await recorder.stop();
    await uploads.waitForIdle();
    if (uploads.failedCount() > 0) {
      toast.error('Some audio failed to upload. Retry it below, or end the case anyway.');
    } else {
      await closeCase();
    }
    setBusy(false);
  };

  const endCaseAnyway = async () => {
    const lost = uploads.failedCount();
    if (lost > 0 && !window.confirm(`${lost} audio segment(s) were never uploaded and will be lost. End the case anyway?`)) {
      return;
    }
    setBusy(true);
    await uploads.waitForIdle();
    await closeCase();
    setBusy(false);
  };

  const retry = async (segment) => {
    if (segment.status === 'upload_failed') {
      uploads.retry(segment.seq);
      return;
    }
    try {
      const response = await axios.post(`/api/cases/${segment.case_id}/segments/${segment.id}/retry`);
      addSegment(response.data);
    } catch (error) {
      console.error('Retry failed:', error);
      toast.error('Could not retry transcription');
    }
  };

  const isOpen = activeCase?.status === 'active';
  const transcriptItems = [
    ...segments,
    ...uploads.pending.filter((item) => !segments.some((s) => s.seq === item.seq)),
  ];

  return (
    <div className="bg-white rounded-lg shadow-md p-6 mb-8">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Radio className="h-5 w-5 text-red-600" /> Live Case — Stream Updates to Doctors
        </h2>
        {isOpen && (
          <span className={`text-xs font-medium ${connection === 'live' ? 'text-green-700' : 'text-orange-600'}`}>
            {CONNECTION_LABELS[connection]}
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
          {busy && <span className="text-gray-600">Uploading remaining audio…</span>}
          {!busy && recorder.isRecording && (
            <>
              <span className="flex items-center gap-2 text-red-600 animate-pulse">
                <span className="h-2 w-2 rounded-full bg-red-600" /> Live — transcript updates every ~{SEGMENT_MS / 1000}s
              </span>
              <button
                onClick={stopAndEndCase}
                className="flex items-center space-x-2 px-4 py-2 bg-gray-700 text-white rounded-lg hover:bg-gray-800"
              >
                <Square className="h-4 w-4" />
                <span>Stop & End Case</span>
              </button>
            </>
          )}
          {!busy && !recorder.isRecording && (
            <>
              <span className="text-orange-700 text-sm">Case is open but not recording.</span>
              <button
                onClick={resumeRecording}
                className="flex items-center space-x-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Play className="h-4 w-4" />
                <span>Resume Recording</span>
              </button>
              <button
                onClick={endCaseAnyway}
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
            emptyText={recorder.isRecording ? 'Listening… first transcript arrives in a few seconds.' : 'No transcript yet.'}
          />
        </div>
      )}
    </div>
  );
};

export default LiveCaseRecorder;
