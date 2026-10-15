import React from 'react';
import { CheckCircle } from 'lucide-react';
import { CategoryBadge, OperationalBadge, ProcessingBadge, ReportingBadge, isReporting } from './CaseBadges';
import { CrewAlertBadges } from './CrewAlerts';
import { ArrivalHighlight } from './HospitalWorkflowSettings';
import { Pill } from './ui';

// An open case with no new audio for this long is shown as idle instead of live.
const IDLE_AFTER_MS = 2 * 60 * 1000;

export const clockTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

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

// The row's left edge says how loudly it asks for attention: a crew's alert first, then what the
// AI or a failure marks urgent, then anything a person has to look at. Rows that are over get none.
const STRIPES = {
  crew: 'border-l-slate-900',
  urgent: 'border-l-red-600',
  attention: 'border-l-amber-500',
  none: 'border-l-transparent',
};

const stripeFor = (c) => {
  if (c.status !== 'active' || c.arrived_at) return 'none';
  if (c.crew_alerts?.active?.length || c.crew_alerts?.unacknowledged) return 'crew';
  if (c.preparation_category === 'Prepare now' || c.processing?.status === 'failed') return 'urgent';
  if (c.operational_status === 'inbound' || c.processing?.needs_review || c.preparation_category === 'Cannot assess') {
    return 'attention';
  }
  return 'none';
};

const MoveCue = ({ move }) => (move.to < move.from
  ? <Pill tone="attention" bold>↑ from #{move.from}</Pill>
  : <Pill>↓ from #{move.from}</Pill>);

// Whether the hospital has seen the latest information. Unseen asks for a person; seen stays quiet.
const Acknowledgment = ({ liveCase }) => {
  if (liveCase.status !== 'active') return null;
  if (liveCase.operational_status === 'inbound') return <Pill tone="attention">Not acknowledged</Pill>;
  if (liveCase.operational_status === 'acknowledged') {
    const ack = liveCase.acknowledgment;
    return (
      <span className="inline-flex items-center gap-1 text-xs text-gray-600"
            title={ack ? `Acknowledged by ${ack.user_name} at ${clockTime(ack.acknowledged_at)}` : undefined}>
        <CheckCircle className="h-3 w-3" /> Acknowledged
      </span>
    );
  }
  return <OperationalBadge status={liveCase.operational_status} />;
};

const CaseStatus = ({ liveCase, serverNow }) => {
  if (liveCase.status !== 'active') return <Pill>Closed</Pill>;
  // The Arrived marker and the Reporting… badge already say where this case is.
  if (liveCase.arrived_at || isReporting(liveCase)) return null;
  const idleMs = serverNow - Date.parse(liveCase.last_segment_at || liveCase.started_at);
  if (idleMs > IDLE_AFTER_MS) {
    // Quiet on purpose: a crew between reports is normal, not something to act on.
    return <span className="text-xs text-gray-500">last audio {Math.floor(idleMs / 60000)}m ago</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-blue-800">
      <span className="h-2 w-2 rounded-full bg-blue-600" /> Live
    </span>
  );
};

/**
 * One case in the hospital's queue. The first lines answer who, how soon, and what changed; why
 * it sits here is in the tooltip and at the top of the case.
 */
const QueueRow = ({ liveCase: c, selected, onSelect, unseen, serverNow, move, arrivalMinutes, moveButtons }) => {
  const open = c.status === 'active' && !c.arrived_at;
  const movedUp = move && move.to < move.from;
  return (
    <li className={`flex border-l-4 ${STRIPES[stripeFor(c)]}`}>
      {moveButtons}
      <button
        onClick={() => onSelect(c.id)}
        title={c.queue_reasons ? `Why here: ${c.queue_reasons.join(' · ')}` : undefined}
        className={`flex-1 min-w-0 text-left px-4 py-3 border-b border-gray-100 hover:bg-blue-50 transition-colors ${selected ? 'bg-blue-50' : ''} ${movedUp ? 'ring-2 ring-inset ring-amber-300' : ''}`}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="flex flex-wrap items-baseline gap-x-2 text-gray-900">
            <span className="font-semibold whitespace-nowrap">
              {c.queue_position && <span className="mr-2 font-normal text-gray-500">#{c.queue_position}</span>}
              {c.ems_unit || `Case #${c.id}`}
            </span>
            {open && <span className="text-sm text-gray-700 whitespace-nowrap">{etaText(c.eta_at, serverNow)}</span>}
          </span>
          <div className="flex items-center gap-2 shrink-0">
            {move && <MoveCue move={move} />}
            {unseen > 0 && <Pill tone="info" bold>{unseen} new</Pill>}
            <CaseStatus liveCase={c} serverNow={serverNow} />
          </div>
        </div>
        <p className="mt-0.5 mb-1 text-sm text-gray-700 truncate">{c.patient_info || 'No patient info'}</p>
        <CrewAlertBadges liveCase={c} serverNow={serverNow} />
        <div className="flex flex-wrap items-center gap-1">
          {c.queue_position && <span className="text-xs text-gray-600">AI suggests:</span>}
          <CategoryBadge liveCase={c} />
          <ReportingBadge liveCase={c} />
          <ArrivalHighlight liveCase={c} serverNow={serverNow} minutes={arrivalMinutes} />
          {c.processing?.needs_review && <ProcessingBadge processing={c.processing} />}
          <Acknowledgment liveCase={c} />
        </div>
        {c.vital_changes?.length > 0 && (
          <p className="mt-1 text-sm text-gray-900">{c.vital_changes.map(vitalChange).join(' · ')}</p>
        )}
        {open && c.suggested_position && c.suggested_position !== c.queue_position && (
          <p className="mt-0.5 text-xs text-gray-600">Standard order would put it #{c.suggested_position}</p>
        )}
      </button>
    </li>
  );
};

export default QueueRow;
