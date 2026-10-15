import React, { useState } from 'react';
import HospitalWorkflowSettings from './HospitalWorkflowSettings';
import { AutoOrderSwitch, RiskFieldSettings } from './DoctorQueueSettings';

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
 * Everything above the queue in one strip: the counts, whose order this is (the rule's or the
 * team's), and two panels that open below it: how the order is set, and the hospital's settings.
 * The panels stay mounted while closed, so an unsaved arrival-highlight draft survives opening
 * and closing them, and the AI disclosure is always in the page.
 */
const QueueStrip = ({ queue, autoOrder, hospital, riskFields, onHospitalChange, onAutoOrderChange }) => {
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
          {linked && <AutoOrderSwitch autoOrder={autoOrder} ruleVersion={queue.rule_version} onChange={onAutoOrderChange} />}
          {counts && (
            <PanelButton open={panel === 'order'} controls="queue-order-note" onClick={() => toggle('order')}>
              How this order is set
            </PanelButton>
          )}
          {linked && (
            <PanelButton open={panel === 'settings'} controls="queue-settings" onClick={() => toggle('settings')}>
              ⚙ Queue settings
            </PanelButton>
          )}
        </div>
      </div>

      {counts && (
        <p id="queue-order-note" hidden={panel !== 'order'} className="px-6 py-2 border-b border-gray-200 text-xs text-gray-600">
          {autoOrder
            ? `Order (${queue.rule_version}): ${queue.rule}`
            : `Your team set this order with the arrows; new cases appear on top. Auto-order would use ${queue.rule_version}: ${queue.rule}`}
          {' '}AI suggestions come from a language model reading the crew&apos;s reports; they are not
          validated on real EMS audio. Check each one against the quotes and missing items in the handoff.
        </p>
      )}

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
