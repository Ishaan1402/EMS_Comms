import React from 'react';
import { Loader2 } from 'lucide-react';
import { StatusText } from './ui';

// What the AI and the pipeline say about a case. Quiet when normal: a state that needs nothing from
// a person gets no marker, and nothing here is a filled or outlined pill (ui.js has the colour rules).

const OPERATIONAL_LABELS = { arrived: 'Arrived', closed: 'Closed' };

const PROCESSING = {
  pending: { label: 'Waiting for assessment', tone: 'neutral' },
  processing: { label: 'Processing…', tone: 'info' },
  failed: { label: 'Needs review · processing failed', tone: 'urgent' },
  needs_review: { label: 'Needs review', tone: 'attention' },
};

// The AI's preparation category (the evaluation contract's draft labels). Only Prepare now asks for
// action; Can wait is not a warning. A label we don't know is shown as one to look at.
const CATEGORY_TONES = { 'Prepare now': 'urgent', 'Can wait': 'neutral', Routine: 'neutral', 'Cannot assess': 'attention' };
const CATEGORY_TEXT = { urgent: 'text-red-600', neutral: 'text-gray-600', attention: 'text-amber-700' };

/** Only the states a person should notice: where the case ended up. Inbound and acknowledged are normal. */
export const OperationalBadge = ({ status }) => (OPERATIONAL_LABELS[status]
  ? <StatusText tone="neutral" dot={false}>{OPERATIONAL_LABELS[status]}</StatusText> : null);

export const ProcessingBadge = ({ processing }) => {
  const state = processing && PROCESSING[processing.status];
  if (!state) return null;   // completed is the normal state
  return (
    <StatusText tone={state.tone} dot={processing.status !== 'processing'}>
      {processing.status === 'processing' && <Loader2 className="h-3 w-3 animate-spin" />}
      {state.label}
    </StatusText>
  );
};

// The crew is recording a report: its words show as they speak, but the handoff and the queue
// only change once they send it.
export const isReporting = (liveCase) => liveCase.status === 'active' && !liveCase.arrived_at && !!liveCase.open_report;

export const ReportingBadge = ({ liveCase }) => (isReporting(liveCase) ? <StatusText tone="info" pulse>Reporting…</StatusText> : null);

// No assessment yet shows as unavailable, never as a guess. The case says "AI:" before it; a queue row
// (prefix={false}) shows the category alone.
export const CategoryBadge = ({ liveCase, prefix = true }) => {
  const ai = prefix ? 'AI: ' : '';
  const category = liveCase.preparation_category;
  const assessing = ['pending', 'processing'].includes(liveCase.processing?.assessment?.status);
  if (!category && assessing) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-600">
        <Loader2 className="h-3 w-3 animate-spin" />{prefix ? 'AI: assessing…' : 'Assessing…'}
      </span>
    );
  }
  const tone = CATEGORY_TONES[category] || 'attention';
  return (
    <span className={`text-xs font-medium ${CATEGORY_TEXT[tone]}`}>
      {ai}{category || (prefix ? 'unavailable' : 'Not assessed')}
      {category && liveCase.assessment_is_outdated && ' · earlier info'}
    </span>
  );
};

// Marks information that arrived after the hospital's last acknowledgment.
export const NewTag = () => <span className="ml-1.5 text-xs font-medium text-blue-600">New</span>;
