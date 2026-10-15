import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import SettingsPage from './SettingsPage';

jest.mock('axios', () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  jest.clearAllMocks();
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const hospital = { id: 1, name: 'General', arrival_highlight_minutes: 5, settings_version: 1 };
const answer = (riskFields) => axios.get.mockImplementation((url) => Promise.resolve({
  data: url === '/api/hospitals/mine' ? hospital : { risk_fields: riskFields },
}));

test('shows the custom risk fields and the arrival alert, each in its own section', async () => {
  answer([{ id: 1, label: 'On blood thinners', kind: 'yes_no' }]);
  await act(async () => root.render(<SettingsPage />));
  expect(container.querySelector('h1').textContent).toBe('Settings');
  const sections = [...container.querySelectorAll('section')];
  expect(sections).toHaveLength(3);                       // the page card, the fields, the arrival card
  expect(container.textContent).toContain('Custom risk fields (1)');
  expect(container.textContent).toContain('On blood thinners');
  expect(container.textContent).toContain('Arrival highlight · 5 min');
});

test('adding a field reloads the list, since this page has no live stream', async () => {
  answer([]);
  axios.post.mockResolvedValue({ data: {} });
  await act(async () => root.render(<SettingsPage />));
  const loads = () => axios.get.mock.calls.filter(([url]) => url === '/api/hospitals/mine/config').length;
  expect(loads()).toBe(1);
  const input = container.querySelector('input[placeholder="e.g. On blood thinners"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'Frailty score');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(axios.post).toHaveBeenCalledWith('/api/hospitals/mine/risk-fields', { label: 'Frailty score', kind: 'yes_no' });
  expect(loads()).toBe(2);
});
