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
 * or Manual, with what each means one click away), and the hospital's settings in a panel below it.
 * The settings panel stays mounted while closed, so an unsaved arrival-highlight draft survives
 * opening and closing it.
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
              ⚙ Queue settings
            </PanelButton>
          )}
        </div>
      </div>

      {linked && (
        <div id="queue-settings" hidden={panel !== 'settings'}
          className="px-6 py-2 border-b border-gray-200 text-sm text-gray-700 [&>details]:px-0 [&>details]:py-1 [&>details]:border-b-0">
          {hospital.settings_version && <HospitalWorkflowSettings hospital={hospital} onChange={onHospitalChange} />}
          <RiskFieldSettings fields={riskFields} />
        </div>
      )}
    </>
  );
};

export default QueueStrip;
