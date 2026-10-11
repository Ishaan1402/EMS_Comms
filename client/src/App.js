import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './contexts/AuthContext';
import Navbar from './components/Navbar';
import Login from './components/Login';
import Register from './components/Register';
import EMTDashboard from './components/EMTDashboard';
import DoctorDashboard from './components/DoctorDashboard';
import LoadingSpinner from './components/LoadingSpinner';

function App() {
  const { user, loading, isAuthenticated } = useAuth();

  if (loading) {
    return <LoadingSpinner />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {isAuthenticated && <Navbar />}
      
      <Routes>
        <Route 
          path="/login" 
          element={isAuthenticated ? <Navigate to="/" /> : <Login />} 
        />
        <Route 
          path="/register" 
          element={isAuthenticated ? <Navigate to="/" /> : <Register />} 
        />
        <Route 
          path="/" 
          element={
            isAuthenticated ? (
              user?.role === 'emt' ? <EMTDashboard /> : <DoctorDashboard />
            ) : (
              <Navigate to="/login" />
            )
          } 
        />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </div>
  );
}

export default App; 