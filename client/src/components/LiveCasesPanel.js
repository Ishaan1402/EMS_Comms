import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Truck, WifiOff } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import { mergeNewer, upsertNewer } from '../utils/liveRows';
import { DEFAULT_VIEW, applyQueueView, isDefaultView, viewCounts } from '../utils/queueView';
import useCaseDetails from '../hooks/useCaseDetails';
import useCaseFindings from '../hooks/useCaseFindings';
import LiveTranscript from './LiveTranscript';
import CaseOverview from './CaseOverview';
import QueueRow from './QueueRow';
import CaseHeader from './CaseHeader';
import { SectionLabel, StatusText } from './ui';
import CaseChat from './CaseChat';
import QueueToolbar from './QueueToolbar';
import { CrewAlertPanel, CrewAlertSound } from './CrewAlerts';
import { CaseRiskFields, DragHandle } from './DoctorQueueSettings';
import useDragReorder from '../hooks/useDragReorder';

const OFFLINE_LABELS = {
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting… transcript may be behind',
  unauthorized: 'Session expired — log in again for live updates',
};

const newestFirst = (cases) => [...cases].sort((a, b) => b.started_at.localeCompare(a.started_at));

// Wait this long after a change before re-fetching the queue, so a burst of events is one request.
const QUEUE_REFRESH_MS = 300;
// How long a case that changed position shows where it came from.
const MOVE_CUE_MS = 30 * 1000;

const ConnectionBadge = ({ status }) =>
  status === 'live' ? (
    <StatusText tone="ok">Live</StatusText>
  ) : (
    <StatusText tone="attention" dot={false}>
      <WifiOff className="h-3.5 w-3.5" /> {OFFLINE_LABELS[status]}
    </StatusText>
  );

const LiveCasesPanel = () => {
  const [cases, setCases] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [segments, setSegments] = useState([]);
  const [riskRefresh, setRiskRefresh] = useState(0);
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
  const applyHospital = useCallback((next) => setHospital((current) => {
    if (current && next && current.id === next.id && current.settings_version > next.settings_version) return current;
    return next;
  }), []);
  const fetchHospital = useCallback(() => {
    axios.get('/api/hospitals/mine').then((response) => applyHospital(response.data))
      .catch(() => toast.error('Could not load hospital settings'));
  }, [applyHospital]);
  // Order, counts and reasons come from the server (GET /api/cases/queue); this view never re-sorts.
  const [queue, setQueue] = useState({ cases: [], counts: null, rule: '' });
  const queueTimer = useRef(null);
  // Cases that just changed position: id -> { from, to }, cleared after MOVE_CUE_MS.
  const [moves, setMoves] = useState({});
  const positions = useRef(null);
  // Overlapping fetches can be answered out of order: keep the snapshot with the latest server as_of.
  const queueAsOf = useRef('');
  // Doctors' own settings (DoctorQueueSettings, shown in QueueStrip): the risk fields, and the latest live risk values.
  const [riskFields, setRiskFields] = useState([]);
  const [liveRiskValues, setLiveRiskValues] = useState(null);

  const fetchQueue = useCallback(async () => {
    try {
      const response = await axios.get('/api/cases/queue');
      if (response.data.as_of <= queueAsOf.current) return;
      queueAsOf.current = response.data.as_of;
      const next = Object.fromEntries(response.data.cases.map((c) => [c.id, c.queue_position]));
      const moved = positions.current ? Object.entries(next)
        .filter(([id, position]) => positions.current[id] && positions.current[id] !== position)
        .map(([id, position]) => [id, { from: positions.current[id], to: position }]) : [];
      positions.current = next;
      if (moved.length) {
        setMoves((current) => ({ ...current, ...Object.fromEntries(moved) }));
        setTimeout(() => setMoves((current) => {
          const rest = { ...current };
          moved.forEach(([id, move]) => { if (rest[id] === move) delete rest[id]; });
          return rest;
        }), MOVE_CUE_MS);
      }
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
    fetchHospital();
  }, [fetchHospital]);

  const hospitalId = hospital?.id;
  const fetchConfig = useCallback(() => {
    if (!hospitalId) return;
    axios.get('/api/hospitals/mine/config').then((response) => setRiskFields(response.data.risk_fields))
      .catch(() => toast.error('Could not load risk fields'));
  }, [hospitalId]);
  useEffect(() => { fetchConfig(); }, [fetchConfig]);

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
      setRiskRefresh((version) => version + 1);
      setClockOffsetMs(Date.parse(data.server_time) - Date.now());
      fetchHospital();
      fetchConfig();
      fetchCases();
      fetchQueue();
      if (selectedRef.current) {
        fetchSegments(selectedRef.current);
        reloadUpdates();
        reloadFindings();
      }
    } else if (type === 'hospital.updated') {
      applyHospital(data.hospital);
    } else if (type === 'queue.reordered') {
      refreshQueueSoon();
    } else if (type === 'risk_fields.updated') {
      setRiskFields(data.risk_fields);
    } else if (type === 'risk_values.updated') {
      setLiveRiskValues(data);
    } else if (type === 'update.created') {
      addCaseUpdate(data.update);
    } else if (type === 'findings.updated') {
      applyFindings(data);
      refreshQueueSoon();  // the rows show which vitals changed
    } else if (type === 'case.opened' || type === 'case.updated') {
      setCases((list) => upsertNewer(list, data.case));
      refreshQueueSoon();
      if (type === 'case.opened') {
        toast(`New live case from ${data.case.emt_first_name} ${data.case.emt_last_name}`, { icon: <Truck className="h-4 w-4" /> });
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
    reloadFindings, fetchHospital, applyHospital, fetchConfig]);

  const connection = useCaseEvents(handleEvent);
  const byId = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);
  // Queue entries show the freshest copy of each case, in the server's order.
  const queued = queue.cases.map((entry) => ({ ...entry, ...(byId.get(entry.id) || {}),
    queue_position: entry.queue_position, queue_reasons: entry.queue_reasons }));
  const queuedIds = new Set(queued.map((c) => c.id));
  const others = newestFirst(cases.filter((c) => !queuedIds.has(c.id)));
  const selectedInQueue = queued.find((c) => c.id === selectedId);
  const selected = byId.get(selectedId) || selectedInQueue;
  const serverNow = now + clockOffsetMs;
  // This screen's own view of the queue (a filter and a sort); it resets on reload and never changes the order.
  const [view, setView] = useState(DEFAULT_VIEW);
  const viewContext = { serverNow, arrivalMinutes: hospital?.arrival_highlight_minutes };
  const visible = applyQueueView(queued, view, viewContext);
  const counts = queue.counts ? viewCounts(queued, viewContext) : null;
  const autoOrder = queue.auto_order !== false;

  // Manual order: move a row and save the whole order for the hospital. The screen shows the new order
  // at once (without a "from #" cue, since this doctor made the move) and the server's queue replaces it.
  const orderIds = queued.map((c) => c.id);
  const moveCase = async (id, to) => {
    const ids = orderIds.filter((other) => other !== id);
    ids.splice(to, 0, id);
    const position = Object.fromEntries(ids.map((id, index) => [id, index + 1]));
    positions.current = position;
    setQueue((current) => ({ ...current, cases: [...current.cases]
      .map((entry) => ({ ...entry, queue_position: position[entry.id] ?? entry.queue_position }))
      .sort((a, b) => a.queue_position - b.queue_position) }));
    try {
      await axios.put('/api/hospitals/mine/doctor-order', { case_ids: ids });
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not move the case');
    }
    fetchQueue();
  };
  const reorder = useDragReorder(orderIds, moveCase);
  const canReorder = !autoOrder && isDefaultView(view) && queued.length > 1;

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
    <div className="mb-8 overflow-clip rounded-xl border border-gray-200 bg-white">
      <div className="flex items-center justify-between px-5 py-3.5">
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-semibold text-gray-900">Inbound cases</h2>
          {hospital && <span className="text-sm text-gray-500">{hospital.name}</span>}
        </div>
        <div className="flex items-center gap-2">
          <ConnectionBadge status={connection} />
          <CrewAlertSound cases={cases} />
        </div>
      </div>

      <QueueToolbar counts={counts} filter={view.filter} onFilter={(filter) => setView((v) => ({ ...v, filter }))}
                    sort={view.sort} onSort={(sort) => setView((v) => ({ ...v, sort }))}
                    queueMode={queue.queue_mode} onModeChange={fetchQueue} hasHospital={Boolean(hospital?.id)} />

      {cases.length === 0 ? (
        <p className="p-6 text-sm text-gray-500">
          {hospital === null
            ? 'Your account isn\'t linked to a hospital, so no inbound cases are shown. Ask an administrator to assign one.'
            : 'No inbound cases. They appear here as soon as an EMT routes one to your hospital.'}
        </p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-5">
          <div className="lg:col-span-2 border-r border-gray-200">
            {!autoOrder && !isDefaultView(view) && (
              <p className="border-b border-gray-100 px-4 py-2 text-xs text-gray-500">
                Show all cases in queue order to drag them into a new order.
              </p>
            )}
            <ul className="max-h-[48rem] overflow-y-auto">
              {visible.map((c, index) => (
                <QueueRow key={c.id} liveCase={c} selected={c.id === selectedId} onSelect={setSelectedId}
                              unseen={unseen[c.id]} serverNow={serverNow} move={moves[c.id]}
                              arrivalMinutes={hospital?.arrival_highlight_minutes}
                              showCategory={queue.queue_mode === 'ai_assisted'}
                              rowProps={canReorder ? reorder.rowProps(c.id, index) : undefined}
                              dragHandle={canReorder && (
                                <DragHandle name={c.ems_unit || `case #${c.id}`} {...reorder.handleProps(c.id, index)} />
                              )} />
              ))}
              {visible.length === 0 && queued.length > 0 && (
                <li className="px-4 py-6 text-sm text-gray-600">
                  No cases match this filter.{' '}
                  <button type="button" onClick={() => setView(DEFAULT_VIEW)} className="text-blue-600 hover:underline">Show all</button>
                </li>
              )}
              {view.filter === 'all' && others.length > 0 && (
                <li className="border-b border-gray-100 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500">
                  Arrived and closed
                </li>
              )}
              {view.filter === 'all' && others.map((c) => (
                <QueueRow key={c.id} liveCase={c} selected={c.id === selectedId} onSelect={setSelectedId}
                              unseen={unseen[c.id]} serverNow={serverNow} />
              ))}
            </ul>
          </div>

          <div className="lg:col-span-3 p-6">
            {selected ? (
              <>
                <CaseHeader liveCase={selected} inQueue={selectedInQueue} queueMode={queue.queue_mode}
                            onAcknowledge={acknowledge}
                            onMarkArrived={markArrived} />
                <CrewAlertPanel liveCase={selected} serverNow={serverNow}
                                onChanged={(c) => { setCases((list) => upsertNewer(list, c)); refreshQueueSoon(); }} />
                <div>
                  <CaseOverview
                    liveCase={selected}
                    updates={details.updates}
                    vitals={details.vitals}
                    findings={extracted.findings}
                    onRetryAssessment={retryAssessment}
                    onRetryFindings={extracted.retry}
                    markNew
                  >
                    <CaseRiskFields caseId={selected.id} fields={riskFields} liveValues={liveRiskValues} refreshToken={riskRefresh} />
                  </CaseOverview>
                </div>
                <SectionLabel as="h3" className="mb-2 mt-4 border-t border-gray-100 pt-4">Transcript</SectionLabel>
                <LiveTranscript
                  segments={segments}
                  caseStartedAt={selected.started_at}
                  onDismiss={dismissSegment}
                  newSinceVersion={selected.acknowledgment?.info_version ?? null}
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
