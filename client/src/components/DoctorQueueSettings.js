import React, { useEffect, useState } from 'react';
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
export function CaseRiskFields({ caseId, fields, liveValues }) {
  const [values, setValues] = useState({});

  useEffect(() => {
    let current = true;
    setValues({});
    axios.get(`/api/cases/${caseId}/risk-values`)
      .then((response) => { if (current) setValues(response.data.values); })
      .catch(() => toast.error('Could not load risk fields'));
    return () => { current = false; };
  }, [caseId]);

  useEffect(() => {
    if (liveValues?.case_id === caseId) setValues(liveValues.values);
  }, [liveValues, caseId]);

  if (fields.length === 0) return null;

  const save = async (field, value) => {
    try {
      const { data } = await axios.put(`/api/cases/${caseId}/risk-values`, { field_id: field.id, value });
      setValues(data.values);
    } catch (error) {
      toast.error(errorText(error, `Could not save ${field.label}`));
    }
  };

  return (
    <div className="p-3 border border-gray-200 rounded-lg">
      <SectionLabel className="mb-2">Custom risk fields</SectionLabel>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 text-sm">
        {fields.map((field) => (
          <div key={field.id} className="flex items-center gap-2">
            <dt className="text-gray-600">{field.label}</dt>
            <dd><RiskInput field={field} value={values[field.id] ?? null} onSave={(value) => save(field, value)} /></dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function RiskInput({ field, value, onSave }) {
  const [draft, setDraft] = useState(value ?? '');
  useEffect(() => setDraft(value ?? ''), [value]);

  if (field.kind === 'yes_no') {
    return (
      <span className="inline-flex rounded border border-gray-300 overflow-hidden" aria-label={field.label}>
        {[[true, 'Yes'], [false, 'No']].map(([option, text]) => (
          <button key={text} type="button" aria-pressed={value === option}
            onClick={() => onSave(value === option ? null : option)}
            className={`px-2 py-0.5 text-xs ${value === option ? 'bg-blue-700 text-white' : 'bg-white text-gray-700'}`}>
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
      className={`${field.kind === 'number' ? 'w-20' : 'w-40'} px-2 py-0.5 border border-gray-300 rounded`} />
  );
}
