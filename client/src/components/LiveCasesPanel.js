import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Radio, Clock, WifiOff, CheckCircle } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import { mergeNewer, upsertNewer } from '../utils/liveRows';
import useCaseDetails from '../hooks/useCaseDetails';
import useCaseFindings from '../hooks/useCaseFindings';
import LiveTranscript from './LiveTranscript';
import CaseOverview, { CategoryBadge, OperationalBadge, ProcessingBadge } from './CaseOverview';
import CaseChat from './CaseChat';

// An open case with no new audio for this long is shown as idle instead of LIVE.
const IDLE_AFTER_MS = 2 * 60 * 1000;

const OFFLINE_LABELS = {
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting… transcript may be behind',
  unauthorized: 'Session expired — log in again for live updates',
};

const newestFirst = (cases) => [...cases].sort((a, b) => b.started_at.localeCompare(a.started_at));

// Wait this long after a change before re-fetching the queue, so a burst of events is one request.
const QUEUE_REFRESH_MS = 300;

const ConnectionBadge = ({ status }) =>
  status === 'live' ? (
    <span className="flex items-center gap-1 text-xs font-medium text-green-700">
      <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" /> Live updates
    </span>
  ) : (
    <span className="flex items-center gap-1 text-xs font-medium text-orange-600">
      <WifiOff className="h-3 w-3" /> {OFFLINE_LABELS[status]}
    </span>
  );

const CaseStatusBadge = ({ liveCase, serverNow }) => {
  if (liveCase.status !== 'active') {
    return <span className="px-2 py-0.5 text-xs text-gray-600 bg-gray-200 rounded-full">Closed</span>;
  }
  if (liveCase.arrived_at) return null;  // the Arrived badge says it; it's no longer live
  const idleMs = serverNow - Date.parse(liveCase.last_segment_at || liveCase.started_at);
  if (idleMs > IDLE_AFTER_MS) {
    return (
      <span className="px-2 py-0.5 text-xs font-medium text-orange-700 bg-orange-100 rounded-full">
        Open · no audio for {Math.floor(idleMs / 60000)}m
      </span>
    );
  }
  return <span className="px-2 py-0.5 text-xs font-bold text-white bg-red-600 rounded-full animate-pulse">LIVE</span>;
};

const CaseListItem = ({ liveCase: c, selected, onSelect, unseen, serverNow }) => (
  <li>
    <button
      onClick={() => onSelect(c.id)}
      className={`w-full text-left px-4 py-3 border-b border-gray-100 hover:bg-blue-50 ${selected ? 'bg-blue-50' : ''}`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-medium text-gray-900">
          {c.queue_position && <span className="mr-2 text-gray-400">#{c.queue_position}</span>}
          Case #{c.id}
        </span>
        <div className="flex items-center gap-2">
          {unseen > 0 && <span className="px-2 text-xs font-bold text-white bg-blue-600 rounded-full">{unseen} new</span>}
          <CaseStatusBadge liveCase={c} serverNow={serverNow} />
        </div>
      </div>
      <div className="flex flex-wrap gap-1 mb-1">
        {c.status === 'active' && <OperationalBadge status={c.operational_status} />}
        <CategoryBadge liveCase={c} />
        {c.processing?.needs_review && <ProcessingBadge processing={c.processing} />}
      </div>
      <p className="text-sm text-gray-700 truncate">{c.patient_info || 'No patient info'}</p>
      {c.queue_reasons && (
        <p className="text-xs text-gray-600 mt-0.5 truncate" title={c.queue_reasons.join(' · ')}>
          Why here: {c.queue_reasons.join(' · ')}
        </p>
      )}
      <p className="text-xs text-gray-500 mt-1 flex items-center gap-1">
        <Clock className="h-3 w-3" />
        {new Date(c.started_at).toLocaleTimeString()} · EMT {c.emt_first_name} {c.emt_last_name} · {c.segment_count || 0} segments
      </p>
    </button>
  </li>
);

const LiveCasesPanel = () => {
  const [cases, setCases] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [segments, setSegments] = useState([]);
  const [unseen, setUnseen] = useState({});
  // Server clock minus local clock, so idle times aren't skewed by a wrong local clock.
  const [clockOffsetMs, setClockOffsetMs] = useState(0);
  const [now, setNow] = useState(Date.now());
  const selectedRef = useRef(null);
  selectedRef.current = selectedId;
  const details = useCaseDetails(selectedId);
  const { addUpdate: addCaseUpdate, reload: reloadUpdates } = details;
  const extracted = useCaseFindings(selectedId);
  const { apply: applyFindings, reload: reloadFindings } = extracted;
  // The hospital whose inbound cases this user sees; null if the account has none.
  const [hospital, setHospital] = useState(undefined);
  // Order, counts and reasons come from the server (GET /api/cases/queue); this view never re-sorts.
  const [queue, setQueue] = useState({ cases: [], counts: null, rule: '' });
  const queueTimer = useRef(null);

  const fetchQueue = useCallback(async () => {
    try {
      const response = await axios.get('/api/cases/queue');
      setQueue(response.data);
      setSelectedId((current) => current ?? response.data.cases[0]?.id ?? null);
    } catch (error) {
      console.error('Error fetching queue:', error);
    }
  }, []);

  const refreshQueueSoon = useCallback(() => {
    clearTimeout(queueTimer.current);
    queueTimer.current = setTimeout(fetchQueue, QUEUE_REFRESH_MS);
  }, [fetchQueue]);

  useEffect(() => () => clearTimeout(queueTimer.current), []);

  useEffect(() => {
    axios.get('/api/hospitals/mine')
      .then((response) => setHospital(response.data))
      .catch((error) => console.error('Error loading hospital:', error));
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);

  const fetchCases = useCallback(async () => {
    try {
      const response = await axios.get('/api/cases');
      setCases((current) => mergeNewer(current, response.data));
    } catch (error) {
      console.error('Error fetching cases:', error);
    }
  }, []);

  const fetchSegments = useCallback(async (caseId) => {
    try {
      const response = await axios.get(`/api/cases/${caseId}/segments`);
      if (selectedRef.current === caseId) setSegments((current) => mergeNewer(current, response.data));
    } catch (error) {
      console.error('Error fetching transcript:', error);
      toast.error('Failed to load transcript');
    }
  }, []);

  // Load even if the event stream can't connect; merging makes the overlap with `ready` harmless.
  useEffect(() => {
    fetchCases();
    fetchQueue();
  }, [fetchCases, fetchQueue]);

  useEffect(() => {
    setSegments([]);
    if (!selectedId) return;
    fetchSegments(selectedId);
    setUnseen((u) => ({ ...u, [selectedId]: 0 }));
  }, [selectedId, fetchSegments]);

  // The stream sends `ready` on every (re)connect; events missed while disconnected are re-fetched.
  const handleEvent = useCallback((type, data) => {
    if (type === 'ready') {
      setClockOffsetMs(Date.parse(data.server_time) - Date.now());
      fetchCases();
      fetchQueue();
      if (selectedRef.current) {
        fetchSegments(selectedRef.current);
        reloadUpdates();
        reloadFindings();
      }
    } else if (type === 'update.created') {
      addCaseUpdate(data.update);
    } else if (type === 'findings.updated') {
      applyFindings(data);
    } else if (type === 'case.opened' || type === 'case.updated') {
      setCases((list) => upsertNewer(list, data.case));
      refreshQueueSoon();
      if (type === 'case.opened') {
        toast(`New live case from ${data.case.emt_first_name} ${data.case.emt_last_name}`, { icon: '🚑' });
        setSelectedId((current) => current ?? data.case.id);
      }
    } else if (type === 'segment.created' || type === 'segment.updated') {
      if (data.case_id === selectedRef.current) {
        setSegments((list) => upsertNewer(list, data.segment));
      } else if (type === 'segment.created') {
        setUnseen((u) => ({ ...u, [data.case_id]: (u[data.case_id] || 0) + 1 }));
      }
    }
  }, [fetchCases, fetchQueue, refreshQueueSoon, fetchSegments, addCaseUpdate, reloadUpdates, applyFindings,
    reloadFindings]);

  const connection = useCaseEvents(handleEvent);
  const byId = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);
  // Queue entries show the freshest copy of each case, in the server's order.
  const queued = queue.cases.map((entry) => ({ ...entry, ...(byId.get(entry.id) || {}),
    queue_position: entry.queue_position, queue_reasons: entry.queue_reasons }));
  const queuedIds = new Set(queued.map((c) => c.id));
  const others = newestFirst(cases.filter((c) => !queuedIds.has(c.id)));
  const selected = byId.get(selectedId) || queued.find((c) => c.id === selectedId);
  const serverNow = now + clockOffsetMs;

  const dismissSegment = async (segment) => {
    try {
      const response = await axios.post(`/api/cases/${segment.case_id}/segments/${segment.id}/dismiss`);
      setSegments((list) => upsertNewer(list, response.data));
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not mark the clip as handled');
    }
  };

  const caseAction = async (path, body, failure) => {
    try {
      const response = await axios.post(`/api/cases/${selected.id}/${path}`, body);
      if (response.data?.id) setCases((list) => upsertNewer(list, response.data));
      refreshQueueSoon();
    } catch (error) {
      console.error(`Error on ${path}:`, error);
      toast.error(error.response?.data?.error || failure);
    }
  };
  // Acknowledge the version on screen, so information that arrives meanwhile stays unacknowledged.
  const acknowledge = () => caseAction('acknowledge', { info_version: selected.info_version }, 'Could not acknowledge');
  const markArrived = () => caseAction('arrive', undefined, 'Could not mark as arrived');
  const retryAssessment = () => caseAction('assessments/retry', undefined, 'Could not retry the assessment');

  return (
    <div className="bg-white rounded-xl shadow-lg border border-gray-100 overflow-hidden mb-8">
      <div className="bg-gradient-to-r from-gray-50 to-gray-100 px-6 py-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-gradient-to-r from-red-600 to-pink-600 rounded-lg">
            <Radio className="h-5 w-5 text-white" />
          </div>
          <h2 className="text-xl font-semibold text-gray-900">
            Inbound Cases{hospital ? ` — ${hospital.name}` : ''}
          </h2>
        </div>
        <ConnectionBadge status={connection} />
      </div>

      {queue.counts && (
        <div className="px-6 py-3 border-b border-gray-200 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span><span className="font-bold text-gray-900">{queue.counts.inbound}</span> inbound</span>
          <span className="text-purple-700"><span className="font-bold">{queue.counts.needs_review}</span> need review</span>
          <span className="text-orange-700"><span className="font-bold">{queue.counts.unacknowledged}</span> not acknowledged</span>
          <span className="text-red-700"><span className="font-bold">{queue.counts.prepare_now}</span> prepare now</span>
          <span className="text-xs text-gray-500 ml-auto" title={queue.rule}>Order: draft rule</span>
        </div>
      )}

      {cases.length === 0 ? (
        <p className="p-6 text-sm text-gray-500">
          {hospital === null
            ? 'Your account isn\'t linked to a hospital, so no inbound cases are shown. Ask an administrator to assign one.'
            : 'No inbound cases. They appear here as soon as an EMT routes one to your hospital.'}
        </p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3">
          <ul className="border-r border-gray-200 max-h-[32rem] overflow-y-auto">
            {queued.map((c) => (
              <CaseListItem key={c.id} liveCase={c} selected={c.id === selectedId} onSelect={setSelectedId}
                            unseen={unseen[c.id]} serverNow={serverNow} />
            ))}
            {others.length > 0 && (
              <li className="px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-100">
                Arrived &amp; closed
              </li>
            )}
            {others.map((c) => (
              <CaseListItem key={c.id} liveCase={c} selected={c.id === selectedId} onSelect={setSelectedId}
                            unseen={unseen[c.id]} serverNow={serverNow} />
            ))}
          </ul>

          <div className="lg:col-span-2 p-6">
            {selected ? (
              <>
                <div className="mb-4">
                  <h3 className="text-lg font-semibold text-gray-900">
                    Case #{selected.id} — EMT {selected.emt_first_name} {selected.emt_last_name}
                  </h3>
                  <p className="text-sm text-gray-600">{selected.patient_info || 'No patient info provided'}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    Started {new Date(selected.started_at).toLocaleString()}
                    {selected.closed_at && ` · Closed ${new Date(selected.closed_at).toLocaleTimeString()}`}
                  </p>
                </div>
                {selected.status === 'active' && (
                  <div className="flex flex-wrap gap-2 mb-4">
                    {!selected.latest_update_acknowledged && (
                      <button
                        onClick={acknowledge}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700"
                      >
                        <CheckCircle className="h-4 w-4" /> Acknowledge latest update
                      </button>
                    )}
                    {!selected.arrived_at && (
                      <button
                        onClick={markArrived}
                        className="px-3 py-1.5 text-sm border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
                      >
                        Mark arrived
                      </button>
                    )}
                  </div>
                )}
                <div className="mb-6">
                  <CaseOverview
                    liveCase={selected}
                    updates={details.updates}
                    vitals={details.vitals}
                    findings={extracted.findings}
                    onRetryAssessment={retryAssessment}
                    onRetryFindings={extracted.retry}
                  />
                </div>
                <h4 className="text-sm font-semibold text-gray-700 mb-2">Transcript</h4>
                <LiveTranscript
                  segments={segments}
                  caseStartedAt={selected.started_at}
                  onDismiss={dismissSegment}
                  emptyText={selected.status === 'active' ? 'Waiting for the EMT\'s first audio update…' : 'No transcript was recorded for this case.'}
                />
                <CaseChat caseId={selected.id} className="mt-6" />
              </>
            ) : (
              <p className="text-sm text-gray-500">Select a case to follow its transcript.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default LiveCasesPanel;
