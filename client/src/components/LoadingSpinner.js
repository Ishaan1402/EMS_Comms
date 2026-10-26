import React from 'react';

const LoadingSpinner = () => {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-red-600 mx-auto mb-4"></div>
        <h2 className="text-xl font-semibold text-gray-700">Loading Eir...</h2>
        <p className="text-gray-500 mt-2">Please wait while we set up your dashboard</p>
      </div>
    </div>
  );
};

export default LoadingSpinner; 