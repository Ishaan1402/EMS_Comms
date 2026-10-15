import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { SectionLabel } from './ui';

const errorText = (error, fallback) => error.response?.data?.error || fallback;

/** Up/down arrows beside a queue row while Auto-order is off. */
export const MoveButtons = ({ onMove, first, last }) => (
  <div className="flex flex-col justify-center border-b border-gray-100 px-1">
    <button type="button" onClick={() => onMove(-1)} disabled={first} aria-label="Move up"
      className="flex h-7 w-7 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-25">
      <ChevronUp className="h-4 w-4" />
    </button>
    <button type="button" onClick={() => onMove(1)} disabled={last} aria-label="Move down"
      className="flex h-7 w-7 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-25">
      <ChevronDown className="h-4 w-4" />
    </button>
  </div>
);

/** The hospital's risk fields on one case, filled in by its doctors. */
export function CaseRiskFields(props) {
  // A new patient owns a new set of field drafts and pending requests.
  return <CaseRiskValues key={props.caseId} {...props} />;
}

function CaseRiskValues({ caseId, fields, liveValues, refreshToken }) {
  const [values, setValues] = useState({});
  const generation = useRef(0);
  const lastLive = useRef(liveValues);

  useEffect(() => {
    let current = true;
    const request = ++generation.current;
    axios.get(`/api/cases/${caseId}/risk-values`)
      .then((response) => { if (current && request === generation.current) setValues(response.data.values); })
      .catch(() => { if (current && request === generation.current) toast.error('Could not load risk fields'); });
    return () => { current = false; generation.current += 1; };
  }, [caseId, refreshToken]);

  useEffect(() => {
    if (liveValues?.case_id === caseId) {
      // New live data wins over an earlier fetch/save; an initial cached event can be refreshed.
      if (lastLive.current !== liveValues) generation.current += 1;
      lastLive.current = liveValues;
      setValues(liveValues.values);
    }
  }, [liveValues, caseId]);

  if (fields.length === 0) return null;

  const save = async (field, value) => {
    const request = ++generation.current;
    try {
      const { data } = await axios.put(`/api/cases/${caseId}/risk-values`, { field_id: field.id, value });
      if (request === generation.current) setValues(data.values);
    } catch (error) {
      if (request === generation.current) toast.error(errorText(error, `Could not save ${field.label}`));
    }
  };

  return (
    <section className="py-4">
      <SectionLabel as="h3" className="mb-2">Custom fields</SectionLabel>
      <dl className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
        {fields.map((field) => (
          <div key={field.id} className="flex items-center justify-between gap-3">
            <dt className="text-gray-600">{field.label}</dt>
            <dd><RiskInput field={field} value={values[field.id] ?? null} onSave={(value) => save(field, value)} /></dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function RiskInput({ field, value, onSave }) {
  const [draft, setDraft] = useState(value ?? '');
  useEffect(() => setDraft(value ?? ''), [value]);

  if (field.kind === 'yes_no') {
    return (
      <span role="group" aria-label={field.label} className="inline-flex gap-4">
        {[[true, 'Yes'], [false, 'No']].map(([option, text]) => (
          <button key={text} type="button" aria-pressed={value === option}
            onClick={() => onSave(value === option ? null : option)}
            className={`text-sm ${value === option ? 'font-semibold text-gray-900 underline decoration-2 underline-offset-4' : 'text-gray-500 hover:text-gray-900'}`}>
            {text}
          </button>
        ))}
      </span>
    );
  }
  const commit = () => {
    const next = field.kind === 'number' ? (draft === '' ? null : Number(draft)) : draft.trim() || null;
    if (next !== value) onSave(next);
  };
  return (
    <input aria-label={field.label} type={field.kind === 'number' ? 'number' : 'text'} value={draft}
      onChange={(event) => setDraft(event.target.value)} onBlur={commit}
      onKeyDown={(event) => { if (event.key === 'Enter') event.target.blur(); }}
      className={`${field.kind === 'number' ? 'w-20' : 'w-44'} rounded-lg border border-gray-300 px-2 py-1 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600`} />
  );
}
