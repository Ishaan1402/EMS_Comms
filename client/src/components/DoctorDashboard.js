import React from 'react';
import { useAuth } from '../contexts/AuthContext';
import LiveCasesPanel from './LiveCasesPanel';

const DoctorDashboard = () => {
  const { user } = useAuth();

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-4xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
            Doctor Dashboard
          </h1>
          <p className="text-gray-600 mt-2">Welcome back, Dr. {user?.last_name}</p>
        </div>

        <LiveCasesPanel />
      </div>
    </div>
  );
};

export default DoctorDashboard;
