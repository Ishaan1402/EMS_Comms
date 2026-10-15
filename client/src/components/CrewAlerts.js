import React, { useEffect, useRef, useState } from 'react';
import { Bell, BellOff, BellRing, CheckCircle } from 'lucide-react';
import axios from 'axios';
import toast from 'react-hot-toast';
import { Button, CrewChip, StatusText } from './ui';

// Crew alerts (case_alerts.py): the crew's call, never the AI's. Shown in ink, apart from the
// status colours the AI's labels use, so the two never read as the same thing.
export const ALERTS = [
  { value: 'stroke', label: 'Stroke', keyTime: 'Last known well', short: 'LKW' },
  { value: 'stemi', label: 'STEMI', keyTime: 'Symptom onset', short: 'onset' },
  { value: 'trauma', label: 'Trauma', keyTime: 'Time of injury', short: 'injury' },
  { value: 'sepsis', label: 'Sepsis', keyTime: 'Symptom onset', short: 'onset' },
];
const byValue = Object.fromEntries(ALERTS.map((a) => [a.value, a]));

export const clockTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

// "LKW 14:10 (52 min ago)", or "LKW not stated": a missing key time is shown, never guessed.
export const keyTimeText = (alert, now) => {
  const { short } = byValue[alert.alert] || { short: 'key time' };
  if (!alert.key_time) return `${short} not stated`;
  const minutes = Math.max(0, Math.round((now - Date.parse(alert.key_time)) / 60000));
  return `${short} ${clockTime(alert.key_time)} (${minutes} min ago)`;
};

const newClientId = () => (window.crypto?.randomUUID
  ? window.crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);

// "14:10" typed on the crew's device -> the latest such time that isn't in the future.
export const keyTimeFromClock = (value, now = Date.now()) => {
  if (!value) return null;
  const [hours, minutes] = value.split(':').map(Number);
  const time = new Date(now);
  time.setHours(hours, minutes, 0, 0);
  if (time.getTime() > now + 5 * 60000) time.setDate(time.getDate() - 1);
  return time.toISOString();
};

/** Queue row: one chip per active crew alert, with its key time counting up, and whether it still needs acknowledging. */
export const CrewAlertBadges = ({ liveCase, serverNow }) => {
  const alerts = liveCase.crew_alerts;
  if (!alerts?.active?.length && !alerts?.unacknowledged) return null;
  return (
    <>
      {alerts.active.map((alert) => (
        <CrewChip key={alert.alert}>Crew alert: {alert.label} · {keyTimeText(alert, serverNow)}</CrewChip>
      ))}
      {alerts.unacknowledged && (
        <StatusText tone="attention">
          {alerts.active.length ? 'Alert not yet acknowledged' : 'Alert cancelled · not yet acknowledged'}
        </StatusText>
      )}
    </>
  );
};

/** Hospital case view: the crew's alerts, who acknowledged them, and the button to do it. */
export const CrewAlertPanel = ({ liveCase, serverNow, onChanged }) => {
  const alerts = liveCase.crew_alerts;
  const [sending, setSending] = useState(false);
  if (!alerts?.latest_change_id) return null;
  const acknowledge = async () => {
    setSending(true);
    try {
      // The change on screen, so one that arrives meanwhile stays unacknowledged.
      const { data } = await axios.post(`/api/cases/${liveCase.id}/alerts/acknowledge`,
        { through_alert_id: alerts.latest_change_id });
      onChanged?.(data);
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not acknowledge the alert');
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="mb-4 rounded-lg border border-gray-200 p-4 text-sm">
      <p className="flex items-center gap-1.5 font-semibold text-gray-900">
        <BellRing className="h-4 w-4" /> Crew alerts
      </p>
      {alerts.active.length === 0 && <p className="text-gray-600">No active alert (the crew cancelled it).</p>}
      <ul>
        {alerts.active.map((alert) => (
          <li key={alert.alert} className="mt-1 text-gray-900">
            <span className="font-semibold">{alert.label}</span> · {alert.key_time_label}:{' '}
            {alert.key_time ? `${clockTime(alert.key_time)} (${Math.max(0, Math.round((serverNow - Date.parse(alert.key_time)) / 60000))} min ago)`
              : <span className="text-amber-700">not stated</span>}
            <span className="text-xs text-gray-500"> · called by {alert.author_name} at {clockTime(alert.created_at)}</span>
          </li>
        ))}
      </ul>
      {alerts.unacknowledged ? (
        <Button variant="ink" onClick={acknowledge} disabled={sending} className="mt-3">
          <CheckCircle className="h-4 w-4" /> Acknowledge crew alert
        </Button>
      ) : (
        <p className="mt-2 text-gray-600">
          Alert acknowledged by {alerts.acknowledgment.user_name} · {clockTime(alerts.acknowledgment.acknowledged_at)}
        </p>
      )}
    </div>
  );
};

const SOUND_KEY = 'crewAlertSound';

const chime = () => {
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return;
  const context = new Context();
  [880, 660].forEach((frequency, index) => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = frequency;
    gain.gain.value = 0.15;
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(context.currentTime + index * 0.25);
    oscillator.stop(context.currentTime + index * 0.25 + 0.2);
  });
  setTimeout(() => context.close(), 1000);
};

/**
 * Plays a short chime when a crew calls an alert, and only then: never for the AI's labels,
 * reports or acknowledgments, so the one sound on this screen always means a person's call.
 * Browsers only allow sound after a click, hence the switch (remembered on this browser).
 */
export const CrewAlertSound = ({ cases, play = chime }) => {
  const [enabled, setEnabled] = useState(() => {
    try { return localStorage.getItem(SOUND_KEY) === '1'; } catch { return false; }
  });
  const seen = useRef(new Map());
  useEffect(() => {
    let called = false;
    cases.forEach((c) => {
      const latest = c.crew_alerts?.latest_change_id || 0;
      const before = seen.current.get(c.id);
      seen.current.set(c.id, Math.max(before || 0, latest));
      if (!latest) return;
      // A case seen for the first time (page load) doesn't ring; a new call on it does.
      const isCall = c.crew_alerts.active.some((alert) => alert.id === latest);
      if (before !== undefined && latest > before && isCall && c.crew_alerts.unacknowledged) called = true;
    });
    if (called && enabled) play();
  }, [cases, enabled, play]);
  const toggle = () => setEnabled((on) => {
    try { localStorage.setItem(SOUND_KEY, on ? '0' : '1'); } catch { /* sound stays per session */ }
    return !on;
  });
  return (
    <button type="button" onClick={toggle} aria-pressed={enabled} aria-label="Sound for crew alerts"
      title={enabled ? 'Sound for crew alerts is on' : 'Play a sound when a crew calls an alert'}
      className={`rounded-md p-1.5 hover:bg-gray-100 ${enabled ? 'text-blue-600' : 'text-gray-500'}`}>
      {enabled ? <Bell className="h-4 w-4" /> : <BellOff className="h-4 w-4" />}
    </button>
  );
};

/** EMT screen: call, correct or cancel an alert on the open case, and see whether it was acknowledged. */
const CrewAlerts = ({ liveCase }) => {
  const [choice, setChoice] = useState(null);
  const [clock, setClock] = useState('');
  const [sending, setSending] = useState(false);
  // An id is reused only when the very same change is retried, so the server stores it once; a
  // different change gets a new id (reusing one would return the old change instead of doing this one).
  const pending = useRef(null);
  const alerts = liveCase.crew_alerts || { active: [] };
  const active = Object.fromEntries(alerts.active.map((a) => [a.alert, a]));

  const send = async (body, done) => {
    const key = JSON.stringify(body);
    if (pending.current?.key !== key) pending.current = { key, id: newClientId() };
    setSending(true);
    try {
      const { data } = await axios.post(`/api/cases/${liveCase.id}/alerts`, { ...body, client_id: pending.current.id });
      pending.current = null;
      if (data?.action !== body.action) {
        toast.error('The alert on record is different from what you sent. Check it and try again.');
        return;
      }
      done();
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not send the alert — try again');
    } finally {
      setSending(false);
    }
  };
  const call = (event) => {
    event.preventDefault();
    send({ alert: choice.value, action: 'called', key_time: keyTimeFromClock(clock) }, () => {
      toast.success(`${choice.label} alert sent to the hospital`);
      setChoice(null);
      setClock('');
    });
  };
  const cancel = (alert) => {
    if (!window.confirm(`Cancel the ${alert.label} alert?`)) return;
    send({ alert: alert.alert, action: 'cancelled' }, () => toast.success(`${alert.label} alert cancelled`));
  };

  return (
    <div className="mb-4 rounded-lg border border-gray-200 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 text-sm font-semibold text-gray-900"><BellRing className="h-4 w-4" /> Crew alert</span>
        {ALERTS.map((option) => (
          <button key={option.value} type="button" disabled={sending}
            onClick={() => { setChoice(option); setClock(''); }}
            className={`rounded-lg border px-3 py-1 text-sm font-medium ${active[option.value] ? 'border-slate-900 bg-slate-900 text-white' : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'}`}>
            {option.label}{active[option.value] && ' · called'}
          </button>
        ))}
      </div>
      {choice && (
        <form onSubmit={call} className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-800">
          <label className="flex items-center gap-2">
            {choice.keyTime}
            <input type="time" value={clock} onChange={(e) => setClock(e.target.value)}
              className="px-2 py-1 border border-slate-400 rounded-md" />
          </label>
          <span className="text-xs text-slate-600">Leave empty if not known</span>
          <button type="submit" disabled={sending} className="px-3 py-1 bg-slate-900 text-white rounded-lg disabled:opacity-50">
            {active[choice.value] ? `Update ${choice.label} alert` : `Call ${choice.label} alert`}
          </button>
          <button type="button" onClick={() => setChoice(null)} className="px-2 py-1 text-slate-700 underline">Close</button>
        </form>
      )}
      {alerts.active.length > 0 && (
        <ul className="mt-2 text-sm text-slate-900">
          {alerts.active.map((alert) => (
            <li key={alert.alert} className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{alert.label}</span>
              <span>{alert.key_time_label}: {alert.key_time ? clockTime(alert.key_time) : 'not stated'}</span>
              <button type="button" onClick={() => cancel(alert)} disabled={sending} className="text-xs underline text-slate-700">Cancel</button>
            </li>
          ))}
        </ul>
      )}
      {alerts.latest_change_id && (
        <p className={`mt-2 text-xs ${alerts.unacknowledged ? 'text-amber-700' : 'text-gray-600'}`}>
          {alerts.unacknowledged ? 'The hospital has not acknowledged the latest alert yet'
            : `Acknowledged by ${alerts.acknowledgment.user_name} · ${clockTime(alerts.acknowledgment.acknowledged_at)}`}
        </p>
      )}
    </div>
  );
};

export default CrewAlerts;
