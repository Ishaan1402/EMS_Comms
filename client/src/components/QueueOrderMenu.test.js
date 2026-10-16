import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import toast from 'react-hot-toast';
import QueueOrderMenu from './QueueOrderMenu';

jest.mock('axios', () => ({ __esModule: true, default: { put: jest.fn() } }));
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

const render = (props = {}) => act(() => root.render(<QueueOrderMenu mode="standard" onChange={() => {}} {...props} />));
const open = () => act(() => container.querySelector('[aria-haspopup]').click());
const option = (label) => [...container.querySelectorAll('[role="option"]')].find((o) => o.textContent.startsWith(label));

test('the mode in use is the button; the three modes are named plainly, never "Smart"', () => {
  render({ mode: 'ai_assisted' });
  expect(container.querySelector('[aria-haspopup]').textContent).toBe('Order: AI-assisted');
  open();
  expect([...container.querySelectorAll('[role="option"]')].map((o) => o.querySelector('span span').textContent))
    .toEqual(['Standard', 'AI-assisted', 'Manual']);
  expect(container.textContent).not.toMatch(/smart/i);
});

test('each mode says what it does in one line; the AI is never said to set the order alone', () => {
  render();
  open();
  expect(option('Standard').textContent).toContain('Crew alerts and unacknowledged information first');
  expect(option('AI-assisted').textContent).toContain("A person's call still comes first");
  expect(option('Manual').textContent).toContain('Your team drags the cases into order');
});

test('choosing a mode saves it for the hospital and then asks the screen to refresh the queue', async () => {
  axios.put.mockResolvedValue({ data: {} });
  const onChange = jest.fn();
  render({ onChange });
  open();
  await act(async () => option('AI-assisted').click());
  expect(axios.put).toHaveBeenCalledWith('/api/hospitals/mine/queue-mode', { mode: 'ai_assisted' });
  expect(onChange).toHaveBeenCalledTimes(1);
});

test('a failed save says so and does not refresh', async () => {
  axios.put.mockRejectedValue({ response: { data: { error: 'Nope' } } });
  const onChange = jest.fn();
  render({ onChange });
  open();
  await act(async () => option('Manual').click());
  expect(toast.error).toHaveBeenCalledWith('Nope');
  expect(onChange).not.toHaveBeenCalled();
});

test('the menu carries the AI disclosure and that the rules are drafts, without version numbers', () => {
  render();
  open();
  expect(container.textContent).toContain('not validated on real EMS audio');
  expect(container.textContent).toContain('drafts a clinician has not yet approved');
  expect(container.textContent).not.toMatch(/draft-\d|ai-assisted-1/);
});
