import React, { useId, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import { Trash2 } from 'lucide-react';
import Menu from './Menu';
import { Button } from './ui';

export const MAX_FIELDS = 10;

const KINDS = [
  { value: 'yes_no', label: 'Yes / no', description: 'A yes or no answer' },
  { value: 'number', label: 'Number', description: 'A number, such as a score' },
  { value: 'text', label: 'Short text', description: 'A few words' },
];
const kindLabel = (kind) => KINDS.find((k) => k.value === kind)?.label || kind;

const errorText = (error, fallback) => error.response?.data?.error || fallback;

/**
 * The hospital's own risk fields: a table of what each case asks its doctors, with an add row under
 * it. They show on every case under the vitals and never change the queue order. `onChanged` asks the
 * page to reload the list (this page has no live stream).
 */
const CustomFieldsSettings = ({ fields, onChanged }) => {
  const id = useId();
  const [label, setLabel] = useState('');
  const [kind, setKind] = useState('yes_no');
  const [saving, setSaving] = useState(false);

  const add = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await axios.post('/api/hospitals/mine/risk-fields', { label, kind });
      setLabel('');
      onChanged();
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
      onChanged();
    } catch (error) {
      toast.error(errorText(error, 'Could not remove the field'));
    }
  };

  return (
    <div>
      <p className="text-sm text-gray-600">
        Questions of your own that appear on every case, under the vitals, for your doctors to answer. They never
        change the queue order.
      </p>

      <table className="mt-5 w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-left text-xs text-gray-500">
            <th className="pb-2 pr-4 font-medium">Name</th>
            <th className="pb-2 pr-4 font-medium">Answer</th>
            <th className="w-10 pb-2"><span className="sr-only">Remove</span></th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.id} className="border-b border-gray-100">
              <td className="py-2.5 pr-4 text-gray-900">{field.label}</td>
              <td className="py-2.5 pr-4 text-gray-600">{kindLabel(field.kind)}</td>
              <td className="py-1.5 text-right">
                <button type="button" onClick={() => remove(field)} aria-label={`Remove ${field.label}`} title="Remove"
                  className="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-red-600">
                  <Trash2 className="h-4 w-4" />
                </button>
              </td>
            </tr>
          ))}
          {fields.length === 0 && (
            <tr><td colSpan={3} className="py-4 text-gray-500">No custom fields yet.</td></tr>
          )}
        </tbody>
      </table>

      <form onSubmit={add} className="mt-4 flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="sr-only">Field name</label>
        <input id={id} value={label} onChange={(event) => setLabel(event.target.value)} maxLength={40} required
          placeholder="e.g. On blood thinners" disabled={saving || fields.length >= MAX_FIELDS}
          className="w-64 rounded-lg border border-gray-300 px-3 py-1.5 text-sm placeholder:text-gray-400 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600" />
        <Menu label="Answer" value={kind} options={KINDS} onChange={setKind} align="left" />
        <Button type="submit" variant="primary" disabled={saving || !label.trim() || fields.length >= MAX_FIELDS}>Add field</Button>
        <span className="text-xs text-gray-500">{fields.length} of {MAX_FIELDS}</span>
      </form>
    </div>
  );
};

export default CustomFieldsSettings;
