import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import CustomFieldsSettings, { MAX_FIELDS } from './CustomFieldsSettings';

jest.mock('axios', () => ({ __esModule: true, default: { post: jest.fn(), delete: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: Object.assign(jest.fn(), { error: jest.fn() }) }));

let container;
let root;
beforeEach(() => {
  jest.clearAllMocks();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  window.confirm = jest.fn(() => true);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

const fields = [{ id: 1, label: 'On blood thinners', kind: 'yes_no' }, { id: 2, label: 'Frailty score', kind: 'number' }];
const render = (props = {}) => act(() => root.render(<CustomFieldsSettings fields={fields} onChanged={() => {}} {...props} />));
const type = (value) => act(() => {
  const input = container.querySelector('input');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
const submit = () => act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));

test('the fields are a table of name and answer type, with a remove button for each, and a plain explanation', () => {
  render();
  const rows = [...container.querySelectorAll('tbody tr')].map((r) => [...r.querySelectorAll('td')].slice(0, 2).map((c) => c.textContent));
  expect(rows).toEqual([['On blood thinners', 'Yes / no'], ['Frailty score', 'Number']]);
  expect(container.querySelector('[aria-label="Remove Frailty score"]')).not.toBeNull();
  expect(container.textContent).toContain('never change the queue order');
  expect(container.querySelector('details, summary, select')).toBeNull();
});

test('no fields yet says so and still offers the add row', () => {
  render({ fields: [] });
  expect(container.textContent).toContain('No custom fields yet.');
  expect(container.querySelector('input[placeholder="e.g. On blood thinners"]')).not.toBeNull();
  expect(container.textContent).toContain('0 of 10');
});

test('adding sends the name and the chosen answer type, clears the box and asks the page to reload', async () => {
  axios.post.mockResolvedValue({ data: {} });
  const onChanged = jest.fn();
  render({ onChanged });
  type('Pregnant');
  act(() => [...container.querySelectorAll('[aria-haspopup]')][0].click());
  act(() => [...container.querySelectorAll('[role="option"]')].find((o) => o.textContent.startsWith('Short text')).click());
  await submit();
  expect(axios.post).toHaveBeenCalledWith('/api/hospitals/mine/risk-fields', { label: 'Pregnant', kind: 'text' });
  expect(onChanged).toHaveBeenCalledTimes(1);
  expect(container.querySelector('input').value).toBe('');
});

test('removing asks first, then deletes and reloads; declining deletes nothing', async () => {
  axios.delete.mockResolvedValue({});
  const onChanged = jest.fn();
  render({ onChanged });
  window.confirm.mockReturnValueOnce(false);
  await act(async () => container.querySelector('[aria-label="Remove Frailty score"]').click());
  expect(axios.delete).not.toHaveBeenCalled();
  await act(async () => container.querySelector('[aria-label="Remove Frailty score"]').click());
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining('Frailty score'));
  expect(axios.delete).toHaveBeenCalledWith('/api/hospitals/mine/risk-fields/2');
  expect(onChanged).toHaveBeenCalledTimes(1);
});

test('the add button needs a name, and the ten-field limit stops it', () => {
  render({ fields: [] });
  const add = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Add field');
  expect(add().disabled).toBe(true);
  type('Pregnant');
  expect(add().disabled).toBe(false);
  render({ fields: Array.from({ length: MAX_FIELDS }, (_, i) => ({ id: i, label: `F${i}`, kind: 'text' })) });
  expect(container.querySelector('input').disabled).toBe(true);
  expect(add().disabled).toBe(true);
  expect(container.textContent).toContain('10 of 10');
});
