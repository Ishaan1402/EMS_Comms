import React from 'react';
import { AlertTriangle, CheckCircle, Clock, Loader2, MapPin, RotateCw, Truck } from 'lucide-react';

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

const formatValue = (value) => (Number.isInteger(value) ? value : Number(value).toFixed(1));

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

const VitalsTrend = ({ vitals }) => {
  const byName = new Map();
  vitals.forEach((reading) => {
    if (!byName.has(reading.name)) byName.set(reading.name, []);
    byName.get(reading.name).push(reading);
  });
  if (byName.size === 0) return <p className="text-sm text-gray-500 italic">No vitals entered yet.</p>;
  return (
    <table className="text-sm">
      <tbody>
        {[...byName.values()].map((readings) => {
          const latest = readings[readings.length - 1];
          return (
            <tr key={latest.name}>
              <td className="pr-3 py-0.5 font-medium text-gray-700 whitespace-nowrap">{latest.label}</td>
              <td className="pr-3 py-0.5 text-gray-900">
                {readings.map((r) => formatValue(r.value)).join(' → ')} {latest.unit}
              </td>
              <td className="py-0.5 text-xs text-gray-500">{formatTime(latest.measured_at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
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
const CaseOverview = ({ liveCase, updates = [], vitals = [], onRetryAssessment }) => {
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
                {assessment.meaningful_change === 'Yes' ? 'Changed since you last acknowledged'
                  : assessment.meaningful_change === 'No' ? 'No meaningful change since you last acknowledged'
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
          <VitalsTrend vitals={vitals} />
        </div>
        <div>
          <h4 className="text-sm font-semibold text-gray-700 mb-1">Updates</h4>
          <UpdatesTimeline updates={updates} />
        </div>
      </div>
    </div>
  );
};

export default CaseOverview;
