import React, { useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import Menu from './Menu';

// One short line per mode, for the menu. Settings has the rule text itself.
export const ORDER_OPTIONS = [
  { value: 'standard', label: 'Standard', description: 'Crew alerts and unacknowledged information first, then arrival time.' },
  { value: 'ai_assisted', label: 'AI-assisted', description: "Same, with the AI's category before arrival time. A person's call still comes first." },
  { value: 'manual', label: 'Manual', description: 'Your team drags the cases into order.' },
];

/** How the hospital's queue is ordered: one choice for all its doctors, saved on the server. */
const QueueOrderMenu = ({ mode = 'standard', onChange }) => {
  const [saving, setSaving] = useState(false);
  const choose = async (value) => {
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
  return (
    <Menu label="Order" value={mode} options={ORDER_OPTIONS} onChange={choose} disabled={saving}
      footer={(
        <>
          AI suggestions come from a language model reading the crew&apos;s reports; they are not validated on real
          EMS audio. The ordering rules are drafts a clinician has not yet approved.
        </>
      )} />
  );
};

export default QueueOrderMenu;
