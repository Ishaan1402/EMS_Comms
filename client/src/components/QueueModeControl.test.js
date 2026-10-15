import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import toast from 'react-hot-toast';
import QueueModeControl from './QueueModeControl';

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

const rules = { standard: 'Standard text.', ai_assisted: 'AI-assisted text.' };
const versions = { standard: 'draft-4', ai_assisted: 'ai-assisted-1' };
const render = (props = {}) => act(() => root.render(
  <QueueModeControl mode="standard" rules={rules} ruleVersions={versions} onChange={() => {}} {...props} />,
));
const radio = (label) => [...container.querySelectorAll('[role="radio"]')].find((r) => r.textContent === label);
const help = () => container.querySelector('#queue-order-help');
const info = () => container.querySelector('[aria-label="How the queue is ordered"]');

test('three choices, one selected, named for what they are', () => {
  render({ mode: 'ai_assisted' });
  expect([...container.querySelectorAll('[role="radio"]')].map((r) => r.textContent)).toEqual(['Standard', 'AI-assisted', 'Manual']);
  expect(radio('AI-assisted').getAttribute('aria-checked')).toBe('true');
  expect(radio('Standard').getAttribute('aria-checked')).toBe('false');
  expect(container.querySelector('[role="radiogroup"]').getAttribute('aria-label')).toBe('Queue order');
});

test('choosing a mode saves it for the hospital and then asks the screen to refresh the queue', async () => {
  axios.put.mockResolvedValue({ data: {} });
  const onChange = jest.fn();
  render({ onChange });
  await act(async () => radio('AI-assisted').click());
  expect(axios.put).toHaveBeenCalledWith('/api/hospitals/mine/queue-mode', { mode: 'ai_assisted' });
  expect(onChange).toHaveBeenCalledTimes(1);
  await act(async () => radio('Standard').click());   // already selected: nothing to save
  expect(axios.put).toHaveBeenCalledTimes(1);
});

test('a failed save says so and does not refresh', async () => {
  axios.put.mockRejectedValue({ response: { data: { error: 'Nope' } } });
  const onChange = jest.fn();
  render({ onChange });
  await act(async () => radio('Manual').click());
  expect(toast.error).toHaveBeenCalledWith('Nope');
  expect(onChange).not.toHaveBeenCalled();
});

test('the explanation and the AI disclosure are in the page while closed, and open with the info button', () => {
  render();
  expect(help().hidden).toBe(true);
  expect(help().textContent).toContain('Standard text.');
  expect(help().textContent).toContain('AI-assisted text.');
  expect(help().textContent).toContain('Your team orders the queue with the arrows');
  expect(help().textContent).toContain('not validated on real EMS audio');
  expect(help().textContent).toContain('Rules draft-4 and ai-assisted-1 are drafts a clinician has not yet approved.');
  act(() => info().click());
  expect(help().hidden).toBe(false);
  expect(info().getAttribute('aria-expanded')).toBe('true');
});

test('Escape and a click elsewhere close the explanation; a click inside does not', () => {
  render();
  act(() => info().click());
  act(() => help().dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
  expect(help().hidden).toBe(false);
  act(() => document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
  expect(help().hidden).toBe(true);
  act(() => info().click());
  act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
  expect(help().hidden).toBe(true);
});
