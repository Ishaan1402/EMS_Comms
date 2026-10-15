import React, { useEffect, useId, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { SectionLabel } from './ui';

const KIND_LABELS = { yes_no: 'Yes / no', number: 'Number', text: 'Short text' };

const errorText = (error, fallback) => error.response?.data?.error || fallback;

/** The risk fields this hospital's doctors fill in on each case: add and remove them here. */
export function RiskFieldSettings({ fields, onChanged }) {
  const id = useId();
  const [label, setLabel] = useState('');
  const [kind, setKind] = useState('yes_no');
  const [saving, setSaving] = useState(false);

  const add = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await axios.post('/api/hospitals/mine/risk-fields', { label, kind });
      setLabel('');  // the other screens update from the live event; this one asks its page to reload
      if (onChanged) onChanged();
    } catch (error) {
      toast.error(errorText(error, 'Could not add the field'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (field) => {
    if (!window.confirm(`Remove "${field.label}"? Values already recorded are kept but no longer shown.`)) return;
    try {
      await axios.delete(`/api/hospitals/mine/risk-fields/${field.id}`);
      if (onChanged) onChanged();
    } catch (error) {
      toast.error(errorText(error, 'Could not remove the field'));
    }
  };

  return (
    <section className="space-y-2">
      <SectionLabel as="h4">Custom risk fields{fields.length > 0 && ` (${fields.length})`}</SectionLabel>
      <p className="text-gray-600">
        Your own yes/no, number or short-text fields (up to 10). They show on every case, under the vitals, for your
        doctors to fill in. They never change the queue order.
      </p>
      {fields.length === 0 && <p className="text-gray-500">None yet.</p>}
      <div className="space-y-1">
        {fields.map((field) => (
          <div key={field.id} className="flex items-center gap-2">
            <span>{field.label}</span>
            <span className="text-xs text-gray-500">{KIND_LABELS[field.kind]}</span>
            <button type="button" onClick={() => remove(field)} className="text-xs text-red-700 underline">Remove</button>
          </div>
        ))}
      </div>
      <form onSubmit={add} className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="sr-only">Field name</label>
        <input id={id} value={label} onChange={(event) => setLabel(event.target.value)} maxLength={40} required
          placeholder="e.g. On blood thinners" disabled={saving}
          className="w-56 px-2 py-1 border border-gray-300 rounded" />
        <select value={kind} onChange={(event) => setKind(event.target.value)} disabled={saving}
          aria-label="Field type" className="px-2 py-1 border border-gray-300 rounded">
          {Object.entries(KIND_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
        </select>
        <button type="submit" disabled={saving || !label.trim()}
          className="px-3 py-1 text-white bg-blue-700 rounded disabled:opacity-50">Add field</button>
      </form>
    </section>
  );
}

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
