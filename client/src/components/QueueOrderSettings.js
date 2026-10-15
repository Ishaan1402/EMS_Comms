import React, { useEffect, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';

const MODES = [
  { value: 'standard', label: 'Standard', text: (rules) => rules.standard },
  { value: 'ai_assisted', label: 'AI-assisted', text: (rules) => rules.ai_assisted },
  { value: 'manual', label: 'Manual', text: () => 'Your team orders the queue with the arrows. New cases appear on top.' },
];

/**
 * How the queue is ordered, for every doctor at the hospital. Each mode is a card with the server's own
 * description of its rule. The same choice is in the Order menu above the queue.
 */
const QueueOrderSettings = ({ mode, rules, onChanged }) => {
  const [saving, setSaving] = useState(false);
  // The card you pressed shows as chosen at once; it gives way to the server's value when that arrives.
  const [pending, setPending] = useState(null);
  useEffect(() => { if (pending === mode) setPending(null); }, [mode, pending]);
  const shown = pending ?? mode;
  const choose = async (value) => {
    if (value === shown) return;
    setPending(value);
    setSaving(true);
    try {
      await axios.put('/api/hospitals/mine/queue-mode', { mode: value });
      onChanged();
    } catch (error) {
      setPending(null);
      toast.error(error.response?.data?.error || 'Could not change the order');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <p className="text-sm text-gray-600">
        One choice for every doctor at this hospital. You can also change it from the Order menu above the queue.
      </p>
      <fieldset className="mt-5 space-y-3" disabled={saving}>
        <legend className="sr-only">Queue order</legend>
        {MODES.map(({ value, label, text }) => (
          <label key={value}
            className={`flex cursor-pointer gap-3 rounded-lg border p-4 transition-colors ${shown === value ? 'border-blue-600 bg-blue-50/40 ring-1 ring-blue-600' : 'border-gray-200 hover:bg-gray-50'}`}>
            <input type="radio" name="queue-order" value={value} checked={shown === value} onChange={() => choose(value)}
              className="mt-1 h-4 w-4 accent-blue-600" />
            <span>
              <span className="block text-sm font-medium text-gray-900">{label}</span>
              <span className="block text-sm text-gray-600">{text(rules)}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <p className="mt-5 text-xs text-gray-500">
        AI suggestions come from a language model reading the crew&apos;s reports; they are not validated on real EMS
        audio. The ordering rules are drafts a clinician has not yet approved.
      </p>
    </div>
  );
};

export default QueueOrderSettings;
