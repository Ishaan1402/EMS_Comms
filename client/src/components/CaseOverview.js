import React from 'react';
import { AlertTriangle, CheckCircle, Clock, Keyboard, Loader2, MapPin, Radio, RotateCw, Truck } from 'lucide-react';
import { buildVitalRows, formatValue } from '../utils/vitalsTrend';

const OPERATIONAL = {
  inbound: { label: 'Inbound', className: 'bg-blue-100 text-blue-800' },
  acknowledged: { label: 'Acknowledged', className: 'bg-green-100 text-green-800' },
  arrived: { label: 'Arrived', className: 'bg-gray-800 text-white' },
  closed: { label: 'Closed', className: 'bg-gray-200 text-gray-700' },
};

const PROCESSING = {
  pending: { label: 'Waiting for assessment', className: 'bg-gray-100 text-gray-700' },
  processing: { label: 'Processing…', className: 'bg-blue-50 text-blue-700' },
  completed: { label: 'Assessed', className: 'bg-green-50 text-green-700' },
  failed: { label: 'Needs review · processing failed', className: 'bg-red-100 text-red-800' },
  needs_review: { label: 'Needs review', className: 'bg-purple-100 text-purple-800' },
};

const UPDATE_LABELS = { note: 'Update', vitals: 'Vitals', correction: 'Correction', eta: 'ETA change' };

const formatTime = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');

export const OperationalBadge = ({ status }) => {
  const { label, className } = OPERATIONAL[status] || OPERATIONAL.inbound;
  return <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${className}`}>{label}</span>;
};

export const ProcessingBadge = ({ processing }) => {
  if (!processing) return null;
  const { label, className } = PROCESSING[processing.status] || PROCESSING.pending;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full ${className}`}>
      {processing.status === 'processing' && <Loader2 className="h-3 w-3 animate-spin" />}
      {processing.needs_review && <AlertTriangle className="h-3 w-3" />}
      {label}
    </span>
  );
};

// The AI's preparation category (the evaluation contract's draft labels). No assessment yet
// shows as unavailable, never as a guess.
const CATEGORY_STYLES = {
  'Prepare now': 'bg-red-50 text-red-700 border-red-200',
  'Can wait': 'bg-orange-50 text-orange-700 border-orange-200',
  Routine: 'bg-gray-50 text-gray-700 border-gray-200',
  'Cannot assess': 'bg-purple-50 text-purple-700 border-purple-200',
};

export const CategoryBadge = ({ liveCase }) => {
  const category = liveCase.preparation_category;
  return (
    <span className={`px-2 py-0.5 text-xs font-bold rounded-full border ${CATEGORY_STYLES[category] || CATEGORY_STYLES['Cannot assess']}`}>
      {category || 'Assessment unavailable'}
      {category && liveCase.assessment_is_outdated && ' · earlier info'}
    </span>
  );
};

const SOURCE_LABELS = { radio: 'Said on the radio', typed: 'Typed by the crew', 'case opened': 'Typed when the case was opened' };

// Where a value or fact came from, for its tooltip: "Said on the radio at 14:02: “Heart rate 118”".
const sourceTitle = (source, at, quote) =>
  `${SOURCE_LABELS[source] || 'Typed by the crew'} at ${formatTime(at)}${quote ? `: “${quote}”` : ''}`;

const SourceIcon = ({ source }) => (source === 'radio'
  ? <Radio className="inline h-3 w-3 ml-0.5 text-gray-400" aria-label="from the radio" />
  : <Keyboard className="inline h-3 w-3 ml-0.5 text-gray-400" aria-label="typed" />);

/** One row per vital sign, oldest value first, each marked with where it came from (hover for the words). */
const VitalsTrend = ({ vitals, spoken }) => {
  const rows = buildVitalRows(vitals, spoken);
  if (rows.length === 0) return <p className="text-sm text-gray-500 italic">No vitals reported yet.</p>;
  return (
    <table className="text-sm">
      <tbody>
        {rows.map((row) => {
          const latest = row.readings[row.readings.length - 1];
          return (
            <tr key={row.name}>
              <td className="pr-3 py-0.5 font-medium text-gray-700 whitespace-nowrap align-top">{row.label}</td>
              <td className="pr-3 py-0.5 text-gray-900">
                {row.readings.map((r, index) => (
                  <React.Fragment key={r.id}>
                    {index > 0 && ' → '}
                    <span title={sourceTitle(r.source === 'typed vitals' ? 'typed' : r.source, r.at, r.quote)}>
                      {r.value}
                      {r.qualifier && <span className="text-xs text-gray-500"> ({r.qualifier})</span>}
                      <SourceIcon source={r.source} />
                    </span>
                  </React.Fragment>
                ))} {row.unit}
              </td>
              <td className="py-0.5 text-xs text-gray-500 align-top">{formatTime(latest.at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};

const FACT_STATUS = { absent: 'absent', 'not assessed': 'not assessed', planned: 'planned, not yet given', given: 'given' };

const FactList = ({ title, facts }) => facts.length > 0 && (
  <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</p>
    <ul className="text-sm space-y-0.5">
      {facts.map((fact) => (
        <li key={fact.id} title={sourceTitle(fact.source.kind, fact.recorded_at, fact.quote)}
            className={fact.status === 'absent' ? 'text-gray-600' : 'text-gray-900'}>
          {fact.text}
          {fact.status && fact.status !== 'present' && (
            <span className={fact.status === 'not assessed' || fact.status === 'planned' ? ' text-orange-700' : ' text-gray-500'}>
              {' '}— {FACT_STATUS[fact.status]}
            </span>
          )}
          <SourceIcon source={fact.source.kind} />
          <span className="text-xs text-gray-400"> {formatTime(fact.recorded_at)}</span>
        </li>
      ))}
    </ul>
  </div>
);

/** What the extraction step pulled out of the crew's reports (case_extraction.py), with its state. */
const PatientInformation = ({ findings, onRetry }) => {
  if (!findings) return null;
  const empty = !findings.chief_complaint && findings.findings.length === 0 && findings.treatments.length === 0;
  return (
    <div className="space-y-2">
      {findings.status === 'processing' && (
        <p className="flex items-center gap-1 text-xs text-blue-700"><Loader2 className="h-3 w-3 animate-spin" /> Reading the latest report…</p>
      )}
      {findings.status === 'failed' && (
        <div className="flex items-center gap-2 text-xs text-red-700">
          <AlertTriangle className="h-3 w-3 shrink-0" /> Couldn't extract the latest report: {findings.error}
          {onRetry && (
            <button onClick={onRetry} className="flex items-center gap-1 px-2 py-0.5 border border-red-300 rounded hover:bg-red-50">
              <RotateCw className="h-3 w-3" /> Retry
            </button>
          )}
        </div>
      )}
      {findings.chief_complaint && (
        <p className="text-sm" title={sourceTitle(findings.chief_complaint.source.kind, findings.chief_complaint.recorded_at, findings.chief_complaint.quote)}>
          <span className="font-medium text-gray-700">Chief complaint:</span> {findings.chief_complaint.text}
        </p>
      )}
      <FactList title="Findings" facts={findings.findings} />
      <FactList title="Treatment" facts={findings.treatments} />
      {empty && findings.status !== 'processing' && <p className="text-sm text-gray-500 italic">Nothing extracted yet.</p>}
    </div>
  );
};

const UpdatesTimeline = ({ updates }) => {
  if (updates.length === 0) return <p className="text-sm text-gray-500 italic">No typed updates yet.</p>;
  return (
    <ul className="space-y-1 text-sm max-h-48 overflow-y-auto">
      {updates.map((update) => (
        <li key={update.id} className="flex gap-2">
          <span className="font-mono text-xs text-gray-500 whitespace-nowrap shrink-0">{formatTime(update.created_at)}</span>
          <span className={`font-medium shrink-0 ${update.kind === 'correction' ? 'text-orange-700' : 'text-gray-700'}`}>
            {UPDATE_LABELS[update.kind]}:
          </span>
          <span className="text-gray-900">
            {update.kind === 'eta' && `arriving ~${formatTime(update.eta_at)}`}
            {update.vitals?.length > 0 && update.vitals.map((v) => `${v.label} ${formatValue(v.value)} ${v.unit}`.trim()).join(', ')}
            {update.vitals?.length > 0 && update.body && ' — '}
            {update.body}
          </span>
        </li>
      ))}
    </ul>
  );
};

/**
 * Case header shared by the EMT and hospital views: routing, where the transport is,
 * what the AI processing is doing (with failures spelled out), the current risk, and
 * the vitals/update history.
 */
const CaseOverview = ({ liveCase, updates = [], vitals = [], findings = null, onRetryAssessment, onRetryFindings }) => {
  const { processing } = liveCase;
  const assessment = liveCase.current_assessment;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
        <OperationalBadge status={liveCase.operational_status} />
        <ProcessingBadge processing={processing} />
        <CategoryBadge liveCase={liveCase} />
        <span className="flex items-center gap-1"><MapPin className="h-4 w-4" />{liveCase.destination_hospital_name || 'No destination set'}</span>
        {liveCase.ems_unit && <span className="flex items-center gap-1"><Truck className="h-4 w-4" />{liveCase.ems_unit}</span>}
        {liveCase.eta_at && !liveCase.arrived_at && (
          <span className="flex items-center gap-1"><Clock className="h-4 w-4" />ETA {formatTime(liveCase.eta_at)}</span>
        )}
        {liveCase.latest_update_acknowledged ? (
          <span className="flex items-center gap-1 text-green-700">
            <CheckCircle className="h-4 w-4" /> Seen by {liveCase.acknowledgment.user_name} at {formatTime(liveCase.acknowledgment.acknowledged_at)}
          </span>
        ) : (
          liveCase.status === 'active' && <span className="text-orange-700">Latest information not yet acknowledged</span>
        )}
      </div>

      {processing?.needs_review && (
        <div className="flex items-start justify-between gap-3 p-3 rounded-md bg-red-50 border border-red-200 text-sm text-red-800">
          <ul className="list-disc pl-4">
            {processing.reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
          {processing.assessment.can_retry && onRetryAssessment && (
            <button
              onClick={onRetryAssessment}
              className="flex items-center gap-1 px-2 py-1 text-xs border border-red-300 rounded hover:bg-red-100 shrink-0"
            >
              <RotateCw className="h-3 w-3" /> Retry assessment
            </button>
          )}
        </div>
      )}

      {assessment?.summary && (
        <div className="text-sm space-y-1">
          <p className="font-medium text-gray-700">
            AI handoff {liveCase.assessment_is_outdated && <span className="text-orange-700 font-normal">(based on earlier information)</span>}
          </p>
          <p className="text-gray-900">{assessment.summary}</p>
          {assessment.meaningful_change && assessment.meaningful_change !== 'No earlier report' && (
            <p className="text-gray-700">
              <span className="font-medium">
                {assessment.meaningful_change === 'Yes' ? 'Changed since the earlier acknowledged report'
                  : assessment.meaningful_change === 'No' ? 'No meaningful change since the earlier acknowledged report'
                    : 'Change unclear'}:
              </span>{' '}
              {assessment.change_explanation}
            </p>
          )}
          {assessment.missing_information && assessment.missing_information !== 'None identified' && (
            <p className="text-gray-700"><span className="font-medium">Missing:</span> {assessment.missing_information}</p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h4 className="text-sm font-semibold text-gray-700 mb-1">Vitals</h4>
          <VitalsTrend vitals={vitals} spoken={findings?.vitals || []} />
        </div>
        <div>
          <h4 className="text-sm font-semibold text-gray-700 mb-1">
            Patient information <span className="font-normal text-xs text-gray-500">· AI-extracted from the crew's reports; hover for their words</span>
          </h4>
          <PatientInformation findings={findings} onRetry={onRetryFindings} />
        </div>
      </div>

      <div>
        <h4 className="text-sm font-semibold text-gray-700 mb-1">Typed updates</h4>
        <UpdatesTimeline updates={updates} />
      </div>
    </div>
  );
};

export default CaseOverview;
