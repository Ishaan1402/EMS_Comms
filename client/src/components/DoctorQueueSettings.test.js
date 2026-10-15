import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { CaseRiskFields } from './DoctorQueueSettings';
const mockRequests = [];
jest.mock('axios', () => ({ __esModule: true, default: {
  get: (url) => new Promise((resolve) => mockRequests.push({ method: 'get', url, resolve })),
  put: (url, body) => new Promise((resolve) => mockRequests.push({ method: 'put', url, body, resolve })),
} }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: { error: jest.fn() } }));
let root;
let container;
const field = { id: 1, label: 'On blood thinners', kind: 'yes_no' };
const render = (caseId, liveValues, fields = [field], refreshToken = 0) => act(() => root.render(
  <CaseRiskFields caseId={caseId} fields={fields} liveValues={liveValues} refreshToken={refreshToken} />));
const reply = async (method, caseId, values) => {
  const index = mockRequests.findIndex((r) => r.method === method && r.url === `/api/cases/${caseId}/risk-values`);
  const request = mockRequests.splice(index, 1)[0];
  if (!request) throw new Error(`Missing ${method} for case ${caseId}`);
  await act(async () => request.resolve({ data: { values } }));
};
const choice = (label) => [...container.querySelectorAll('button')].find((b) => b.textContent === label);
beforeEach(() => { mockRequests.length = 0; container = document.createElement('div'); root = createRoot(container); });
afterEach(() => act(() => root.unmount()));

test('a late custom-field save for patient A never appears on patient B', async () => {
  render(1); await reply('get', 1, { 1: false });
  act(() => choice('Yes').click());
  render(2); await reply('get', 2, { 1: false });
  await reply('put', 1, { 1: true });
  expect(choice('Yes').getAttribute('aria-pressed')).toBe('false');
  expect(choice('No').getAttribute('aria-pressed')).toBe('true');
});

test('a live field update supersedes an older initial fetch', async () => {
  render(1);
  render(1, { case_id: 1, values: { 1: true } });
  await reply('get', 1, { 1: false });
  expect(choice('Yes').getAttribute('aria-pressed')).toBe('true');
});

test('an unsaved text field is not carried into the next patient', async () => {
  const fields = [{ id: 2, label: 'Transport note', kind: 'text' }];
  render(1, null, fields); await reply('get', 1, {});
  act(() => {
    const input = container.querySelector('input');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'only patient A');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(container.querySelector('input').value).toBe('only patient A');
  render(2, null, fields); await reply('get', 2, {});
  expect(container.querySelector('input').value).toBe('');
});


test('a reconnect refresh reads field changes missed while disconnected', async () => {
  render(1); await reply('get', 1, { 1: false });
  render(1, undefined, [field], 1); await reply('get', 1, { 1: true });
  expect(choice('Yes').getAttribute('aria-pressed')).toBe('true');
});

test('initial cached live values do not prevent a fresh read when revisiting a patient', async () => {
  render(1, { case_id: 1, values: { 1: false } });
  await reply('get', 1, { 1: true });
  expect(choice('Yes').getAttribute('aria-pressed')).toBe('true');
});
