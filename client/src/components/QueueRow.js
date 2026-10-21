import React from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { CategoryBadge, ProcessingBadge, ReportingBadge, isReporting } from './CaseBadges';
import { CrewAlertBadges } from './CrewAlerts';
import { StatusText } from './ui';
import { FILTERS } from '../utils/queueView';

// An open case with no new audio for this long is called out as quiet.
const QUIET_AFTER_MS = 5 * 60 * 1000;

export const clockTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

// "14 min", counted down against the server clock; the clock time is the tooltip.
const etaText = (etaAt, serverNow) => {
  if (!etaAt) return 'No ETA';
  const minutes = Math.round((Date.parse(etaAt) - serverNow) / 60000);
  if (minutes > 0) return `${minutes} min`;
  if (minutes === 0) return 'Arriving now';
  return `${-minutes} min overdue`;
};

// The row's left edge asks for attention only for a crew's alert or a failure. Rows that are over get none.
const stripeFor = (c) => {
  if (c.status !== 'active' || c.arrived_at) return 'border-l-transparent';
  if (c.crew_alerts?.active?.length || c.crew_alerts?.unacknowledged) return 'border-l-slate-900';
  if (c.processing?.status === 'failed') return 'border-l-red-600';
  return 'border-l-transparent';
};

const MoveCue = ({ move }) => (move.to < move.from
  ? <span className="inline-flex items-center gap-0.5 text-xs font-medium text-blue-600"><ArrowUp className="h-3 w-3" />from #{move.from}</span>
  : <span className="inline-flex items-center gap-0.5 text-xs text-gray-500"><ArrowDown className="h-3 w-3" />from #{move.from}</span>);

/**
 * One case in the hospital's queue, in three lines: who and how soon, what is wrong with the patient
 * (the complaint), and only the exceptions that need a person. A normal state, such as acknowledged,
 * gets no marker. Why the case sits here is the tooltip and the top of the case. The AI's category is on
 * the right without its "AI:" prefix; the case shows where it comes from.
 */
const QueueRow = ({ liveCase: c, selected, onSelect, unseen, serverNow, move, arrivalMinutes, dragHandle = null,
  rowProps = {} }) => {
  const open = c.status === 'active' && !c.arrived_at;
  const soon = open && FILTERS.arrivingSoon(c, { serverNow, arrivalMinutes });
  const quietMs = serverNow - Date.parse(c.last_segment_at || c.started_at);
  const why = [
    c.queue_reasons && `Why here: ${c.queue_reasons.join(' · ')}`,
    open && c.suggested_position && c.suggested_position !== c.queue_position
      && `Standard order would put it #${c.suggested_position}`,
  ].filter(Boolean).join('\n');
  return (
    <li {...rowProps} className={`flex border-l-[3px] bg-white ${stripeFor(c)} ${rowProps['data-dragging'] ? 'shadow-lg' : ''}`}>
      {dragHandle}
      <button
        onClick={() => onSelect(c.id)}
        title={why || undefined}
        className={`min-w-0 flex-1 cursor-pointer border-b border-gray-100 px-4 py-3 text-left transition-colors ${selected ? 'bg-blue-50/70 hover:bg-blue-50' : 'hover:bg-gray-100'}`}
      >
        <div className="flex items-baseline justify-between gap-3">
          <span className="flex min-w-0 items-baseline gap-2">
            {c.queue_position && <span className="text-sm tabular-nums text-gray-500">#{c.queue_position}</span>}
            <span className="truncate text-sm font-semibold text-gray-900">{c.ems_unit || `Case #${c.id}`}</span>
            {move && <MoveCue move={move} />}
          </span>
          {open
            ? (
              <span className={`shrink-0 text-sm tabular-nums ${soon ? 'font-semibold text-blue-600' : 'text-gray-700'}`}
                    title={c.eta_at ? `ETA ${clockTime(c.eta_at)}` : undefined}>
                {etaText(c.eta_at, serverNow)}
              </span>
            )
            : (
              <span className="shrink-0 text-xs tabular-nums text-gray-500">
                {c.status === 'closed' ? `Closed ${clockTime(c.closed_at)}` : `Arrived ${clockTime(c.arrived_at)}`}
              </span>
            )}
        </div>
        <p className="mt-0.5 truncate text-sm text-gray-600">{c.patient_info || 'No patient info'}</p>
        {open && (
          <div className="mt-1.5 flex items-start justify-between gap-3">
            <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <CrewAlertBadges liveCase={c} serverNow={serverNow} />
              <ReportingBadge liveCase={c} />
              {c.processing?.needs_review && <ProcessingBadge processing={c.processing} />}
              {c.operational_status === 'inbound' && <StatusText tone="attention">Not acknowledged</StatusText>}
              {unseen > 0 && <span className="text-xs font-medium text-blue-600">{unseen} new</span>}
              {!isReporting(c) && quietMs > QUIET_AFTER_MS && (
                <span className="text-xs text-gray-500">No audio {Math.floor(quietMs / 60000)}m</span>
              )}
            </span>
            <span className="shrink-0"><CategoryBadge liveCase={c} prefix={false} /></span>
          </div>
        )}
      </button>
    </li>
  );
};

export default QueueRow;
