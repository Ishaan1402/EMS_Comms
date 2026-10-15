import React from 'react';
import { useAuth } from '../contexts/AuthContext';
import LiveCasesPanel from './LiveCasesPanel';

const DoctorDashboard = () => {
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <h1 className="mb-5 text-xl font-semibold text-gray-900">Welcome back, Dr. {user?.last_name}</h1>
      <LiveCasesPanel />
    </div>
  );
};

export default DoctorDashboard;
