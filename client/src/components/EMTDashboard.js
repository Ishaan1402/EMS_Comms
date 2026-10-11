import React from 'react';
import { useAuth } from '../contexts/AuthContext';
import LiveCaseRecorder from './LiveCaseRecorder';

const EMTDashboard = () => {
  const { user } = useAuth();

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">
          Welcome back, {user?.first_name}! 🏥
        </h1>
        <p className="mt-2 text-gray-600">
          Open a case for each patient and keep the receiving hospital updated on the way in.
        </p>
      </div>

      <LiveCaseRecorder />
    </div>
  );
};

export default EMTDashboard;
