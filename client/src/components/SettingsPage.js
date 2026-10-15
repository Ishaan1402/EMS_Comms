import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import CustomFieldsSettings from './CustomFieldsSettings';
import HospitalWorkflowSettings from './HospitalWorkflowSettings';
import QueueOrderSettings from './QueueOrderSettings';

const SECTIONS = [
  { id: 'order', title: 'Queue order' },
  { id: 'fields', title: 'Custom fields' },
  { id: 'arrival', title: 'Arrival alert' },
];

/** The hospital's own settings for its doctors. Reached from the navbar; doctors only. */
const SettingsPage = () => {
  const [section, setSection] = useState('order');
  const [hospital, setHospital] = useState(null);
  const [config, setConfig] = useState(null);

  const load = useCallback(() => {
    axios.get('/api/hospitals/mine').then((response) => setHospital(response.data))
      .catch(() => toast.error('Could not load hospital settings'));
    axios.get('/api/hospitals/mine/config').then((response) => setConfig(response.data))
      .catch(() => toast.error('Could not load the settings'));
  }, []);
  useEffect(() => { load(); }, [load]);

  const current = SECTIONS.find((s) => s.id === section);
  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      <h1 className="text-xl font-semibold text-gray-900">Settings</h1>
      {hospital && <p className="mt-1 text-sm text-gray-500">{hospital.name}</p>}

      <div className="mt-6 grid gap-8 md:grid-cols-[11rem_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="flex gap-1 md:flex-col">
          {SECTIONS.map(({ id, title }) => (
            <button key={id} type="button" onClick={() => setSection(id)} aria-current={id === section ? 'page' : undefined}
              className={`rounded-md px-3 py-2 text-left text-sm font-medium transition-colors ${id === section ? 'bg-gray-100 text-gray-900' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'}`}>
              {title}
            </button>
          ))}
        </nav>

        <section aria-labelledby="settings-title" className="rounded-xl border border-gray-200 bg-white p-6">
          <h2 id="settings-title" className="mb-1 text-base font-semibold text-gray-900">{current.title}</h2>
          {section === 'order' && config && (
            <QueueOrderSettings mode={config.queue_mode} rules={config.rules} onChanged={load} />
          )}
          {section === 'fields' && config && <CustomFieldsSettings fields={config.risk_fields} onChanged={load} />}
          {section === 'arrival' && hospital?.settings_version && (
            <div>
              <p className="mb-4 text-sm text-gray-600">
                Flag a patient as arriving soon once their ETA is within this many minutes. It only changes the
                &quot;Arriving soon&quot; filter and the blue ETA, never the AI or the queue order.
              </p>
              <HospitalWorkflowSettings hospital={hospital} onChange={setHospital} />
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default SettingsPage;
