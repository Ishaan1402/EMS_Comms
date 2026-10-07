import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Radio, Clock, WifiOff } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import useCaseEvents from '../hooks/useCaseEvents';
import LiveTranscript from './LiveTranscript';

const upsertById = (list, item) => {
  const index = list.findIndex((x) => x.id === item.id);
  if (index === -1) return [...list, item];
  const next = [...list];
  next[index] = { ...next[index], ...item };
  return next;
};

// A fetched snapshot can be older than events that arrived while it was in flight.
const mergeSnapshot = (current, fetched) => {
  const byId = new Map(current.map((s) => [s.id, s]));
  fetched.forEach((row) => {
    const live = byId.get(row.id);
    const liveIsNewer = live && live.attempts >= row.attempts && live.status !== 'pending' && row.status === 'pending';
    if (!liveIsNewer) byId.set(row.id, row);
  });
  return [...byId.values()];
};

const sortCases = (cases) =>
  [...cases].sort((a, b) => {
    if (a.status !== b.status) return a.status === 'active' ? -1 : 1;
    return new Date(b.started_at) - new Date(a.started_at);
  });

const ConnectionBadge = ({ status }) =>
  status === 'live' ? (
    <span className="flex items-center gap-1 text-xs font-medium text-green-700">
      <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" /> Live updates
    </span>
  ) : (
    <span className="flex items-center gap-1 text-xs font-medium text-orange-600">
      <WifiOff className="h-3 w-3" /> {status === 'connecting' ? 'Connecting…' : 'Reconnecting… transcript may be behind'}
    </span>
  );

const LiveCasesPanel = () => {
  const [cases, setCases] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [segments, setSegments] = useState([]);
  const [unseen, setUnseen] = useState({});
  const selectedRef = useRef(null);
  selectedRef.current = selectedId;

  const fetchCases = useCallback(async () => {
    try {
      const response = await axios.get('/api/cases');
      const sorted = sortCases(response.data);
      setCases(sorted);
      setSelectedId((current) => current ?? sorted[0]?.id ?? null);
    } catch (error) {
      console.error('Error fetching cases:', error);
    }
  }, []);

  const fetchSegments = useCallback(async (caseId) => {
    if (!caseId) return;
    try {
      const response = await axios.get(`/api/cases/${caseId}/segments`);
      if (selectedRef.current === caseId) setSegments((current) => mergeSnapshot(current, response.data));
    } catch (error) {
      console.error('Error fetching transcript:', error);
      toast.error('Failed to load transcript');
    }
  }, []);

  useEffect(() => {
    fetchCases();
  }, [fetchCases]);

  useEffect(() => {
    setSegments([]);
    fetchSegments(selectedId);
    setUnseen((u) => ({ ...u, [selectedId]: 0 }));
  }, [selectedId, fetchSegments]);

  const handleEvent = useCallback((type, data) => {
    if (type === 'ready') {
      // (Re)connected: anything sent while disconnected was missed, so re-sync.
      fetchCases();
      fetchSegments(selectedRef.current);
    } else if (type === 'case.opened' || type === 'case.updated') {
      setCases((list) => sortCases(upsertById(list, data.case)));
      if (type === 'case.opened') {
        toast(`New live case from ${data.case.emt_first_name} ${data.case.emt_last_name}`, { icon: '🚑' });
        setSelectedId((current) => current ?? data.case.id);
      }
    } else if (type === 'segment.created' || type === 'segment.updated') {
      const { case_id: caseId, segment } = data;
      if (caseId === selectedRef.current) {
        setSegments((list) => upsertById(list, segment));
      } else if (type === 'segment.created') {
        setUnseen((u) => ({ ...u, [caseId]: (u[caseId] || 0) + 1 }));
      }
      if (type === 'segment.created') {
        setCases((list) => list.map((c) => (c.id === caseId
          ? { ...c, segment_count: (c.segment_count || 0) + 1, last_segment_at: segment.recorded_at }
          : c)));
      }
    }
  }, [fetchCases, fetchSegments]);

  const connection = useCaseEvents(handleEvent);
  const selected = cases.find((c) => c.id === selectedId);

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
            {cases.map((c) => (
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
                      {c.status === 'active' ? (
                        <span className="px-2 py-0.5 text-xs font-bold text-white bg-red-600 rounded-full animate-pulse">LIVE</span>
                      ) : (
                        <span className="px-2 py-0.5 text-xs text-gray-600 bg-gray-200 rounded-full">Closed</span>
                      )}
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
