import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import { Info } from 'lucide-react';

export const MODES = [
  { value: 'standard', label: 'Standard' },
  { value: 'ai_assisted', label: 'AI-assisted' },
  { value: 'manual', label: 'Manual' },
];

const MANUAL_RULE = 'Your team orders the queue with the arrows; new cases appear on top.';

/**
 * How this hospital's queue is ordered: Standard (the written rule), AI-assisted (the rule plus the
 * AI's category) or Manual (the doctors' own order). One choice for the whole hospital, saved on the
 * server (PUT /api/hospitals/mine/queue-mode). The (i) opens what each mode does, in the server's own
 * words, and the AI disclosure; it stays in the page while closed.
 */
const QueueModeControl = ({ mode = 'standard', rules = {}, ruleVersions = {}, onChange }) => {
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);
  const root = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => { if (event.type === 'keydown' ? event.key === 'Escape' : !root.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);

  const choose = async (value) => {
    if (value === mode || saving) return;
    setSaving(true);
    try {
      await axios.put('/api/hospitals/mine/queue-mode', { mode: value });
      onChange();
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not change the order');
    } finally {
      setSaving(false);
    }
  };

  const explanation = { standard: rules.standard, ai_assisted: rules.ai_assisted, manual: MANUAL_RULE };
  return (
    <div ref={root} className="relative flex items-center gap-2 text-sm text-gray-700">
      <span>Order</span>
      <div role="radiogroup" aria-label="Queue order" className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
        {MODES.map(({ value, label }) => (
          <button key={value} type="button" role="radio" aria-checked={mode === value} disabled={saving}
            onClick={() => choose(value)}
            className={`px-3 py-1 text-sm border-l border-gray-300 first:border-l-0 disabled:opacity-60 ${mode === value ? 'bg-blue-700 font-medium text-white' : 'bg-white text-gray-700 hover:bg-gray-50'}`}>
            {label}
          </button>
        ))}
      </div>
      <button type="button" aria-label="How the queue is ordered" aria-expanded={open} aria-controls="queue-order-help"
        onClick={() => setOpen((current) => !current)} className="rounded-full p-0.5 text-gray-500 hover:text-gray-900">
        <Info className="h-4 w-4" />
      </button>
      <div id="queue-order-help" hidden={!open}
        className="absolute right-0 top-full z-20 mt-2 w-[26rem] max-w-[90vw] space-y-2 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700 shadow-lg">
        {MODES.map(({ value, label }) => (
          <p key={value}>
            <span className={mode === value ? 'font-semibold text-gray-900' : 'font-medium text-gray-900'}>{label}</span>
            {mode === value && <span className="text-xs text-gray-500"> (current)</span>}
            {' '}{explanation[value]}
          </p>
        ))}
        <p className="text-xs text-gray-600">
          AI suggestions come from a language model reading the crew&apos;s reports; they are not validated on real
          EMS audio. Check each one against the quotes and missing items in the handoff.
        </p>
        {ruleVersions.standard && (
          <p className="text-xs text-gray-500">
            Rules {ruleVersions.standard} and {ruleVersions.ai_assisted} are drafts a clinician has not yet approved.
          </p>
        )}
      </div>
    </div>
  );
};

export default QueueModeControl;
