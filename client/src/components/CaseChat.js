import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Send, RefreshCw, AlertTriangle, MessageSquare, Wifi, WifiOff } from 'lucide-react';
import axios from 'axios';

const RECONNECT_DELAY_MS = 3000;
// The server will keep refusing these, so retrying the stream is pointless.
const FATAL_STREAM_STATUSES = [400, 401, 403, 404];
const MAX_LENGTH = 2000;

const newClientId = () =>
  (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

// Merge server messages into the list: dedupe by id, drop the pending copy with the same client_id.
const mergeMessages = (current, incoming) => {
  const byId = new Map(current.filter(m => m.id).map(m => [m.id, m]));
  incoming.forEach(m => byId.set(m.id, m));
  const confirmedClientIds = new Set([...byId.values()].map(m => m.client_id).filter(Boolean));
  const pending = current.filter(m => !m.id && !confirmedClientIds.has(m.client_id));
  return [...[...byId.values()].sort((a, b) => a.id - b.id), ...pending];
};

const formatTime = (iso) => {
  const date = new Date(iso);
  const sameDay = date.toDateString() === new Date().toDateString();
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? time : `${date.toLocaleDateString()} ${time}`;
};

const senderLabel = (message) => {
  const name = `${message.sender_first_name || ''} ${message.sender_last_name || ''}`.trim();
  return message.sender_role === 'doctor' ? `Dr. ${message.sender_last_name || name}` : `EMT ${name}`;
};

// Reads a server-sent event stream with fetch so the Authorization header can be sent.
const readEventStream = async (url, headers, signal, onOpen, onMessage) => {
  const response = await fetch(url, { headers, signal, cache: 'no-store' });
  if (!response.ok || !response.body) {
    const error = new Error(`Stream failed with ${response.status}`);
    error.status = response.status;
    throw error;
  }
  onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame
        .split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())
        .join('\n');
      if (data) onMessage(JSON.parse(data));
    }
  }
};

const CaseChat = ({ recordingId, className = '' }) => {
  const { user } = useAuth();
  const [messages, setMessages] = useState([]);
  const [status, setStatus] = useState('loading'); // loading | ready | error
  const [loadError, setLoadError] = useState('');
  const [connection, setConnection] = useState('connecting'); // connecting | live | reconnecting | offline
  const [draft, setDraft] = useState('');
  const lastIdRef = useRef(0);
  const scrollRef = useRef(null);

  const applyMessages = useCallback((incoming) => {
    // Never accept a message for a different case, whatever the server sends.
    const forThisCase = incoming.filter(m => m.recording_id === recordingId);
    if (forThisCase.length === 0) return;
    lastIdRef.current = Math.max(lastIdRef.current, ...forThisCase.map(m => m.id));
    setMessages(current => mergeMessages(current, forThisCase));
  }, [recordingId]);

  // Bumped by the Retry button to reload history.
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    setLoadError('');
    setMessages([]);
    lastIdRef.current = 0;

    axios.get(`/api/recordings/${recordingId}/messages`)
      .then(response => {
        if (cancelled) return;
        applyMessages(response.data.messages);
        setConnection('connecting');
        setStatus('ready');
      })
      .catch(error => {
        if (cancelled) return;
        console.error('Error loading messages:', error);
        setLoadError(error.response?.data?.error || 'Could not load messages');
        setStatus('error');
      });

    return () => { cancelled = true; };
  }, [recordingId, applyMessages, loadAttempt]);

  // Live updates once history has loaded; reconnects from the last seen id after any drop.
  useEffect(() => {
    if (status !== 'ready') return undefined;
    const controller = new AbortController();
    let timer = null;
    let stopped = false;

    const connect = async () => {
      try {
        const headers = { Authorization: axios.defaults.headers.common['Authorization'] };
        await readEventStream(
          `/api/recordings/${recordingId}/messages/stream?after_id=${lastIdRef.current}`,
          headers,
          controller.signal,
          () => setConnection('live'),
          (message) => applyMessages([message])
        );
      } catch (error) {
        if (stopped) return;
        console.warn('Message stream dropped:', error);
        if (FATAL_STREAM_STATUSES.includes(error.status)) {
          setConnection('offline');
          return;
        }
      }
      if (stopped) return;
      setConnection('reconnecting');
      timer = setTimeout(connect, RECONNECT_DELAY_MS);
    };

    connect();

    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [status, recordingId, applyMessages]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const postMessage = async (pending) => {
    setMessages(current => current.map(m => (m.client_id === pending.client_id ? { ...m, failed: false } : m)));
    try {
      const response = await axios.post(`/api/recordings/${recordingId}/messages`, {
        body: pending.body,
        client_id: pending.client_id,
      });
      applyMessages([response.data]);
    } catch (error) {
      console.error('Error sending message:', error);
      setMessages(current => current.map(m => (m.client_id === pending.client_id ? { ...m, failed: true } : m)));
    }
  };

  const handleSend = (e) => {
    e.preventDefault();
    const body = draft.trim();
    if (!body || body.length > MAX_LENGTH) return;
    const pending = {
      client_id: newClientId(),
      recording_id: recordingId,
      sender_id: user?.id,
      sender_role: user?.role,
      sender_first_name: user?.first_name,
      sender_last_name: user?.last_name,
      body,
      created_at: new Date().toISOString(),
    };
    setMessages(current => [...current, pending]);
    setDraft('');
    postMessage(pending);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) handleSend(e);
  };

  const counterpart = user?.role === 'doctor' ? 'the EMT crew' : 'the receiving hospital team';

  return (
    <div className={`flex flex-col border border-gray-200 rounded-lg bg-white ${className}`}>
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50 rounded-t-lg">
        <div className="flex items-center space-x-2">
          <MessageSquare className="h-4 w-4 text-blue-600" />
          <span className="text-sm font-semibold text-gray-800">Case messages</span>
        </div>
        {status === 'ready' && (
          connection === 'live' ? (
            <span className="flex items-center space-x-1 text-xs text-green-600">
              <Wifi className="h-3 w-3" /><span>Live</span>
            </span>
          ) : (
            <span className="flex items-center space-x-1 text-xs text-amber-600">
              <WifiOff className="h-3 w-3" />
              <span>
                {{ connecting: 'Connecting…', reconnecting: 'Reconnecting…', offline: 'Offline. Reopen to retry' }[connection]}
              </span>
            </span>
          )
        )}
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[12rem] max-h-80">
        {status === 'loading' && (
          <div className="flex items-center justify-center py-8 text-sm text-gray-500">
            <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-600 mr-2"></div>
            Loading messages…
          </div>
        )}

        {status === 'error' && (
          <div className="flex flex-col items-center justify-center py-8 text-sm text-red-600">
            <AlertTriangle className="h-5 w-5 mb-2" />
            <p className="mb-3">{loadError}</p>
            <button
              onClick={() => setLoadAttempt(n => n + 1)}
              className="flex items-center space-x-1 px-3 py-1 border border-red-300 rounded-md hover:bg-red-50"
            >
              <RefreshCw className="h-3 w-3" /><span>Retry</span>
            </button>
          </div>
        )}

        {status === 'ready' && messages.length === 0 && (
          <div className="text-center py-8 text-sm text-gray-500">
            No messages yet. Start the conversation with {counterpart}.
          </div>
        )}

        {status === 'ready' && messages.map(message => {
          const mine = message.sender_id === user?.id;
          return (
            <div key={message.id || message.client_id} className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
              <div className="max-w-[80%]">
                <div className={`text-xs mb-1 ${mine ? 'text-right' : ''} text-gray-500`}>
                  <span className="font-medium text-gray-700">{mine ? 'You' : senderLabel(message)}</span>
                  {' · '}
                  <span className="uppercase">{message.sender_role === 'doctor' ? 'Hospital' : 'EMT'}</span>
                  {' · '}
                  <span>{formatTime(message.created_at)}</span>
                </div>
                <div
                  className={`px-3 py-2 rounded-lg text-sm whitespace-pre-wrap break-words ${
                    mine ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-900'
                  } ${!message.id ? 'opacity-70' : ''}`}
                >
                  {message.body}
                </div>
                {!message.id && (
                  <div className="text-xs mt-1 text-right">
                    {message.failed ? (
                      <button onClick={() => postMessage(message)} className="text-red-600 hover:underline">
                        Not sent. Tap to retry
                      </button>
                    ) : (
                      <span className="text-gray-400">Sending…</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <form onSubmit={handleSend} className="border-t border-gray-200 p-3 flex items-end space-x-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={`Message ${counterpart}…`}
          rows="2"
          maxLength={MAX_LENGTH}
          disabled={status !== 'ready'}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
        />
        <button
          type="submit"
          disabled={status !== 'ready' || !draft.trim()}
          className="flex items-center space-x-1 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Send className="h-4 w-4" /><span>Send</span>
        </button>
      </form>
    </div>
  );
};

export default CaseChat;
