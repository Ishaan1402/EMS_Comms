import React, { useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import Disclosure from './Disclosure';
import Tabs from './Tabs';
import { Button } from './ui';

const VITAL_FIELDS = [
  { name: 'hr', label: 'HR', unit: 'bpm' },
  { name: 'sbp', label: 'BP sys', unit: 'mmHg' },
  { name: 'dbp', label: 'BP dia', unit: 'mmHg' },
  { name: 'rr', label: 'RR', unit: '/min' },
  { name: 'spo2', label: 'SpO2', unit: '%' },
  { name: 'temp_c', label: 'Temp', unit: '°C' },
  { name: 'gcs', label: 'GCS', unit: '' },
  { name: 'glucose', label: 'Glucose', unit: 'mg/dL' },
];

const KINDS = [
  { value: 'vitals', label: 'Vitals' },
  { value: 'note', label: 'Note' },
  { value: 'correction', label: 'Correction' },
  { value: 'eta', label: 'ETA' },
];

const newClientId = () =>
  (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

/** EMT adds typed information to an open case. Every update is kept; a correction never erases the original. */
const CaseUpdateForm = ({ caseId, onAdded }) => {
  const [kind, setKind] = useState('vitals');
  const [body, setBody] = useState('');
  const [vitals, setVitals] = useState({});
  const [etaMinutes, setEtaMinutes] = useState('');
  const [sending, setSending] = useState(false);
  // Reused if the same submission is retried, so the server stores it once.
  const [clientId, setClientId] = useState(newClientId);

  const buildPayload = () => {
    if (kind === 'eta') {
      if (etaMinutes === '') return null;
      return { kind, eta_minutes: Number(etaMinutes) };
    }
    if (kind === 'vitals') {
      const readings = Object.fromEntries(
        Object.entries(vitals).filter(([, v]) => v !== '').map(([k, v]) => [k, Number(v)]),
      );
      if (Object.keys(readings).length === 0) return null;
      return { kind, vitals: readings, body: body.trim() || null };
    }
    if (!body.trim()) return null;
    return { kind, body: body.trim() };
  };

  const submit = async (e) => {
    e.preventDefault();
    const payload = buildPayload();
    if (!payload) {
      toast.error(kind === 'vitals' ? 'Enter at least one vital sign' : kind === 'eta' ? 'Enter the ETA in minutes' : 'Enter the update');
      return;
    }
    setSending(true);
    try {
      const response = await axios.post(`/api/cases/${caseId}/updates`, { ...payload, client_id: clientId });
      onAdded?.(response.data);
      setBody('');
      setVitals({});
      setEtaMinutes('');
      setClientId(newClientId());
      toast.success('Update sent to the hospital');
    } catch (error) {
      console.error('Error adding update:', error);
      toast.error(error.response?.data?.error || 'Could not send update — try again');
    } finally {
      setSending(false);
    }
  };

  const inputClass = 'rounded-lg border border-gray-300 px-2 py-1 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600';

  return (
    <Disclosure title="Type an update" summary="vitals, a note, a correction or a new ETA" className="rounded-lg border border-gray-200 px-4 py-2">
      <form onSubmit={submit} className="space-y-3 pb-2">
        <div className="border-b border-gray-200">
          <Tabs label="Kind of update" required value={kind} onChange={setKind}
            items={KINDS.map((option) => ({ id: option.value, label: option.label }))} />
        </div>

        {kind === 'vitals' && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {VITAL_FIELDS.map((field) => (
              <label key={field.name} className="text-xs text-gray-600">
                {field.label} {field.unit && <span className="text-gray-500">({field.unit})</span>}
                <input
                  type="number"
                  step="any"
                  inputMode="decimal"
                  value={vitals[field.name] ?? ''}
                  onChange={(e) => setVitals((v) => ({ ...v, [field.name]: e.target.value }))}
                  className={`${inputClass} w-full`}
                />
              </label>
            ))}
          </div>
        )}

        {kind === 'eta' ? (
          <label className="text-sm text-gray-700 flex items-center gap-2">
            Arriving in
            <input type="number" min="0" value={etaMinutes} onChange={(e) => setEtaMinutes(e.target.value)} className={`${inputClass} w-24`} />
            minutes
          </label>
        ) : (
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={2}
            maxLength={2000}
            placeholder={
              kind === 'correction' ? 'What was wrong earlier, and what is correct now'
                : kind === 'vitals' ? 'Optional comment (e.g. on 4L O2)' : 'New findings, treatments given, changes…'
            }
            className={`${inputClass} w-full`}
          />
        )}

        <Button type="submit" variant="primary" disabled={sending}>{sending ? 'Sending…' : 'Send update'}</Button>
      </form>
    </Disclosure>
  );
};

export default CaseUpdateForm;
