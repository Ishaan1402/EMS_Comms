import React, { useState } from 'react';
import HospitalWorkflowSettings from './HospitalWorkflowSettings';
import { RiskFieldSettings } from './DoctorQueueSettings';
import QueueModeControl from './QueueModeControl';

// A count that is also a filter: click it to show only those cases, click it again for all. A count of
// zero fades out, so the counts that are not zero are what the eye finds. The counts are always the
// hospital's whole queue, so a filter can't hide that something needs attention.
const FilterChip = ({ id, n, label, tone, filter, onFilter }) => {
  const active = filter === id;
  const narrowing = active && id !== 'all';   // "inbound" is pressed when nothing is filtered: no highlight then
  return (
    <button type="button" aria-pressed={active} onClick={() => onFilter(active ? 'all' : id)}
      title={narrowing ? 'Showing only these. Click to show all.' : id === 'all' ? 'Show all' : 'Show only these'}
      className={`rounded-full px-2 py-0.5 ${narrowing ? 'bg-blue-50 text-blue-900 ring-1 ring-blue-300' : `hover:bg-gray-100 ${n === 0 ? 'text-gray-500' : tone}`}`}>
      <span className="font-bold">{n}</span> {label}
    </button>
  );
};

const PanelButton = ({ open, controls, onClick, children }) => (
  <button type="button" onClick={onClick} aria-expanded={open} aria-controls={controls}
    className="py-1 text-gray-600 underline decoration-dotted underline-offset-4 hover:text-gray-900">
    {children}
  </button>
);

/**
 * Everything above the queue in one strip: the counts, how the queue is ordered (Standard, Smart (AI)
 * or Manual, with what each means one click away), and a Settings panel below it: the custom risk
 * fields (always showing their list and add form), then the arrival highlight. The panel stays
 * mounted while closed, so an unsaved arrival-highlight draft survives opening and closing it.
 */
const QueueStrip = ({ queue, counts, filter = 'all', onFilter = () => {}, hospital, riskFields, onHospitalChange,
  onModeChange }) => {
  const [panel, setPanel] = useState(null);
  const toggle = (name) => setPanel((current) => (current === name ? null : name));
  const linked = Boolean(hospital?.id);
  if (!counts && !linked) return null;

  return (
    <>
      <div className="px-6 py-3 border-b border-gray-200 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 text-sm">
        {counts && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <FilterChip id="all" n={counts.inbound} label="inbound" tone="text-gray-900" filter={filter} onFilter={onFilter} />
            <FilterChip id="needsReview" n={counts.needsReview} label="need review" tone="text-amber-800" filter={filter} onFilter={onFilter} />
            <FilterChip id="unacknowledged" n={counts.unacknowledged} label="not acknowledged" tone="text-amber-800" filter={filter} onFilter={onFilter} />
            <FilterChip id="crewAlerts" n={counts.crewAlerts} label={`crew alert${counts.crewAlerts === 1 ? '' : 's'}`} tone="text-slate-900" filter={filter} onFilter={onFilter} />
            <FilterChip id="prepareNow" n={counts.prepareNow} label="AI suggests prepare now" tone="text-red-700" filter={filter} onFilter={onFilter} />
            {counts.arrivingSoon !== undefined && (
              <FilterChip id="arrivingSoon" n={counts.arrivingSoon} label="arriving soon" tone="text-blue-800" filter={filter} onFilter={onFilter} />
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-gray-700">
          {linked && (
            <QueueModeControl mode={queue.queue_mode} rules={queue.rules} ruleVersions={queue.rule_versions}
                              onChange={onModeChange} />
          )}
          {linked && (
            <PanelButton open={panel === 'settings'} controls="queue-settings" onClick={() => toggle('settings')}>
              ⚙ Settings
            </PanelButton>
          )}
        </div>
      </div>

      {linked && (
        <div id="queue-settings" hidden={panel !== 'settings'}
          className="px-6 py-4 border-b border-gray-200 text-sm text-gray-700 space-y-4 [&_details]:px-0 [&_details]:py-1 [&_details]:border-b-0">
          <RiskFieldSettings fields={riskFields} />
          {hospital.settings_version && (
            <div className="pt-3 border-t border-gray-100">
              <HospitalWorkflowSettings hospital={hospital} onChange={onHospitalChange} />
            </div>
          )}
        </div>
      )}
    </>
  );
};

export default QueueStrip;
