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

// The ESI level: ESI 1 asks for action, other levels are normal text, and no level (Cannot assess or no
// answer yet) asks someone to look.
const ESI_TEXT = { urgent: 'text-red-600', neutral: 'text-gray-600', attention: 'text-amber-700' };

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

// The case's ESI level (a doctor's when one set it, else the AI's suggestion) and the AI's risk score.
// No assessment yet shows as unavailable, never as a guess. The case pane (detail) also says where the
// level came from; a queue row shows the level and score alone, with the source on hover.
export const EsiBadge = ({ liveCase, detail = true }) => {
  const { esi_level: level, ai_esi_level: suggested, esi_change: change, risk_score: risk } = liveCase;
  const assessing = ['pending', 'processing'].includes(liveCase.processing?.assessment?.status);
  if (!level && !liveCase.preparation_category && assessing) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-600">
        <Loader2 className="h-3 w-3 animate-spin" />Assessing…
      </span>
    );
  }
  const tone = level === 1 ? 'urgent' : level ? 'neutral' : 'attention';
  const label = level ? `ESI ${level}`
    : liveCase.preparation_category === 'Cannot assess' ? 'ESI: cannot assess' : 'Not assessed';
  const setBy = change && `set by ${change.user_name}${suggested !== level ? ` (suggested ${suggested ? `ESI ${suggested}` : 'none'})` : ''}`;
  let text = label;
  if (detail && setBy) text = `${label} · ${setBy}`;
  else if (detail && level) text = `Suggested ${label}`;
  return (
    <span className={`text-xs font-medium ${ESI_TEXT[tone]}`}
          title={!detail && level ? (setBy ? `ESI ${setBy}` : 'Suggested ESI') : undefined}>
      {text}
      {risk != null && ` · Risk ${risk}/10`}
      {(level || risk != null) && liveCase.assessment_is_outdated && ' · earlier info'}
    </span>
  );
};

// Marks information that arrived after the hospital's last acknowledgment.
export const NewTag = () => <span className="ml-1.5 text-xs font-medium text-blue-600">New</span>;
