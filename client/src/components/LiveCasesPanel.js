import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Radio, Clock, WifiOff, CheckCircle } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import { mergeNewer, upsertNewer } from '../utils/liveRows';
import useCaseDetails from '../hooks/useCaseDetails';
import useCaseFindings from '../hooks/useCaseFindings';
import LiveTranscript from './LiveTranscript';
import CaseOverview from './CaseOverview';
import { CategoryBadge, OperationalBadge, ProcessingBadge, ReportingBadge } from './CaseBadges';
import { SectionLabel } from './ui';
import CaseChat from './CaseChat';
import HospitalWorkflowSettings, { ArrivalHighlight } from './HospitalWorkflowSettings';
import { CrewAlertBadges, CrewAlertPanel, CrewAlertSound } from './CrewAlerts';
import DoctorQueueSettings, { CaseRiskFields, MoveButtons } from './DoctorQueueSettings';

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
// How long a case that changed position shows where it came from.
const MOVE_CUE_MS = 30 * 1000;

const clockTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

// "arrives in 11 min (14:32)", counted down against the server clock.
const etaText = (etaAt, serverNow) => {
  if (!etaAt) return 'ETA not given';
  const minutes = Math.round((Date.parse(etaAt) - serverNow) / 60000);
  if (minutes > 0) return `arrives in ${minutes} min (${clockTime(etaAt)})`;
  if (minutes === 0) return `arriving now (${clockTime(etaAt)})`;
  return `ETA passed ${-minutes} min ago (${clockTime(etaAt)})`;
};

// "SpO2 94→89", with the conditions when they differ: "SpO2 89 (on room air)→92 (on 4 liters oxygen)".
const vitalChange = (v) => {
  const conditions = v.from_qualifier !== v.to_qualifier;
  const value = (number, qualifier) => (conditions && qualifier ? `${number} (${qualifier})` : number);
  return `${v.label} ${value(v.from, v.from_qualifier)}→${value(v.to, v.to_qualifier)}`;
};

const MoveCue = ({ move }) => (move.to < move.from ? (
  <span className="px-2 py-0.5 text-xs font-bold text-white bg-amber-500 rounded-full">↑ from #{move.from}</span>
) : (
  <span className="px-2 py-0.5 text-xs font-medium text-gray-600 bg-gray-100 rounded-full">↓ from #{move.from}</span>
));

const ConnectionBadge = ({ status }) =>
  status === 'live' ? (
    <span className="flex items-center gap-1 text-xs font-medium text-green-700">
      <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" /> Live updates
    </span>
  ) : (
    <span className="flex items-center gap-1 text-xs font-medium text-amber-800">
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
    // Quiet on purpose: a crew between reports is normal, not something to act on.
    return <span className="text-xs text-gray-500">last audio {Math.floor(idleMs / 60000)}m ago</span>;
  }
  return <span className="px-2 py-0.5 text-xs font-bold text-white bg-red-600 rounded-full animate-pulse">LIVE</span>;
};

const CaseListItem = ({ liveCase: c, selected, onSelect, unseen, serverNow, move, arrivalMinutes, moveButtons }) => (
  <li className="flex">
    {moveButtons}
    <button
      onClick={() => onSelect(c.id)}
      className={`flex-1 min-w-0 text-left px-4 py-3 border-b border-gray-100 hover:bg-blue-50 transition-colors ${selected ? 'bg-blue-50' : ''} ${move && move.to < move.from ? 'ring-2 ring-inset ring-amber-300' : ''}`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="flex flex-wrap items-baseline gap-x-2 text-gray-900">
          <span className="font-semibold whitespace-nowrap">
            {c.queue_position && <span className="mr-2 text-gray-500">#{c.queue_position}</span>}
            {c.ems_unit || `Case #${c.id}`}
          </span>
          {c.status === 'active' && !c.arrived_at && (
            <span className="text-sm text-gray-600 whitespace-nowrap">{etaText(c.eta_at, serverNow)}</span>
          )}
        </span>
        <div className="flex items-center gap-2 shrink-0">
          {move && <MoveCue move={move} />}
          {unseen > 0 && <span className="px-2 text-xs font-bold text-white bg-blue-600 rounded-full">{unseen} new</span>}
          <CaseStatusBadge liveCase={c} serverNow={serverNow} />
        </div>
      </div>
      <CrewAlertBadges liveCase={c} serverNow={serverNow} />
      <div className="flex flex-wrap items-center gap-1 mb-1">
        {c.queue_position && <span className="text-xs text-gray-500">AI suggests:</span>}
        <CategoryBadge liveCase={c} />
        <ReportingBadge liveCase={c} />
        <ArrivalHighlight liveCase={c} serverNow={serverNow} minutes={arrivalMinutes} />
        {c.status === 'active' && <OperationalBadge status={c.operational_status} />}
        {c.processing?.needs_review && <ProcessingBadge processing={c.processing} />}
      </div>
      <p className="text-sm text-gray-700 truncate">{c.patient_info || 'No patient info'}</p>
      {c.vital_changes?.length > 0 && (
        <p className="text-xs font-medium text-gray-800 mt-0.5">
          {c.vital_changes.map(vitalChange).join(' · ')}
        </p>
      )}
      {c.queue_reasons && (
        <p className="text-xs text-gray-600 mt-0.5 truncate" title={c.queue_reasons.join(' · ')}>
          {c.suggested_position ? `Auto-order would put it #${c.suggested_position}: ` : 'Why here: '}
          {c.queue_reasons.join(' · ')}
        </p>
      )}
      <p className="text-xs text-gray-500 mt-1 flex items-center gap-1">
        <Clock className="h-3 w-3" />
        #{c.id} · {clockTime(c.started_at)} · {c.emt_first_name} {c.emt_last_name}
      </p>
    </button>
  </li>
);

// The verified handoff as a FHIR R4 Bundle (GET /api/cases/:id/fhir), saved as a file.
const downloadFhir = async (caseId) => {
  try {
    const { data } = await axios.get(`/api/cases/${caseId}/fhir`);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/fhir+json' }));
    link.download = `case-${caseId}-handoff.fhir.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    toast.error('Could not export the handoff');
  }
};

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
  // Doctors' own settings (DoctorQueueSettings): the risk fields, and the latest live risk values.
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
    reloadFindings, fetchHospital, applyHospital, fetchConfig]);

  const connection = useCaseEvents(handleEvent);
  const byId = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);
  // Queue entries show the freshest copy of each case, in the server's order.
  const queued = queue.cases.map((entry) => ({ ...entry, ...(byId.get(entry.id) || {}),
    queue_position: entry.queue_position, queue_reasons: entry.queue_reasons }));
  const queuedIds = new Set(queued.map((c) => c.id));
  const others = newestFirst(cases.filter((c) => !queuedIds.has(c.id)));
  const selected = byId.get(selectedId) || queued.find((c) => c.id === selectedId);
  const serverNow = now + clockOffsetMs;
  const autoOrder = queue.auto_order !== false;

  // Auto-order off: swap a row with its neighbour and save the whole order for the hospital.
  const moveCase = async (index, step) => {
    const ids = queued.map((c) => c.id);
    [ids[index], ids[index + step]] = [ids[index + step], ids[index]];
    try {
      await axios.put('/api/hospitals/mine/doctor-order', { case_ids: ids });
      fetchQueue();
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not move the case');
    }
  };

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
        <div className="flex items-center gap-4">
          <CrewAlertSound cases={cases} />
          <ConnectionBadge status={connection} />
        </div>
      </div>

      {hospital?.settings_version && <HospitalWorkflowSettings hospital={hospital} onChange={applyHospital} />}
      {hospital?.id && queue.counts && (
        <DoctorQueueSettings autoOrder={autoOrder} riskFields={riskFields} onAutoOrderChange={fetchQueue} />
      )}

      {queue.counts && (
        <div className="px-6 py-3 border-b border-gray-200 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span><span className="font-bold text-gray-900">{queue.counts.inbound}</span> inbound</span>
          <span className="text-amber-800"><span className="font-bold">{queue.counts.needs_review}</span> need review</span>
          <span className="text-amber-800"><span className="font-bold">{queue.counts.unacknowledged}</span> not acknowledged</span>
          {queue.counts.crew_alerts !== undefined && (
            <span className="text-slate-900"><span className="font-bold">{queue.counts.crew_alerts}</span> crew alert{queue.counts.crew_alerts === 1 ? '' : 's'}</span>
          )}
          <span className="text-red-700"><span className="font-bold">{queue.counts.prepare_now}</span> AI suggests prepare now</span>
          <details className="w-full text-xs text-gray-600">
            <summary className="cursor-pointer text-gray-500">How this order is set</summary>
            <p className="mt-1">
              {autoOrder
                ? `Order (${queue.rule_version}): ${queue.rule}`
                : `Your team set this order with the arrows; new cases appear on top. Auto-order would use ${queue.rule_version}: ${queue.rule}`}
              {' '}AI suggestions come from a language model reading the crew&apos;s reports; they are not
              validated on real EMS audio. Check each one against the quotes and missing items in the handoff.
            </p>
          </details>
        </div>
      )}

      {cases.length === 0 ? (
        <p className="p-6 text-sm text-gray-500">
          {hospital === null
            ? 'Your account isn\'t linked to a hospital, so no inbound cases are shown. Ask an administrator to assign one.'
            : 'No inbound cases. They appear here as soon as an EMT routes one to your hospital.'}
        </p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-5">
          <ul className="lg:col-span-2 border-r border-gray-200 max-h-[48rem] overflow-y-auto">
            {queued.map((c, index) => (
              <CaseListItem key={c.id} liveCase={c} selected={c.id === selectedId} onSelect={setSelectedId}
                            unseen={unseen[c.id]} serverNow={serverNow} move={moves[c.id]}
                            arrivalMinutes={hospital?.arrival_highlight_minutes}
                            moveButtons={!autoOrder && (
                              <MoveButtons first={index === 0} last={index === queued.length - 1}
                                           onMove={(step) => moveCase(index, step)} />
                            )} />
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

          <div className="lg:col-span-3 p-6">
            {selected ? (
              <>
                <div className="mb-4">
                  <h3 className="text-lg font-semibold text-gray-900">
                    {selected.ems_unit && `${selected.ems_unit} · `}Case #{selected.id} — EMT {selected.emt_first_name} {selected.emt_last_name}
                  </h3>
                  <p className="text-sm text-gray-600">{selected.patient_info || 'No patient info provided'}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    Started {clockTime(selected.started_at)}
                    {selected.closed_at && ` · Closed ${clockTime(selected.closed_at)}`}
                    {' · '}
                    <button onClick={() => downloadFhir(selected.id)} className="text-blue-700 underline"
                            title="The handoff as a FHIR R4 Bundle for the hospital's record system: each extracted fact with the report it quotes">
                      Download for the EHR (FHIR R4)
                    </button>
                  </p>
                </div>
                {selected.status === 'active' && (
                  <div className="flex flex-wrap gap-2 mb-4">
                    {!selected.latest_update_acknowledged && (
                      <button
                        onClick={acknowledge}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700"
                      >
                        <CheckCircle className="h-4 w-4" /> Acknowledge
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
                <CaseRiskFields caseId={selected.id} fields={riskFields} liveValues={liveRiskValues} />
                <CrewAlertPanel liveCase={selected} serverNow={serverNow}
                                onChanged={(c) => { setCases((list) => upsertNewer(list, c)); refreshQueueSoon(); }} />
                <div className="mb-6">
                  <CaseOverview
                    liveCase={selected}
                    updates={details.updates}
                    vitals={details.vitals}
                    findings={extracted.findings}
                    onRetryAssessment={retryAssessment}
                    onRetryFindings={extracted.retry}
                    markNew
                  />
                </div>
                <SectionLabel className="mb-2">Transcript</SectionLabel>
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
