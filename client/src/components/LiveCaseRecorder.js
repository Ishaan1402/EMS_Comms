import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Radio, Square, Play, FilePlus } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import useSegmentRecorder, { getMicrophone } from '../hooks/useSegmentRecorder';
import useSegmentUploads from '../hooks/useSegmentUploads';
import { mergeNewer, upsertNewer } from '../utils/liveRows';
import useCaseDetails from '../hooks/useCaseDetails';
import LiveTranscript from './LiveTranscript';
import CaseOverview from './CaseOverview';
import CaseUpdateForm from './CaseUpdateForm';
import CaseChat from './CaseChat';

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

const nextSeqAfter = (segments) => segments.reduce((max, s) => Math.max(max, s.seq + 1), 0);

const LiveCaseRecorder = () => {
  const [patientInfo, setPatientInfo] = useState('');
  const [hospitals, setHospitals] = useState([]);
  const [destinationId, setDestinationId] = useState('');
  const [emsUnit, setEmsUnit] = useState('');
  const [etaMinutes, setEtaMinutes] = useState('');
  const [activeCase, setActiveCase] = useState(null);
  const [segments, setSegments] = useState([]);
  const [openCaseCheck, setOpenCaseCheck] = useState('checking'); // checking | done | failed
  const [busyMessage, setBusyMessage] = useState(null);
  const caseRef = useRef(null);
  const nextSeqRef = useRef(0);
  caseRef.current = activeCase;

  const addSegment = useCallback((segment) => setSegments((list) => upsertNewer(list, segment)), []);
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

  const runBusy = async (message, action) => {
    setBusyMessage(message);
    try {
      await action();
    } finally {
      setBusyMessage(null);
    }
  };

  // Pick up a case left open by a page refresh or closed tab. It is shown only once its
  // transcript is loaded, so resumed audio continues after the last seq the server has.
  const loadOpenCase = useCallback(async () => {
    setOpenCaseCheck('checking');
    try {
      const open = (await axios.get('/api/cases?status=active')).data[0];
      if (open) {
        const existing = (await axios.get(`/api/cases/${open.id}/segments`)).data;
        nextSeqRef.current = nextSeqAfter(existing);
        setSegments(existing);
        setActiveCase(open);
      }
      setOpenCaseCheck('done');
    } catch (error) {
      console.error('Error checking for an open case:', error);
      setOpenCaseCheck('failed');
    }
  }, []);

  useEffect(() => {
    loadOpenCase();
  }, [loadOpenCase]);

  useEffect(() => {
    axios.get('/api/hospitals')
      .then((response) => setHospitals(response.data))
      .catch((error) => console.error('Error loading hospitals:', error));
  }, []);

  const details = useCaseDetails(activeCase?.id);
  const addCaseUpdate = details.addUpdate;

  const handleEvent = useCallback((type, data) => {
    const current = caseRef.current;
    if (!current) return;
    if (type === 'ready') {
      axios.get(`/api/cases/${current.id}/segments`)
        .then((response) => setSegments((list) => mergeNewer(list, response.data)))
        .catch((error) => console.error('Error re-syncing transcript:', error));
    } else if ((type === 'segment.created' || type === 'segment.updated') && data.case_id === current.id) {
      addSegment(data.segment);
    } else if (type === 'case.updated' && data.case.id === current.id) {
      setActiveCase((c) => (c && c.updated_at > data.case.updated_at ? c : data.case));
    } else if (type === 'update.created') {
      addCaseUpdate(data.update);
    }
  }, [addSegment, addCaseUpdate]);

  const connection = useCaseEvents(handleEvent, !!activeCase);

  // withAudio: open the microphone and start streaming; otherwise the case starts from typed information.
  const startLiveCase = (withAudio) => runBusy('Starting case…', async () => {
    if (!destinationId) {
      toast.error('Choose the destination hospital');
      return;
    }
    let stream = null;
    if (withAudio) {
      try {
        stream = await getMicrophone();
      } catch (error) {
        toast.error(microphoneErrorMessage(error));
        return;
      }
    }
    try {
      const response = await axios.post('/api/cases', {
        patient_info: patientInfo.trim() || null,
        destination_hospital_id: Number(destinationId),
        ems_unit: emsUnit.trim() || null,
        eta_minutes: etaMinutes === '' ? null : Number(etaMinutes),
      });
      caseRef.current = response.data;
      setActiveCase(response.data);
      setSegments([]);
      uploads.reset();
      nextSeqRef.current = 0;
      if (stream) recorder.start(stream);
      toast.success(`Case #${response.data.id} started — the hospital can follow along`);
    } catch (error) {
      if (stream) stream.getTracks().forEach((track) => track.stop());
      if (error.response?.status === 409) {
        toast.error('You already have an open case — resume or end it first.');
        await loadOpenCase();
      } else {
        console.error('Error starting case:', error);
        toast.error(error.response?.data?.error || 'Failed to start case');
      }
    }
  });

  const resumeRecording = () => runBusy('Connecting microphone…', async () => {
    try {
      recorder.start(await getMicrophone());
    } catch (error) {
      toast.error(microphoneErrorMessage(error));
    }
  });

  const closeCase = async () => {
    try {
      const response = await axios.post(`/api/cases/${caseRef.current.id}/close`);
      setActiveCase(response.data);
      uploads.reset();
      setPatientInfo('');
      setEtaMinutes('');
      toast.success(`Case #${response.data.id} closed`);
    } catch (error) {
      console.error('Error closing case:', error);
      toast.error('Failed to close case — try again');
    }
  };

  const stopAndEndCase = () => runBusy('Uploading remaining audio…', async () => {
    await recorder.stop();
    await uploads.waitForIdle();
    if (uploads.failedCount() > 0) {
      toast.error('Some audio failed to upload. Retry it below, or end the case anyway.');
    } else {
      await closeCase();
    }
  });

  const endCaseAnyway = () => {
    const lost = uploads.failedCount();
    if (lost > 0 && !window.confirm(`${lost} audio segment(s) were never uploaded and will be lost. End the case anyway?`)) {
      return;
    }
    runBusy('Ending case…', async () => {
      await uploads.waitForIdle();
      await closeCase();
    });
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

  const dismissSegment = async (segment) => {
    try {
      addSegment((await axios.post(`/api/cases/${segment.case_id}/segments/${segment.id}/dismiss`)).data);
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not mark the clip as handled');
    }
  };

  const retryAssessment = async () => {
    try {
      await axios.post(`/api/cases/${activeCase.id}/assessments/retry`);
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not retry the assessment');
    }
  };

  const markArrived = async () => {
    try {
      setActiveCase((await axios.post(`/api/cases/${activeCase.id}/arrive`)).data);
    } catch (error) {
      toast.error('Could not mark the patient as arrived');
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
          <Radio className="h-5 w-5 text-red-600" /> Patient Case — Keep the Hospital Updated
        </h2>
        {isOpen && (
          <span className={`text-xs font-medium ${connection === 'live' ? 'text-green-700' : 'text-orange-600'}`}>
            {CONNECTION_LABELS[connection]}
          </span>
        )}
      </div>

      {!isOpen && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <label className="text-sm text-gray-700">
              Destination hospital
              <select
                value={destinationId}
                onChange={(e) => setDestinationId(e.target.value)}
                className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-red-500"
              >
                <option value="">Choose…</option>
                {hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
              </select>
            </label>
            <label className="text-sm text-gray-700">
              EMS unit
              <input
                value={emsUnit}
                onChange={(e) => setEmsUnit(e.target.value)}
                maxLength={40}
                placeholder="e.g. Medic 12"
                className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
              />
            </label>
            <label className="text-sm text-gray-700">
              ETA (minutes)
              <input
                type="number"
                min="0"
                value={etaMinutes}
                onChange={(e) => setEtaMinutes(e.target.value)}
                className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
              />
            </label>
          </div>
          <textarea
            value={patientInfo}
            onChange={(e) => setPatientInfo(e.target.value)}
            placeholder="Patient summary: age, sex, chief complaint, what you found…"
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
            rows="2"
          />
          <div className="flex flex-wrap items-center gap-4">
            <button
              onClick={() => startLiveCase(true)}
              disabled={openCaseCheck !== 'done' || !!busyMessage}
              className="flex items-center space-x-2 px-6 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Radio className="h-5 w-5" />
              <span>{busyMessage || 'Start Case & Record'}</span>
            </button>
            <button
              onClick={() => startLiveCase(false)}
              disabled={openCaseCheck !== 'done' || !!busyMessage}
              className="flex items-center space-x-2 px-4 py-3 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <FilePlus className="h-5 w-5" />
              <span>Start Case Without Audio</span>
            </button>
            {openCaseCheck === 'checking' && <span className="text-sm text-gray-500">Checking for an open case…</span>}
            {openCaseCheck === 'failed' && (
              <span className="text-sm text-red-700">
                Couldn't check for an open case.{' '}
                <button onClick={loadOpenCase} className="underline">Try again</button>
              </span>
            )}
          </div>
        </div>
      )}

      {isOpen && (
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <span className="font-medium text-gray-900">Case #{activeCase.id}</span>
          {busyMessage && <span className="text-gray-600">{busyMessage}</span>}
          {!busyMessage && recorder.isRecording && (
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
          {!busyMessage && !recorder.isRecording && (
            <>
              <span className="text-orange-700 text-sm">Case is open but not recording.</span>
              <button
                onClick={resumeRecording}
                className="flex items-center space-x-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Play className="h-4 w-4" />
                <span>{segments.length || uploads.pending.length ? 'Resume Recording' : 'Start Recording'}</span>
              </button>
              <button
                onClick={endCaseAnyway}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
              >
                End Case
              </button>
            </>
          )}
          {!activeCase.arrived_at && (
            <button onClick={markArrived} className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50">
              Mark Arrived
            </button>
          )}
        </div>
      )}

      {activeCase && (
        <div className="mt-4 space-y-4">
          <CaseOverview
            liveCase={activeCase}
            updates={details.updates}
            vitals={details.vitals}
            onRetryAssessment={isOpen ? retryAssessment : undefined}
          />
          {isOpen && <CaseUpdateForm caseId={activeCase.id} onAdded={addCaseUpdate} />}
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
            onDismiss={dismissSegment}
            emptyText={recorder.isRecording ? 'Listening… first transcript arrives in a few seconds.' : 'No transcript yet.'}
          />
        </div>
      )}

      {activeCase && <CaseChat caseId={activeCase.id} className="mt-6" />}
    </div>
  );
};

export default LiveCaseRecorder;
