import React, { useState, useEffect, useRef } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Mic, Upload, Square, List, Clock, TestTube, MessageSquare } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import CaseChat from './CaseChat';

const EMTDashboard = () => {
  const { user } = useAuth();
  const [isRecording, setIsRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState(null);
  const [recordings, setRecordings] = useState([]);
  const [loading, setLoading] = useState(false);
  const [patientInfo, setPatientInfo] = useState('');
  const [showUploadForm, setShowUploadForm] = useState(false);
  const [openChatId, setOpenChatId] = useState(null);
  
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  useEffect(() => {
    fetchRecordings();
    checkBrowserSupport();
  }, []);

  const fetchRecordings = async () => {
    try {
      const response = await axios.get('/api/recordings/my-recordings');
      setRecordings(response.data);
    } catch (error) {
      console.error('Error fetching recordings:', error);
      toast.error('Failed to fetch recordings');
    }
  };

  // Check browser compatibility
  const checkBrowserSupport = () => {
    const support = {
      getUserMedia: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
      MediaRecorder: !!window.MediaRecorder,
      webm: MediaRecorder.isTypeSupported('audio/webm'),
      mp4: MediaRecorder.isTypeSupported('audio/mp4'),
      opus: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    };
    
    console.log('Browser support:', support);
    
    if (!support.getUserMedia) {
      toast.error('This browser does not support audio recording');
    }
    
    return support;
  };

  // Test microphone access
  const testMicrophone = async () => {
    try {
      console.log('Testing microphone access...');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      console.log('✅ Microphone access successful');
      stream.getTracks().forEach(track => track.stop());
      toast.success('Microphone test successful!');
      return true;
    } catch (error) {
      console.error('❌ Microphone test failed:', error);
      toast.error('Microphone test failed: ' + error.message);
      return false;
    }
  };

  const startRecording = async () => {
    try {
      console.log('Starting recording...');
      
      // Check if getUserMedia is supported
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('getUserMedia not supported');
      }

      // Request microphone access with better error handling
      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 44100
        } 
      });
      
      console.log('Microphone access granted');
      
      // Check if MediaRecorder is supported
      if (!window.MediaRecorder) {
        throw new Error('MediaRecorder not supported');
      }

      // Create MediaRecorder with better options
      const options = {
        mimeType: 'audio/webm;codecs=opus'
      };
      
      // Fallback options for different browsers
      if (!MediaRecorder.isTypeSupported(options.mimeType)) {
        options.mimeType = 'audio/webm';
        if (!MediaRecorder.isTypeSupported(options.mimeType)) {
          options.mimeType = 'audio/mp4';
          if (!MediaRecorder.isTypeSupported(options.mimeType)) {
            delete options.mimeType; // Use default
          }
        }
      }
      
      console.log('Using mimeType:', options.mimeType || 'default');
      
      mediaRecorderRef.current = new MediaRecorder(stream, options);
      audioChunksRef.current = [];

      mediaRecorderRef.current.ondataavailable = (event) => {
        console.log('Data available:', event.data.size, 'bytes');
        audioChunksRef.current.push(event.data);
      };

      mediaRecorderRef.current.onstop = () => {
        console.log('Recording stopped, creating blob...');
        const audioBlob = new Blob(audioChunksRef.current, { 
          type: options.mimeType || 'audio/webm' 
        });
        console.log('Audio blob created:', audioBlob.size, 'bytes');
        setAudioBlob(audioBlob);
        stream.getTracks().forEach(track => track.stop());
      };

      mediaRecorderRef.current.onerror = (event) => {
        console.error('MediaRecorder error:', event.error);
        toast.error('Recording error: ' + event.error.message);
        setIsRecording(false);
      };

      mediaRecorderRef.current.start(1000); // Collect data every second
      setIsRecording(true);
      toast.success('Recording started');
      
    } catch (error) {
      console.error('Error starting recording:', error);
      
      if (error.name === 'NotAllowedError') {
        toast.error('Microphone access denied. Please allow microphone permissions and refresh the page.');
      } else if (error.name === 'NotFoundError') {
        toast.error('No microphone found. Please connect a microphone.');
      } else if (error.name === 'NotSupportedError') {
        toast.error('Audio recording not supported in this browser.');
      } else {
        toast.error('Failed to start recording: ' + error.message);
      }
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      toast.success('Recording stopped');
    }
  };

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!audioBlob || !patientInfo.trim()) {
      toast.error('Please provide both audio recording and patient information');
      return;
    }

    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'recording.webm');
      formData.append('patient_info', patientInfo);

      await axios.post('/api/recordings/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      toast.success('Recording uploaded successfully! AI is analyzing...');
      setAudioBlob(null);
      setPatientInfo('');
      setShowUploadForm(false);
      fetchRecordings();
    } catch (error) {
      console.error('Upload error:', error);
      toast.error('Failed to upload recording');
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'pending': return 'text-yellow-600 bg-yellow-100';
      case 'processing': return 'text-blue-600 bg-blue-100';
      case 'completed': return 'text-green-600 bg-green-100';
      case 'notified': return 'text-purple-600 bg-purple-100';
      case 'error': return 'text-red-600 bg-red-100';
      default: return 'text-gray-600 bg-gray-100';
    }
  };

  const getUrgencyColor = (urgency) => {
    switch (urgency) {
      case 'critical': return 'text-red-600 bg-red-100';
      case 'high': return 'text-orange-600 bg-orange-100';
      case 'medium': return 'text-yellow-600 bg-yellow-100';
      case 'low': return 'text-green-600 bg-green-100';
      default: return 'text-gray-600 bg-gray-100';
    }
  };

  const getRiskColor = (riskScore) => {
    if (riskScore >= 80) return 'text-red-600 bg-red-100';
    if (riskScore >= 60) return 'text-orange-600 bg-orange-100';
    if (riskScore >= 40) return 'text-yellow-600 bg-yellow-100';
    return 'text-green-600 bg-green-100';
  };

  const getPriorityColor = (priority) => {
    switch (priority) {
      case 1: return 'text-red-600 bg-red-100';
      case 2: return 'text-orange-600 bg-orange-100';
      case 3: return 'text-yellow-600 bg-yellow-100';
      case 4: return 'text-blue-600 bg-blue-100';
      case 5: return 'text-green-600 bg-green-100';
      default: return 'text-gray-600 bg-gray-100';
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">
          Welcome back, {user?.first_name}! 🏥
        </h1>
        <p className="mt-2 text-gray-600">
          Record patient conversations and get AI-powered risk assessment for doctors
        </p>
      </div>

      {/* Recording Section */}
      <div className="bg-white rounded-lg shadow-md p-6 mb-8">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">
          🎤 Record Patient Conversation
        </h2>
        
        <div className="flex items-center space-x-4 mb-6">
          <button
            onClick={testMicrophone}
            className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            <TestTube className="h-4 w-4" />
            <span>Test Mic</span>
          </button>
          
          <button
            onClick={startRecording}
            disabled={isRecording}
            className="flex items-center space-x-2 px-6 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Mic className="h-5 w-5" />
            <span>Start Recording</span>
          </button>
          
          <button
            onClick={stopRecording}
            disabled={!isRecording}
            className="flex items-center space-x-2 px-6 py-3 bg-gray-600 text-white rounded-lg hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Square className="h-5 w-5" />
            <span>Stop Recording</span>
          </button>
        </div>

        {isRecording && (
          <div className="flex items-center space-x-2 text-red-600 animate-pulse-slow">
            <Mic className="h-5 w-5" />
            <span>Recording in progress...</span>
          </div>
        )}

        {audioBlob && (
          <div className="mt-4 p-4 bg-green-50 rounded-lg border border-green-200">
            <h3 className="font-medium text-green-800 mb-2">✅ Recording Complete!</h3>
            <p className="text-sm text-green-700 mb-2">
              Audio size: {(audioBlob.size / 1024).toFixed(1)} KB
            </p>
            <button
              onClick={() => setShowUploadForm(true)}
              className="flex items-center space-x-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
            >
              <Upload className="h-4 w-4" />
              <span>Upload & Analyze</span>
            </button>
          </div>
        )}
      </div>

      {/* Upload Form */}
      {showUploadForm && (
        <div className="bg-white rounded-lg shadow-md p-6 mb-8">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">
            📋 Patient Information & AI Analysis
          </h3>
          
          <form onSubmit={handleUpload}>
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Patient Information
              </label>
              <textarea
                value={patientInfo}
                onChange={(e) => setPatientInfo(e.target.value)}
                placeholder="Enter patient details, symptoms, vital signs, location, etc..."
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-red-500"
                rows="4"
                required
              />
            </div>
            
            <div className="flex space-x-4">
              <button
                type="submit"
                disabled={loading}
                className="flex items-center space-x-2 px-6 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <Upload className="h-4 w-4" />
                <span>{loading ? 'Analyzing...' : 'Upload & Analyze'}</span>
              </button>
              
              <button
                type="button"
                onClick={() => setShowUploadForm(false)}
                className="px-6 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Recordings List */}
      <div className="bg-white rounded-lg shadow-md p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-gray-900">
            📝 My Recordings
          </h2>
          <button
            onClick={fetchRecordings}
            className="text-red-600 hover:text-red-700 transition-colors"
          >
            Refresh
          </button>
        </div>

        {recordings.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            <List className="h-12 w-12 mx-auto mb-4 text-gray-400" />
            <p>No recordings yet. Start recording your first patient conversation!</p>
          </div>
        ) : (
          <div className="space-y-4">
            {recordings.map((recording) => (
              <div
                key={recording.id}
                className="border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center space-x-3 mb-2">
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(recording.status)}`}>
                        {recording.status}
                      </span>
                      {recording.urgency_level && (
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${getUrgencyColor(recording.urgency_level)}`}>
                          {recording.urgency_level}
                        </span>
                      )}
                      {recording.risk_score && (
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${getRiskColor(recording.risk_score)}`}>
                          Risk: {recording.risk_score}
                        </span>
                      )}
                      {recording.priority_level && (
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${getPriorityColor(recording.priority_level)}`}>
                          Priority: {recording.priority_level}
                        </span>
                      )}
                    </div>
                    
                    <p className="text-sm text-gray-600 mb-2">
                      <strong>Patient Info:</strong> {recording.patient_info || 'No information provided'}
                    </p>
                    
                    {recording.chief_complaint && (
                      <p className="text-sm text-gray-600 mb-1">
                        <strong>Chief Complaint:</strong> {recording.chief_complaint}
                      </p>
                    )}
                    
                    {recording.vital_signs && (
                      <p className="text-sm text-gray-600 mb-1">
                        <strong>Vital Signs:</strong> {recording.vital_signs}
                      </p>
                    )}
                    
                    {recording.symptoms && (
                      <p className="text-sm text-gray-600 mb-1">
                        <strong>Symptoms:</strong> {recording.symptoms}
                      </p>
                    )}
                    
                    {recording.llm_summary && (
                      <div className="mb-2">
                        <p className="text-sm text-gray-600">
                          <strong>AI Analysis:</strong>
                        </p>
                        <p className="text-sm text-gray-800 bg-gray-50 p-2 rounded mt-1">
                          {recording.llm_summary}
                        </p>
                      </div>
                    )}
                    
                    {recording.recommended_actions && (
                      <div className="mb-2">
                        <p className="text-sm text-gray-600">
                          <strong>Recommended Actions:</strong>
                        </p>
                        <p className="text-sm text-gray-800 bg-blue-50 p-2 rounded mt-1">
                          {recording.recommended_actions}
                        </p>
                      </div>
                    )}
                    
                    <div className="flex items-center space-x-4 text-xs text-gray-500">
                      <div className="flex items-center space-x-1">
                        <Clock className="h-3 w-3" />
                        <span>{new Date(recording.created_at).toLocaleString()}</span>
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={() => setOpenChatId(openChatId === recording.id ? null : recording.id)}
                    className="ml-4 flex items-center space-x-1 px-3 py-2 text-sm font-medium text-blue-700 border border-blue-300 rounded-lg hover:bg-blue-50 transition-colors"
                  >
                    <MessageSquare className="h-4 w-4" />
                    <span>{openChatId === recording.id ? 'Hide messages' : 'Hospital messages'}</span>
                  </button>
                </div>

                {openChatId === recording.id && (
                  <CaseChat recordingId={recording.id} className="mt-4" />
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default EMTDashboard;
