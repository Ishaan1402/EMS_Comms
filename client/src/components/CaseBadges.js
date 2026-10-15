import React from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { Pill } from './ui';

// The badges that describe a case's state. Their colours come from ui.js: red = act now,
// amber = someone needs to look, gray = normal status, blue = new or in progress.

const OPERATIONAL_LABELS = { inbound: 'Inbound', acknowledged: 'Acknowledged', arrived: 'Arrived', closed: 'Closed' };

const PROCESSING = {
  pending: { label: 'Waiting for assessment', tone: 'neutral' },
  processing: { label: 'Processing…', tone: 'neutral' },
  completed: { label: 'Assessed', tone: 'neutral' },
  failed: { label: 'Needs review · processing failed', tone: 'urgent' },
  needs_review: { label: 'Needs review', tone: 'attention' },
};

// The AI's preparation category (the evaluation contract's draft labels). Only Prepare now asks
// for action; Can wait is not a warning. A label we don't know is shown as one to look at.
const CATEGORY_TONES = { 'Prepare now': 'urgent', 'Can wait': 'neutral', Routine: 'neutral', 'Cannot assess': 'attention' };

export const OperationalBadge = ({ status }) => <Pill>{OPERATIONAL_LABELS[status] || OPERATIONAL_LABELS.inbound}</Pill>;

export const ProcessingBadge = ({ processing }) => {
  if (!processing) return null;
  const { label, tone } = PROCESSING[processing.status] || PROCESSING.pending;
  return (
    <Pill tone={tone}>
      {processing.status === 'processing' && <Loader2 className="h-3 w-3 animate-spin" />}
      {processing.needs_review && <AlertTriangle className="h-3 w-3" />}
      {label}
    </Pill>
  );
};

// The crew is recording a report: its words show as they speak, but the handoff and the queue
// only change once they send it.
export const isReporting = (liveCase) => liveCase.status === 'active' && !liveCase.arrived_at && !!liveCase.open_report;

export const ReportingBadge = ({ liveCase }) => (isReporting(liveCase) ? <Pill tone="info" pulse>Reporting…</Pill> : null);

// No assessment yet shows as unavailable, never as a guess.
export const CategoryBadge = ({ liveCase }) => {
  const category = liveCase.preparation_category;
  const assessing = ['pending', 'processing'].includes(liveCase.processing?.assessment?.status);
  if (!category && assessing) {
    return (
      <Pill bold>
        <Loader2 className="h-3 w-3 animate-spin" /> Assessing…
      </Pill>
    );
  }
  return (
    <Pill bold tone={CATEGORY_TONES[category] || 'attention'}>
      {category || 'Assessment unavailable'}
      {category && liveCase.assessment_is_outdated && ' · earlier info'}
    </Pill>
  );
};

// Marks information that arrived after the hospital's last acknowledgment.
export const NewTag = () => (
  <span className="ml-1 px-1 py-0.5 text-xs font-bold leading-none uppercase tracking-wide text-white bg-blue-700 rounded align-middle">new</span>
);
