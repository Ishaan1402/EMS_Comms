import React, { useEffect, useId, useState } from 'react';
import axios from 'axios';

export const ArrivalHighlight = ({ liveCase, serverNow, minutes }) => {
  const remaining = Date.parse(liveCase.eta_at) - serverNow;
  if (liveCase.status !== 'active' || liveCase.arrived_at || !minutes
      || !Number.isFinite(remaining) || remaining < 0 || remaining > minutes * 60000) return null;
  return <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-blue-100 text-blue-800">Arriving soon</span>;
};

/** KAN-34: a hospital's display preference, separate from assessment and priority policy. */
export default function HospitalWorkflowSettings({ hospital, onChange }) {
  const id = useId();
  const [minutes, setMinutes] = useState(hospital.arrival_highlight_minutes);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => setMinutes(hospital.arrival_highlight_minutes),
    [hospital.id, hospital.arrival_highlight_minutes, hospital.settings_version]);

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      const { data } = await axios.patch('/api/hospitals/mine', {
        arrival_highlight_minutes: Number(minutes), expected_version: hospital.settings_version,
      });
      onChange(data);
      setMessage('Saved for this hospital.');
    } catch (error) {
      setMessage(error.response?.data?.error || 'Could not save. Try again.');
      if (error.response?.status === 409) {
        try {
          const { data } = await axios.get('/api/hospitals/mine');
          onChange(data);
          setMessage('Another user changed this setting. The saved value is shown; review it before saving again.');
        } catch {
          setMessage('This setting changed. Refresh the page before saving again.');
        }
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="px-6 py-2 border-b border-gray-200 text-sm text-gray-700">
      <summary className="cursor-pointer">Arrival highlight · {hospital.arrival_highlight_minutes} min</summary>
      <form onSubmit={save} className="flex flex-wrap items-center gap-2 py-2">
        <label htmlFor={id}>Highlight arrivals within</label>
        <input id={id} type="number" min="1" max="60" step="1" required value={minutes}
          onChange={(event) => setMinutes(event.target.value)} disabled={saving}
          className="w-20 px-2 py-1 border border-gray-300 rounded" />
        <span>minutes</span>
        <button type="submit" disabled={saving || Number(minutes) === hospital.arrival_highlight_minutes}
          className="px-3 py-1 text-white bg-blue-700 rounded disabled:opacity-50">{saving ? 'Saving…' : 'Save'}</button>
        {message && <p role="status" className="w-full">{message}</p>}
      </form>
    </details>
  );
}
