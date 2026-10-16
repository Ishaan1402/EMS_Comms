import React, { useId } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, CheckCircle, Keyboard, Loader2, MapPin, Radio, RotateCw } from 'lucide-react';
import { buildVitalRows, formatValue } from '../utils/vitalsTrend';
import { CategoryBadge, NewTag, OperationalBadge, ProcessingBadge, ReportingBadge, isReporting } from './CaseBadges';
import Disclosure from './Disclosure';
import { Button, Caption, SectionLabel, StatusText } from './ui';

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

/**
 * A value or fact the crew reported. Hovering it (or focusing it, or tapping it on a phone) shows where it
 * came from at once, in the crew's own words, instead of waiting on the browser's slow tooltip.
 */
const Sourced = ({ as: Tag = 'span', source, at, quote, className = '', children }) => {
  const id = useId();
  return (
    <Tag tabIndex={0} aria-describedby={id}
         className={`group relative cursor-help rounded decoration-gray-400 decoration-dotted underline-offset-4 outline-none hover:bg-gray-100 hover:underline focus-visible:bg-gray-100 focus-visible:underline ${className}`}>
      {children}
      <span role="tooltip" id={id}
            className="pointer-events-none invisible absolute left-0 top-full z-20 mt-1 w-max max-w-[min(18rem,80vw)] whitespace-normal rounded-md bg-gray-900 px-2.5 py-1.5 text-xs font-normal leading-snug text-white shadow-lg group-hover:visible group-focus:visible">
        {sourceTitle(source, at, quote)}
      </span>
    </Tag>
  );
};

const SourceIcon = ({ source }) => (source === 'radio'
  ? <Radio className="ml-1 inline h-3 w-3 text-gray-500" aria-label="from the radio" />
  : <Keyboard className="ml-1 inline h-3 w-3 text-gray-500" aria-label="typed" />);

// One block of the case: a title with a caption beside it, then its content, a rule above each block.
const Section = ({ title, caption = null, children }) => (
  <section className="py-4 first:pt-0 last:pb-0">
    {title && (
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4">
        <SectionLabel as="h3">{title}</SectionLabel>
        {caption && <Caption>{caption}</Caption>}
      </div>
    )}
    {children}
  </section>
);

const latestOf = (row) => row.readings[row.readings.length - 1];

// "SpO2 89 % (on room air)": a number never travels without its unit and the conditions it was taken in.
const latestReading = (row) => {
  const latest = latestOf(row);
  return `${row.label} ${latest.value}${row.unit ? ` ${row.unit}` : ''}${latest.qualifier ? ` (${latest.qualifier})` : ''}`;
};

// Which way the latest reading moved from the one before it ("100/64" compares by the first number).
const direction = (row) => {
  if (row.readings.length < 2) return null;
  const now = Number.parseFloat(latestOf(row).value);
  const before = Number.parseFloat(row.readings[row.readings.length - 2].value);
  if (Number.isNaN(now) || Number.isNaN(before) || now === before) return null;
  return now > before ? 'up' : 'down';
};

/**
 * Folded to one line: the latest reading of each sign, with which way it moved. Open it for one row per
 * sign, oldest value first, each marked with where it came from (hover a value for the crew's words).
 */
const VitalsSection = ({ vitals, spoken, isNew }) => {
  const rows = buildVitalRows(vitals, spoken);
  if (rows.length === 0) {
    return <Section title="Vitals"><p className="text-sm text-gray-500">No vitals reported yet.</p></Section>;
  }
  const anyNew = rows.some((row) => row.readings.some((r) => isNew(r.version)));
  const summary = rows.map((row, index) => {
    const way = direction(row);
    // The space around the separator is where a narrow screen may wrap; each reading stays whole.
    return (
      <React.Fragment key={row.name}>
        {index > 0 && <>{' '}<span className="mx-1 text-gray-400">·</span>{' '}</>}
        <span className="whitespace-nowrap">
        {latestReading(row)}
        {way && (way === 'up'
          ? <ArrowUp className="ml-0.5 inline h-3 w-3 text-gray-500" aria-label={`up from ${row.readings[row.readings.length - 2].value}`} />
          : <ArrowDown className="ml-0.5 inline h-3 w-3 text-gray-500" aria-label={`down from ${row.readings[row.readings.length - 2].value}`} />)}
        </span>
      </React.Fragment>
    );
  });
  return (
    <Section>
      <Disclosure title={<>Vitals{anyNew && <NewTag />}</>} summary={summary} summaryClassName="whitespace-normal">
        <table className="text-sm">
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <td className="whitespace-nowrap py-1 pr-4 align-top font-medium text-gray-700">
                  {row.label} {row.unit && <span className="text-xs font-normal text-gray-500">{row.unit}</span>}
                </td>
                <td className="py-1 pr-4 text-gray-500">
                  {row.readings.map((r, index) => {
                    const isLatest = index === row.readings.length - 1;
                    return (
                      <React.Fragment key={r.id}>
                        {index > 0 && <span className="text-gray-400"> → </span>}
                        <Sourced source={r.source === 'typed vitals' ? 'typed' : r.source} at={r.at} quote={r.quote}
                                 className={isLatest ? 'font-semibold text-gray-900' : 'text-gray-500'}>
                          {r.value}
                          {r.qualifier && <span className="text-xs font-normal text-gray-500"> ({r.qualifier})</span>}
                          {isLatest && <SourceIcon source={r.source} />}
                          {isNew(r.version) && <NewTag />}
                        </Sourced>
                      </React.Fragment>
                    );
                  })}
                </td>
                <td className="py-1 align-top text-xs tabular-nums text-gray-500">{formatTime(latestOf(row).at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Disclosure>
    </Section>
  );
};

const FACT_STATUS = { absent: 'absent', 'not assessed': 'not assessed', planned: 'planned, not yet given', given: 'given' };

const FactList = ({ title, facts, isNew }) => facts.length > 0 && (
  <div>
    <p className="mb-1 text-xs font-medium text-gray-500">{title}</p>
    <ul className="space-y-1 text-sm">
      {facts.map((fact) => (
        <li key={fact.id}>
          <Sourced source={fact.source.kind} at={fact.recorded_at} quote={fact.quote}
                   className={fact.status === 'absent' ? 'text-gray-500' : 'text-gray-900'}>
            {fact.text}
            {fact.status && fact.status !== 'present' && (
              <span className={fact.status === 'not assessed' || fact.status === 'planned' ? ' text-amber-700' : ' text-gray-500'}>
                {' '}— {FACT_STATUS[fact.status]}
              </span>
            )}
            <SourceIcon source={fact.source.kind} />
            <span className="text-xs tabular-nums text-gray-500"> {formatTime(fact.recorded_at)}</span>
            {isNew(fact.source_version) && <NewTag />}
          </Sourced>
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
    <Section title="Patient information">
      <div className="space-y-3">
        {['processing', 'pending'].includes(findings.status) && (
          <StatusText tone="info" dot={false}><Loader2 className="h-3 w-3 animate-spin" /> Reading the latest report…</StatusText>
        )}
        {findings.status === 'failed' && (
          <div className="flex items-center gap-2 text-sm text-red-700">
            <AlertTriangle className="h-4 w-4 shrink-0" /> Couldn&apos;t extract the latest report: {findings.error}
            {onRetry && <Button onClick={onRetry} className="px-2 py-0.5 text-xs"><RotateCw className="h-3 w-3" /> Retry</Button>}
          </div>
        )}
        {findings.chief_complaint && (
          <p className="text-sm">
            <span className="font-medium text-gray-700">Chief complaint:</span>{' '}
            <Sourced source={findings.chief_complaint.source.kind} at={findings.chief_complaint.recorded_at} quote={findings.chief_complaint.quote}>
              {findings.chief_complaint.text}
            </Sourced>
            {isNew(findings.chief_complaint.first_stated_version) && <NewTag />}
          </p>
        )}
        <FactList title="Findings" facts={findings.findings} isNew={isNew} />
        <FactList title="Treatment" facts={findings.treatments} isNew={isNew} />
        {empty && !['processing', 'pending'].includes(findings.status) && <p className="text-sm text-gray-500">Nothing extracted yet.</p>}
      </div>
    </Section>
  );
};

const UpdatesTimeline = ({ updates, isNew }) => (
  <ul className="max-h-48 space-y-1 overflow-y-auto text-sm">
    {updates.map((update) => (
      <li key={update.id} className="flex gap-2">
        <span className="shrink-0 whitespace-nowrap text-xs tabular-nums text-gray-500">{formatTime(update.created_at)}</span>
        <span className={`shrink-0 font-medium ${update.kind === 'correction' ? 'text-amber-700' : 'text-gray-700'}`}>
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

const CHANGE_LABELS = { Yes: 'Changed', No: 'No change', Unclear: 'Change unclear' };

/**
 * The case as the EMT and the hospital both see it: where it stands, the AI's handoff, vitals (folded
 * to one line), what was extracted, and typed updates. markNew (hospital view) tags what arrived since
 * the last acknowledgment, so the clinician sees exactly what they are acknowledging. `children` go
 * after the patient information (the hospital's custom fields).
 */
const CaseOverview = ({ liveCase, updates = [], vitals = [], findings = null, onRetryAssessment, onRetryFindings,
  markNew = false, children = null }) => {
  const { processing } = liveCase;
  const assessment = liveCase.current_assessment;
  const isNew = newSince(liveCase, markNew);
  const acknowledged = liveCase.latest_update_acknowledged;
  return (
    <div className="divide-y divide-gray-100">
      <Section>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-gray-600">
          <CategoryBadge liveCase={liveCase} />
          <ReportingBadge liveCase={liveCase} />
          {/* Nothing left to do is the normal state; only a state that needs a person is marked. */}
          {processing?.status !== 'completed' && <ProcessingBadge processing={processing} />}
          <OperationalBadge status={liveCase.operational_status} />
          {!markNew && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="h-4 w-4 text-gray-500" />{liveCase.destination_hospital_name || 'No destination set'}
            </span>
          )}
          {liveCase.eta_at && !liveCase.arrived_at && <span className="tabular-nums">ETA {formatTime(liveCase.eta_at)}</span>}
          {acknowledged ? (
            <span className="inline-flex items-center gap-1">
              <CheckCircle className="h-4 w-4 text-green-600" />
              Seen by {liveCase.acknowledgment.user_name} at {formatTime(liveCase.acknowledgment.acknowledged_at)}
            </span>
          ) : (
            liveCase.status === 'active' && (
              <StatusText tone="attention">
                {markNew && liveCase.acknowledgment
                  ? <>New since {liveCase.acknowledgment.user_name} acknowledged at {formatTime(liveCase.acknowledgment.acknowledged_at)}: marked <NewTag /> below</>
                  : 'Latest information not yet acknowledged'}
              </StatusText>
            )
          )}
        </div>

        {processing?.needs_review && (
          <div className="mt-3 flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            <ul className="list-disc pl-4">
              {processing.reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
            {processing.assessment.can_retry && onRetryAssessment && (
              <Button onClick={onRetryAssessment} className="shrink-0 border-red-300 px-2 py-1 text-xs text-red-800 hover:bg-red-100">
                <RotateCw className="h-3 w-3" /> Retry assessment
              </Button>
            )}
          </div>
        )}
      </Section>

      {assessment?.summary && (
        <Section
          title={<>AI handoff{liveCase.assessment_is_outdated && <span className="ml-2 text-xs font-normal text-amber-700">(based on earlier information or ETA)</span>}</>}
          caption={assessment.completed_at ? `Assessed at ${formatTime(assessment.completed_at)} · current arrival time shown above` : null}>
          {isReporting(liveCase) && (
            <StatusText tone="info" className="mb-2">
              The crew is reporting now. This handoff covers the reports they&apos;ve already sent.
            </StatusText>
          )}
          <p className="text-base leading-relaxed text-gray-900">{assessment.summary}</p>
          <dl className="mt-3 space-y-2 text-sm">
            {assessment.meaningful_change && assessment.meaningful_change !== 'No earlier report' && (
              <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-3">
                <dt className={assessment.meaningful_change === 'Yes' ? 'font-semibold text-gray-900' : 'font-medium text-gray-500'}
                    title="Compared with the earlier acknowledged report">
                  {CHANGE_LABELS[assessment.meaningful_change] || 'Change unclear'}
                </dt>
                <dd className="text-gray-800">{assessment.change_explanation}</dd>
              </div>
            )}
            {assessment.missing_information && assessment.missing_information !== 'None identified' && (
              <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-3">
                <dt className="font-medium text-gray-500">Missing</dt>
                <dd className="text-gray-800">{assessment.missing_information}</dd>
              </div>
            )}
          </dl>
        </Section>
      )}

      <VitalsSection vitals={vitals} spoken={findings?.vitals || []} isNew={isNew} />
      <PatientInformation findings={findings} onRetry={onRetryFindings} isNew={isNew} />
      {children}

      {updates.length > 0 && (
        <Section>
          <Disclosure title={<>Typed updates ({updates.length}){updates.some((u) => (u.kind === 'eta' ? isNew.since(u.created_at) : isNew(u.info_version))) && <NewTag />}</>}>
            <UpdatesTimeline updates={updates} isNew={isNew} />
          </Disclosure>
        </Section>
      )}
    </div>
  );
};

export default CaseOverview;
