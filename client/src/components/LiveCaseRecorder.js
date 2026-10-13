import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Radio, Square, Play, FilePlus } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import useSegmentRecorder, { getMicrophone } from '../hooks/useSegmentRecorder';
import useSegmentUploads from '../hooks/useSegmentUploads';
import { mergeNewer, upsertNewer } from '../utils/liveRows';
import useCaseDetails from '../hooks/useCaseDetails';
import useCaseFindings from '../hooks/useCaseFindings';
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

const newReportId = () => (window.crypto?.randomUUID
  ? window.crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);

const END_REPORT_ATTEMPTS = 3;

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
  // The report being recorded: { caseId, id, clips }. Its clips become new information for the
  // hospital together, once it is sent with its clip count.
  const reportRef = useRef(null);
  // The acknowledgment last announced, so each one is announced once.
  const announcedAck = useRef(null);
  caseRef.current = activeCase;

  const addSegment = useCallback((segment) => setSegments((list) => upsertNewer(list, segment)), []);
  const uploads = useSegmentUploads(addSegment);
  const recorder = useSegmentRecorder({
    segmentMs: SEGMENT_MS,
    onSegment: ({ blob, recordedAt, durationMs }) => {
      const report = reportRef.current;
      if (report) report.clips += 1;
      uploads.enqueue({
        caseId: caseRef.current.id,
        seq: nextSeqRef.current++,
        recorded_at: recordedAt,
        duration_ms: durationMs,
        report_id: report?.id,
        blob,
      });
    },
    onError: (message) => toast.error(message),
  });

  // Tells the server how many clips the report has. If this keeps failing, ending the case or
  // marking arrival still ends the report on the server with the clips it received.
  const endReport = useCallback(async (report) => {
    for (let attempt = 1; attempt <= END_REPORT_ATTEMPTS; attempt += 1) {
      try {
        await axios.post(`/api/cases/${report.caseId}/reports/${report.id}/end`, { clips: report.clips });
        return true;
      } catch (error) {
        if (error.response && error.response.status < 500) break;
        await new Promise((resolve) => setTimeout(resolve, 1000 * attempt));
      }
    }
    toast.error('Could not send the report. It will be sent when you end the case.');
    return false;
  }, []);

  const startReport = (stream) => {
    // A report left open (the microphone dropped out, say) is sent before the next one starts.
    if (reportRef.current) endReport(reportRef.current);
    reportRef.current = { caseId: caseRef.current.id, id: newReportId(), clips: 0 };
    recorder.start(stream);
  };

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
  // Only the latest check applies its answer, and never over a case opened here meanwhile.
  const loadSeq = useRef(0);
  const loadOpenCase = useCallback(async ({ sendOpenReport = false } = {}) => {
    const seq = ++loadSeq.current;
    const stillCurrent = () => seq === loadSeq.current && caseRef.current?.status !== 'active';
    setOpenCaseCheck('checking');
    try {
      const open = (await axios.get('/api/cases?status=active')).data[0];
      if (open) {
        const existing = (await axios.get(`/api/cases/${open.id}/segments`)).data;
        if (!stillCurrent()) return;
        nextSeqRef.current = nextSeqAfter(existing);
        caseRef.current = open;
        setSegments(existing);
        setActiveCase(open);
        // This page was reloaded mid-report: the clips it hadn't uploaded are gone, so send the
        // report with what the server has.
        if (sendOpenReport && open.open_report) {
          endReport({ caseId: open.id, id: open.open_report.id, clips: open.open_report.clips });
        }
      }
      if (seq === loadSeq.current) setOpenCaseCheck('done');
    } catch (error) {
      console.error('Error checking for an open case:', error);
      if (seq === loadSeq.current) setOpenCaseCheck('failed');
    }
  }, [endReport]);

  useEffect(() => {
    loadOpenCase({ sendOpenReport: true });
  }, [loadOpenCase]);

  useEffect(() => {
    axios.get('/api/hospitals')
      .then((response) => setHospitals(response.data))
      .catch((error) => console.error('Error loading hospitals:', error));
  }, []);

  const details = useCaseDetails(activeCase?.id);
  const { addUpdate: addCaseUpdate, reload: reloadCaseUpdates } = details;
  const extracted = useCaseFindings(activeCase?.id);
  const { apply: applyFindings, reload: reloadFindings } = extracted;

  const handleEvent = useCallback((type, data) => {
    const current = caseRef.current;
    if ((type === 'case.opened' || type === 'ready') && current?.status !== 'active') {
      // No case open here: pick up one this EMT opened somewhere else (another device, or the
      // demo replay), including one opened while this screen was disconnected.
      loadOpenCase();
      return;
    }
    if (!current) return;
    if (type === 'ready') {
      // Events sent while disconnected aren't replayed: re-fetch everything this view shows.
      // An answer for a case that is no longer on screen (closed, another one opened) is dropped.
      const caseId = current.id;
      const stillShown = () => caseRef.current?.id === caseId;
      axios.get(`/api/cases/${caseId}/segments`)
        .then((response) => stillShown() && setSegments((list) => mergeNewer(list, response.data)))
        .catch((error) => console.error('Error re-syncing transcript:', error));
      axios.get(`/api/cases/${caseId}`)
        .then((response) => stillShown() && setActiveCase((c) => (c && c.id === caseId && c.updated_at < response.data.updated_at ? response.data : c)))
        .catch((error) => console.error('Error re-syncing case:', error));
      reloadCaseUpdates();
      reloadFindings();
    } else if ((type === 'segment.created' || type === 'segment.updated') && data.case_id === current.id) {
      addSegment(data.segment);
    } else if (type === 'case.updated' && data.case.id === current.id) {
      const ack = data.case.acknowledgment;
      const seen = announcedAck.current?.caseId === current.id
        ? announcedAck.current.version : (current.acknowledgment?.info_version || 0);
      if (ack && ack.info_version > seen) {
        announcedAck.current = { caseId: current.id, version: ack.info_version };
        toast.success(`${data.case.destination_hospital_name || 'The hospital'} saw your latest update (${ack.user_name})`,
          { icon: '✅', duration: 6000 });
      }
      // Seen by the next event in the same batch too (e.g. "closed" then "opened" before a re-render).
      if (current.updated_at < data.case.updated_at) caseRef.current = data.case;
      setActiveCase((c) => (c && c.id === data.case.id && c.updated_at < data.case.updated_at ? data.case : c));
    } else if (type === 'update.created') {
      addCaseUpdate(data.update);
    } else if (type === 'findings.updated') {
      applyFindings(data);
    }
  }, [addSegment, addCaseUpdate, reloadCaseUpdates, applyFindings, reloadFindings, loadOpenCase]);

  // Always connected, so a case opened elsewhere shows up here too.
  const connection = useCaseEvents(handleEvent);

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
      reportRef.current = null;
      if (stream) startReport(stream);
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

  const recordReport = () => runBusy('Connecting microphone…', async () => {
    try {
      startReport(await getMicrophone());
    } catch (error) {
      toast.error(microphoneErrorMessage(error));
    }
  });

  // The hospital gets the report as one update once all its clips are in.
  const sendReport = () => runBusy('Sending report…', async () => {
    await recorder.stop();
    const report = reportRef.current;
    reportRef.current = null;
    if (report) await endReport(report);
  });

  const closeCase = async () => {
    try {
      const response = await axios.post(`/api/cases/${caseRef.current.id}/close`);
      setActiveCase(response.data);
      uploads.reset();
      reportRef.current = null;
      setPatientInfo('');
      setEtaMinutes('');
      toast.success(`Case #${response.data.id} closed`);
    } catch (error) {
      console.error('Error closing case:', error);
      toast.error('Failed to close case — try again');
    }
  };

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
              <span>{busyMessage || 'Start Case & Record Report'}</span>
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
                <span className="h-2 w-2 rounded-full bg-red-600" /> Recording report — the hospital sees the words as you speak
              </span>
              <button
                onClick={sendReport}
                className="flex items-center space-x-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Square className="h-4 w-4" />
                <span>Send Report</span>
              </button>
            </>
          )}
          {!busyMessage && !recorder.isRecording && (
            <>
              <button
                onClick={recordReport}
                className="flex items-center space-x-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Play className="h-4 w-4" />
                <span>Record Report</span>
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
            findings={extracted.findings}
            onRetryAssessment={isOpen ? retryAssessment : undefined}
            onRetryFindings={isOpen ? extracted.retry : undefined}
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
