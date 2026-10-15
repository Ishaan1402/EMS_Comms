import React from 'react';
import LiveCasesPanel from './LiveCasesPanel';

// The navbar already names the dashboard and the signed-in doctor, so the queue starts at the top.
const DoctorDashboard = () => (
  <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 px-6 pb-6 pt-4">
    <div className="max-w-7xl mx-auto">
      <LiveCasesPanel />
    </div>
  </div>
);

export default DoctorDashboard;
