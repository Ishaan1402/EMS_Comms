import React from 'react';
import { useAuth } from '../contexts/AuthContext';
import LiveCasesPanel from './LiveCasesPanel';

const DoctorDashboard = () => {
  const { user } = useAuth();

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-6">
      <div className="max-w-7xl mx-auto">
        <h1 className="mb-6 text-3xl font-bold text-gray-900">
          Welcome back, Dr. {user?.last_name}
        </h1>

        <LiveCasesPanel />
      </div>
    </div>
  );
};

export default DoctorDashboard;
