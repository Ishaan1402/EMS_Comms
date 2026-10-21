import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { ChevronRight } from 'lucide-react';
import useCaseEvents from '../hooks/useCaseEvents';
import useCaseDetails from '../hooks/useCaseDetails';
import useCaseFindings from '../hooks/useCaseFindings';
import { mergeNewer, upsertNewer } from '../utils/liveRows';
import CaseOverview from './CaseOverview';
import Disclosure from './Disclosure';
import LiveTranscript from './LiveTranscript';
import { SectionLabel } from './ui';

// "14:40" today, "Oct 19, 14:40" on another day.
export const whenText = (iso) => {
  const date = new Date(iso);
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (date.toDateString() === new Date().toDateString()) return time;
  return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`;
};

const newestClosedFirst = (cases) => cases.filter((c) => c.status === 'closed')
  .sort((a, b) => (b.closed_at || b.started_at).localeCompare(a.closed_at || a.started_at));

// One past case opened: the handoff as the hospital got it, and the transcript. Read-only.
// `refresh` changes when the stream says more arrived for this case (a report still finishing after close).
const PastCaseDetails = ({ liveCase, refresh }) => {
  const details = useCaseDetails(liveCase.id);
  const { reload: reloadUpdates } = details;
  const { findings, reload: reloadFindings } = useCaseFindings(liveCase.id);
  const [segments, setSegments] = useState([]);
  useEffect(() => {
    axios.get(`/api/cases/${liveCase.id}/segments`).then((response) => setSegments(response.data))
      .catch((error) => console.error('Error fetching transcript:', error));
  }, [liveCase.id, refresh]);
  useEffect(() => {
    if (!refresh) return;
    reloadUpdates();
    reloadFindings();
  }, [refresh, reloadUpdates, reloadFindings]);
  return (
    <div className="border-b border-gray-100 bg-gray-50/60 px-4 pb-4 sm:pl-10">
      <CaseOverview liveCase={liveCase} updates={details.updates} vitals={details.vitals} findings={findings} />
      <SectionLabel as="h4" className="mb-2 mt-2 border-t border-gray-100 pt-3">Transcript</SectionLabel>
      <LiveTranscript segments={segments} caseStartedAt={liveCase.started_at}
                      emptyText="No transcript was recorded for this case." />
    </div>
  );
};

/**
 * The crew's closed cases, folded away under the case they are working on. Opening one shows what the
 * hospital received; nothing in it can be changed.
 */
const PastCases = () => {
  const [cases, setCases] = useState([]);
  const [open, setOpen] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [refresh, setRefresh] = useState(0);

  const fetchCases = useCallback(async () => {
    try {
      const response = await axios.get('/api/cases?status=closed');
      setCases((current) => mergeNewer(current, response.data));
    } catch (error) {
      console.error('Error fetching past cases:', error);
    }
  }, []);

  useEffect(() => { fetchCases(); }, [fetchCases]);

  useCaseEvents(useCallback((type, data) => {
    if (type === 'ready') fetchCases();
    else if (type === 'case.updated' && data.case.status === 'closed') setCases((list) => upsertNewer(list, data.case));
    else if (type.startsWith('segment.') || type === 'findings.updated' || type === 'update.created') {
      if (data.case_id === openId) setRefresh((n) => n + 1);
    }
  }, [fetchCases, openId]));

  const past = newestClosedFirst(cases);
  if (past.length === 0) return null;

  return (
    <div className="mb-8 rounded-lg bg-white px-6 py-4 shadow-md">
      <Disclosure title="Past cases" summary={`${past.length} closed`} open={open} onToggle={setOpen}>
        <ul className="-ml-6 border-t border-gray-100">
          {past.map((c) => {
            const expanded = c.id === openId;
            return (
              <li key={c.id}>
                <button type="button" onClick={() => setOpenId(expanded ? null : c.id)} aria-expanded={expanded}
                        className="flex w-full items-start gap-2 border-b border-gray-100 px-4 py-3 text-left hover:bg-gray-50">
                  <ChevronRight className={`mt-0.5 h-4 w-4 shrink-0 text-gray-500 transition-transform ${expanded ? 'rotate-90' : ''}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className="text-sm font-semibold text-gray-900">
                        {c.ems_unit || `Case #${c.id}`}
                        <span className="font-normal text-gray-500"> · {c.destination_hospital_name || 'No destination'}</span>
                      </span>
                      <span className="text-xs tabular-nums text-gray-500">Closed {whenText(c.closed_at || c.started_at)}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-sm text-gray-600">{c.patient_info || 'No patient info'}</span>
                  </span>
                </button>
                {expanded && <PastCaseDetails liveCase={c} refresh={refresh} />}
              </li>
            );
          })}
        </ul>
      </Disclosure>
    </div>
  );
};

export default PastCases;
