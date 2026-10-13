import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import toast from 'react-hot-toast';
import CrewAlerts, { CrewAlertBadges, CrewAlertPanel, CrewAlertSound, keyTimeFromClock } from './CrewAlerts';

jest.mock('axios', () => ({ __esModule: true, default: { post: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  jest.clearAllMocks();
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const now = Date.parse('2026-10-13T15:02:00Z');
const stroke = (id, keyTime = '2026-10-13T14:10:00Z') => ({
  id, alert: 'stroke', action: 'called', label: 'Stroke', key_time_label: 'Last known well', key_time: keyTime,
  author_name: 'Mike Wilson', created_at: '2026-10-13T14:55:00Z',
});
const withAlerts = (id, active, latest, unacknowledged = true) => ({
  id, status: 'active', crew_alerts: {
    active, latest_change_id: latest, unacknowledged,
    acknowledgment: unacknowledged ? null : { through_alert_id: latest, user_name: 'John Smith', acknowledged_at: '2026-10-13T14:58:00Z' },
  },
});

test('a queue row shows the crew alert with its key time counting up, and a missing time as not stated', () => {
  act(() => root.render(<CrewAlertBadges liveCase={withAlerts(1, [stroke(3)], 3)} serverNow={now} />));
  expect(container.textContent).toContain('Crew alert: Stroke · LKW');
  expect(container.textContent).toContain('(52 min ago)');
  expect(container.textContent).toContain('Alert not yet acknowledged');
  act(() => root.render(<CrewAlertBadges liveCase={withAlerts(1, [stroke(3, null)], 3, false)} serverNow={now} />));
  expect(container.textContent).toBe('Crew alert: Stroke · LKW not stated');
  act(() => root.render(<CrewAlertBadges liveCase={{ id: 1, crew_alerts: { active: [], latest_change_id: null, unacknowledged: false } }} serverNow={now} />));
  expect(container.textContent).toBe('');
});

test('the chime rings for a new crew call only: not on page load, not for an acknowledgment', () => {
  const play = jest.fn();
  localStorage.setItem('crewAlertSound', '1');
  const render = (cases) => act(() => root.render(<CrewAlertSound cases={cases} play={play} />));
  render([withAlerts(1, [stroke(3)], 3)]);
  expect(play).not.toHaveBeenCalled();
  render([withAlerts(1, [stroke(3)], 3, false)]);  // acknowledged: same change
  expect(play).not.toHaveBeenCalled();
  render([withAlerts(1, [stroke(3)], 3, false), withAlerts(2, [], null, false)]);
  expect(play).not.toHaveBeenCalled();
  render([withAlerts(1, [stroke(3)], 3, false), withAlerts(2, [{ ...stroke(7), alert: 'stemi' }], 7)]);
  expect(play).toHaveBeenCalledTimes(1);
  render([withAlerts(1, [], 9)]);  // a cancellation is shown, not rung
  expect(play).toHaveBeenCalledTimes(1);
});

test('the chime stays silent until switched on', () => {
  const play = jest.fn();
  localStorage.setItem('crewAlertSound', '0');
  act(() => root.render(<CrewAlertSound cases={[withAlerts(1, [], null, false)]} play={play} />));
  act(() => root.render(<CrewAlertSound cases={[withAlerts(1, [stroke(3)], 3)]} play={play} />));
  expect(play).not.toHaveBeenCalled();
});

test('the hospital acknowledges the alert change it is looking at', async () => {
  const onChanged = jest.fn();
  axios.post.mockResolvedValue({ data: { id: 1, crew_alerts: {} } });
  act(() => root.render(<CrewAlertPanel liveCase={withAlerts(1, [stroke(3)], 4)} serverNow={now} onChanged={onChanged} />));
  expect(container.textContent).toContain('Last known well:');
  expect(container.textContent).toContain('called by Mike Wilson');
  await act(async () => container.querySelector('button').click());
  expect(axios.post).toHaveBeenCalledWith('/api/cases/1/alerts/acknowledge', { through_alert_id: 4 });
  expect(onChanged).toHaveBeenCalled();
  act(() => root.render(<CrewAlertPanel liveCase={withAlerts(1, [stroke(3)], 4, false)} serverNow={now} />));
  expect(container.textContent).toContain('Alert acknowledged by John Smith');
});

test('the crew calls an alert with the key time it typed', async () => {
  axios.post.mockResolvedValue({ data: { action: 'called' } });
  act(() => root.render(<CrewAlerts liveCase={withAlerts(1, [], null, false)} />));
  const strokeButton = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Stroke');
  act(() => strokeButton.click());
  const input = container.querySelector('input[type="time"]');
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '14:10');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  const [url, body] = axios.post.mock.calls[0];
  expect(url).toBe('/api/cases/1/alerts');
  expect(body).toMatchObject({ alert: 'stroke', action: 'called' });
  expect(new Date(body.key_time).getHours()).toBe(14);
  expect(body.client_id).toBeTruthy();
});

test('after a lost reply, a different change gets a new id and a mismatched reply is not called success', async () => {
  // From Astra's Oct 13 audit (R3): the call committed but its reply was lost; the live stream shows the call.
  axios.post.mockRejectedValueOnce(new Error('reply lost'));
  act(() => root.render(<CrewAlerts liveCase={withAlerts(1, [], null, false)} />));
  act(() => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Stroke').click());
  await act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  act(() => root.render(<CrewAlerts liveCase={withAlerts(1, [stroke(3)], 3)} />));
  window.confirm = jest.fn(() => true);
  axios.post.mockResolvedValueOnce({ data: stroke(3) });  // a server answering with the old call
  await act(async () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Cancel').click());
  const [first, second] = axios.post.mock.calls.map(([, body]) => body);
  expect([first.action, second.action]).toEqual(['called', 'cancelled']);
  expect(second.client_id).not.toBe(first.client_id);
  expect(toast.success).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalled();
});

test('a typed key time later than now means yesterday', () => {
  const evening = new Date(2026, 9, 13, 0, 20).getTime();
  const lkw = new Date(keyTimeFromClock('23:50', evening));
  expect(lkw.getDate()).toBe(12);
  expect(keyTimeFromClock('', evening)).toBeNull();
});
