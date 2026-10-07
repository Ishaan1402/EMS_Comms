import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Radio, Clock, WifiOff } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import { mergeNewer, upsertNewer } from '../utils/liveRows';
import LiveTranscript from './LiveTranscript';

// An open case with no new audio for this long is shown as idle instead of LIVE.
const IDLE_AFTER_MS = 2 * 60 * 1000;

const OFFLINE_LABELS = {
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting… transcript may be behind',
  unauthorized: 'Session expired — log in again for live updates',
};

const sortCases = (cases) =>
  [...cases].sort((a, b) => {
    if (a.status !== b.status) return a.status === 'active' ? -1 : 1;
    return b.started_at.localeCompare(a.started_at);
  });

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

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);

  const fetchCases = useCallback(async () => {
    try {
      const response = await axios.get('/api/cases');
      setCases((current) => mergeNewer(current, response.data));
      setSelectedId((current) => current ?? sortCases(response.data)[0]?.id ?? null);
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
  }, [fetchCases]);

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
      if (selectedRef.current) fetchSegments(selectedRef.current);
    } else if (type === 'case.opened' || type === 'case.updated') {
      setCases((list) => upsertNewer(list, data.case));
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
  }, [fetchCases, fetchSegments]);

  const connection = useCaseEvents(handleEvent);
  const sortedCases = useMemo(() => sortCases(cases), [cases]);
  const selected = cases.find((c) => c.id === selectedId);
  const serverNow = now + clockOffsetMs;

  return (
    <div className="bg-white rounded-xl shadow-lg border border-gray-100 overflow-hidden mb-8">
      <div className="bg-gradient-to-r from-gray-50 to-gray-100 px-6 py-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-gradient-to-r from-red-600 to-pink-600 rounded-lg">
            <Radio className="h-5 w-5 text-white" />
          </div>
          <h2 className="text-xl font-semibold text-gray-900">Live Cases — Incoming Transcripts</h2>
        </div>
        <ConnectionBadge status={connection} />
      </div>

      {cases.length === 0 ? (
        <p className="p-6 text-sm text-gray-500">No live cases. Transcripts will appear here as soon as an EMT starts one.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3">
          <ul className="border-r border-gray-200 max-h-[28rem] overflow-y-auto">
            {sortedCases.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => setSelectedId(c.id)}
                  className={`w-full text-left px-4 py-3 border-b border-gray-100 hover:bg-blue-50 ${c.id === selectedId ? 'bg-blue-50' : ''}`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-medium text-gray-900">Case #{c.id}</span>
                    <div className="flex items-center gap-2">
                      {unseen[c.id] > 0 && (
                        <span className="px-2 text-xs font-bold text-white bg-blue-600 rounded-full">{unseen[c.id]} new</span>
                      )}
                      <CaseStatusBadge liveCase={c} serverNow={serverNow} />
                    </div>
                  </div>
                  <p className="text-sm text-gray-700 truncate">{c.patient_info || 'No patient info'}</p>
                  <p className="text-xs text-gray-500 mt-1 flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {new Date(c.started_at).toLocaleTimeString()} · EMT {c.emt_first_name} {c.emt_last_name} · {c.segment_count || 0} segments
                  </p>
                </button>
              </li>
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
                <LiveTranscript
                  segments={segments}
                  caseStartedAt={selected.started_at}
                  emptyText={selected.status === 'active' ? 'Waiting for the EMT\'s first audio update…' : 'No transcript was recorded for this case.'}
                />
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
