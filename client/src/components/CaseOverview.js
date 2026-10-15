import React from 'react';
import { AlertTriangle, CheckCircle, Clock, Keyboard, Loader2, MapPin, Radio, RotateCw, Truck } from 'lucide-react';
import { buildVitalRows, formatValue } from '../utils/vitalsTrend';
import { CategoryBadge, NewTag, OperationalBadge, ProcessingBadge, ReportingBadge, isReporting } from './CaseBadges';
import { SectionLabel } from './ui';

// The badges used to live here; other files still import them from this module.
export { CategoryBadge, NewTag, OperationalBadge, ProcessingBadge, ReportingBadge };

const UPDATE_LABELS = { note: 'Update', vitals: 'Vitals', correction: 'Correction', eta: 'ETA change' };

const formatTime = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');

// isNew(version): did this arrive after the last acknowledgment? Before any acknowledgment
// everything is new, so nothing is marked. isNew.since(time) is the same test by time, for an
// ETA change, which doesn't create a new information version.
const newSince = (liveCase, enabled) => {
  const ack = enabled ? liveCase.acknowledgment : null;
  const isNew = (version) => Boolean(ack && version != null && version > ack.info_version);
  isNew.since = (time) => Boolean(ack && time && time > ack.acknowledged_at);
  return isNew;
};

const SOURCE_LABELS = { radio: 'Said on the radio', typed: 'Typed by the crew', 'case opened': 'Typed when the case was opened' };

// Where a value or fact came from, for its tooltip: "Said on the radio at 14:02: “Heart rate 118”".
const sourceTitle = (source, at, quote) =>
  `${SOURCE_LABELS[source] || 'Typed by the crew'} at ${formatTime(at)}${quote ? `: “${quote}”` : ''}`;

const SourceIcon = ({ source }) => (source === 'radio'
  ? <Radio className="inline h-3 w-3 ml-0.5 text-gray-500" aria-label="from the radio" />
  : <Keyboard className="inline h-3 w-3 ml-0.5 text-gray-500" aria-label="typed" />);

const latestOf = (row) => row.readings[row.readings.length - 1];

// "SpO2 89 % (on room air)": a number never travels without its unit and the conditions it was taken in.
const latestReading = (row) => {
  const latest = latestOf(row);
  return `${row.label} ${latest.value}${row.unit ? ` ${row.unit}` : ''}${latest.qualifier ? ` (${latest.qualifier})` : ''}`;
};

/**
 * Folded to the latest reading of each sign; open it for one row per sign, oldest value first,
 * each marked with where it came from (hover for the words). startOpen: the hospital reads how
 * the numbers moved, so it starts open; the crew's own screen starts folded.
 */
const VitalsTrend = ({ vitals, spoken, isNew, startOpen }) => {
  const rows = buildVitalRows(vitals, spoken);
  if (rows.length === 0) {
    return (
      <>
        <SectionLabel className="mb-1">Vitals</SectionLabel>
        <p className="text-sm text-gray-500 italic">No vitals reported yet.</p>
      </>
    );
  }
  const anyNew = rows.some((row) => row.readings.some((r) => isNew(r.version)));
  return (
    <details className="group" open={startOpen}>
      <summary className="cursor-pointer text-sm">
        <SectionLabel as="span">Vitals</SectionLabel>
        <span className="text-gray-900 group-open:hidden">
          {' '}{rows.map(latestReading).join(' · ')}
        </span>
        {anyNew && <NewTag />}
      </summary>
      <table className="mt-1 text-sm">
        <tbody>
          {rows.map((row) => (
            <tr key={row.name}>
              <td className="pr-3 py-0.5 font-medium text-gray-700 whitespace-nowrap align-top">
                {row.label} {row.unit && <span className="text-xs font-normal text-gray-500">{row.unit}</span>}
              </td>
              <td className="pr-3 py-0.5 text-gray-900">
                {row.readings.map((r, index) => {
                  const isLatest = index === row.readings.length - 1;
                  return (
                    <React.Fragment key={r.id}>
                      {index > 0 && <span className="text-gray-400"> → </span>}
                      <span title={sourceTitle(r.source === 'typed vitals' ? 'typed' : r.source, r.at, r.quote)}
                            className={isLatest ? 'font-semibold text-gray-900' : 'text-gray-500'}>
                        {r.value}
                        {r.qualifier && <span className="text-xs font-normal text-gray-500"> ({r.qualifier})</span>}
                        {isLatest && <SourceIcon source={r.source} />}
                        {isNew(r.version) && <NewTag />}
                      </span>
                    </React.Fragment>
                  );
                })}
              </td>
              <td className="py-0.5 text-xs text-gray-500 align-top">{formatTime(latestOf(row).at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
};

const FACT_STATUS = { absent: 'absent', 'not assessed': 'not assessed', planned: 'planned, not yet given', given: 'given' };

const FactList = ({ title, facts, isNew }) => facts.length > 0 && (
  <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-gray-600">{title}</p>
    <ul className="text-sm space-y-0.5">
      {facts.map((fact) => (
        <li key={fact.id} title={sourceTitle(fact.source.kind, fact.recorded_at, fact.quote)}
            className={fact.status === 'absent' ? 'text-gray-600' : 'text-gray-900'}>
          {fact.text}
          {fact.status && fact.status !== 'present' && (
            <span className={fact.status === 'not assessed' || fact.status === 'planned' ? ' text-amber-800' : ' text-gray-500'}>
              {' '}— {FACT_STATUS[fact.status]}
            </span>
          )}
          <SourceIcon source={fact.source.kind} />
          <span className="text-xs text-gray-500"> {formatTime(fact.recorded_at)}</span>
          {isNew(fact.source_version) && <NewTag />}
        </li>
      ))}
    </ul>
  </div>
);

/** What the extraction step pulled out of the crew's reports (case_extraction.py), with its state. */
const PatientInformation = ({ findings, onRetry, isNew }) => {
  if (!findings) return null;
  const empty = !findings.chief_complaint && findings.findings.length === 0 && findings.treatments.length === 0;
  return (
    <div className="space-y-2">
      {['processing', 'pending'].includes(findings.status) && (
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
          {isNew(findings.chief_complaint.first_stated_version) && <NewTag />}
        </p>
      )}
      <FactList title="Findings" facts={findings.findings} isNew={isNew} />
      <FactList title="Treatment" facts={findings.treatments} isNew={isNew} />
      {empty && !['processing', 'pending'].includes(findings.status) && <p className="text-sm text-gray-500 italic">Nothing extracted yet.</p>}
    </div>
  );
};

const UpdatesTimeline = ({ updates, isNew }) => {
  return (
    <ul className="space-y-1 text-sm max-h-48 overflow-y-auto">
      {updates.map((update) => (
        <li key={update.id} className="flex gap-2">
          <span className="font-mono text-xs text-gray-500 whitespace-nowrap shrink-0">{formatTime(update.created_at)}</span>
          <span className={`font-medium shrink-0 ${update.kind === 'correction' ? 'text-amber-800' : 'text-gray-700'}`}>
            {UPDATE_LABELS[update.kind]}:
          </span>
          <span className="text-gray-900">
            {update.kind === 'eta' && `arriving ~${formatTime(update.eta_at)}`}
            {update.vitals?.length > 0 && update.vitals.map((v) => `${v.label} ${formatValue(v.value)} ${v.unit}`.trim()).join(', ')}
            {update.vitals?.length > 0 && update.body && ' — '}
            {update.body}
            {(update.kind === 'eta' ? isNew.since(update.created_at) : isNew(update.info_version)) && <NewTag />}
          </span>
        </li>
      ))}
    </ul>
  );
};

/**
 * Case header shared by the EMT and hospital views: routing, where the transport is,
 * what the AI processing is doing (with failures spelled out), the current risk, and
 * the vitals/update history. markNew (hospital view) tags what arrived since the last
 * acknowledgment, so the clinician sees exactly what they are acknowledging.
 */
const CaseOverview = ({ liveCase, updates = [], vitals = [], findings = null, onRetryAssessment, onRetryFindings,
  markNew = false, children = null }) => {
  const { processing } = liveCase;
  const assessment = liveCase.current_assessment;
  const isNew = newSince(liveCase, markNew);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
        <OperationalBadge status={liveCase.operational_status} />
        {/* Nothing left to do is the normal state; the category badge says what the AI found. */}
        {processing?.status !== 'completed' && <ProcessingBadge processing={processing} />}
        <CategoryBadge liveCase={liveCase} />
        <ReportingBadge liveCase={liveCase} />
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
          liveCase.status === 'active' && (
            <span className="text-amber-800">
              {markNew && liveCase.acknowledgment
                ? <>New since {liveCase.acknowledgment.user_name} acknowledged at {formatTime(liveCase.acknowledgment.acknowledged_at)}: marked <NewTag /> below</>
                : 'Latest information not yet acknowledged'}
            </span>
          )
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
          <SectionLabel as="p">
            AI handoff {liveCase.assessment_is_outdated && <span className="text-amber-800 font-normal">(based on earlier information or ETA)</span>}
          </SectionLabel>
          {assessment.completed_at && <p className="text-xs text-gray-500">Assessed at {formatTime(assessment.completed_at)} · current arrival time shown above</p>}
          {isReporting(liveCase) && (
            <p className="text-sm text-amber-900 bg-amber-50 border border-amber-300 rounded px-3 py-2">
              The crew is reporting now. This handoff covers the reports they've already sent.
            </p>
          )}
          <p className="text-base leading-relaxed text-gray-900">{assessment.summary}</p>
          {assessment.meaningful_change && assessment.meaningful_change !== 'No earlier report' && (
            <p className={`pl-3 border-l-4 text-gray-700 ${assessment.meaningful_change === 'Yes' ? 'border-amber-400' : 'border-gray-200'}`}>
              <span className="font-medium text-gray-900">
                {assessment.meaningful_change === 'Yes' ? 'Changed since the earlier acknowledged report'
                  : assessment.meaningful_change === 'No' ? 'No meaningful change since the earlier acknowledged report'
                    : 'Change unclear'}:
              </span>{' '}
              {assessment.change_explanation}
            </p>
          )}
          {assessment.missing_information && assessment.missing_information !== 'None identified' && (
            <p className="pl-3 border-l-4 border-gray-200 text-gray-700"><span className="font-medium text-gray-900">Missing:</span> {assessment.missing_information}</p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <VitalsTrend vitals={vitals} spoken={findings?.vitals || []} isNew={isNew} startOpen={markNew} />
        </div>
        <div>
          <SectionLabel className="mb-1">
            Patient information <span className="font-normal text-xs text-gray-600">· AI-extracted, hover for quotes</span>
          </SectionLabel>
          <PatientInformation findings={findings} onRetry={onRetryFindings} isNew={isNew} />
        </div>
      </div>

      {children}

      {updates.length > 0 && (
        <details>
          <summary className="cursor-pointer">
            <SectionLabel as="span">Typed updates ({updates.length})</SectionLabel>
            {updates.some((u) => (u.kind === 'eta' ? isNew.since(u.created_at) : isNew(u.info_version))) && <NewTag />}
          </summary>
          <div className="mt-1"><UpdatesTimeline updates={updates} isNew={isNew} /></div>
        </details>
      )}
    </div>
  );
};

export default CaseOverview;
