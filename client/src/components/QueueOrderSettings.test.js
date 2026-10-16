import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import toast from 'react-hot-toast';
import QueueOrderSettings from './QueueOrderSettings';

jest.mock('axios', () => ({ __esModule: true, default: { put: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  jest.clearAllMocks();
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));

const rules = { standard: 'Standard rule text.', ai_assisted: 'AI rule text.' };
const render = (props = {}) => act(() => root.render(<QueueOrderSettings mode="standard" rules={rules} onChanged={() => {}} {...props} />));
const radio = (value) => container.querySelector(`input[value="${value}"]`);

test('three modes as cards, each with the server\'s own description of its rule, the current one selected', () => {
  render({ mode: 'ai_assisted' });
  expect([...container.querySelectorAll('label')].map((l) => l.querySelector('span span').textContent)).toEqual(['Standard', 'AI-assisted', 'Manual']);
  expect(container.textContent).toContain('Standard rule text.');
  expect(container.textContent).toContain('AI rule text.');
  expect(container.textContent).toContain('Your team drags the cases into order');
  expect(radio('ai_assisted').checked).toBe(true);
  expect(radio('standard').checked).toBe(false);
});

test('choosing another mode saves it for the hospital and reloads; choosing the current one does nothing', async () => {
  axios.put.mockResolvedValue({ data: {} });
  const onChanged = jest.fn();
  render({ onChanged });
  await act(async () => radio('manual').click());
  expect(axios.put).toHaveBeenCalledWith('/api/hospitals/mine/queue-mode', { mode: 'manual' });
  expect(onChanged).toHaveBeenCalledTimes(1);
  render({ mode: 'manual', onChanged });                   // the page reloaded and the server says Manual
  await act(async () => radio('manual').click());          // pressing the current one saves nothing
  expect(axios.put).toHaveBeenCalledTimes(1);
});

test('the card you press shows as chosen at once, before the server answers, and reverts if the save fails', async () => {
  let fail;
  axios.put.mockReturnValue(new Promise((_, reject) => { fail = reject; }));
  render({ mode: 'standard' });
  await act(async () => radio('ai_assisted').click());
  expect(radio('ai_assisted').checked).toBe(true);        // not waiting for the round trip
  expect(radio('standard').checked).toBe(false);
  await act(async () => { fail({ response: { data: { error: 'Nope' } } }); });
  expect(radio('standard').checked).toBe(true);
  expect(radio('ai_assisted').checked).toBe(false);
});

test('a failed save says so and does not reload', async () => {
  axios.put.mockRejectedValue({ response: { data: { error: 'Nope' } } });
  const onChanged = jest.fn();
  render({ onChanged });
  await act(async () => radio('manual').click());
  expect(toast.error).toHaveBeenCalledWith('Nope');
  expect(onChanged).not.toHaveBeenCalled();
});

test('it says the choice is for every doctor, carries the AI disclosure and that the rules are drafts', () => {
  render();
  expect(container.textContent).toContain('every doctor at this hospital');
  expect(container.textContent).toContain('not validated on real EMS audio');
  expect(container.textContent).toContain('drafts a clinician has not yet approved');
  expect(container.textContent).not.toMatch(/smart|draft-\d/i);
});
