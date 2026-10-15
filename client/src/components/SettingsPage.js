import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';
import HospitalWorkflowSettings from './HospitalWorkflowSettings';
import { RiskFieldSettings } from './DoctorQueueSettings';

const Card = ({ children }) => <section className="rounded-xl border border-gray-200 bg-white p-5">{children}</section>;

/** The hospital's own settings for its doctors. Reached from the navbar; doctors only. */
const SettingsPage = () => {
  const [hospital, setHospital] = useState(null);
  const [riskFields, setRiskFields] = useState([]);

  const load = useCallback(() => {
    axios.get('/api/hospitals/mine').then((response) => setHospital(response.data))
      .catch(() => toast.error('Could not load hospital settings'));
    axios.get('/api/hospitals/mine/config').then((response) => setRiskFields(response.data.risk_fields))
      .catch(() => toast.error('Could not load the custom fields'));
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
      <h1 className="text-xl font-semibold text-gray-900">Settings</h1>
      <Card><RiskFieldSettings fields={riskFields} onChanged={load} /></Card>
      {hospital?.settings_version && (
        <Card><HospitalWorkflowSettings hospital={hospital} onChange={setHospital} /></Card>
      )}
    </div>
  );
};

export default SettingsPage;
