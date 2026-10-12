import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import HospitalWorkflowSettings, { ArrivalHighlight } from './HospitalWorkflowSettings';

jest.mock('axios', () => ({ __esModule: true, default: { patch: jest.fn(), get: jest.fn() } }));
let container;
let root;
beforeEach(() => {
  jest.clearAllMocks();
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));
const hospital = { id: 1, arrival_highlight_minutes: 5, settings_version: 1 };
const now = Date.parse('2026-10-11T19:00:00Z');
const inbound = { status: 'active', eta_at: '2026-10-11T19:10:00Z' };
const edit = (value) => {
  const input = container.querySelector('input');
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
};
const submit = () => act(async () => container.querySelector('form')
  .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));

test('two hospitals highlight the same arrival differently, including the exact boundary', () => {
  act(() => root.render(<ArrivalHighlight liveCase={inbound} serverNow={now} minutes={5} />));
  expect(container.textContent).toBe('');
  act(() => root.render(<ArrivalHighlight liveCase={inbound} serverNow={now} minutes={10} />));
  expect(container.textContent).toBe('Arriving soon');
  act(() => root.render(<ArrivalHighlight liveCase={inbound} serverNow={now + 600001} minutes={10} />));
  expect(container.textContent).toBe(''); // overdue is already named explicitly by etaText
});

test.each([{ eta_at: null }, { eta_at: 'bad' }, { arrived_at: 'now' }, { status: 'closed' }])(
  'missing ETA and completed transports are not highlighted: %j', (overrides) => {
    act(() => root.render(<ArrivalHighlight liveCase={{ ...inbound, ...overrides }} serverNow={now} minutes={15} />));
    expect(container.textContent).toBe('');
  },
);

test('saves a versioned setting to the signed-in hospital', async () => {
  const onChange = jest.fn();
  const saved = { ...hospital, arrival_highlight_minutes: 15, settings_version: 2 };
  axios.patch.mockResolvedValue({ data: saved });
  act(() => root.render(<HospitalWorkflowSettings hospital={hospital} onChange={onChange} />));
  edit('15');
  await submit();
  expect(axios.patch).toHaveBeenCalledWith('/api/hospitals/mine', {
    arrival_highlight_minutes: 15, expected_version: 1,
  });
  expect(onChange).toHaveBeenCalledWith(saved);
  expect(container.textContent).toContain('Saved for this hospital.');
});

test('a conflicting save reloads the latest settings and asks the user to review', async () => {
  const onChange = jest.fn();
  const saved = { ...hospital, arrival_highlight_minutes: 20, settings_version: 2 };
  axios.patch.mockRejectedValue({ response: { status: 409, data: { error: 'This setting changed.' } } });
  axios.get.mockResolvedValue({ data: saved });
  act(() => root.render(<HospitalWorkflowSettings hospital={hospital} onChange={onChange} />));
  edit('15');
  await submit();
  expect(onChange).toHaveBeenCalledWith(saved);
  expect(container.textContent).toContain('review it before saving again');
  expect(axios.patch).toHaveBeenCalledTimes(1);
});
