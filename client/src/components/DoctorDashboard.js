import React, { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { 
  Bell, Eye, MessageSquare, Clock, AlertTriangle, CheckCircle, Shield, 
  Activity, Heart, Zap, TrendingUp, Users, RefreshCw, Star, ArrowUp, ArrowDown, Filter
} from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import CaseChat from './CaseChat';

const DoctorDashboard = () => {
  const { user } = useAuth();
  const [notifications, setNotifications] = useState([]);
  const [selectedRecording, setSelectedRecording] = useState(null);
  const [showRecordingModal, setShowRecordingModal] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [sortBy, setSortBy] = useState('priority'); // Default to priority sorting

  useEffect(() => {
    fetchNotifications();
  }, [sortBy]);

  const fetchNotifications = async () => {
    setIsLoading(true);
    try {
      const response = await axios.get(`/api/doctors/notifications?sortBy=${sortBy}`);
      setNotifications(response.data);
    } catch (error) {
      console.error('Error fetching notifications:', error);
      toast.error('Failed to fetch notifications');
    } finally {
      setIsLoading(false);
    }
  };

  const markAsRead = async (notificationId) => {
    try {
      await axios.patch(`/api/doctors/notifications/${notificationId}/read`);
      fetchNotifications();
      toast.success('Marked as read');
    } catch (error) {
      console.error('Error marking as read:', error);
      toast.error('Failed to mark as read');
    }
  };

  const viewRecording = async (recordingId) => {
    try {
      const response = await axios.get(`/api/doctors/recording/${recordingId}`);
      setSelectedRecording(response.data);
      setShowRecordingModal(true);
    } catch (error) {
      console.error('Error fetching recording:', error);
      toast.error('Failed to fetch recording details');
    }
  };

  const handleSortChange = (newSortBy) => {
    setSortBy(newSortBy);
  };

  const getRiskColor = (riskScore) => {
    if (riskScore >= 8) return 'text-red-600 bg-red-50 border-red-200';
    if (riskScore >= 6) return 'text-orange-600 bg-orange-50 border-orange-200';
    if (riskScore >= 4) return 'text-yellow-600 bg-yellow-50 border-yellow-200';
    return 'text-green-600 bg-green-50 border-green-200';
  };

  const getPriorityColor = (priorityLevel) => {
    if (priorityLevel <= 2) return 'text-red-600 bg-red-100';
    if (priorityLevel <= 3) return 'text-orange-600 bg-orange-100';
    if (priorityLevel <= 4) return 'text-yellow-600 bg-yellow-100';
    return 'text-green-600 bg-green-100';
  };

  const getRiskIcon = (riskScore) => {
    if (riskScore >= 8) return <AlertTriangle className="h-5 w-5 text-red-600" />;
    if (riskScore >= 6) return <Zap className="h-5 w-5 text-orange-600" />;
    if (riskScore >= 4) return <Activity className="h-5 w-5 text-yellow-600" />;
    return <CheckCircle className="h-5 w-5 text-green-600" />;
  };

  const getPriorityText = (priorityLevel) => {
    if (priorityLevel <= 2) return 'CRITICAL';
    if (priorityLevel <= 3) return 'HIGH';
    if (priorityLevel <= 4) return 'MEDIUM';
    return 'LOW';
  };

  const unreadCount = notifications.filter(n => !n.read_at).length;
  const criticalCount = notifications.filter(n => (n.risk_score || 0) >= 8).length;
  const highRiskCount = notifications.filter(n => (n.risk_score || 0) >= 6 && (n.risk_score || 0) < 8).length;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                Doctor Dashboard
              </h1>
              <p className="text-gray-600 mt-2">Welcome back, Dr. {user?.last_name}</p>
            </div>
            
            {/* Filter Controls */}
            <div className="flex items-center space-x-4 bg-white rounded-xl p-4 shadow-lg border border-gray-100">
              <Filter className="w-5 h-5 text-gray-500" />
              <span className="text-sm font-medium text-gray-700">Sort by:</span>
              <div className="flex space-x-2">
                <button
                  onClick={() => handleSortChange('priority')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                    sortBy === 'priority'
                      ? 'bg-red-500 text-white shadow-md'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  <AlertTriangle className="w-4 h-4 inline mr-1" />
                  Priority
                </button>
                <button
                  onClick={() => handleSortChange('newest')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                    sortBy === 'newest'
                      ? 'bg-blue-500 text-white shadow-md'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  <Clock className="w-4 h-4 inline mr-1" />
                  Newest
                </button>
                <button
                  onClick={() => handleSortChange('oldest')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                    sortBy === 'oldest'
                      ? 'bg-green-500 text-white shadow-md'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  <RefreshCw className="w-4 h-4 inline mr-1" />
                  Oldest
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Enhanced Stats Overview */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <div className="bg-white rounded-xl shadow-lg p-6 border border-gray-100 hover:shadow-xl transition-all duration-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">Total Cases</p>
                <p className="text-3xl font-bold text-gray-900">{notifications.length}</p>
              </div>
              <div className="p-3 bg-blue-100 rounded-lg">
                <Users className="h-6 w-6 text-blue-600" />
              </div>
            </div>
            <div className="mt-4 flex items-center text-sm">
              <span className="text-green-600 font-medium">{unreadCount} new</span>
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-lg p-6 border border-gray-100 hover:shadow-xl transition-all duration-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">Critical Risk</p>
                <p className="text-3xl font-bold text-red-600">{criticalCount}</p>
              </div>
              <div className="p-3 bg-red-100 rounded-lg">
                <AlertTriangle className="h-6 w-6 text-red-600" />
              </div>
            </div>
            <div className="mt-4 flex items-center text-sm">
              <span className="text-red-600 font-medium">Immediate attention</span>
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-lg p-6 border border-gray-100 hover:shadow-xl transition-all duration-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">High Risk</p>
                <p className="text-3xl font-bold text-orange-600">{highRiskCount}</p>
              </div>
              <div className="p-3 bg-orange-100 rounded-lg">
                <Zap className="h-6 w-6 text-orange-600" />
              </div>
            </div>
            <div className="mt-4 flex items-center text-sm">
              <span className="text-orange-600 font-medium">Monitor closely</span>
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-lg p-6 border border-gray-100 hover:shadow-xl transition-all duration-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">Response Rate</p>
                <p className="text-3xl font-bold text-green-600">
                  {notifications.length > 0 ? Math.round(((notifications.length - unreadCount) / notifications.length) * 100) : 0}%
                </p>
              </div>
              <div className="p-3 bg-green-100 rounded-lg">
                <TrendingUp className="h-6 w-6 text-green-600" />
              </div>
            </div>
            <div className="mt-4 flex items-center text-sm">
              <span className="text-green-600 font-medium">Cases reviewed</span>
            </div>
          </div>
        </div>

        {/* Patient Cases - Ranked by Risk */}
        <div className="bg-white rounded-xl shadow-lg border border-gray-100 overflow-hidden">
          <div className="bg-gradient-to-r from-gray-50 to-gray-100 px-6 py-4 border-b border-gray-200">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-gradient-to-r from-red-500 to-orange-500 rounded-lg">
                  <Heart className="h-5 w-5 text-white" />
                </div>
                <h2 className="text-xl font-semibold text-gray-900">
                  Patient Cases (Ranked by Risk)
                </h2>
              </div>
              <div className="flex items-center space-x-2 text-sm text-gray-600">
                <ArrowUp className="h-4 w-4 text-red-500" />
                <span>Highest Risk</span>
                <ArrowDown className="h-4 w-4 text-green-500" />
                <span>Lowest Risk</span>
              </div>
            </div>
          </div>

          <div className="p-6">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
                <span className="ml-3 text-gray-600">Loading cases...</span>
              </div>
            ) : notifications.length === 0 ? (
              <div className="text-center py-12">
                <div className="p-4 bg-green-100 rounded-full w-16 h-16 mx-auto mb-4 flex items-center justify-center">
                  <CheckCircle className="h-8 w-8 text-green-600" />
                </div>
                <h3 className="text-lg font-medium text-gray-900 mb-2">All Clear!</h3>
                <p className="text-gray-600">No active cases. You're all caught up!</p>
              </div>
            ) : (
              <div className="space-y-4">
                {notifications.map((notification, index) => (
                  <div
                    key={notification.id}
                    className={`border-2 rounded-xl p-6 transition-all duration-300 hover:shadow-lg ${
                      notification.read_at 
                        ? 'border-gray-200 bg-gray-50' 
                        : 'border-blue-200 bg-blue-50 shadow-md'
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        {/* Risk and Priority Indicators */}
                        <div className="flex items-center space-x-3 mb-4">
                          <div className="flex items-center space-x-2">
                            <span className="text-lg font-bold text-gray-400">#{index + 1}</span>
                            {getRiskIcon(notification.risk_score || 0)}
                          </div>
                          
                          <div className={`px-3 py-1 rounded-full text-sm font-bold border ${getRiskColor(notification.risk_score || 0)}`}>
                            Risk: {notification.risk_score || 0}/10
                          </div>
                          
                          <div className={`px-3 py-1 rounded-full text-sm font-bold ${getPriorityColor(notification.priority_level || 5)}`}>
                            {getPriorityText(notification.priority_level || 5)}
                          </div>
                          
                          {!notification.read_at && (
                            <div className="px-3 py-1 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-xs font-bold rounded-full animate-pulse">
                              NEW
                            </div>
                          )}
                        </div>

                        {/* Patient Information */}
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-4">
                          <div>
                            <h3 className="text-lg font-semibold text-gray-900 mb-3">Patient Assessment</h3>
                            <div className="space-y-3">
                              <div className="bg-white p-3 rounded-lg border border-gray-200">
                                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Chief Complaint</p>
                                <p className="text-sm text-gray-800">{notification.chief_complaint || 'Not specified'}</p>
                              </div>
                              
                              <div className="bg-white p-3 rounded-lg border border-gray-200">
                                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Vital Signs</p>
                                <p className="text-sm text-gray-800">{notification.vital_signs || 'Not recorded'}</p>
                              </div>
                              
                              <div className="bg-white p-3 rounded-lg border border-gray-200">
                                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Symptoms</p>
                                <p className="text-sm text-gray-800">{notification.symptoms || 'Not specified'}</p>
                              </div>
                              
                              <div className="bg-white p-3 rounded-lg border border-gray-200">
                                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">EMT</p>
                                <p className="text-sm text-gray-800">{notification.emt_first_name} {notification.emt_last_name}</p>
                              </div>
                            </div>
                          </div>

                          <div>
                            <h3 className="text-lg font-semibold text-gray-900 mb-3">Medical Analysis</h3>
                            {notification.llm_summary ? (
                              <div className="bg-white p-4 rounded-lg border border-gray-200 shadow-sm">
                                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Clinical Summary</p>
                                <p className="text-sm text-gray-800 leading-relaxed">
                                  {notification.llm_summary}
                                </p>
                              </div>
                            ) : (
                              <p className="text-sm text-gray-500 italic">No medical analysis available</p>
                            )}
                          </div>
                        </div>

                        {/* Critical Information */}
                        {notification.critical_info && (
                          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                            <div className="flex items-center space-x-2 mb-2">
                              <AlertTriangle className="h-4 w-4 text-red-600" />
                              <span className="font-semibold text-red-800">Critical Information</span>
                            </div>
                            <p className="text-sm text-red-700">{notification.critical_info}</p>
                          </div>
                        )}

                        {/* Recommended Actions */}
                        {notification.recommended_actions && (
                          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
                            <div className="flex items-center space-x-2 mb-2">
                              <Star className="h-4 w-4 text-blue-600" />
                              <span className="font-semibold text-blue-800">Recommended Actions</span>
                            </div>
                            <p className="text-sm text-blue-700">{notification.recommended_actions}</p>
                          </div>
                        )}

                        {/* Timestamp */}
                        <div className="flex items-center space-x-1 text-xs text-gray-500">
                          <Clock className="h-3 w-3" />
                          <span>{new Date(notification.recording_time).toLocaleString()}</span>
                        </div>
                      </div>

                      {/* Action Buttons */}
                      <div className="flex flex-col space-y-3 ml-6">
                        <button
                          onClick={() => viewRecording(notification.recording_id)}
                          className="flex items-center space-x-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-sm font-medium rounded-lg hover:from-blue-700 hover:to-indigo-700 transition-all duration-200 shadow-md hover:shadow-lg"
                        >
                          <Eye className="h-4 w-4" />
                          <span>View Details</span>
                        </button>

                        <button
                          onClick={() => viewRecording(notification.recording_id)}
                          className="flex items-center space-x-2 px-4 py-2 bg-white border border-blue-300 text-blue-700 text-sm font-medium rounded-lg hover:bg-blue-50 transition-colors shadow-md"
                        >
                          <MessageSquare className="h-4 w-4" />
                          <span>Message EMT</span>
                        </button>
                        
                        {!notification.read_at && (
                          <button
                            onClick={() => markAsRead(notification.id)}
                            className="px-4 py-2 bg-gray-600 text-white text-sm font-medium rounded-lg hover:bg-gray-700 transition-colors shadow-md hover:shadow-lg"
                          >
                            Mark as Read
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Enhanced Recording Detail Modal */}
        {showRecordingModal && selectedRecording && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
            <div className="bg-white rounded-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto shadow-2xl">
              <div className="p-6">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center space-x-3">
                    <div className="p-2 bg-gradient-to-r from-blue-600 to-indigo-600 rounded-lg">
                      <Heart className="h-6 w-6 text-white" />
                    </div>
                    <h3 className="text-2xl font-bold text-gray-900">
                      Patient Case Details
                    </h3>
                  </div>
                  <button
                    onClick={() => setShowRecordingModal(false)}
                    className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
                  >
                    ✕
                  </button>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Left Column - Patient Information */}
                  <div className="space-y-6">
                    <div className="bg-gray-50 rounded-lg p-4">
                      <h4 className="font-semibold text-gray-900 mb-3">Patient Information</h4>
                      <p className="text-gray-700">
                        {selectedRecording.patient_info || 'No information provided'}
                      </p>
                    </div>

                    <div className="bg-blue-50 rounded-lg p-4 border border-blue-200">
                      <h4 className="font-semibold text-blue-900 mb-2">Chief Complaint</h4>
                      <p className="text-blue-800">{selectedRecording.chief_complaint || 'Not specified'}</p>
                    </div>

                    <div className="bg-green-50 rounded-lg p-4 border border-green-200">
                      <h4 className="font-semibold text-green-900 mb-2">Vital Signs</h4>
                      <p className="text-green-800">{selectedRecording.vital_signs || 'Not recorded'}</p>
                    </div>

                    <div className="bg-yellow-50 rounded-lg p-4 border border-yellow-200">
                      <h4 className="font-semibold text-yellow-900 mb-2">Symptoms</h4>
                      <p className="text-yellow-800">{selectedRecording.symptoms || 'Not specified'}</p>
                    </div>
                  </div>

                  {/* Right Column - Medical Analysis */}
                  <div className="space-y-6">
                    {selectedRecording.transcription && (
                      <div className="bg-gray-50 rounded-lg p-4">
                        <h4 className="font-semibold text-gray-900 mb-3">EMT Conversation</h4>
                        <p className="text-gray-700 text-sm leading-relaxed">
                          {selectedRecording.transcription}
                        </p>
                      </div>
                    )}

                    {selectedRecording.llm_summary && (
                      <div className="bg-blue-50 rounded-lg p-4 border border-blue-200">
                        <h4 className="font-semibold text-blue-900 mb-3">Clinical Summary</h4>
                        <p className="text-blue-800 leading-relaxed">
                          {selectedRecording.llm_summary}
                        </p>
                      </div>
                    )}

                    {selectedRecording.critical_info && (
                      <div className="bg-red-50 rounded-lg p-4 border border-red-200">
                        <h4 className="font-semibold text-red-900 mb-2">Critical Information</h4>
                        <p className="text-red-800">{selectedRecording.critical_info}</p>
                      </div>
                    )}

                    {selectedRecording.recommended_actions && (
                      <div className="bg-green-50 rounded-lg p-4 border border-green-200">
                        <h4 className="font-semibold text-green-900 mb-2">Recommended Actions</h4>
                        <p className="text-green-800">{selectedRecording.recommended_actions}</p>
                      </div>
                    )}
                  </div>
                </div>

                {/* Messages with the EMT */}
                <div className="mt-8">
                  <h4 className="font-semibold text-gray-900 mb-3">
                    Messages with EMT {selectedRecording.emt_first_name} {selectedRecording.emt_last_name}
                  </h4>
                  <CaseChat recordingId={selectedRecording.id} />
                </div>

                <div className="flex space-x-4 pt-6">
                  <button
                    onClick={() => setShowRecordingModal(false)}
                    className="px-6 py-3 border border-gray-300 text-gray-700 font-medium rounded-lg hover:bg-gray-50 transition-colors"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default DoctorDashboard; 