import React from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import { CheckCircle } from 'lucide-react';
import { clockTime } from './QueueRow';
import { Button } from './ui';

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

/**
 * The top of a case on the hospital's screen. One line says which case it is and keeps the two
 * actions in reach while the rest of the case scrolls underneath (it sticks to the top of the
 * window); under it, the case's particulars on one line, and why the queue puts it where it is.
 * The ETA is in the status row below, so it is not repeated here.
 * A fragment on purpose: a sticky bar only sticks inside its parent, so the bar has to be a
 * direct child of the tall column the case lives in, not of a wrapper as short as the header.
 */
const CaseHeader = ({ liveCase: c, inQueue, queueMode = 'standard', onAcknowledge, onMarkArrived }) => {
  const open = c.status === 'active';
  const reasons = inQueue?.queue_reasons;
  return (
    <>
      <div className="sticky top-0 z-10 -mx-6 -mt-6 mb-3 px-6 pt-4 pb-3 bg-white/95 backdrop-blur border-b border-gray-200 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h3 className="min-w-0 text-base font-semibold text-gray-900">
          {c.ems_unit || `Case #${c.id}`}
          <span className="font-normal text-gray-600"> · {c.patient_info || 'No patient info provided'}</span>
        </h3>
        {open && (
          <div className="flex gap-2 shrink-0">
            {!c.latest_update_acknowledged && (
              <Button variant="primary" onClick={onAcknowledge}>
                <CheckCircle className="h-4 w-4" /> Acknowledge
              </Button>
            )}
            {!c.arrived_at && (
              <Button onClick={onMarkArrived}>Mark arrived</Button>
            )}
          </div>
        )}
      </div>
      <div className="mb-4">
        <p className="text-sm text-gray-500">
          Case #{c.id} · EMT {c.emt_first_name} {c.emt_last_name} · started {clockTime(c.started_at)}
          {c.closed_at && ` · closed ${clockTime(c.closed_at)}`}
          {' · '}
          <button onClick={() => downloadFhir(c.id)} className="text-blue-600 hover:underline"
                  title="The handoff as a FHIR R4 Bundle for the hospital's record system: each extracted fact with the report it quotes">
            Export FHIR
          </button>
        </p>
        {reasons && (
          <p className="mt-1 text-sm text-gray-500">
            {queueMode === 'manual'
              ? `Placed by your team. Standard order would put it #${inQueue.suggested_position}: `
              : inQueue.pinned ? 'Moved here by your team. Why the AI order had it: ' : 'Why here: '}
            {reasons.join(' · ')}
            {queueMode === 'ai_assisted' && inQueue.suggested_position !== inQueue.queue_position
              && ` · Standard order: #${inQueue.suggested_position}`}
          </p>
        )}
      </div>
    </>
  );
};

export default CaseHeader;
