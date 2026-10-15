import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import CaseUpdateForm from './CaseUpdateForm';

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

const render = (props = {}) => act(() => root.render(<CaseUpdateForm caseId={7} onAdded={() => {}} {...props} />));
const tab = (label) => [...container.querySelectorAll('[aria-pressed]')].find((b) => b.textContent === label);
const set = (node, value, proto = HTMLInputElement.prototype) => act(() => {
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, value);
  node.dispatchEvent(new Event('input', { bubbles: true }));
});
const submit = () => act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));

test('it starts folded, as one titled row with what it is for, and the form is in the page', () => {
  render();
  const toggle = container.querySelector('button[aria-expanded]');
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(toggle.textContent).toContain('Type an update');
  expect(toggle.textContent).toContain('vitals, a note, a correction or a new ETA');
  expect(container.querySelector('form')).not.toBeNull();
  expect(container.querySelector('details, summary')).toBeNull();
});

test('the kind of update is chosen with underlined tabs, one always chosen, never a row of pills', () => {
  render();
  expect([...container.querySelectorAll('[aria-pressed]')].map((b) => b.textContent)).toEqual(['Vitals', 'Note', 'Correction', 'ETA']);
  expect(tab('Vitals').getAttribute('aria-pressed')).toBe('true');
  act(() => tab('Note').click());
  expect(tab('Note').getAttribute('aria-pressed')).toBe('true');
  expect(tab('Vitals').getAttribute('aria-pressed')).toBe('false');
  act(() => tab('Note').click());                              // pressing the chosen one does not un-choose it
  expect(tab('Note').getAttribute('aria-pressed')).toBe('true');
  expect(container.innerHTML).not.toMatch(/rounded-full/);
});

test('each kind shows its own fields', () => {
  render();
  expect(container.querySelectorAll('input[type="number"]').length).toBeGreaterThan(4);   // the vital signs
  act(() => tab('Note').click());
  expect(container.querySelector('textarea')).not.toBeNull();
  expect(container.querySelectorAll('input[type="number"]')).toHaveLength(0);
  act(() => tab('ETA').click());
  expect(container.querySelector('textarea')).toBeNull();
  expect(container.textContent).toContain('Arriving in');
});

test('sending a note posts it once with a client id, tells the screen, and clears the box', async () => {
  axios.post.mockResolvedValue({ data: { id: 1 } });
  const onAdded = jest.fn();
  render({ onAdded });
  act(() => tab('Note').click());
  set(container.querySelector('textarea'), 'Pale and sweating', HTMLTextAreaElement.prototype);
  await submit();
  expect(axios.post).toHaveBeenCalledTimes(1);
  const [url, payload] = axios.post.mock.calls[0];
  expect(url).toBe('/api/cases/7/updates');
  expect(payload).toMatchObject({ kind: 'note', body: 'Pale and sweating' });
  expect(payload.client_id).toBeTruthy();
  expect(onAdded).toHaveBeenCalledWith({ id: 1 });
  expect(container.querySelector('textarea').value).toBe('');
});

test('an empty update is not sent', async () => {
  render();
  await submit();
  expect(axios.post).not.toHaveBeenCalled();
});

test('the send button is the one blue action', () => {
  render();
  const send = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Send update');
  expect(send.className).toContain('bg-blue-600');
});
