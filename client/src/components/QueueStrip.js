import React, { useState } from 'react';
import HospitalWorkflowSettings from './HospitalWorkflowSettings';
import { RiskFieldSettings } from './DoctorQueueSettings';
import QueueModeControl from './QueueModeControl';

// A count of zero fades out, so the counts that are not zero are what the eye finds.
const Count = ({ n, label, tone }) => (
  <span className={n === 0 ? 'text-gray-500' : tone}>
    <span className="font-bold">{n}</span> {label}
  </span>
);

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
const QueueStrip = ({ queue, hospital, riskFields, onHospitalChange, onModeChange }) => {
  const [panel, setPanel] = useState(null);
  const toggle = (name) => setPanel((current) => (current === name ? null : name));
  const counts = queue.counts;
  const linked = Boolean(hospital?.id);
  if (!counts && !linked) return null;

  return (
    <>
      <div className="px-6 py-3 border-b border-gray-200 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 text-sm">
        {counts && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
            <Count n={counts.inbound} label="inbound" tone="text-gray-900" />
            <Count n={counts.needs_review} label="need review" tone="text-amber-800" />
            <Count n={counts.unacknowledged} label="not acknowledged" tone="text-amber-800" />
            {counts.crew_alerts !== undefined && (
              <Count n={counts.crew_alerts} label={`crew alert${counts.crew_alerts === 1 ? '' : 's'}`} tone="text-slate-900" />
            )}
            <Count n={counts.prepare_now} label="AI suggests prepare now" tone="text-red-700" />
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
