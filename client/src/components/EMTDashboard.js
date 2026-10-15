import React from 'react';
import { useAuth } from '../contexts/AuthContext';
import LiveCaseRecorder from './LiveCaseRecorder';

const EMTDashboard = () => {
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <h1 className="mb-5 text-xl font-semibold text-gray-900">Welcome back, {user?.first_name}</h1>
      <LiveCaseRecorder />
    </div>
  );
};

export default EMTDashboard;
