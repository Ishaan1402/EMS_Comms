import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import SettingsPage from './SettingsPage';

jest.mock('axios', () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  jest.clearAllMocks();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

const hospital = { id: 1, name: 'General', arrival_highlight_minutes: 5, settings_version: 1 };
const rules = { standard: 'Standard rule text.', ai_assisted: 'AI rule text.' };
const answer = (config = {}) => axios.get.mockImplementation((url) => Promise.resolve({
  data: url === '/api/hospitals/mine' ? hospital : { queue_mode: 'standard', rules, risk_fields: [], ...config },
}));
const render = async () => act(async () => root.render(<SettingsPage />));
const nav = (title) => [...container.querySelectorAll('nav button')].find((b) => b.textContent === title);

test('a left navigation with three sections, opening on the queue order, with the hospital named', async () => {
  answer();
  await render();
  expect(container.querySelector('h1').textContent).toBe('Settings');
  expect(container.textContent).toContain('General');
  expect([...container.querySelectorAll('nav button')].map((b) => b.textContent)).toEqual(['Queue order', 'Custom fields', 'Arrival alert']);
  expect(nav('Queue order').getAttribute('aria-current')).toBe('page');
  expect(container.querySelector('h2').textContent).toBe('Queue order');
});

test('choosing a section shows only that section', async () => {
  answer({ risk_fields: [{ id: 1, label: 'On blood thinners', kind: 'yes_no' }] });
  await render();
  expect(container.textContent).not.toContain('On blood thinners');
  act(() => nav('Custom fields').click());
  expect(container.querySelector('h2').textContent).toBe('Custom fields');
  expect(nav('Custom fields').getAttribute('aria-current')).toBe('page');
  expect(container.textContent).toContain('On blood thinners');
  act(() => nav('Arrival alert').click());
  expect(container.querySelector('input[type="number"]').value).toBe('5');
  expect(container.textContent).not.toContain('On blood thinners');
});

test('the arrival alert is a plain form: no accordion, nothing to open first', async () => {
  answer();
  await render();
  act(() => nav('Arrival alert').click());
  expect(container.querySelector('details, summary')).toBeNull();
  expect(container.textContent).toContain('Highlight arrivals within');
});

test('changing the order in Settings saves it and reloads what the page shows', async () => {
  answer();
  axios.put.mockResolvedValue({ data: {} });
  await render();
  const loads = () => axios.get.mock.calls.filter(([url]) => url === '/api/hospitals/mine/config').length;
  expect(loads()).toBe(1);
  await act(async () => container.querySelector('input[value="ai_assisted"]').click());
  expect(axios.put).toHaveBeenCalledWith('/api/hospitals/mine/queue-mode', { mode: 'ai_assisted' });
  expect(loads()).toBe(2);
});
